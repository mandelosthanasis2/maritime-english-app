import logging
import uuid
from datetime import datetime, timedelta, timezone

from flask import Flask, Response, jsonify, request
from flask_cors import CORS
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from admin import (
    ALLOWED_DIFFICULTY,
    ALLOWED_ROLE_CATEGORIES,
    ALLOWED_SKILL_TYPES,
    ALLOWED_TRACKS,
    MAX_ITEMS_PER_LESSON,
    AdminGenError,
    analyze_lesson_gaps,
    auto_categorize_lessons,
    extract_text_from_pdf,
    generate_enrichment_items,
    generate_lessons,
    generate_teaching_for_lesson,
    item_signature,
    lesson_category_samples,
    lesson_digest,
)
from adaptive import choose_next, choose_next_lesson
from auth import ADMIN_API_KEY_HEADER, AuthError, verify_admin, verify_request
from db import SessionLocal, init_db
from rate_limit import RateLimiter
from models import Item, Lesson, UserItemStat, UserLessonCompletion, UserProgress
from placement import (
    grade_answer,
    score_grammar,
    score_maritime,
    section_for_track,
    select_questions,
)
from pronunciation import PronunciationError, assess_pronunciation
from roleplay import RoleplayError, chat as roleplay_chat
from transcription import transcribe
from tts import synthesize as synthesize_speech

# Rate limit for the expensive admin generation endpoint (per caller). Generous
# enough never to bother a human admin in the /admin UI, but it caps a headless
# agent's Claude usage. See rate_limit.py for the per-process caveat.
_GENERATE_LIMITER = RateLimiter(max_calls=10, period=60)


def _admin_rate_key(request):
    """A per-caller key for rate limiting that never logs/stores the secret."""
    api_key = request.headers.get(ADMIN_API_KEY_HEADER)
    if api_key:
        # Bucket all API-key traffic together without keying on the secret value.
        return "admin-api-key"
    return f"ip:{request.remote_addr or 'unknown'}"


# XP awards.
XP_FIRST_COMPLETION = 50
XP_REVIEW = 10
# Smart-practice answers (recorded via /api/lessons/<id>/answer).
XP_PRACTICE_CORRECT = 5
XP_PRACTICE_WRONG = 1

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Allow the frontend (Vercel) to call the API from the browser. Open to all
# origins for now; we'll lock this down later.
CORS(app)

# Create tables on startup if they don't exist. Guarded so a database hiccup
# never takes down the health check.
try:
    init_db()
    logger.info("Database tables initialized.")
except Exception as exc:  # pragma: no cover - startup best effort
    logger.warning("Could not initialize database on startup: %s", exc)

# Apply schema migrations on startup (idempotent, concurrency-safe). This adds
# the editorial columns and relaxes items.lesson_id to NULLABLE so generated
# drafts can be stored. Guarded so a migration hiccup never takes down the app.
try:
    from migrate import run as run_migrations

    run_migrations()
except Exception as exc:  # pragma: no cover - startup best effort
    logger.warning("Could not apply migrations on startup: %s", exc)


# --- Serialization helpers ---------------------------------------------------


def serialize_lesson_meta(lesson, item_count):
    """Lesson metadata only (no items)."""
    return {
        "lesson_id": lesson.lesson_id,
        "track": lesson.track,
        "role_category": lesson.role_category or "common",
        "module": lesson.module,
        "title": lesson.title,
        "description": lesson.description,
        "source": lesson.source,
        "interface_language": lesson.interface_language,
        "target_language": lesson.target_language,
        "version": lesson.version,
        "item_count": item_count,
    }


def serialize_item(item):
    """A single item: identifying columns plus its full rich `data` object."""
    return {
        "item_id": item.item_id,
        "type": item.type,
        "level": item.level,
        "difficulty": item.difficulty,
        "status": item.status,
        "skill_type": item.skill_type,
        "data": item.data,
    }


def item_counts(session):
    """Return a {lesson_id: approved-item count} map in a single query."""
    rows = (
        session.query(Item.lesson_id, func.count(Item.id))
        .filter(Item.status == "approved")
        .group_by(Item.lesson_id)
        .all()
    )
    return dict(rows)


# --- Progress helpers --------------------------------------------------------


def get_or_create_progress(session, user_id, email=None):
    progress = session.get(UserProgress, user_id)
    if progress is None:
        progress = UserProgress(user_id=user_id, email=email, total_xp=0, current_streak=0)
        session.add(progress)
        session.flush()
    elif email and progress.email != email:
        progress.email = email
    return progress


def touch_streak(progress, today):
    """Count today as activity for the streak (used by lessons and practice)."""
    last = progress.last_active_date
    if last is None or last < today - timedelta(days=1):
        progress.current_streak = 1  # first activity, or a gap > 1 day
    elif last == today - timedelta(days=1):
        progress.current_streak += 1  # consecutive day
    # last == today -> already counted today, leave streak unchanged
    progress.last_active_date = today


def serialize_progress(session, progress):
    rows = (
        session.query(UserLessonCompletion.lesson_id)
        .filter_by(user_id=progress.user_id)
        .all()
    )
    completed_ids = [r[0] for r in rows]
    return {
        "total_xp": progress.total_xp,
        "current_streak": progress.current_streak,
        "last_active_date": progress.last_active_date.isoformat()
        if progress.last_active_date
        else None,
        "completed_lesson_ids": completed_ids,
        "lessons_completed": len(completed_ids),
        # Placement results; null until the user takes the placement test.
        "cefr_level": progress.cefr_level,
        "maritime_level": progress.maritime_level,
        # Onboarding role; null until chosen.
        "user_role": progress.user_role,
    }


# --- Routes ------------------------------------------------------------------


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


@app.route("/api/lessons", methods=["GET"])
def list_lessons():
    session = SessionLocal()
    try:
        counts = item_counts(session)
        # Only approved lessons are user-visible (drafts stay hidden).
        lessons = (
            session.query(Lesson)
            .filter(Lesson.status == "approved")
            .order_by(Lesson.lesson_id)
            .all()
        )
        return jsonify(
            [serialize_lesson_meta(l, counts.get(l.lesson_id, 0)) for l in lessons]
        )
    finally:
        session.close()


@app.route("/api/lessons/<lesson_id>", methods=["GET"])
def get_lesson(lesson_id):
    session = SessionLocal()
    try:
        lesson = (
            session.query(Lesson)
            .filter_by(lesson_id=lesson_id, status="approved")
            .one_or_none()
        )
        if lesson is None:
            return (
                jsonify({"error": f"Lesson '{lesson_id}' not found."}),
                404,
            )
        # Only approved items are served to learners.
        approved_items = [i for i in lesson.items if i.status == "approved"]
        payload = serialize_lesson_meta(lesson, len(approved_items))
        payload["items"] = [serialize_item(item) for item in approved_items]
        return jsonify(payload)
    finally:
        session.close()


@app.route("/api/tracks/<track>/lessons", methods=["GET"])
def list_lessons_by_track(track):
    session = SessionLocal()
    try:
        counts = item_counts(session)
        lessons = (
            session.query(Lesson)
            .filter_by(track=track, status="approved")
            .order_by(Lesson.lesson_id)
            .all()
        )
        return jsonify(
            [serialize_lesson_meta(l, counts.get(l.lesson_id, 0)) for l in lessons]
        )
    finally:
        session.close()


@app.route("/api/assess-pronunciation", methods=["POST"])
def assess_pronunciation_route():
    audio_file = request.files.get("audio")
    if audio_file is None:
        return jsonify({"error": "Missing 'audio' file."}), 400

    reference_text = request.form.get("reference_text", "")

    audio_bytes = audio_file.read()
    if not audio_bytes:
        return jsonify({"error": "Uploaded audio is empty."}), 400

    try:
        result = assess_pronunciation(audio_bytes, reference_text)
        return jsonify(result)
    except PronunciationError as exc:
        return jsonify({"error": str(exc)}), exc.status_code
    except Exception:  # pragma: no cover - unexpected failure
        logger.exception("Pronunciation assessment failed unexpectedly")
        return jsonify({"error": "Internal error during assessment."}), 500


@app.route("/api/transcribe", methods=["POST"])
def transcribe_route():
    audio_file = request.files.get("audio")
    if audio_file is None:
        return jsonify({"error": "Missing 'audio' file."}), 400

    audio_bytes = audio_file.read()
    if not audio_bytes:
        return jsonify({"error": "Uploaded audio is empty."}), 400

    try:
        return jsonify(transcribe(audio_bytes))
    except PronunciationError as exc:
        return jsonify({"error": str(exc)}), exc.status_code
    except Exception:  # pragma: no cover - unexpected failure
        logger.exception("Transcription failed unexpectedly")
        return jsonify({"error": "Internal error during transcription."}), 500


@app.route("/api/tts", methods=["POST"])
def tts_route():
    payload = request.get_json(silent=True) or {}
    try:
        audio = synthesize_speech(payload.get("text", ""))
        return Response(audio, mimetype="audio/mpeg")
    except PronunciationError as exc:
        return jsonify({"error": str(exc)}), exc.status_code
    except Exception:  # pragma: no cover - unexpected failure
        logger.exception("Text-to-speech failed unexpectedly")
        return jsonify({"error": "Internal error during synthesis."}), 500


@app.route("/api/roleplay/chat", methods=["POST"])
def roleplay_chat_route():
    payload = request.get_json(silent=True) or {}

    try:
        result = roleplay_chat(
            scenario=payload.get("scenario", ""),
            user_role=payload.get("user_role", ""),
            history=payload.get("history", []),
            user_message=payload.get("user_message", ""),
        )
        return jsonify(result)
    except RoleplayError as exc:
        return jsonify({"error": str(exc)}), exc.status_code
    except Exception:  # pragma: no cover - unexpected failure
        logger.exception("Role-play chat failed unexpectedly")
        return jsonify({"error": "Internal error during role-play."}), 500


@app.route("/api/me/progress", methods=["GET"])
def get_my_progress():
    try:
        user_id, email = verify_request(request)
    except AuthError as exc:
        return jsonify({"error": str(exc)}), exc.status_code

    session = SessionLocal()
    try:
        progress = get_or_create_progress(session, user_id, email)
        session.commit()
        return jsonify(serialize_progress(session, progress))
    except Exception:  # pragma: no cover - unexpected failure
        session.rollback()
        logger.exception("Fetching progress failed")
        return jsonify({"error": "Internal error."}), 500
    finally:
        session.close()


@app.route("/api/me/role", methods=["POST"])
def set_my_role():
    """Save the user's onboarding role choice. Body: {"role": engineer|deck|undecided}."""
    try:
        user_id, email = verify_request(request)
    except AuthError as exc:
        return jsonify({"error": str(exc)}), exc.status_code

    payload = request.get_json(silent=True) or {}
    role = payload.get("role")
    if role not in ("engineer", "deck", "undecided"):
        return jsonify({"error": "role must be one of: engineer, deck, undecided."}), 400

    session = SessionLocal()
    try:
        progress = get_or_create_progress(session, user_id, email)
        progress.user_role = role
        session.commit()
        return jsonify(serialize_progress(session, progress))
    except Exception:  # pragma: no cover - unexpected failure
        session.rollback()
        logger.exception("Saving user role failed")
        return jsonify({"error": "Internal error."}), 500
    finally:
        session.close()


@app.route("/api/lessons/<lesson_id>/complete", methods=["POST"])
def complete_lesson(lesson_id):
    try:
        user_id, email = verify_request(request)
    except AuthError as exc:
        return jsonify({"error": str(exc)}), exc.status_code

    session = SessionLocal()
    try:
        lesson = session.query(Lesson).filter_by(lesson_id=lesson_id).one_or_none()
        if lesson is None:
            return jsonify({"error": f"Lesson '{lesson_id}' not found."}), 404

        progress = get_or_create_progress(session, user_id, email)

        completion = (
            session.query(UserLessonCompletion)
            .filter_by(user_id=user_id, lesson_id=lesson_id)
            .one_or_none()
        )
        already_completed = completion is not None
        xp_earned = XP_REVIEW if already_completed else XP_FIRST_COMPLETION

        now = datetime.now(timezone.utc)
        today = now.date()

        if completion is None:
            completion = UserLessonCompletion(
                user_id=user_id,
                lesson_id=lesson_id,
                times_completed=1,
                xp_earned=xp_earned,
                completed_at=now,
            )
            session.add(completion)
        else:
            completion.times_completed += 1
            completion.xp_earned += xp_earned
            completion.completed_at = now

        # Streak: based on the day of the most recent activity.
        touch_streak(progress, today)

        progress.total_xp += xp_earned

        session.commit()

        payload = serialize_progress(session, progress)
        payload["xp_earned"] = xp_earned
        payload["already_completed"] = already_completed
        return jsonify(payload)
    except Exception:  # pragma: no cover - unexpected failure
        session.rollback()
        logger.exception("Completing lesson failed")
        return jsonify({"error": "Internal error."}), 500
    finally:
        session.close()


# --- Placement test ----------------------------------------------------------


def _approved_items_with_track(session, item_ids=None):
    """(Item, lesson_track) pairs for approved items in approved lessons."""
    query = (
        session.query(Item, Lesson.track)
        .join(Lesson, Item.lesson_id == Lesson.lesson_id)
        .filter(Item.status == "approved", Lesson.status == "approved")
    )
    if item_ids is not None:
        query = query.filter(Item.item_id.in_(item_ids))
    return query.all()


@app.route("/api/placement/questions", methods=["GET"])
def placement_questions():
    """A balanced ~10-question placement test drawn from approved items."""
    session = SessionLocal()
    try:
        rows = _approved_items_with_track(session)
        return jsonify({"questions": select_questions(rows)})
    except Exception:  # pragma: no cover - unexpected failure
        logger.exception("Building placement questions failed")
        return jsonify({"error": "Internal error."}), 500
    finally:
        session.close()


@app.route("/api/placement/submit", methods=["POST"])
def placement_submit():
    """Grade placement answers, store cefr_level + maritime_level, return both.

    Body: {"answers": [{"item_id": "...", "answer": <string or list>}, ...]}
    """
    try:
        user_id, email = verify_request(request)
    except AuthError as exc:
        return jsonify({"error": str(exc)}), exc.status_code

    payload = request.get_json(silent=True) or {}
    answers = payload.get("answers")
    if not isinstance(answers, list) or not answers:
        return jsonify({"error": "Body must include a non-empty 'answers' list."}), 400

    session = SessionLocal()
    try:
        item_ids = {
            a.get("item_id") for a in answers if isinstance(a, dict) and a.get("item_id")
        }
        by_id = {
            item.item_id: (item, track)
            for item, track in _approved_items_with_track(session, item_ids)
        }

        grammar_results = []  # (difficulty, is_correct)
        maritime_results = []  # is_correct
        details = []
        graded = set()
        for entry in answers:
            if not isinstance(entry, dict):
                continue
            item_id = entry.get("item_id")
            if item_id in graded:  # ignore duplicate answers for the same item
                continue
            found = by_id.get(item_id)
            if found is None:  # unknown/unapproved item — skip, don't fail
                continue
            graded.add(item_id)
            item, track = found
            correct, expected = grade_answer(item, entry.get("answer"))
            section = section_for_track(track)
            if section == "grammar":
                grammar_results.append((item.difficulty, correct))
            else:
                maritime_results.append(correct)
            details.append(
                {
                    "item_id": item_id,
                    "section": section,
                    "difficulty": item.difficulty,
                    "correct": correct,
                    "correct_answer": expected,
                }
            )

        if not details:
            return jsonify({"error": "No gradable answers were submitted."}), 400

        cefr_level, grammar_breakdown = score_grammar(grammar_results)
        maritime_level, maritime_percent = score_maritime(maritime_results)

        progress = get_or_create_progress(session, user_id, email)
        # Only overwrite a result the test actually measured (e.g. a maritime-only
        # retake must not wipe an existing cefr_level).
        if cefr_level is not None:
            progress.cefr_level = cefr_level
        if maritime_level is not None:
            progress.maritime_level = maritime_level
        session.commit()

        return jsonify(
            {
                "cefr_level": progress.cefr_level,
                "maritime_level": progress.maritime_level,
                "grammar": {
                    "answered": len(grammar_results),
                    "correct": sum(ok for _, ok in grammar_results),
                    "by_level": grammar_breakdown,
                },
                "maritime": {
                    "answered": len(maritime_results),
                    "correct": sum(maritime_results),
                    "percent_correct": maritime_percent,
                },
                "results": details,
            }
        )
    except Exception:  # pragma: no cover - unexpected failure
        session.rollback()
        logger.exception("Placement submit failed")
        return jsonify({"error": "Internal error."}), 500
    finally:
        session.close()


# --- Adaptive engine ----------------------------------------------------------


def serialize_item_stat(stat):
    total = stat.correct_count + stat.wrong_count
    return {
        "item_id": stat.item_id,
        "track": stat.track,
        "skill_type": stat.skill_type,
        "difficulty": stat.difficulty,
        "correct_count": stat.correct_count,
        "wrong_count": stat.wrong_count,
        "success_rate": round(stat.correct_count / total, 3) if total else None,
        "last_correct": stat.last_correct,
        "last_answered_at": stat.last_answered_at.isoformat()
        if stat.last_answered_at
        else None,
    }


@app.route("/api/lessons/<lesson_id>/answer", methods=["POST"])
def record_answer(lesson_id):
    """Record one answered item; feeds the user's adaptive performance stats.

    Body: {"item_id": "...", "correct": true|false}

    Also awards practice XP and counts the day toward the streak, so the smart
    practice flow rewards the user the same way lessons do.
    """
    try:
        user_id, email = verify_request(request)
    except AuthError as exc:
        return jsonify({"error": str(exc)}), exc.status_code

    payload = request.get_json(silent=True) or {}
    item_id = payload.get("item_id")
    correct = payload.get("correct")
    if not item_id or not isinstance(item_id, str):
        return jsonify({"error": "Body must include 'item_id'."}), 400
    if not isinstance(correct, bool):
        return jsonify({"error": "'correct' must be true or false."}), 400

    session = SessionLocal()
    try:
        row = (
            session.query(Item, Lesson.track)
            .join(Lesson, Item.lesson_id == Lesson.lesson_id)
            .filter(Item.item_id == item_id, Item.lesson_id == lesson_id)
            .one_or_none()
        )
        if row is None:
            return (
                jsonify({"error": f"Item '{item_id}' not found in lesson '{lesson_id}'."}),
                404,
            )
        item, lesson_track = row

        stat = (
            session.query(UserItemStat)
            .filter_by(user_id=user_id, item_id=item_id)
            .one_or_none()
        )
        if stat is None:
            stat = UserItemStat(
                user_id=user_id, item_id=item_id, correct_count=0, wrong_count=0
            )
            session.add(stat)
        # Refresh the denormalized fields on every answer so they track edits.
        stat.track = section_for_track(lesson_track)
        stat.skill_type = item.skill_type or item.type
        stat.difficulty = item.difficulty
        if correct:
            stat.correct_count += 1
        else:
            stat.wrong_count += 1
        now = datetime.now(timezone.utc)
        stat.last_correct = correct
        stat.last_answered_at = now

        progress = get_or_create_progress(session, user_id, email)
        xp_earned = XP_PRACTICE_CORRECT if correct else XP_PRACTICE_WRONG
        progress.total_xp += xp_earned
        touch_streak(progress, now.date())

        session.commit()
        payload = serialize_item_stat(stat)
        payload["xp_earned"] = xp_earned
        payload["total_xp"] = progress.total_xp
        payload["current_streak"] = progress.current_streak
        return jsonify(payload)
    except IntegrityError:
        # Two concurrent first-answers raced on the unique (user, item) row;
        # this attempt loses but the answer was equivalent — report gracefully.
        session.rollback()
        logger.warning("Concurrent answer insert for user=%s item=%s", user_id, item_id)
        return jsonify({"error": "Concurrent update, please retry."}), 409
    except Exception:  # pragma: no cover - unexpected failure
        session.rollback()
        logger.exception("Recording answer failed")
        return jsonify({"error": "Internal error."}), 500
    finally:
        session.close()


@app.route("/api/next-exercise", methods=["GET"])
def next_exercise():
    """The adaptive engine's pick for this user's next item (see adaptive.py)."""
    try:
        user_id, email = verify_request(request)
    except AuthError as exc:
        return jsonify({"error": str(exc)}), exc.status_code

    session = SessionLocal()
    try:
        progress = get_or_create_progress(session, user_id, email)
        session.commit()

        rows = _approved_items_with_track(session)
        stats = session.query(UserItemStat).filter_by(user_id=user_id).all()

        choice = choose_next(progress, rows, stats)
        if choice is None:  # empty pool — friendly response, not an error
            return jsonify(
                {"item": None, "message": "Δεν υπάρχουν διαθέσιμες ασκήσεις ακόμη."}
            )

        item, track, meta = choice
        payload = serialize_item(item)
        payload["lesson_id"] = item.lesson_id
        return jsonify({"item": payload, "track": track, "meta": meta})
    except Exception:  # pragma: no cover - unexpected failure
        session.rollback()
        logger.exception("Choosing next exercise failed")
        return jsonify({"error": "Internal error."}), 500
    finally:
        session.close()


@app.route("/api/next-lesson", methods=["GET"])
def next_lesson():
    """The adaptive engine's pick for the user's next WHOLE lesson, with a
    Greek explanation of why (see choose_next_lesson in adaptive.py)."""
    try:
        user_id, email = verify_request(request)
    except AuthError as exc:
        return jsonify({"error": str(exc)}), exc.status_code

    session = SessionLocal()
    try:
        progress = get_or_create_progress(session, user_id, email)
        session.commit()

        # Approved lessons with their approved items, grouped per lesson.
        lessons = session.query(Lesson).filter_by(status="approved").all()
        items_by_lesson = {}
        for item, _track in _approved_items_with_track(session):
            items_by_lesson.setdefault(item.lesson_id, []).append(item)
        lesson_pool = [(l, items_by_lesson.get(l.lesson_id, [])) for l in lessons]

        stats = session.query(UserItemStat).filter_by(user_id=user_id).all()
        completions = (
            session.query(UserLessonCompletion).filter_by(user_id=user_id).all()
        )

        choice = choose_next_lesson(progress, lesson_pool, stats, completions)
        if choice is None:  # empty pool, or everything finished very recently
            return jsonify(
                {"lesson": None, "message": "Δεν υπάρχουν νέα μαθήματα ακόμα."}
            )

        lesson, reason_el, meta = choice
        payload = serialize_lesson_meta(
            lesson, len(items_by_lesson.get(lesson.lesson_id, []))
        )
        payload["title_el"] = lesson.title_el
        return jsonify({"lesson": payload, "reason_el": reason_el, "meta": meta})
    except Exception:  # pragma: no cover - unexpected failure
        session.rollback()
        logger.exception("Choosing next lesson failed")
        return jsonify({"error": "Internal error."}), 500
    finally:
        session.close()


# --- Admin: item generation & curation --------------------------------------

# Source `type` <-> editorial `skill_type` for stored items.
_SKILL_TYPE_FROM_TYPE = {"dialogue": "roleplay", "translation": "speaking"}


def serialize_admin_item(item):
    data = item.data or {}
    return {
        "item_id": item.item_id,
        "lesson_id": item.lesson_id,
        "type": item.type,
        "level": item.level,
        "difficulty": item.difficulty,
        "status": item.status,
        "skill_type": item.skill_type,
        # track lives inside the JSON data for drafts (lessons own the real track).
        "track": data.get("track"),
        "order_index": item.order_index,
        "data": data,
    }


def serialize_admin_lesson(lesson, items):
    return {
        "lesson_id": lesson.lesson_id,
        "title": lesson.title,
        "title_el": lesson.title_el,
        "description": lesson.description,
        "source": lesson.source,
        "track": lesson.track,
        "role_category": lesson.role_category or "common",
        "status": lesson.status,
        # True when items are being attached to an already-approved lesson.
        "existing": lesson.status == "approved",
        "items": [serialize_admin_item(i) for i in items],
    }


def store_generated_item(session, raw, lesson_id, track, fallback_difficulty, order_index):
    """Persist one generated item dict as a draft Item under a lesson; return it."""
    data = dict(raw)
    data.pop("audio_url", None)
    if track:
        data["track"] = track

    item_type = data.get("type") or data.get("skill_type") or "vocabulary"
    skill_type = data.get("skill_type") or _SKILL_TYPE_FROM_TYPE.get(item_type, item_type)
    if skill_type not in ALLOWED_SKILL_TYPES:
        skill_type = _SKILL_TYPE_FROM_TYPE.get(item_type, "vocabulary")

    difficulty = (data.get("difficulty") or data.get("level") or fallback_difficulty)
    if difficulty not in ALLOWED_DIFFICULTY:
        difficulty = fallback_difficulty
    level = data.get("level") if data.get("level") in ALLOWED_DIFFICULTY else difficulty

    item_id = f"draft_{uuid.uuid4().hex[:12]}"
    data["id"] = item_id

    row = Item(
        item_id=item_id,
        lesson_id=lesson_id,
        type=item_type,
        level=level,
        difficulty=difficulty,
        skill_type=skill_type,
        status="draft",
        order_index=order_index,
        data=data,
    )
    session.add(row)
    return row


def dedup_lesson_in_session(session, lesson_id, cap=None):
    """Remove duplicate items (and optionally cap) for a lesson, in `session`.

    Uses the same content signature as generation/enrichment (#56): keep the
    FIRST item of each duplicate group in pedagogical order (order_index, so
    teaching stays first), delete the rest along with their per-item adaptive
    stats. When `cap` is set, also trims to the first `cap` distinct items.

    Flushes pending inserts so freshly-stored items are visible, but does NOT
    commit. Returns (before, removed_count, remaining_count).
    """
    session.flush()
    items = (
        session.query(Item)
        .filter_by(lesson_id=lesson_id)
        .order_by(Item.order_index)
        .all()
    )
    before = len(items)

    seen = set()
    kept, removed = [], []
    for item in items:
        sig = item_signature(item.data or {})
        if sig and sig in seen:
            removed.append(item)
        else:
            if sig:
                seen.add(sig)
            kept.append(item)

    if cap is not None and len(kept) > cap:
        removed.extend(kept[cap:])
        kept = kept[:cap]

    removed_ids = [i.item_id for i in removed]
    if removed_ids:
        session.query(UserItemStat).filter(
            UserItemStat.item_id.in_(removed_ids)
        ).delete(synchronize_session=False)
        for item in removed:
            session.delete(item)
    return before, len(removed), len(kept)


@app.route("/api/admin/generate-items", methods=["POST"])
def admin_generate_items():
    try:
        verify_admin(request)
    except AuthError as exc:
        return jsonify({"error": str(exc)}), exc.status_code

    allowed, retry_after = _GENERATE_LIMITER.check(_admin_rate_key(request))
    if not allowed:
        resp = jsonify(
            {"error": "Πάρα πολλά αιτήματα παραγωγής. Δοκίμασε ξανά σε λίγο."}
        )
        resp.headers["Retry-After"] = str(retry_after)
        return resp, 429

    # Accept multipart (PDF + fields) or a JSON body.
    js = request.get_json(silent=True) or {}

    def field(name, default=""):
        return request.form.get(name) or js.get(name) or default

    kind = field("kind", "auto")
    page_range = field("page_range", "")
    source_text = field("source_text", "")
    pdf_file = request.files.get("pdf")

    session = SessionLocal()
    try:
        if pdf_file is not None:
            pdf_text = extract_text_from_pdf(pdf_file.read(), page_range)
            source_text = (
                f"{pdf_text}\n\n{source_text}".strip() if source_text.strip() else pdf_text
            )
        existing = [
            {"lesson_id": l.lesson_id, "title": l.title, "track": l.track}
            for l in session.query(Lesson).all()
            if l.title
        ]
        proposed = generate_lessons(
            source_text=source_text, kind=kind, existing_lessons=existing
        )
    except AdminGenError as exc:
        session.close()
        return jsonify({"error": str(exc)}), exc.status_code

    try:
        affected = []  # lessons we stored items into (unique, in order)
        for entry in proposed:
            if entry["existing_lesson_id"]:
                lesson = (
                    session.query(Lesson)
                    .filter_by(lesson_id=entry["existing_lesson_id"])
                    .one_or_none()
                )
                if lesson is None:  # vanished between read and write — skip
                    continue
            else:
                lesson = Lesson(
                    lesson_id=f"dl_{uuid.uuid4().hex[:12]}",
                    track=entry["track"],
                    role_category=entry.get("role_category") or "common",
                    module=None,
                    title=entry["title_en"],
                    title_el=entry.get("title_el"),
                    description=entry.get("description_el"),
                    interface_language="el",
                    target_language="en",
                    version=1,
                    status="draft",
                )
                session.add(lesson)
                session.flush()

            base = (
                session.query(func.coalesce(func.max(Item.order_index), -1))
                .filter(Item.lesson_id == lesson.lesson_id)
                .scalar()
            )
            for offset, raw in enumerate(entry["items"], start=1):
                store_generated_item(
                    session, raw, lesson.lesson_id, entry["track"], "B1", base + offset
                )
            if lesson not in affected:
                affected.append(lesson)

        # De-dup each affected lesson against ALL its items (existing + just
        # stored). This makes the endpoint effectively idempotent at item level:
        # if a timed-out call was retried and re-sent the same items, the
        # duplicates are removed here instead of piling onto the lesson.
        for lesson in affected:
            _, removed, _ = dedup_lesson_in_session(session, lesson.lesson_id)
            if removed:
                logger.info(
                    "generate-items: removed %d duplicate item(s) from lesson %s",
                    removed,
                    lesson.lesson_id,
                )

        session.commit()

        # Re-query each lesson's surviving items for the response (post-dedup).
        result = []
        for lesson in affected:
            items = (
                session.query(Item)
                .filter_by(lesson_id=lesson.lesson_id)
                .order_by(Item.order_index)
                .all()
            )
            result.append((lesson, items))
        return jsonify({"lessons": [serialize_admin_lesson(l, items) for l, items in result]})
    except IntegrityError as exc:
        session.rollback()
        logger.exception("Storing generated items failed (integrity error)")
        detail = str(getattr(exc, "orig", exc))
        if "lesson_id" in detail and "null" in detail.lower():
            message = (
                "Η αποθήκευση απέτυχε: η βάση έχει ακόμη NOT NULL στο items.lesson_id, "
                "οπότε δεν επιτρέπει drafts χωρίς μάθημα. Τρέξε το migration στο Railway "
                "(python migrate.py) και ξαναδοκίμασε."
            )
        else:
            message = f"Η αποθήκευση απέτυχε λόγω περιορισμού της βάσης: {detail[:300]}"
        return jsonify({"error": message}), 500
    except SQLAlchemyError as exc:
        session.rollback()
        logger.exception("Storing generated items failed (database error)")
        detail = str(getattr(exc, "orig", exc))[:300]
        return jsonify({"error": f"Σφάλμα βάσης κατά την αποθήκευση: {detail}"}), 500
    except Exception as exc:  # pragma: no cover - unexpected failure
        session.rollback()
        logger.exception("Storing generated items failed")
        return jsonify(
            {"error": f"Εσωτερικό σφάλμα κατά την αποθήκευση των items: {exc}"}
        ), 500
    finally:
        session.close()


@app.route("/api/admin/items", methods=["GET"])
def admin_list_items():
    try:
        verify_admin(request)
    except AuthError as exc:
        return jsonify({"error": str(exc)}), exc.status_code

    status = request.args.get("status", "draft")
    session = SessionLocal()
    try:
        query = session.query(Item)
        if status:
            query = query.filter(Item.status == status)
        items = query.order_by(Item.id.desc()).all()
        return jsonify({"items": [serialize_admin_item(i) for i in items]})
    finally:
        session.close()


@app.route("/api/admin/items/<item_id>/approve", methods=["POST"])
def admin_approve_item(item_id):
    try:
        verify_admin(request)
    except AuthError as exc:
        return jsonify({"error": str(exc)}), exc.status_code

    session = SessionLocal()
    try:
        item = session.query(Item).filter_by(item_id=item_id).one_or_none()
        if item is None:
            return jsonify({"error": f"Item '{item_id}' not found."}), 404
        item.status = "approved"
        session.commit()
        return jsonify(serialize_admin_item(item))
    finally:
        session.close()


@app.route("/api/admin/items/<item_id>", methods=["POST"])
def admin_edit_item(item_id):
    try:
        verify_admin(request)
    except AuthError as exc:
        return jsonify({"error": str(exc)}), exc.status_code

    payload = request.get_json(silent=True) or {}
    session = SessionLocal()
    try:
        item = session.query(Item).filter_by(item_id=item_id).one_or_none()
        if item is None:
            return jsonify({"error": f"Item '{item_id}' not found."}), 404

        if "difficulty" in payload:
            if payload["difficulty"] not in ALLOWED_DIFFICULTY:
                return jsonify({"error": "Invalid difficulty."}), 400
            item.difficulty = payload["difficulty"]
        if "status" in payload:
            if payload["status"] not in ("draft", "approved"):
                return jsonify({"error": "Invalid status."}), 400
            item.status = payload["status"]
        if "skill_type" in payload:
            if payload["skill_type"] not in ALLOWED_SKILL_TYPES:
                return jsonify({"error": "Invalid skill_type."}), 400
            item.skill_type = payload["skill_type"]
        if "type" in payload:
            item.type = payload["type"]
        if "level" in payload:
            item.level = payload["level"]
        if "order_index" in payload:
            item.order_index = payload["order_index"]
        if "data" in payload:
            if not isinstance(payload["data"], dict):
                return jsonify({"error": "data must be an object."}), 400
            item.data = payload["data"]
        if "lesson_id" in payload:
            target = (
                session.query(Lesson)
                .filter_by(lesson_id=payload["lesson_id"])
                .one_or_none()
            )
            if target is None:
                return jsonify({"error": "Target lesson_id not found."}), 400
            item.lesson_id = payload["lesson_id"]

        session.commit()
        return jsonify(serialize_admin_item(item))
    except Exception:  # pragma: no cover - unexpected failure
        session.rollback()
        logger.exception("Editing item failed")
        return jsonify({"error": "Internal error editing item."}), 500
    finally:
        session.close()


@app.route("/api/admin/items/<item_id>", methods=["DELETE"])
def admin_delete_item(item_id):
    try:
        verify_admin(request)
    except AuthError as exc:
        return jsonify({"error": str(exc)}), exc.status_code

    session = SessionLocal()
    try:
        item = session.query(Item).filter_by(item_id=item_id).one_or_none()
        if item is None:
            return jsonify({"error": f"Item '{item_id}' not found."}), 404
        if item.status != "draft":
            return jsonify({"error": "Only draft items can be deleted."}), 409
        session.delete(item)
        session.commit()
        return jsonify({"deleted": item_id})
    finally:
        session.close()


@app.route("/api/admin/draft-lessons", methods=["GET"])
def admin_draft_lessons():
    """Draft items grouped by their lesson, for the review UI."""
    try:
        verify_admin(request)
    except AuthError as exc:
        return jsonify({"error": str(exc)}), exc.status_code

    session = SessionLocal()
    try:
        draft_items = (
            session.query(Item)
            .filter(Item.status == "draft")
            .order_by(Item.order_index)
            .all()
        )

        # Group draft items by lesson_id (None grouped separately).
        grouped = {}
        for item in draft_items:
            grouped.setdefault(item.lesson_id, []).append(item)

        # Include empty draft lessons too (so they can be reviewed/deleted).
        draft_lessons = session.query(Lesson).filter(Lesson.status == "draft").all()
        for lesson in draft_lessons:
            grouped.setdefault(lesson.lesson_id, [])

        lessons_payload = []
        ungrouped = []
        for lesson_id, items in grouped.items():
            if lesson_id is None:
                ungrouped = [serialize_admin_item(i) for i in items]
                continue
            lesson = session.query(Lesson).filter_by(lesson_id=lesson_id).one_or_none()
            if lesson is None:
                ungrouped.extend(serialize_admin_item(i) for i in items)
                continue
            lessons_payload.append(serialize_admin_lesson(lesson, items))

        # Draft lessons first, then existing lessons that have draft items.
        lessons_payload.sort(key=lambda l: (l["existing"], l["title"] or ""))
        return jsonify({"lessons": lessons_payload, "ungrouped": ungrouped})
    finally:
        session.close()


@app.route("/api/admin/auto-categorize", methods=["POST"])
def admin_auto_categorize():
    """Classify unclassified approved lessons into engineer/deck/common.

    Candidates are approved lessons still on the default "common" category,
    excluding grammar-track lessons (those are ALWAYS common and never sent
    to the model). Only role_category changes — items are untouched. The
    admin can still override any result with the existing dropdowns.
    """
    try:
        verify_admin(request)
    except AuthError as exc:
        return jsonify({"error": str(exc)}), exc.status_code

    session = SessionLocal()
    try:
        candidates = (
            session.query(Lesson)
            .filter(
                Lesson.status == "approved",
                Lesson.track != "grammar",
                (Lesson.role_category == "common") | (Lesson.role_category.is_(None)),
            )
            .order_by(Lesson.lesson_id)
            .all()
        )
        if not candidates:
            return jsonify(
                {
                    "checked": 0,
                    "updated": 0,
                    "counts": {},
                    "results": [],
                    "message": "Δεν υπάρχουν μαθήματα για ταξινόμηση.",
                }
            )

        items_by_lesson = {}
        for item, _track in _approved_items_with_track(session):
            items_by_lesson.setdefault(item.lesson_id, []).append(item)

        payload = [
            {
                "lesson_id": lesson.lesson_id,
                "title": lesson.title,
                "samples": lesson_category_samples(
                    items_by_lesson.get(lesson.lesson_id, [])
                ),
            }
            for lesson in candidates
        ]
        try:
            categories = auto_categorize_lessons(payload)
        except AdminGenError as exc:
            return jsonify({"error": str(exc)}), exc.status_code

        updated = 0
        counts = {}
        results = []
        for lesson in candidates:
            category = categories.get(lesson.lesson_id)
            if category is None:
                continue  # the model skipped it — leave unchanged
            if category != (lesson.role_category or "common"):
                lesson.role_category = category
                updated += 1
            counts[category] = counts.get(category, 0) + 1
            results.append(
                {
                    "lesson_id": lesson.lesson_id,
                    "title": lesson.title,
                    "role_category": category,
                }
            )
        session.commit()
        logger.info(
            "Auto-categorized %d lesson(s), %d changed: %s", len(results), updated, counts
        )
        return jsonify(
            {"checked": len(results), "updated": updated, "counts": counts, "results": results}
        )
    except Exception:  # pragma: no cover - unexpected failure
        session.rollback()
        logger.exception("Auto-categorize failed")
        return jsonify({"error": "Internal error."}), 500
    finally:
        session.close()


@app.route("/api/admin/lessons/<lesson_id>/generate-teaching", methods=["POST"])
def admin_generate_teaching(lesson_id):
    """Backfill: generate 1-2 DRAFT teaching items for an existing lesson.

    The cards are generated from the lesson's own items, stored as drafts with
    order_index BEFORE the existing items, and reviewed/approved like any other
    generated content. Existing items are never touched.
    """
    try:
        verify_admin(request)
    except AuthError as exc:
        return jsonify({"error": str(exc)}), exc.status_code

    session = SessionLocal()
    try:
        lesson = session.query(Lesson).filter_by(lesson_id=lesson_id).one_or_none()
        if lesson is None:
            return jsonify({"error": f"Lesson '{lesson_id}' not found."}), 404

        # Never stack a second explanation: drafts awaiting review count too.
        has_teaching = (
            session.query(Item)
            .filter(
                Item.lesson_id == lesson_id,
                (Item.skill_type == "teaching") | (Item.type == "teaching"),
            )
            .count()
        )
        if has_teaching:
            return (
                jsonify(
                    {"error": "Το μάθημα έχει ήδη διδασκαλία (ή draft που περιμένει έγκριση)."}
                ),
                409,
            )

        items = (
            session.query(Item)
            .filter_by(lesson_id=lesson_id, status="approved")
            .order_by(Item.order_index)
            .all()
        )
        if not items:
            return jsonify({"error": "Το μάθημα δεν έχει approved items ακόμη."}), 400

        track = "grammar" if lesson.track == "grammar" else "maritime"
        try:
            raws = generate_teaching_for_lesson(lesson.title, track, lesson_digest(items))
        except AdminGenError as exc:
            return jsonify({"error": str(exc)}), exc.status_code

        # Place the cards BEFORE everything that exists (negative indexes are
        # fine — items are served ordered by order_index).
        min_order = min((i.order_index or 0) for i in items)
        fallback_difficulty = items[0].difficulty or "B1"
        stored = []
        for offset, raw in enumerate(raws):
            stored.append(
                store_generated_item(
                    session,
                    raw,
                    lesson_id,
                    track,
                    fallback_difficulty,
                    min_order - len(raws) + offset,
                )
            )
        session.commit()
        return jsonify(
            {"lesson_id": lesson_id, "items": [serialize_admin_item(i) for i in stored]}
        )
    except Exception:  # pragma: no cover - unexpected failure
        session.rollback()
        logger.exception("Teaching backfill failed")
        return jsonify({"error": "Internal error."}), 500
    finally:
        session.close()


@app.route("/api/admin/lessons/<lesson_id>/enrich", methods=["POST"])
def admin_enrich_lesson(lesson_id):
    """Bring a small/incomplete lesson up to standard by generating ONLY the
    items it is missing (a speaking item, a closing roleplay, and enough varied
    exercises to reach 8-12), grounded in its own content. The new items are
    stored as DRAFTS after the existing ones; existing items are never touched.
    """
    try:
        verify_admin(request)
    except AuthError as exc:
        return jsonify({"error": str(exc)}), exc.status_code

    session = SessionLocal()
    try:
        lesson = session.query(Lesson).filter_by(lesson_id=lesson_id).one_or_none()
        if lesson is None:
            return jsonify({"error": f"Lesson '{lesson_id}' not found."}), 404

        # Don't pile new drafts on top of drafts still awaiting review.
        pending = (
            session.query(Item)
            .filter(Item.lesson_id == lesson_id, Item.status == "draft")
            .count()
        )
        if pending:
            return (
                jsonify(
                    {"error": "Υπάρχουν ήδη drafts σε αναμονή — έλεγξέ τα/ενέκρινέ τα πρώτα."}
                ),
                409,
            )

        items = (
            session.query(Item)
            .filter_by(lesson_id=lesson_id, status="approved")
            .order_by(Item.order_index)
            .all()
        )
        if not items:
            return jsonify({"error": "Το μάθημα δεν έχει approved items ακόμη."}), 400

        gaps = analyze_lesson_gaps(items)
        if not gaps["needed"]:
            # Already complete — friendly 200 (not an error) so the UI can say so.
            return jsonify(
                {
                    "lesson_id": lesson_id,
                    "items": [],
                    "message": "Το μάθημα είναι ήδη πλήρες (8+ items, με speaking και roleplay).",
                }
            )

        track = "grammar" if lesson.track == "grammar" else "maritime"
        role_category = lesson.role_category or "common"
        try:
            raws = generate_enrichment_items(
                lesson.title, track, role_category, lesson_digest(items), gaps["needed"]
            )
        except AdminGenError as exc:
            return jsonify({"error": str(exc)}), exc.status_code

        # Append after the existing items, preserving the generated (pedagogical)
        # order. Existing items keep their order_index untouched.
        base = max((i.order_index or 0) for i in items)
        fallback_difficulty = items[-1].difficulty or "B1"
        stored = []
        for offset, raw in enumerate(raws, start=1):
            stored.append(
                store_generated_item(
                    session, raw, lesson_id, track, fallback_difficulty, base + offset
                )
            )
        session.commit()
        return jsonify(
            {"lesson_id": lesson_id, "items": [serialize_admin_item(i) for i in stored]}
        )
    except Exception:  # pragma: no cover - unexpected failure
        session.rollback()
        logger.exception("Lesson enrichment failed")
        return jsonify({"error": "Internal error."}), 500
    finally:
        session.close()


@app.route("/api/admin/lessons/<lesson_id>/dedup", methods=["POST"])
def admin_dedup_lesson(lesson_id):
    """Remove duplicate/repeated items from an EXISTING lesson in place.

    Uses the same content signature as generation/enrichment (#56): keep the
    FIRST item of each duplicate group, delete the rest. If more than 20 distinct
    items remain, keep the first 20 in pedagogical order (teaching first, since
    items are ordered by order_index). Unique items are never touched. Removed
    items and their per-item adaptive stats are deleted (like the delete
    endpoint); the approve flow and user-facing AI are not involved.
    """
    try:
        verify_admin(request)
    except AuthError as exc:
        return jsonify({"error": str(exc)}), exc.status_code

    session = SessionLocal()
    try:
        lesson = session.query(Lesson).filter_by(lesson_id=lesson_id).one_or_none()
        if lesson is None:
            return jsonify({"error": f"Lesson '{lesson_id}' not found."}), 404

        # Dedup keeping the first of each duplicate group, then enforce the hard
        # per-lesson cap (same as #56). Teaching stays first via order_index.
        before, removed, remaining = dedup_lesson_in_session(
            session, lesson_id, cap=MAX_ITEMS_PER_LESSON
        )
        session.commit()

        logger.info(
            "Deduped lesson %s: %d -> %d item(s) (%d removed)",
            lesson_id,
            before,
            remaining,
            removed,
        )
        return jsonify(
            {
                "lesson_id": lesson_id,
                "before": before,
                "removed": removed,
                "remaining": remaining,
            }
        )
    except Exception:  # pragma: no cover - unexpected failure
        session.rollback()
        logger.exception("Dedup lesson failed")
        return jsonify({"error": "Internal error during dedup."}), 500
    finally:
        session.close()


@app.route("/api/admin/lessons/<lesson_id>/approve", methods=["POST"])
def admin_approve_lesson(lesson_id):
    try:
        verify_admin(request)
    except AuthError as exc:
        return jsonify({"error": str(exc)}), exc.status_code

    session = SessionLocal()
    try:
        lesson = session.query(Lesson).filter_by(lesson_id=lesson_id).one_or_none()
        if lesson is None:
            logger.warning("Approve failed: lesson %s not found", lesson_id)
            return jsonify({"error": f"Lesson '{lesson_id}' not found."}), 404

        lesson.status = "approved"  # publish the lesson
        items = session.query(Item).filter_by(lesson_id=lesson_id, status="draft").all()
        for item in items:
            item.status = "approved"
        session.commit()
        logger.info(
            "Approved lesson %s (%d draft item(s) published)", lesson_id, len(items)
        )

        approved = session.query(Item).filter_by(lesson_id=lesson_id).order_by(Item.order_index).all()
        return jsonify(serialize_admin_lesson(lesson, approved))
    except Exception:  # pragma: no cover
        session.rollback()
        logger.exception("Approving lesson failed")
        return jsonify({"error": "Internal error approving lesson."}), 500
    finally:
        session.close()


@app.route("/api/admin/lessons/<lesson_id>", methods=["POST"])
def admin_edit_lesson(lesson_id):
    try:
        verify_admin(request)
    except AuthError as exc:
        return jsonify({"error": str(exc)}), exc.status_code

    payload = request.get_json(silent=True) or {}
    session = SessionLocal()
    try:
        lesson = session.query(Lesson).filter_by(lesson_id=lesson_id).one_or_none()
        if lesson is None:
            return jsonify({"error": f"Lesson '{lesson_id}' not found."}), 404

        if "title" in payload:
            lesson.title = payload["title"]
        if "title_el" in payload:
            lesson.title_el = payload["title_el"]
        if "description" in payload:
            lesson.description = payload["description"]
        if "source" in payload:
            lesson.source = payload["source"]
        if "track" in payload:
            if payload["track"] not in ALLOWED_TRACKS:
                # Legacy lessons carry tracks like "engine"; a client that
                # round-trips one here gets a clean 400 — log it so the
                # rejection is visible in production logs.
                logger.warning(
                    "Edit lesson %s rejected: invalid track %r",
                    lesson_id,
                    payload["track"],
                )
                return jsonify({"error": f"Invalid track: '{payload['track']}'."}), 400
            lesson.track = payload["track"]
        if "role_category" in payload:
            if payload["role_category"] not in ALLOWED_ROLE_CATEGORIES:
                logger.warning(
                    "Edit lesson %s rejected: invalid role_category %r",
                    lesson_id,
                    payload["role_category"],
                )
                return (
                    jsonify(
                        {"error": f"Invalid role_category: '{payload['role_category']}'."}
                    ),
                    400,
                )
            lesson.role_category = payload["role_category"]

        session.commit()
        items = session.query(Item).filter_by(lesson_id=lesson_id).order_by(Item.order_index).all()
        return jsonify(serialize_admin_lesson(lesson, items))
    except Exception:  # pragma: no cover
        session.rollback()
        logger.exception("Editing lesson failed")
        return jsonify({"error": "Internal error editing lesson."}), 500
    finally:
        session.close()


@app.route("/api/admin/lessons/<lesson_id>", methods=["DELETE"])
def admin_delete_lesson(lesson_id):
    try:
        verify_admin(request)
    except AuthError as exc:
        return jsonify({"error": str(exc)}), exc.status_code

    session = SessionLocal()
    try:
        lesson = session.query(Lesson).filter_by(lesson_id=lesson_id).one_or_none()
        if lesson is None:
            return jsonify({"error": f"Lesson '{lesson_id}' not found."}), 404

        # Capture before deletion (attributes expire after commit).
        title = lesson.title
        items = session.query(Item).filter_by(lesson_id=lesson_id).all()
        item_ids = [i.item_id for i in items]
        item_count = len(items)

        # Remove dependent user records that have no DB-level FK to cascade:
        # per-item adaptive stats and per-lesson completions. UserProgress
        # (global XP/streak/placement) is intentionally left untouched.
        stats_deleted = 0
        if item_ids:
            stats_deleted = (
                session.query(UserItemStat)
                .filter(UserItemStat.item_id.in_(item_ids))
                .delete(synchronize_session=False)
            )
        completions_deleted = (
            session.query(UserLessonCompletion)
            .filter_by(lesson_id=lesson_id)
            .delete(synchronize_session=False)
        )

        # Delete the lesson; its items cascade via the relationship.
        session.delete(lesson)
        session.commit()
        logger.info(
            "Deleted lesson %s (%d item(s), %d stat(s), %d completion(s))",
            lesson_id,
            item_count,
            stats_deleted,
            completions_deleted,
        )
        return jsonify(
            {
                "deleted": lesson_id,
                "title": title,
                "items_deleted": item_count,
                "stats_deleted": stats_deleted,
                "completions_deleted": completions_deleted,
            }
        )
    except Exception:  # pragma: no cover - unexpected failure
        session.rollback()
        logger.exception("Deleting lesson failed")
        return jsonify({"error": "Internal error deleting lesson."}), 500
    finally:
        session.close()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
