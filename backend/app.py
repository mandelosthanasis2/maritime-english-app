import logging
import os
import random
import uuid
from datetime import date, datetime, timedelta, timezone

from flask import Flask, Response, jsonify, request
from flask_cors import CORS
from sqlalchemy import and_, case, func, or_, text as sql_text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from admin import (
    ALLOWED_CEFR_LEVELS,
    ALLOWED_DIFFICULTY,
    ALLOWED_ROLE_CATEGORIES,
    ALLOWED_SKILL_AREAS,
    ALLOWED_SKILL_TYPES,
    ALLOWED_TRACKS,
    MAX_ITEMS_PER_LESSON,
    AdminGenError,
    analyze_lesson_gaps,
    auto_categorize_lessons,
    extract_text_from_pdf,
    generate_email_scenarios,
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
from email_feedback import EmailFeedbackError, generate_feedback as email_feedback
from lang import DEFAULT_LANG, normalize_item_data, resolve_item_data, resolve_lang
from rate_limit import RateLimiter
from models import (
    ApiUsageLog,
    Item,
    Lesson,
    UserActivityDay,
    UserItemStat,
    UserLessonCompletion,
    UserLevelTest,
    UserProgress,
    UserSectionTest,
)
from placement import (
    grade_answer,
    is_testable,
    score_grammar,
    score_maritime,
    section_for_track,
    select_questions,
)
from pronunciation import PronunciationError, assess_pronunciation
from roleplay import RoleplayError, chat as roleplay_chat
from transcription import transcribe
from tts import synthesize as synthesize_speech

def _env_int(name, default):
    """An integer env var, falling back to `default` when unset/invalid."""
    try:
        return int(os.environ.get(name, "") or default)
    except (TypeError, ValueError):
        return default


# Rate limit for the expensive admin generation endpoint (per caller). Generous
# enough never to bother a human admin in the /admin UI, but it caps a headless
# agent's Claude usage. See rate_limit.py for the per-process caveat.
_GENERATE_LIMITER = RateLimiter(max_calls=10, period=60)

# Per-user rate limits for the endpoints that trigger paid external API calls
# (Azure Speech, Claude). Defaults are generous for a single human learner but
# cap a scripted abuser. Per-process, like the admin limiter — a cheap safety
# valve, not a cluster-wide guarantee (see rate_limit.py).
_TTS_LIMITER = RateLimiter(_env_int("RATE_LIMIT_TTS_PER_MIN", 30), 60)
# Microphone endpoints (pronunciation assessment + transcription) share a bucket.
_SPEECH_LIMITER = RateLimiter(_env_int("RATE_LIMIT_SPEECH_PER_MIN", 15), 60)
# Claude-backed chat endpoints (role-play + email feedback) share a bucket.
_AI_CHAT_LIMITER = RateLimiter(_env_int("RATE_LIMIT_AI_CHAT_PER_MIN", 10), 60)


def _rate_limited(limiter, key):
    """A ready 429 response when `key` is over `limiter`'s budget, else None."""
    allowed, retry_after = limiter.check(key)
    if allowed:
        return None
    resp = jsonify({"error": "Πάρα πολλά αιτήματα. Δοκίμασε ξανά σε λίγο."})
    resp.headers["Retry-After"] = str(retry_after)
    return resp, 429


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

# Skill-tree unlock: a lesson must be completed with at least this score (0-100)
# to count as "passed" and open the next lesson in its section. A NULL score
# (legacy completions, or lessons with no auto-graded items) is grandfathered.
LESSON_PASS_SCORE = 75

# Module test (one per section = cefr_level + skill_area). Draws a random sample
# of the section's auto-graded items each attempt; uses all of them when the
# section has fewer than the target. A section with fewer than TEST_MIN_ITEMS
# testable items has NO module test. Pass mark is the same as a lesson (75).
TEST_TARGET_ITEMS = 20
TEST_MIN_ITEMS = 4
# Item kinds we can auto-grade in a test (same set the placement test uses).
TESTABLE_ITEM_TYPES = ("vocabulary", "fill_gap", "word_order")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)


def _cors_origins():
    """Browser origins allowed to call the API, from CORS_ALLOWED_ORIGINS.

    Comma-separated, e.g. "https://marlingo.app,https://myapp.vercel.app".
    Unset -> local dev origins only (set the env var in production!). A single
    "*" restores the old allow-all behaviour (not recommended).
    """
    raw = os.environ.get("CORS_ALLOWED_ORIGINS", "")
    origins = [o.strip().rstrip("/") for o in raw.split(",") if o.strip()]
    if not origins:
        logger.warning(
            "CORS_ALLOWED_ORIGINS is not set — only local dev origins are "
            "allowed. Set it to your frontend origin(s) in production."
        )
        return ["http://localhost:5173", "http://127.0.0.1:5173"]
    if "*" in origins:
        logger.warning("CORS_ALLOWED_ORIGINS is '*' — API is open to all origins.")
        return "*"
    return origins


# Allow only the configured frontend origin(s) to call the API from a browser.
CORS(app, origins=_cors_origins())

# Global request-size cap. Flask rejects bigger bodies with 413 before any
# route code runs (handler below returns it as JSON). Audio uploads get a
# tighter, dedicated cap checked in their routes.
MAX_CONTENT_LENGTH_MB = _env_int("MAX_CONTENT_LENGTH_MB", 10)
app.config["MAX_CONTENT_LENGTH"] = MAX_CONTENT_LENGTH_MB * 1024 * 1024

MAX_AUDIO_UPLOAD_MB = _env_int("MAX_AUDIO_UPLOAD_MB", 5)
MAX_AUDIO_UPLOAD_BYTES = MAX_AUDIO_UPLOAD_MB * 1024 * 1024

# Placement submissions never legitimately exceed the test size (~10 questions).
MAX_PLACEMENT_ANSWERS = 100


@app.errorhandler(413)
def payload_too_large(_exc):
    return (
        jsonify(
            {
                "error": (
                    f"Το αίτημα είναι πολύ μεγάλο (όριο {MAX_CONTENT_LENGTH_MB} MB)."
                )
            }
        ),
        413,
    )

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


def _user_lang(session, user_id):
    """The user's explanation language ('el' when unset/unknown). Never raises."""
    try:
        progress = session.get(UserProgress, user_id)
        if progress and progress.explanation_language:
            return progress.explanation_language
    except Exception:  # pragma: no cover - preference lookup is best-effort
        pass
    return DEFAULT_LANG


def request_lang(session):
    """Explanation language for THIS request.

    The stored preference when a valid session token is attached, else the
    default 'el'. Content endpoints are public, so this never raises and
    never requires a token — an anonymous (or token-less) fetch simply gets
    Greek, which is today's only language anyway.
    """
    try:
        user_id, _email = verify_request(request)
    except Exception:
        return DEFAULT_LANG
    return _user_lang(session, user_id)


def serialize_lesson_meta(
    lesson, item_count, writing_practice=False, gradable_count=None, lang=DEFAULT_LANG
):
    """Lesson metadata only (no items).

    `writing_practice` is True for email-track lessons that hold an email_compose
    item (the free-writing scenarios), so the home can split the email path into
    "Μαθήματα" vs "Εξάσκηση γραψίματος".

    `gradable_count` is the number of approved auto-graded items in the lesson
    (fill_gap / word_order / vocabulary choice). The home sums it per section to
    decide whether that section has a module test. None when not computed.
    """
    return {
        "lesson_id": lesson.lesson_id,
        "track": lesson.track,
        "role_category": lesson.role_category or "common",
        # New lesson architecture (null for legacy/email lessons; the home falls
        # back gracefully). cefr_level: A2-C2; skill_area: vocabulary|grammar|
        # listening|speaking.
        "cefr_level": lesson.cefr_level,
        "skill_area": lesson.skill_area,
        # Position within its (cefr_level, skill_area) section; drives the
        # skill-tree sequence/unlock on the home. Null for legacy/email lessons.
        "order_index": lesson.order_index,
        "module": lesson.module,
        "title": lesson.title,
        # Language-keyed when available (lang.py); the flat column is the
        # fallback so legacy rows keep working. The client receives a plain
        # string either way, exactly as before.
        "description": resolve_lang(lesson.description_i18n, lang) or lesson.description,
        "source": lesson.source,
        "interface_language": lesson.interface_language,
        "target_language": lesson.target_language,
        "version": lesson.version,
        "item_count": item_count,
        "gradable_count": gradable_count,
        "writing_practice": writing_practice,
    }


def serialize_item(item, lang=DEFAULT_LANG):
    """A single item for LEARNERS: columns plus its `data` with the language
    layer resolved to plain strings/`explanations.el` exactly as the frontend
    has always consumed it (see lang.resolve_item_data). Admin endpoints use
    serialize_admin_item, which returns the raw language-keyed objects."""
    return {
        "item_id": item.item_id,
        "type": item.type,
        "level": item.level,
        "difficulty": item.difficulty,
        "status": item.status,
        "skill_type": item.skill_type,
        "data": resolve_item_data(item.data, lang),
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


def _testable_items_query(session):
    """Approved auto-graded items in approved, non-email lessons (joined to Lesson)."""
    return (
        session.query(Item, Lesson)
        .join(Lesson, Item.lesson_id == Lesson.lesson_id)
        .filter(
            Item.status == "approved",
            Lesson.status == "approved",
            Lesson.track != "email",
            or_(
                Item.skill_type.in_(TESTABLE_ITEM_TYPES),
                Item.type.in_(TESTABLE_ITEM_TYPES),
            ),
        )
    )


def gradable_counts(session):
    """Return {lesson_id: count of approved auto-graded items} for module tests."""
    counts = {}
    for item, _lesson in _testable_items_query(session).all():
        if is_testable(item):
            counts[item.lesson_id] = counts.get(item.lesson_id, 0) + 1
    return counts


def section_testable_items(session, cefr_level, skill_area):
    """All approved auto-graded items for a (cefr_level, skill_area) section."""
    rows = (
        _testable_items_query(session)
        .filter(Lesson.cefr_level == cefr_level, Lesson.skill_area == skill_area)
        .all()
    )
    return [item for item, _lesson in rows if is_testable(item)]


def level_testable_items_by_skill(session, cefr_level):
    """{skill_area: [items]} of approved auto-graded items across a CEFR level."""
    rows = _testable_items_query(session).filter(Lesson.cefr_level == cefr_level).all()
    by_skill = {}
    for item, lesson in rows:
        if is_testable(item):
            by_skill.setdefault(lesson.skill_area, []).append(item)
    return by_skill


def balanced_sample(pools, target):
    """A balanced sample of up to `target` items, round-robin across `pools`.

    `pools` is a list of item lists (one per skill). Each pool is shuffled, then
    we take one item from each non-empty pool in turn until we reach `target` or
    every pool is exhausted — so skills are represented as evenly as supply
    allows. The result is shuffled so the skills interleave during the test.
    """
    queues = [list(p) for p in pools]
    for q in queues:
        random.shuffle(q)
    picked = []
    while len(picked) < target and any(queues):
        for q in queues:
            if not q:
                continue
            picked.append(q.pop())
            if len(picked) >= target:
                break
    random.shuffle(picked)
    return picked


def writing_practice_lesson_ids(session):
    """Lesson ids holding an approved email_compose item (writing scenarios)."""
    rows = (
        session.query(Item.lesson_id)
        .filter(
            Item.status == "approved",
            (Item.skill_type == "email_compose") | (Item.type == "email_compose"),
        )
        .distinct()
        .all()
    )
    return {r[0] for r in rows}


# --- Progress helpers --------------------------------------------------------

# The app's audience lives on Greek time; admin metrics bucket days in
# Europe/Athens regardless of the server timezone. The `tzdata` package in
# requirements guarantees the zone exists even on slim images; the fixed
# UTC+3 fallback keeps the app alive if it somehow doesn't.
try:
    from zoneinfo import ZoneInfo

    ATHENS_TZ = ZoneInfo("Europe/Athens")
except Exception:  # pragma: no cover - missing tz database
    ATHENS_TZ = timezone(timedelta(hours=3))


def athens_date(dt=None):
    """The Europe/Athens calendar date of `dt` (default: now)."""
    return (dt or datetime.now(timezone.utc)).astimezone(ATHENS_TZ).date()


def record_activity(session, user_id, answered=False):
    """Upsert today's (Athens) row in the daily-activity rollup.

    Presence-only calls (opened the app, completed a lesson) keep `answers`
    unchanged; `answered=True` (one recorded answer) increments it. On
    PostgreSQL this is a real ON CONFLICT upsert so concurrent first-writes
    of the day can't collide; elsewhere (SQLite tests) a flush + select is
    enough. Never raises on failure — metrics must not break user requests.
    """
    day = athens_date()
    increment = 1 if answered else 0
    try:
        if session.get_bind().dialect.name == "postgresql":
            from sqlalchemy.dialects.postgresql import insert as pg_insert

            table = UserActivityDay.__table__
            stmt = pg_insert(table).values(
                user_id=user_id, activity_date=day, answers=increment
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["user_id", "activity_date"],
                set_={"answers": table.c.answers + increment},
            )
            session.execute(stmt)
        else:
            session.flush()  # autoflush is off — make pending rows visible
            row = (
                session.query(UserActivityDay)
                .filter_by(user_id=user_id, activity_date=day)
                .one_or_none()
            )
            if row is None:
                session.add(
                    UserActivityDay(user_id=user_id, activity_date=day, answers=increment)
                )
            else:
                row.answers += increment
    except Exception:  # pragma: no cover - best-effort rollup
        logger.exception("Recording daily activity failed (ignored)")


def get_or_create_progress(session, user_id, email=None):
    progress = session.get(UserProgress, user_id)
    if progress is None:
        progress = UserProgress(
            user_id=user_id,
            email=email,
            total_xp=0,
            current_streak=0,
            created_at=datetime.now(timezone.utc),
        )
        session.add(progress)
        session.flush()
    elif email and progress.email != email:
        progress.email = email
    # Every authenticated user endpoint passes through here — the single choke
    # point where "this user was active today" is recorded for beta metrics.
    record_activity(session, user_id)
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
        session.query(UserLessonCompletion.lesson_id, UserLessonCompletion.best_score)
        .filter_by(user_id=progress.user_id)
        .all()
    )
    completed_ids = [r[0] for r in rows]
    # A lesson is "passed" (unlocks the next in its skill-tree section) when its
    # best score is >= the pass mark, or NULL (legacy / un-scored completions are
    # grandfathered). The home uses this set for the strict unlock + the ✓ state.
    passed_ids = [lid for lid, score in rows if score is None or score >= LESSON_PASS_SCORE]
    # Module-test results per section (cefr_level + skill_area). The home shows
    # the test node's ✓/score from these; "mastered" = best_score >= pass mark.
    section_rows = (
        session.query(UserSectionTest)
        .filter_by(user_id=progress.user_id)
        .all()
    )
    section_tests = [
        {
            "cefr_level": s.cefr_level,
            "skill_area": s.skill_area,
            "best_score": s.best_score,
            "mastered": s.best_score is not None and s.best_score >= LESSON_PASS_SCORE,
        }
        for s in section_rows
    ]
    # Level-test results per CEFR level. "completed" = best_score >= pass mark.
    level_rows = session.query(UserLevelTest).filter_by(user_id=progress.user_id).all()
    level_tests = [
        {
            "cefr_level": l.cefr_level,
            "best_score": l.best_score,
            "completed": l.best_score is not None and l.best_score >= LESSON_PASS_SCORE,
        }
        for l in level_rows
    ]
    return {
        "total_xp": progress.total_xp,
        "current_streak": progress.current_streak,
        "last_active_date": progress.last_active_date.isoformat()
        if progress.last_active_date
        else None,
        "completed_lesson_ids": completed_ids,
        "passed_lesson_ids": passed_ids,
        "section_tests": section_tests,
        "level_tests": level_tests,
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
        writing_ids = writing_practice_lesson_ids(session)
        gradable = gradable_counts(session)
        # Only approved lessons are user-visible (drafts stay hidden).
        lessons = (
            session.query(Lesson)
            .filter(Lesson.status == "approved")
            .order_by(Lesson.lesson_id)
            .all()
        )
        lang = request_lang(session)
        return jsonify(
            [
                serialize_lesson_meta(
                    l,
                    counts.get(l.lesson_id, 0),
                    l.lesson_id in writing_ids,
                    gradable.get(l.lesson_id, 0),
                    lang=lang,
                )
                for l in lessons
            ]
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
        lang = request_lang(session)
        payload = serialize_lesson_meta(lesson, len(approved_items), lang=lang)
        payload["items"] = [serialize_item(item, lang) for item in approved_items]
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
        lang = request_lang(session)
        return jsonify(
            [serialize_lesson_meta(l, counts.get(l.lesson_id, 0), lang=lang) for l in lessons]
        )
    finally:
        session.close()


@app.route("/api/assess-pronunciation", methods=["POST"])
def assess_pronunciation_route():
    # Auth + rate limit: every call costs an Azure Speech assessment.
    try:
        user_id, _email = verify_request(request)
    except AuthError as exc:
        return jsonify({"error": str(exc)}), exc.status_code
    limited = _rate_limited(_SPEECH_LIMITER, f"speech:{user_id}")
    if limited is not None:
        return limited

    audio_file = request.files.get("audio")
    if audio_file is None:
        return jsonify({"error": "Missing 'audio' file."}), 400

    reference_text = request.form.get("reference_text", "")

    audio_bytes = audio_file.read()
    if not audio_bytes:
        return jsonify({"error": "Uploaded audio is empty."}), 400
    if len(audio_bytes) > MAX_AUDIO_UPLOAD_BYTES:
        return (
            jsonify(
                {"error": f"Η ηχογράφηση είναι πολύ μεγάλη (όριο {MAX_AUDIO_UPLOAD_MB} MB)."}
            ),
            413,
        )

    try:
        result = assess_pronunciation(audio_bytes, reference_text, user_id=user_id)
        return jsonify(result)
    except PronunciationError as exc:
        return jsonify({"error": str(exc)}), exc.status_code
    except Exception:  # pragma: no cover - unexpected failure
        logger.exception("Pronunciation assessment failed unexpectedly")
        return jsonify({"error": "Internal error during assessment."}), 500


@app.route("/api/transcribe", methods=["POST"])
def transcribe_route():
    # Auth + rate limit: every call costs an Azure Speech transcription.
    try:
        user_id, _email = verify_request(request)
    except AuthError as exc:
        return jsonify({"error": str(exc)}), exc.status_code
    limited = _rate_limited(_SPEECH_LIMITER, f"speech:{user_id}")
    if limited is not None:
        return limited

    audio_file = request.files.get("audio")
    if audio_file is None:
        return jsonify({"error": "Missing 'audio' file."}), 400

    audio_bytes = audio_file.read()
    if not audio_bytes:
        return jsonify({"error": "Uploaded audio is empty."}), 400
    if len(audio_bytes) > MAX_AUDIO_UPLOAD_BYTES:
        return (
            jsonify(
                {"error": f"Η ηχογράφηση είναι πολύ μεγάλη (όριο {MAX_AUDIO_UPLOAD_MB} MB)."}
            ),
            413,
        )

    try:
        return jsonify(transcribe(audio_bytes, user_id=user_id))
    except PronunciationError as exc:
        return jsonify({"error": str(exc)}), exc.status_code
    except Exception:  # pragma: no cover - unexpected failure
        logger.exception("Transcription failed unexpectedly")
        return jsonify({"error": "Internal error during transcription."}), 500


@app.route("/api/tts", methods=["POST"])
def tts_route():
    # Auth + rate limit: every call costs an Azure Speech synthesis.
    try:
        user_id, _email = verify_request(request)
    except AuthError as exc:
        return jsonify({"error": str(exc)}), exc.status_code
    limited = _rate_limited(_TTS_LIMITER, f"tts:{user_id}")
    if limited is not None:
        return limited

    payload = request.get_json(silent=True) or {}
    try:
        audio = synthesize_speech(payload.get("text", ""), user_id=user_id)
        return Response(audio, mimetype="audio/mpeg")
    except PronunciationError as exc:
        return jsonify({"error": str(exc)}), exc.status_code
    except Exception:  # pragma: no cover - unexpected failure
        logger.exception("Text-to-speech failed unexpectedly")
        return jsonify({"error": "Internal error during synthesis."}), 500


@app.route("/api/roleplay/chat", methods=["POST"])
def roleplay_chat_route():
    # Auth + rate limit: every call costs a Claude request.
    try:
        user_id, _email = verify_request(request)
    except AuthError as exc:
        return jsonify({"error": str(exc)}), exc.status_code
    limited = _rate_limited(_AI_CHAT_LIMITER, f"ai-chat:{user_id}")
    if limited is not None:
        return limited

    payload = request.get_json(silent=True) or {}

    try:
        result = roleplay_chat(
            scenario=payload.get("scenario", ""),
            user_role=payload.get("user_role", ""),
            history=payload.get("history", []),
            user_message=payload.get("user_message", ""),
            user_id=user_id,
        )
        return jsonify(result)
    except RoleplayError as exc:
        return jsonify({"error": str(exc)}), exc.status_code
    except Exception:  # pragma: no cover - unexpected failure
        logger.exception("Role-play chat failed unexpectedly")
        return jsonify({"error": "Internal error during role-play."}), 500


@app.route("/api/email/feedback", methods=["POST"])
def email_feedback_route():
    """AI feedback on a learner's written email (email_compose items).

    Body: {"scenario": <Greek task>, "instructions": <Greek guidance>,
    "email_text": <the learner's email>}. Returns {good, improve, suggestion}.
    """
    # Auth + rate limit: every call costs a Claude request.
    try:
        user_id, _email = verify_request(request)
    except AuthError as exc:
        return jsonify({"error": str(exc)}), exc.status_code
    limited = _rate_limited(_AI_CHAT_LIMITER, f"ai-chat:{user_id}")
    if limited is not None:
        return limited

    payload = request.get_json(silent=True) or {}

    try:
        result = email_feedback(
            scenario=payload.get("scenario", ""),
            instructions=payload.get("instructions", ""),
            email_text=payload.get("email_text", ""),
            user_id=user_id,
        )
        return jsonify(result)
    except EmailFeedbackError as exc:
        return jsonify({"error": str(exc)}), exc.status_code
    except Exception:  # pragma: no cover - unexpected failure
        logger.exception("Email feedback failed unexpectedly")
        return jsonify({"error": "Internal error during email feedback."}), 500


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

    # Optional lesson score (0-100) for the skill-tree unlock. NULL/absent means
    # "not measured" (e.g. a lesson with no auto-graded items) — grandfathered.
    payload = request.get_json(silent=True) or {}
    raw_score = payload.get("score")
    score = None
    if isinstance(raw_score, (int, float)) and not isinstance(raw_score, bool):
        score = max(0, min(100, int(round(raw_score))))

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
                best_score=score,
            )
            session.add(completion)
        else:
            completion.times_completed += 1
            completion.xp_earned += xp_earned
            completion.completed_at = now
            # Keep the best score ever achieved; never downgrade. A legacy NULL
            # (already passed) stays NULL so a low replay can't relock the next.
            if completion.best_score is not None and score is not None:
                completion.best_score = max(completion.best_score, score)

        # Streak: based on the day of the most recent activity.
        touch_streak(progress, today)

        progress.total_xp += xp_earned

        session.commit()

        out = serialize_progress(session, progress)
        out["xp_earned"] = xp_earned
        out["already_completed"] = already_completed
        out["best_score"] = completion.best_score
        out["passed"] = completion.best_score is None or completion.best_score >= LESSON_PASS_SCORE
        return jsonify(out)
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
    if len(answers) > MAX_PLACEMENT_ANSWERS:
        return (
            jsonify(
                {"error": f"Πάρα πολλές απαντήσεις (όριο {MAX_PLACEMENT_ANSWERS})."}
            ),
            400,
        )

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


# --- Module tests (one per section: cefr_level + skill_area) ------------------


@app.route("/api/sections/<cefr_level>/<skill_area>/test", methods=["GET"])
def section_test(cefr_level, skill_area):
    """A random sample of the section's auto-graded items, as a playable test.

    Items carry their full data (answers included), like the lesson player —
    scoring happens client-side via the same onResult path. A fresh random
    sample is drawn each call so retries are not identical. Returns 404 when the
    section has fewer than TEST_MIN_ITEMS testable items (no test exists).
    """
    if cefr_level not in ALLOWED_CEFR_LEVELS or skill_area not in ALLOWED_SKILL_AREAS:
        return jsonify({"error": "Unknown section."}), 404

    session = SessionLocal()
    try:
        pool = section_testable_items(session, cefr_level, skill_area)
        if len(pool) < TEST_MIN_ITEMS:
            return (
                jsonify({"error": "Αυτή η ενότητα δεν έχει test ακόμη.", "available": len(pool)}),
                404,
            )
        sample = random.sample(pool, min(TEST_TARGET_ITEMS, len(pool)))
        random.shuffle(sample)
        lang = request_lang(session)
        return jsonify(
            {
                "cefr_level": cefr_level,
                "skill_area": skill_area,
                "available": len(pool),
                "items": [serialize_item(item, lang) for item in sample],
            }
        )
    finally:
        session.close()


@app.route("/api/sections/<cefr_level>/<skill_area>/test/complete", methods=["POST"])
def section_test_complete(cefr_level, skill_area):
    """Record a module-test attempt. Body: {"score": 0-100}.

    Stores the best score ever achieved for the section (never downgraded) and
    returns whether the section is now mastered (best_score >= pass mark).
    """
    if cefr_level not in ALLOWED_CEFR_LEVELS or skill_area not in ALLOWED_SKILL_AREAS:
        return jsonify({"error": "Unknown section."}), 404

    try:
        user_id, email = verify_request(request)
    except AuthError as exc:
        return jsonify({"error": str(exc)}), exc.status_code

    payload = request.get_json(silent=True) or {}
    raw_score = payload.get("score")
    if not isinstance(raw_score, (int, float)) or isinstance(raw_score, bool):
        return jsonify({"error": "Body must include a numeric 'score'."}), 400
    score = max(0, min(100, int(round(raw_score))))

    session = SessionLocal()
    try:
        # Make sure the user's progress row exists (keeps the email in sync).
        get_or_create_progress(session, user_id, email)

        row = (
            session.query(UserSectionTest)
            .filter_by(user_id=user_id, cefr_level=cefr_level, skill_area=skill_area)
            .one_or_none()
        )
        now = datetime.now(timezone.utc)
        if row is None:
            row = UserSectionTest(
                user_id=user_id,
                cefr_level=cefr_level,
                skill_area=skill_area,
                best_score=score,
                passed_at=now if score >= LESSON_PASS_SCORE else None,
            )
            session.add(row)
        else:
            if row.best_score is None or score > row.best_score:
                row.best_score = score
            if row.passed_at is None and row.best_score >= LESSON_PASS_SCORE:
                row.passed_at = now

        session.commit()
        return jsonify(
            {
                "cefr_level": cefr_level,
                "skill_area": skill_area,
                "score": score,
                "best_score": row.best_score,
                "mastered": row.best_score >= LESSON_PASS_SCORE,
            }
        )
    except Exception:  # pragma: no cover - unexpected failure
        session.rollback()
        logger.exception("Section test complete failed")
        return jsonify({"error": "Internal error."}), 500
    finally:
        session.close()


# --- Level tests (one per CEFR level, spanning all its skill areas) -----------


@app.route("/api/levels/<cefr_level>/test", methods=["GET"])
def level_test(cefr_level):
    """A balanced random sample of the level's auto-graded items, across skills.

    Like the section test but wider: it draws from every skill area of the level
    (see balanced_sample) and is the level's final milestone. Returns 404 when
    the whole level has fewer than TEST_MIN_ITEMS testable items (no level test).
    """
    if cefr_level not in ALLOWED_CEFR_LEVELS:
        return jsonify({"error": "Unknown level."}), 404

    session = SessionLocal()
    try:
        by_skill = level_testable_items_by_skill(session, cefr_level)
        total = sum(len(items) for items in by_skill.values())
        if total < TEST_MIN_ITEMS:
            return (
                jsonify({"error": "Αυτό το επίπεδο δεν έχει Level Test ακόμη.", "available": total}),
                404,
            )
        sample = balanced_sample(list(by_skill.values()), TEST_TARGET_ITEMS)
        lang = request_lang(session)
        return jsonify(
            {
                "cefr_level": cefr_level,
                "available": total,
                "items": [serialize_item(item, lang) for item in sample],
            }
        )
    finally:
        session.close()


@app.route("/api/levels/<cefr_level>/test/complete", methods=["POST"])
def level_test_complete(cefr_level):
    """Record a level-test attempt. Body: {"score": 0-100}.

    Stores the best score for the level (never downgraded) and returns whether
    the level is now completed (best_score >= pass mark).
    """
    if cefr_level not in ALLOWED_CEFR_LEVELS:
        return jsonify({"error": "Unknown level."}), 404

    try:
        user_id, email = verify_request(request)
    except AuthError as exc:
        return jsonify({"error": str(exc)}), exc.status_code

    payload = request.get_json(silent=True) or {}
    raw_score = payload.get("score")
    if not isinstance(raw_score, (int, float)) or isinstance(raw_score, bool):
        return jsonify({"error": "Body must include a numeric 'score'."}), 400
    score = max(0, min(100, int(round(raw_score))))

    session = SessionLocal()
    try:
        get_or_create_progress(session, user_id, email)

        row = (
            session.query(UserLevelTest)
            .filter_by(user_id=user_id, cefr_level=cefr_level)
            .one_or_none()
        )
        now = datetime.now(timezone.utc)
        if row is None:
            row = UserLevelTest(
                user_id=user_id,
                cefr_level=cefr_level,
                best_score=score,
                passed_at=now if score >= LESSON_PASS_SCORE else None,
            )
            session.add(row)
        else:
            if row.best_score is None or score > row.best_score:
                row.best_score = score
            if row.passed_at is None and row.best_score >= LESSON_PASS_SCORE:
                row.passed_at = now

        session.commit()
        return jsonify(
            {
                "cefr_level": cefr_level,
                "score": score,
                "best_score": row.best_score,
                "completed": row.best_score >= LESSON_PASS_SCORE,
            }
        )
    except Exception:  # pragma: no cover - unexpected failure
        session.rollback()
        logger.exception("Level test complete failed")
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
        # Count the answer itself in the daily rollup (presence was already
        # recorded by get_or_create_progress above).
        record_activity(session, user_id, answered=True)

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

        # Email-writing is a self-contained track (phase 1): keep it out of the
        # grammar/maritime adaptive smart-practice pool.
        rows = [(i, t) for i, t in _approved_items_with_track(session) if t != "email"]
        stats = session.query(UserItemStat).filter_by(user_id=user_id).all()

        choice = choose_next(progress, rows, stats)
        if choice is None:  # empty pool — friendly response, not an error
            return jsonify(
                {"item": None, "message": "Δεν υπάρχουν διαθέσιμες ασκήσεις ακόμη."}
            )

        item, track, meta = choice
        payload = serialize_item(item, progress.explanation_language or DEFAULT_LANG)
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

        # Approved lessons with their approved items, grouped per lesson. Email
        # lessons are a self-contained track (phase 1) — excluded from the
        # adaptive "next lesson" recommendation (they're browsed from home).
        lessons = (
            session.query(Lesson)
            .filter(Lesson.status == "approved", Lesson.track != "email")
            .all()
        )
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
            lesson,
            len(items_by_lesson.get(lesson.lesson_id, [])),
            lang=progress.explanation_language or DEFAULT_LANG,
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

# Source `type` <-> editorial `skill_type` for stored items. A few kinds use a
# different `type` token than their editorial `skill_type` (the model emits
# both): a roleplay is type "dialogue", a translation is type "translation"
# (skill_type "speaking"). Both tokens are normalised through this map so the
# kind is recognised wherever it appears.
_SKILL_TYPE_FROM_TYPE = {"dialogue": "roleplay", "translation": "speaking"}

# Every editorial kind the generator can emit, AFTER the type->skill_type
# normalisation above. This is the closed vocabulary of kinds the rules below
# know about — see the generator prompts in admin.py (item `type`/`skill_type`).
# Anything outside this set is UNKNOWN and treated as a mismatch (fail-closed):
# it is never silently allowed.
KNOWN_ITEM_KINDS = {
    "teaching",
    "vocabulary",
    "listening",
    "fill_gap",
    "word_order",
    "speaking",
    "roleplay",
    "email_compose",
}

# Maritime-track only: which item kinds belong in each skill_area. `teaching` is
# allowed everywhere — it's the intro/explanation. email_compose is email-only,
# so it appears in no maritime skill_area and is rejected on the maritime path.
# The email track has its own structure and is exempt entirely; lessons without
# a recognised skill_area are left alone (nothing to validate against).
SKILL_AREA_ITEM_TYPES = {
    "vocabulary": {"teaching", "vocabulary", "fill_gap"},
    "grammar": {"teaching", "fill_gap", "word_order"},
    # listening items are self-contained (sentence + blanks since #78), so a
    # separate fill_gap would be a soundless leftover — only teaching + listening.
    "listening": {"teaching", "listening"},
    "speaking": {"teaching", "speaking", "roleplay"},
}


def _canon_kind(token):
    """Lower-case a type/skill_type token and map type synonyms to their kind."""
    if not token:
        return None
    k = str(token).strip().lower()
    return _SKILL_TYPE_FROM_TYPE.get(k, k)


def _declared_kinds(skill_type, item_type):
    """All editorial kinds an item declares, from BOTH its skill_type and type.

    Both are considered on purpose: a stored skill_type can be a lossy default
    (store_generated_item falls back to "vocabulary" for an unrecognised type),
    so the original `type` is often the only signal that an item is actually
    off-skill. An empty set means the item declares no kind at all.
    """
    kinds = set()
    for token in (skill_type, item_type):
        canon = _canon_kind(token)
        if canon:
            kinds.add(canon)
    return kinds


def item_declared_kinds(item):
    return _declared_kinds(item.skill_type, item.type)


def raw_declared_kinds(raw):
    return _declared_kinds(raw.get("skill_type"), raw.get("type"))


def item_skill_kind(item):
    """The item's single primary kind (skill_type preferred), normalized."""
    return _canon_kind(item.skill_type) or _canon_kind(item.type) or ""


def kinds_fit_skill(kinds, allowed):
    """Whether every declared kind is allowed for the skill (fail-closed).

    An unknown kind (not in KNOWN_ITEM_KINDS) is never in `allowed`, so it makes
    this False. An item that declares no kind at all can't be judged and passes.
    """
    if not kinds:
        return True
    return all(k in allowed for k in kinds)


def offending_kinds(kinds, allowed):
    """The declared kinds that aren't allowed (for messaging)."""
    return sorted(k for k in kinds if k not in allowed)


def allowed_item_kinds(track, skill_area):
    """The item kinds permitted for a lesson, or None when unrestricted.

    The restriction is keyed on SKILL_AREA, not track: any non-email lesson with
    a recognised skill_area (vocabulary/grammar/listening/speaking) is restricted
    to SKILL_AREA_ITEM_TYPES — whatever its track. (Grammar-skill lessons live on
    a separate "grammar" track, and listening/speaking may get their own; keying
    on track would let them all skip the type check.) The email track keeps its
    own structure, and a lesson with no/unknown skill_area is unrestricted —
    both return None.
    """
    if track == "email":
        return None
    return SKILL_AREA_ITEM_TYPES.get(skill_area)


def lesson_skill_mismatches(lesson, items):
    """Items whose type doesn't fit the lesson's skill_area (fail-closed).

    Maritime track only: email lessons (own structure) and lessons without a
    recognised skill_area are exempt and return []. An item is a mismatch when
    ANY kind it declares (skill_type OR type) is not allowed — including unknown
    kinds. Returns (item, kind) tuples, kind being a representative offender.
    """
    allowed = allowed_item_kinds(lesson.track, lesson.skill_area)
    if not allowed:
        return []
    bad = []
    for item in items:
        kinds = item_declared_kinds(item)
        if not kinds_fit_skill(kinds, allowed):
            offenders = offending_kinds(kinds, allowed) or ["unknown"]
            bad.append((item, offenders[0]))
    return bad


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
    # Flag items that don't belong in this lesson's skill_area (maritime only),
    # so the admin UI can warn before approval and mark existing offenders.
    bad_ids = {item.item_id for item, _kind in lesson_skill_mismatches(lesson, items)}
    serialized_items = []
    for i in items:
        entry = serialize_admin_item(i)
        entry["skill_mismatch"] = i.item_id in bad_ids
        serialized_items.append(entry)
    return {
        "lesson_id": lesson.lesson_id,
        "title": lesson.title,
        "title_el": lesson.title_el,
        "description": lesson.description,
        "source": lesson.source,
        "track": lesson.track,
        "role_category": lesson.role_category or "common",
        "cefr_level": lesson.cefr_level,
        "skill_area": lesson.skill_area,
        "order_index": lesson.order_index,
        "status": lesson.status,
        # True when items are being attached to an already-approved lesson.
        "existing": lesson.status == "approved",
        # Count of items whose type doesn't fit skill_area (0 = clean).
        "skill_mismatch_count": len(bad_ids),
        "items": serialized_items,
    }


def store_generated_item(session, raw, lesson_id, track, fallback_difficulty, order_index):
    """Persist one generated item dict as a draft Item under a lesson; return it."""
    data = dict(raw)
    data.pop("audio_url", None)
    if track:
        data["track"] = track
    # Multi-language readiness: whatever shape the generator (or an admin)
    # sent, language-bearing flat strings are stored language-keyed ({"el": …}).
    data, _changed = normalize_item_data(data)

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
        skipped_total = 0  # items dropped for not fitting the lesson's skill_area
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
                    cefr_level=entry.get("cefr_level"),
                    skill_area=entry.get("skill_area"),
                    order_index=entry.get("order_index"),
                    module=None,
                    title=entry["title_en"],
                    title_el=entry.get("title_el"),
                    description=entry.get("description_el"),
                    # Keyed sibling of `description` (lang.py): kept in sync so
                    # new lessons never accumulate Greek-only descriptions.
                    description_i18n=(
                        {DEFAULT_LANG: entry["description_el"]}
                        if entry.get("description_el")
                        else None
                    ),
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
            # Drop generated items whose type doesn't belong in this lesson's
            # skill_area, using the SAME fail-closed rule the approve validation
            # enforces (maritime + known skill_area only; email/auto unrestricted)
            # — including unknown types. This stops e.g. a vocabulary lesson
            # getting auto-added speaking/roleplay items that would only be
            # rejected at approve.
            allowed_kinds = allowed_item_kinds(lesson.track, lesson.skill_area)
            order_index = base
            for raw in entry["items"]:
                kinds = raw_declared_kinds(raw)
                if allowed_kinds is not None and not kinds_fit_skill(kinds, allowed_kinds):
                    skipped_total += 1
                    logger.info(
                        "generate-items: skipped %s item — not allowed in skill_area %r (lesson %s)",
                        offending_kinds(kinds, allowed_kinds) or ["unknown"],
                        lesson.skill_area,
                        lesson.lesson_id,
                    )
                    continue
                order_index += 1
                store_generated_item(
                    session, raw, lesson.lesson_id, entry["track"], "B1", order_index
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
        if skipped_total:
            logger.info(
                "generate-items: dropped %d item(s) that didn't fit their lesson's skill_area",
                skipped_total,
            )
        return jsonify(
            {
                "lessons": [serialize_admin_lesson(l, items) for l, items in result],
                "skipped_off_skill": skipped_total,
            }
        )
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


def create_scenario_lesson(session, title, scenario, instructions):
    """Create a DRAFT email-track lesson holding one email_compose item.

    This is how a "writing scenario" is stored — reusing the lesson/item model,
    so it flows through the existing draft/approve, lesson player and XP paths.
    Returns (lesson, item).
    """
    lesson = Lesson(
        lesson_id=f"dl_{uuid.uuid4().hex[:12]}",
        track="email",
        role_category="common",
        module=None,
        title=(title or "").strip() or "Σενάριο γραψίματος",
        interface_language="el",
        target_language="en",
        version=1,
        status="draft",
    )
    session.add(lesson)
    session.flush()
    raw = {
        "type": "email_compose",
        "skill_type": "email_compose",
        "level": "B1",
        "difficulty": "B1",
        "english": {
            "scenario": (scenario or "").strip(),
            "instructions": (instructions or "").strip(),
        },
    }
    item = store_generated_item(session, raw, lesson.lesson_id, "email", "B1", 1)
    return lesson, item


@app.route("/api/admin/email-scenarios", methods=["POST"])
def admin_create_email_scenario():
    """Create one writing scenario by hand. Body: {title, scenario, instructions}."""
    try:
        verify_admin(request)
    except AuthError as exc:
        return jsonify({"error": str(exc)}), exc.status_code

    payload = request.get_json(silent=True) or {}
    scenario = (payload.get("scenario") or "").strip()
    if not scenario:
        return jsonify({"error": "Το σενάριο είναι υποχρεωτικό."}), 400

    session = SessionLocal()
    try:
        lesson, items = create_scenario_lesson(
            session,
            payload.get("title"),
            scenario,
            payload.get("instructions"),
        )
        session.commit()
        return jsonify({"lessons": [serialize_admin_lesson(lesson, [items])]})
    except Exception:  # pragma: no cover - unexpected failure
        session.rollback()
        logger.exception("Creating email scenario failed")
        return jsonify({"error": "Internal error creating scenario."}), 500
    finally:
        session.close()


@app.route("/api/admin/email-scenarios/generate", methods=["POST"])
def admin_generate_email_scenarios():
    """Generate writing scenarios with AI. Body: {topic, count}. Stores drafts."""
    try:
        verify_admin(request)
    except AuthError as exc:
        return jsonify({"error": str(exc)}), exc.status_code

    payload = request.get_json(silent=True) or {}
    try:
        scenarios = generate_email_scenarios(payload.get("topic", ""), payload.get("count", 5))
    except AdminGenError as exc:
        return jsonify({"error": str(exc)}), exc.status_code

    session = SessionLocal()
    try:
        result = []
        for spec in scenarios:
            lesson, item = create_scenario_lesson(
                session, spec.get("title"), spec.get("scenario"), spec.get("instructions")
            )
            result.append((lesson, [item]))
        session.commit()
        return jsonify(
            {"lessons": [serialize_admin_lesson(l, items) for l, items in result]}
        )
    except Exception:  # pragma: no cover - unexpected failure
        session.rollback()
        logger.exception("Generating email scenarios failed")
        return jsonify({"error": "Internal error generating scenarios."}), 500
    finally:
        session.close()


@app.route("/api/admin/items", methods=["GET"])
def admin_list_items():
    try:
        verify_admin(request)
    except AuthError as exc:
        return jsonify({"error": str(exc)}), exc.status_code

    # Optional filters. `lesson_id` returns a single lesson's items (any status)
    # ordered by order_index — used by the admin item reorder UI. `status` filters
    # by draft/approved; pass "all" (or empty) for every status.
    status = request.args.get("status", "draft")
    lesson_id = request.args.get("lesson_id")
    session = SessionLocal()
    try:
        query = session.query(Item)
        if lesson_id:
            query = query.filter(Item.lesson_id == lesson_id)
        if status and status != "all":
            query = query.filter(Item.status == status)
        # For a single lesson, return display order (order_index); otherwise the
        # legacy newest-first listing.
        if lesson_id:
            query = query.order_by(Item.order_index, Item.id)
        else:
            query = query.order_by(Item.id.desc())
        items = query.all()
        return jsonify({"items": [serialize_admin_item(i) for i in items]})
    finally:
        session.close()


def _item_skill_block(session, item):
    """A ready 422 response when `item` doesn't fit its lesson's skill_area.

    The SAME fail-closed rule as lesson-level approve (lesson_skill_mismatches),
    applied to one item — so single-item approval can't publish e.g. a fill_gap
    into a listening lesson that lesson approval would refuse. Items without a
    lesson (unassigned drafts) have nothing to validate against; returns None
    when the item is allowed.
    """
    if not item.lesson_id:
        return None
    lesson = session.query(Lesson).filter_by(lesson_id=item.lesson_id).one_or_none()
    if lesson is None:
        return None
    mismatches = lesson_skill_mismatches(lesson, [item])
    if not mismatches:
        return None
    _item, kind = mismatches[0]
    allowed = sorted(SKILL_AREA_ITEM_TYPES.get(lesson.skill_area, set()))
    return (
        jsonify(
            {
                "error": (
                    f"Ο τύπος '{kind}' δεν επιτρέπεται στη δεξιότητα "
                    f"'{lesson.skill_area}'. Επιτρεπτοί τύποι: {', '.join(allowed)}."
                ),
                "skill_area": lesson.skill_area,
                "allowed_types": allowed,
                "kind": kind,
            }
        ),
        422,
    )


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
        # Same skill-area gate as lesson approval — refuse to publish a mismatch.
        blocked = _item_skill_block(session, item)
        if blocked is not None:
            return blocked
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

        # Snapshot for the skill-area gate below: it must only fire on edits
        # that CHANGE the publication state or kind, so plain text edits to
        # legacy (already-approved, off-skill) items keep working.
        old_status = item.status
        old_kind = (item.skill_type, item.type)
        old_lesson_id = item.lesson_id

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
            # Accept both shapes; store language-keyed (see lang.py).
            item.data, _changed = normalize_item_data(payload["data"])
        if "lesson_id" in payload:
            target = (
                session.query(Lesson)
                .filter_by(lesson_id=payload["lesson_id"])
                .one_or_none()
            )
            if target is None:
                return jsonify({"error": "Target lesson_id not found."}), 400
            item.lesson_id = payload["lesson_id"]

        # Skill-area gate (same rule as approval): validate when this edit
        # publishes the item (draft -> approved), moves it to another lesson,
        # or changes the kind of an approved item. Pure content edits to
        # existing approved items are untouched (legacy offenders stay
        # editable — they are surfaced read-only by /admin/skill-mismatches).
        becomes_approved = item.status == "approved" and old_status != "approved"
        moved = item.lesson_id != old_lesson_id
        kind_changed = (item.skill_type, item.type) != old_kind
        if becomes_approved or (item.status == "approved" and (moved or kind_changed)):
            blocked = _item_skill_block(session, item)
            if blocked is not None:
                session.rollback()
                return blocked

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


def _section_sort_key(cefr_level, skill_area):
    """Canonical (level, skill) ordering; unknown/missing values sort last."""
    level_rank = _CEFR_ORDER.index(cefr_level) if cefr_level in _CEFR_ORDER else len(_CEFR_ORDER)
    skill_rank = _SKILL_ORDER.index(skill_area) if skill_area in _SKILL_ORDER else len(_SKILL_ORDER)
    return (level_rank, cefr_level or "", skill_rank, skill_area or "")


@app.route("/api/admin/review-queue", methods=["GET"])
def admin_review_queue():
    """The content-review queue: draft work awaiting review, oldest first.

    A queue entry is a lesson that is itself a draft OR an approved lesson
    holding draft items (enrichment / teaching-backfill output). Each entry
    carries ONLY its draft items — that is what's under review — with the same
    skill-mismatch flags the approval gate enforces, so the admin sees the ⚠
    before hitting the 422. Paginated: ?offset=&limit= (limit capped at 50).

    Also returns a dashboard summary computed in SQL (no item bodies loaded):
    totals plus draft-item/lesson counts per (cefr_level, skill_area) section.
    """
    try:
        verify_admin(request)
    except AuthError as exc:
        return jsonify({"error": str(exc)}), exc.status_code

    try:
        offset = max(0, int(request.args.get("offset", 0)))
    except (TypeError, ValueError):
        offset = 0
    try:
        limit = min(50, max(1, int(request.args.get("limit", 20))))
    except (TypeError, ValueError):
        limit = 20

    session = SessionLocal()
    try:
        has_draft_items = (
            session.query(Item.lesson_id)
            .filter(Item.status == "draft", Item.lesson_id.isnot(None))
            .scalar_subquery()
        )
        in_queue = or_(Lesson.status == "draft", Lesson.lesson_id.in_(has_draft_items))

        total = session.query(func.count(Lesson.id)).filter(in_queue).scalar() or 0
        # Oldest first (creation order = auto-increment id), so the queue is FIFO.
        lessons = (
            session.query(Lesson)
            .filter(in_queue)
            .order_by(Lesson.id)
            .offset(offset)
            .limit(limit)
            .all()
        )

        items_by_lesson = {}
        ids = [l.lesson_id for l in lessons]
        if ids:
            page_items = (
                session.query(Item)
                .filter(Item.lesson_id.in_(ids), Item.status == "draft")
                .order_by(Item.order_index, Item.id)
                .all()
            )
            for item in page_items:
                items_by_lesson.setdefault(item.lesson_id, []).append(item)

        # Draft items with no lesson (rare) ride along on the first page only.
        ungrouped = []
        if offset == 0:
            ungrouped = (
                session.query(Item)
                .filter(Item.status == "draft", Item.lesson_id.is_(None))
                .order_by(Item.id)
                .all()
            )

        # Summary (SQL aggregates only). by_section counts draft items and the
        # lessons holding them, grouped by the lesson's level + skill.
        draft_items_total = (
            session.query(func.count(Item.id)).filter(Item.status == "draft").scalar() or 0
        )
        draft_lessons_total = (
            session.query(func.count(Lesson.id))
            .filter(Lesson.status == "draft")
            .scalar()
            or 0
        )
        section_rows = (
            session.query(
                Lesson.cefr_level,
                Lesson.skill_area,
                func.count(func.distinct(Lesson.id)),
                func.count(Item.id),
            )
            .join(Item, Item.lesson_id == Lesson.lesson_id)
            .filter(Item.status == "draft")
            .group_by(Lesson.cefr_level, Lesson.skill_area)
            .all()
        )
        by_section = [
            {"cefr_level": level, "skill_area": skill, "lessons": lessons_n, "items": items_n}
            for level, skill, lessons_n, items_n in sorted(
                section_rows, key=lambda r: _section_sort_key(r[0], r[1])
            )
        ]

        return jsonify(
            {
                "summary": {
                    "queue_lessons": total,
                    "draft_lessons": draft_lessons_total,
                    "draft_items": draft_items_total,
                    "by_section": by_section,
                },
                "lessons": [
                    serialize_admin_lesson(l, items_by_lesson.get(l.lesson_id, []))
                    for l in lessons
                ],
                "ungrouped": [serialize_admin_item(i) for i in ungrouped],
                "total": total,
                "offset": offset,
                "limit": limit,
            }
        )
    finally:
        session.close()


@app.route("/api/admin/lessons/<lesson_id>/approve-items", methods=["POST"])
def admin_approve_lesson_items(lesson_id):
    """Bulk-approve a lesson's VALID draft items; skip + report the mismatches.

    Unlike lesson approval (all-or-nothing, 422 on any mismatch), this approves
    every draft item that fits the lesson's skill_area and returns the ones it
    skipped — so one off-skill item doesn't block the rest of the batch. The
    LESSON's own status is untouched: items approved under a draft lesson go
    live only when the lesson itself is approved (existing behaviour).
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

        drafts = (
            session.query(Item)
            .filter_by(lesson_id=lesson_id, status="draft")
            .order_by(Item.order_index, Item.id)
            .all()
        )
        bad = {
            item.item_id: kind for item, kind in lesson_skill_mismatches(lesson, drafts)
        }

        approved_ids, skipped = [], []
        for item in drafts:
            if item.item_id in bad:
                skipped.append(
                    {
                        "item_id": item.item_id,
                        "type": item.type,
                        "skill_type": item.skill_type,
                        "kind": bad[item.item_id],
                    }
                )
            else:
                item.status = "approved"
                approved_ids.append(item.item_id)
        session.commit()

        logger.info(
            "Bulk-approved %d item(s) in lesson %s (%d skipped as off-skill)",
            len(approved_ids),
            lesson_id,
            len(skipped),
        )
        return jsonify(
            {
                "lesson_id": lesson_id,
                "skill_area": lesson.skill_area,
                "approved": approved_ids,
                "skipped": skipped,
            }
        )
    except Exception:  # pragma: no cover - unexpected failure
        session.rollback()
        logger.exception("Bulk item approval failed")
        return jsonify({"error": "Internal error approving items."}), 500
    finally:
        session.close()


@app.route("/api/admin/structure-overview", methods=["GET"])
def admin_structure_overview():
    """Content completeness per CEFR level and skill area, for the Levels tab.

    Counts are computed IN SQL with a single grouped query — item bodies
    (JSONB) are never loaded (avoiding the gradable_counts pattern that pulls
    every row into Python). `gradable_items` counts approved items whose
    skill_type/type is auto-gradable (vocabulary/fill_gap/word_order); this is
    the same kind filter the module test uses, without is_testable's per-item
    data-shape checks — a fine approximation for an editorial overview.

    Aggregates (approved_items, gradable_items, has_test) count approved items
    in APPROVED lessons — what learners can actually see. Draft lessons are
    still listed with status so the admin sees them in context.
    """
    try:
        verify_admin(request)
    except AuthError as exc:
        return jsonify({"error": str(exc)}), exc.status_code

    session = SessionLocal()
    try:
        testable = or_(
            Item.skill_type.in_(TESTABLE_ITEM_TYPES), Item.type.in_(TESTABLE_ITEM_TYPES)
        )
        approved_n = func.coalesce(
            func.sum(case((Item.status == "approved", 1), else_=0)), 0
        )
        gradable_n = func.coalesce(
            func.sum(case((and_(Item.status == "approved", testable), 1), else_=0)), 0
        )
        draft_n = func.coalesce(func.sum(case((Item.status == "draft", 1), else_=0)), 0)

        rows = (
            session.query(Lesson, approved_n, gradable_n, draft_n)
            .outerjoin(Item, Item.lesson_id == Lesson.lesson_id)
            .group_by(Lesson.id)
            .all()
        )

        def lesson_entry(lesson, items_n, gradable, drafts_n):
            return {
                "lesson_id": lesson.lesson_id,
                "title": lesson.title,
                "title_el": lesson.title_el,
                "status": lesson.status,
                "track": lesson.track,
                "role_category": lesson.role_category or "common",
                "cefr_level": lesson.cefr_level,
                "skill_area": lesson.skill_area,
                "order_index": lesson.order_index,
                "source": lesson.source,
                "item_count": int(items_n),
                "gradable_count": int(gradable),
                "draft_count": int(drafts_n),
            }

        def lesson_sort(entry):
            order = entry["order_index"]
            return (order if order is not None else 1_000_000, entry["title"] or "")

        grouped = {}  # (cefr_level, skill_area) -> [lesson entries]
        email_lessons = []
        for lesson, items_n, gradable, drafts_n in rows:
            entry = lesson_entry(lesson, items_n, gradable, drafts_n)
            if lesson.track == "email":
                email_lessons.append(entry)
                continue
            grouped.setdefault((lesson.cefr_level, lesson.skill_area), []).append(entry)

        by_level = {}  # cefr_level -> [skill payloads]
        for (level, skill), entries in sorted(
            grouped.items(), key=lambda kv: _section_sort_key(kv[0][0], kv[0][1])
        ):
            entries.sort(key=lesson_sort)
            approved_entries = [e for e in entries if e["status"] == "approved"]
            gradable_total = sum(e["gradable_count"] for e in approved_entries)
            by_level.setdefault(level, []).append(
                {
                    "skill_area": skill,
                    "approved_lessons": len(approved_entries),
                    "total_lessons": len(entries),
                    "approved_items": sum(e["item_count"] for e in approved_entries),
                    "gradable_items": gradable_total,
                    "draft_items": sum(e["draft_count"] for e in entries),
                    "has_test": gradable_total >= TEST_MIN_ITEMS,
                    "lessons": entries,
                }
            )

        levels = [
            {"cefr_level": level, "skills": skills}
            for level, skills in sorted(
                by_level.items(), key=lambda kv: _section_sort_key(kv[0], None)
            )
        ]
        email_lessons.sort(key=lesson_sort)

        return jsonify(
            {
                "test_min_items": TEST_MIN_ITEMS,
                "levels": levels,
                "email_lessons": email_lessons,
            }
        )
    finally:
        session.close()


# --- Admin: users / beta health ------------------------------------------------


def _as_utc(dt):
    """Normalize a DB datetime to aware UTC (SQLite returns naive)."""
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _iso(dt):
    dt = _as_utc(dt)
    return dt.isoformat() if dt else None


def _beta_summary(session):
    """The beta-health numbers for the Users tab header.

    Actives come from the daily rollup (user_activity_days); retention cohorts
    are evaluated in Python over ONE (user_id, created_at) query and ONE
    activity query — the user count is the bound (closed beta, tens of rows),
    no answer histories are ever loaded.
    """
    now = datetime.now(timezone.utc)
    today = athens_date()
    week_start = today - timedelta(days=6)  # rolling 7 Athens days incl. today

    active_today = (
        session.query(func.count(func.distinct(UserActivityDay.user_id)))
        .filter(UserActivityDay.activity_date == today)
        .scalar()
        or 0
    )
    active_week = (
        session.query(func.count(func.distinct(UserActivityDay.user_id)))
        .filter(UserActivityDay.activity_date >= week_start)
        .scalar()
        or 0
    )
    new_week = (
        session.query(func.count(UserProgress.user_id))
        .filter(UserProgress.created_at >= now - timedelta(days=7))
        .scalar()
        or 0
    )
    completions_week = (
        session.query(func.count(UserLessonCompletion.id))
        .filter(UserLessonCompletion.completed_at >= now - timedelta(days=7))
        .scalar()
        or 0
    )

    # Retention. Signup day = the Athens date of created_at. D1: active the
    # day AFTER signup. D7: active on any of days 1-7 after signup.
    signup_rows = (
        session.query(UserProgress.user_id, UserProgress.created_at)
        .filter(UserProgress.created_at.isnot(None))
        .all()
    )
    signup = {uid: athens_date(_as_utc(created)) for uid, created in signup_rows}
    d1_cohort = [uid for uid, day in signup.items() if day <= today - timedelta(days=2)]
    d7_cohort = [uid for uid, day in signup.items() if day <= today - timedelta(days=8)]

    activity_days = {}
    relevant = set(d1_cohort) | set(d7_cohort)
    if relevant:
        rows = (
            session.query(UserActivityDay.user_id, UserActivityDay.activity_date)
            .filter(UserActivityDay.user_id.in_(relevant))
            .all()
        )
        for uid, day in rows:
            activity_days.setdefault(uid, set()).add(day)

    d1_returned = sum(
        1
        for uid in d1_cohort
        if signup[uid] + timedelta(days=1) in activity_days.get(uid, ())
    )
    d7_returned = sum(
        1
        for uid in d7_cohort
        if any(
            signup[uid] + timedelta(days=1) <= day <= signup[uid] + timedelta(days=7)
            for day in activity_days.get(uid, ())
        )
    )

    return {
        "active_today": active_today,
        "active_week": active_week,
        "new_week": new_week,
        "lessons_per_active_week": (
            round(completions_week / active_week, 1) if active_week else None
        ),
        # The frontend shows a raw fraction when the cohort is small (<5).
        "d1_retention": {"returned": d1_returned, "cohort": len(d1_cohort)},
        "d7_retention": {"returned": d7_returned, "cohort": len(d7_cohort)},
    }


# "Furthest position" on the skill tree: the highest CEFR level the user has
# completed anything in; ties within the level go to the skill with the most
# completed lessons.
def _positions_for(session, user_ids):
    if not user_ids:
        return {}
    rows = (
        session.query(
            UserLessonCompletion.user_id,
            Lesson.cefr_level,
            Lesson.skill_area,
            func.count(UserLessonCompletion.id),
        )
        .join(Lesson, Lesson.lesson_id == UserLessonCompletion.lesson_id)
        .filter(
            UserLessonCompletion.user_id.in_(user_ids),
            Lesson.cefr_level.isnot(None),
        )
        .group_by(UserLessonCompletion.user_id, Lesson.cefr_level, Lesson.skill_area)
        .all()
    )
    best = {}
    for uid, level, skill, count in rows:
        level_rank = _CEFR_ORDER.index(level) if level in _CEFR_ORDER else -1
        key = (level_rank, count)
        current = best.get(uid)
        if current is None or key > current[0]:
            best[uid] = (key, {"cefr_level": level, "skill_area": skill})
    return {uid: pos for uid, (_key, pos) in best.items()}


@app.route("/api/admin/users", methods=["GET"])
def admin_users():
    """Beta-health summary + a paginated, sortable, filterable user list.

    Query params: sort=last_active|xp|created (default last_active),
    q=<email substring>, offset, limit (<=50, default 25). All aggregates are
    SQL (grouped subqueries joined to user_progress); only the current page's
    rows are serialized.
    """
    try:
        verify_admin(request)
    except AuthError as exc:
        return jsonify({"error": str(exc)}), exc.status_code

    sort = request.args.get("sort", "last_active")
    email_q = (request.args.get("q") or "").strip()
    try:
        offset = max(0, int(request.args.get("offset", 0)))
    except (TypeError, ValueError):
        offset = 0
    try:
        limit = min(50, max(1, int(request.args.get("limit", 25))))
    except (TypeError, ValueError):
        limit = 25

    session = SessionLocal()
    try:
        comp_sub = (
            session.query(
                UserLessonCompletion.user_id.label("uid"),
                func.count(UserLessonCompletion.id).label("lessons_completed"),
                func.max(UserLessonCompletion.completed_at).label("last_completion"),
            )
            .group_by(UserLessonCompletion.user_id)
            .subquery()
        )
        ans_sub = (
            session.query(
                UserItemStat.user_id.label("uid"),
                func.max(UserItemStat.last_answered_at).label("last_answer"),
            )
            .group_by(UserItemStat.user_id)
            .subquery()
        )

        query = (
            session.query(
                UserProgress,
                comp_sub.c.lessons_completed,
                comp_sub.c.last_completion,
                ans_sub.c.last_answer,
            )
            .outerjoin(comp_sub, comp_sub.c.uid == UserProgress.user_id)
            .outerjoin(ans_sub, ans_sub.c.uid == UserProgress.user_id)
        )
        if email_q:
            query = query.filter(UserProgress.email.ilike(f"%{email_q}%"))

        # COALESCE keeps NULLs last on every dialect (PG sorts NULLs first on
        # DESC, SQLite last — a fixed epoch fallback makes them agree).
        epoch_dt = datetime(1970, 1, 1, tzinfo=timezone.utc)
        epoch_d = date(1970, 1, 1)
        if sort == "xp":
            query = query.order_by(UserProgress.total_xp.desc(), UserProgress.user_id)
        elif sort == "created":
            query = query.order_by(
                func.coalesce(UserProgress.created_at, epoch_dt).desc(),
                UserProgress.user_id,
            )
        else:  # last_active (default)
            query = query.order_by(
                func.coalesce(UserProgress.last_active_date, epoch_d).desc(),
                UserProgress.total_xp.desc(),
                UserProgress.user_id,
            )

        count_query = session.query(func.count(UserProgress.user_id))
        if email_q:
            count_query = count_query.filter(UserProgress.email.ilike(f"%{email_q}%"))
        total = count_query.scalar() or 0

        rows = query.offset(offset).limit(limit).all()
        positions = _positions_for(session, [p.user_id for p, *_rest in rows])

        users = []
        for progress, lessons_completed, last_completion, last_answer in rows:
            seen_candidates = [d for d in (_as_utc(last_completion), _as_utc(last_answer)) if d]
            users.append(
                {
                    "user_id": progress.user_id,
                    "email": progress.email,
                    "created_at": _iso(progress.created_at),
                    "last_active_date": (
                        progress.last_active_date.isoformat()
                        if progress.last_active_date
                        else None
                    ),
                    "last_seen_at": _iso(max(seen_candidates)) if seen_candidates else None,
                    "total_xp": progress.total_xp,
                    "current_streak": progress.current_streak,
                    "lessons_completed": int(lessons_completed or 0),
                    "cefr_level": progress.cefr_level,
                    "maritime_level": progress.maritime_level,
                    "user_role": progress.user_role,
                    "position": positions.get(progress.user_id),
                }
            )

        return jsonify(
            {
                "summary": _beta_summary(session),
                "users": users,
                "total": total,
                "offset": offset,
                "limit": limit,
            }
        )
    finally:
        session.close()


@app.route("/api/admin/users/<user_id>", methods=["GET"])
def admin_user_detail(user_id):
    """READ-ONLY drill-down for one beta user: full skill-tree journey,
    14-day activity sparkline, placement, and where they struggle."""
    try:
        verify_admin(request)
    except AuthError as exc:
        return jsonify({"error": str(exc)}), exc.status_code

    session = SessionLocal()
    try:
        progress = session.get(UserProgress, user_id)
        if progress is None:
            return jsonify({"error": f"User '{user_id}' not found."}), 404

        # Journey: this user's completions with their lessons, grouped by
        # (cefr_level, skill_area), plus section/level test results.
        journey_rows = (
            session.query(UserLessonCompletion, Lesson)
            .outerjoin(Lesson, Lesson.lesson_id == UserLessonCompletion.lesson_id)
            .filter(UserLessonCompletion.user_id == user_id)
            .all()
        )
        section_tests = (
            session.query(UserSectionTest).filter_by(user_id=user_id).all()
        )
        level_tests = session.query(UserLevelTest).filter_by(user_id=user_id).all()

        grouped = {}  # (cefr, skill) -> [lesson entries]
        for completion, lesson in journey_rows:
            level = lesson.cefr_level if lesson else None
            skill = lesson.skill_area if lesson else None
            grouped.setdefault((level, skill), []).append(
                {
                    "lesson_id": completion.lesson_id,
                    "title": lesson.title if lesson else completion.lesson_id,
                    "order_index": lesson.order_index if lesson else None,
                    "best_score": completion.best_score,
                    "passed": completion.best_score is None
                    or completion.best_score >= LESSON_PASS_SCORE,
                    "times_completed": completion.times_completed,
                    "completed_at": _iso(completion.completed_at),
                }
            )
        tests_by_section = {
            (t.cefr_level, t.skill_area): {
                "best_score": t.best_score,
                "mastered": t.best_score is not None and t.best_score >= LESSON_PASS_SCORE,
                "passed_at": _iso(t.passed_at),
            }
            for t in section_tests
        }
        # Sections with a test attempt but no completions still show up.
        for key in tests_by_section:
            grouped.setdefault(key, [])

        by_level = {}
        for (level, skill), lessons in sorted(
            grouped.items(), key=lambda kv: _section_sort_key(kv[0][0], kv[0][1])
        ):
            lessons.sort(
                key=lambda e: (
                    e["order_index"] if e["order_index"] is not None else 1_000_000,
                    e["title"] or "",
                )
            )
            by_level.setdefault(level, []).append(
                {
                    "skill_area": skill,
                    "lessons": lessons,
                    "section_test": tests_by_section.get((level, skill)),
                }
            )
        journey = [
            {"cefr_level": level, "skills": skills}
            for level, skills in sorted(
                by_level.items(), key=lambda kv: _section_sort_key(kv[0], None)
            )
        ]

        # 14-day sparkline from the rollup (oldest -> newest, gaps = 0).
        today = athens_date()
        first = today - timedelta(days=13)
        spark_rows = (
            session.query(UserActivityDay.activity_date, UserActivityDay.answers)
            .filter(
                UserActivityDay.user_id == user_id,
                UserActivityDay.activity_date >= first,
            )
            .all()
        )
        by_day = {day: answers for day, answers in spark_rows}
        activity = [
            {
                "date": (first + timedelta(days=i)).isoformat(),
                "answers": by_day.get(first + timedelta(days=i), 0),
            }
            for i in range(14)
        ]

        # Totals + "where they get stuck", all aggregated in SQL.
        correct_total, wrong_total = (
            session.query(
                func.coalesce(func.sum(UserItemStat.correct_count), 0),
                func.coalesce(func.sum(UserItemStat.wrong_count), 0),
            )
            .filter(UserItemStat.user_id == user_id)
            .one()
        )

        wrong_sum = func.coalesce(func.sum(UserItemStat.wrong_count), 0)
        most_wrong_row = (
            session.query(Item.lesson_id, Lesson.title, wrong_sum.label("wrong"))
            .select_from(UserItemStat)
            .join(Item, Item.item_id == UserItemStat.item_id)
            .join(Lesson, Lesson.lesson_id == Item.lesson_id)
            .filter(UserItemStat.user_id == user_id)
            .group_by(Item.lesson_id, Lesson.title)
            .order_by(wrong_sum.desc())
            .first()
        )
        most_wrong = None
        if most_wrong_row and int(most_wrong_row[2]) > 0:
            most_wrong = {
                "lesson_id": most_wrong_row[0],
                "title": most_wrong_row[1],
                "wrong": int(most_wrong_row[2]),
            }

        last_row = (
            session.query(UserItemStat.last_answered_at, Item.lesson_id, Lesson.title)
            .select_from(UserItemStat)
            .join(Item, Item.item_id == UserItemStat.item_id)
            .join(Lesson, Lesson.lesson_id == Item.lesson_id)
            .filter(
                UserItemStat.user_id == user_id,
                UserItemStat.last_answered_at.isnot(None),
            )
            .order_by(UserItemStat.last_answered_at.desc())
            .first()
        )
        last_attempted = (
            {"lesson_id": last_row[1], "title": last_row[2], "at": _iso(last_row[0])}
            if last_row
            else None
        )

        return jsonify(
            {
                "user": {
                    "user_id": progress.user_id,
                    "email": progress.email,
                    "created_at": _iso(progress.created_at),
                    "last_active_date": (
                        progress.last_active_date.isoformat()
                        if progress.last_active_date
                        else None
                    ),
                    "total_xp": progress.total_xp,
                    "current_streak": progress.current_streak,
                    "user_role": progress.user_role,
                    # Placement result (all we store): NULL = not taken.
                    "placement": {
                        "cefr_level": progress.cefr_level,
                        "maritime_level": progress.maritime_level,
                    },
                },
                "journey": journey,
                "level_tests": [
                    {
                        "cefr_level": t.cefr_level,
                        "best_score": t.best_score,
                        "completed": t.best_score is not None
                        and t.best_score >= LESSON_PASS_SCORE,
                        "passed_at": _iso(t.passed_at),
                    }
                    for t in level_tests
                ],
                "activity": activity,
                "totals": {
                    "answers": int(correct_total) + int(wrong_total),
                    "correct": int(correct_total),
                    "wrong": int(wrong_total),
                },
                "stuck": {"most_wrong": most_wrong, "last_attempted": last_attempted},
            }
        )
    finally:
        session.close()


# --- Admin: costs (💰) ---------------------------------------------------------

# The 💰 tab groups the five logged providers into the three spend buckets the
# admin thinks in: Azure Speech vs AI text (DeepSeek) vs AI chat (Claude). A
# generation call that fell back to Claude is logged as claude — it lands in
# the Claude bucket because that's where the money actually went.
_PROVIDER_GROUP = {
    "azure_tts": "azure_speech",
    "azure_stt": "azure_speech",
    "azure_pronunciation": "azure_speech",
    "deepseek": "deepseek",
    "claude": "claude",
}
_COST_GROUPS = ("azure_speech", "deepseek", "claude")


def _athens_midnight_utc(day):
    """The UTC instant when the Athens calendar day `day` starts."""
    return datetime(day.year, day.month, day.day, tzinfo=ATHENS_TZ).astimezone(timezone.utc)


def _athens_day_expr(session, column):
    """SQL expression bucketing a UTC timestamp column by Athens calendar day.

    Shifts by the CURRENT Athens UTC offset, so days right around a DST switch
    inside the chart window can land one bucket off — fine for a cost estimate
    chart. (On PostgreSQL the final date() uses the session timezone, UTC on
    Railway, which after the shift IS the Athens date.)
    """
    minutes = int(datetime.now(timezone.utc).astimezone(ATHENS_TZ).utcoffset().total_seconds() // 60)
    if session.get_bind().dialect.name == "postgresql":
        return func.date(column + sql_text(f"interval '{minutes} minutes'"))
    return func.date(column, f"{minutes:+d} minutes")


def _cost_float(value):
    """SUM(est_cost_usd) comes back as Decimal (PostgreSQL) or float — JSON-safe."""
    return round(float(value or 0), 6)


@app.route("/api/admin/costs", methods=["GET"])
def admin_costs():
    """Estimated external-API spend for the 💰 tab, aggregated in SQL.

    Query params: days=<chart window, 1..90, default 14>. Everything reads the
    api_usage_log rollup on the fly (no cron): today's and this month's totals
    (Athens calendar), the month split by provider group, a per-day chart for
    the window, top spenders this month plus the admin/system row (user_id
    NULL), and a per-endpoint breakdown. All figures are ESTIMATES from
    usage.PRICING list prices — exact amounts live in the provider consoles.
    """
    try:
        verify_admin(request)
    except AuthError as exc:
        return jsonify({"error": str(exc)}), exc.status_code

    try:
        days = min(90, max(1, int(request.args.get("days", 14))))
    except (TypeError, ValueError):
        days = 14

    today = athens_date()
    today_start = _athens_midnight_utc(today)
    month_start = _athens_midnight_utc(today.replace(day=1))
    chart_start_day = today - timedelta(days=days - 1)
    chart_start = _athens_midnight_utc(chart_start_day)

    session = SessionLocal()
    try:
        cost_sum = func.sum(ApiUsageLog.est_cost_usd)
        calls = func.count(ApiUsageLog.id)

        today_cost, today_calls = (
            session.query(cost_sum, calls).filter(ApiUsageLog.ts >= today_start).one()
        )
        month_cost, month_calls = (
            session.query(cost_sum, calls).filter(ApiUsageLog.ts >= month_start).one()
        )

        # This month, split by provider — folded into the three UI groups.
        groups = {g: {"cost": 0.0, "calls": 0} for g in _COST_GROUPS}
        rows = (
            session.query(ApiUsageLog.provider, cost_sum, calls)
            .filter(ApiUsageLog.ts >= month_start)
            .group_by(ApiUsageLog.provider)
            .all()
        )
        for provider, cost, count in rows:
            group = groups.get(_PROVIDER_GROUP.get(provider))
            if group is not None:
                group["cost"] = round(group["cost"] + _cost_float(cost), 6)
                group["calls"] += int(count)

        # Daily chart: one row per (Athens day, provider), zero-filled so the
        # frontend always renders exactly `days` bars.
        day_expr = _athens_day_expr(session, ApiUsageLog.ts)
        daily_rows = (
            session.query(day_expr, ApiUsageLog.provider, cost_sum)
            .filter(ApiUsageLog.ts >= chart_start)
            .group_by(day_expr, ApiUsageLog.provider)
            .all()
        )
        by_day = {}
        for day_value, provider, cost in daily_rows:
            key = str(day_value)  # date object on PostgreSQL, string on SQLite
            bucket = by_day.setdefault(key, dict.fromkeys(_COST_GROUPS, 0.0))
            group = _PROVIDER_GROUP.get(provider)
            if group:
                bucket[group] = round(bucket[group] + _cost_float(cost), 6)
        daily = []
        for i in range(days):
            day = chart_start_day + timedelta(days=i)
            bucket = by_day.get(day.isoformat(), dict.fromkeys(_COST_GROUPS, 0.0))
            daily.append(
                {
                    "date": day.isoformat(),
                    **bucket,
                    "total": round(sum(bucket.values()), 6),
                }
            )

        # Top spenders this month. The NULL user_id bucket (admin/Hermes calls)
        # is reported separately as the "system" row.
        top_rows = (
            session.query(ApiUsageLog.user_id, cost_sum, calls)
            .filter(ApiUsageLog.ts >= month_start, ApiUsageLog.user_id.isnot(None))
            .group_by(ApiUsageLog.user_id)
            .order_by(cost_sum.desc())
            .limit(5)
            .all()
        )
        emails = {}
        if top_rows:
            emails = dict(
                session.query(UserProgress.user_id, UserProgress.email)
                .filter(UserProgress.user_id.in_([uid for uid, _c, _n in top_rows]))
                .all()
            )
        top_users = [
            {
                "user_id": uid,
                "email": emails.get(uid),
                "cost": _cost_float(cost),
                "calls": int(count),
            }
            for uid, cost, count in top_rows
        ]
        system_cost, system_calls = (
            session.query(cost_sum, calls)
            .filter(ApiUsageLog.ts >= month_start, ApiUsageLog.user_id.is_(None))
            .one()
        )

        # Per-endpoint breakdown, this month.
        endpoint_rows = (
            session.query(
                ApiUsageLog.provider,
                ApiUsageLog.endpoint,
                calls,
                func.sum(ApiUsageLog.units),
                cost_sum,
            )
            .filter(ApiUsageLog.ts >= month_start)
            .group_by(ApiUsageLog.provider, ApiUsageLog.endpoint)
            .order_by(cost_sum.desc())
            .all()
        )
        endpoints = [
            {
                "provider": provider,
                "endpoint": endpoint,
                "calls": int(count),
                "units": int(units or 0),
                "cost": _cost_float(cost),
            }
            for provider, endpoint, count, units, cost in endpoint_rows
        ]

        return jsonify(
            {
                "days": days,
                "today": {"cost": _cost_float(today_cost), "calls": int(today_calls)},
                "month": {"cost": _cost_float(month_cost), "calls": int(month_calls)},
                "month_groups": groups,
                "daily": daily,
                "top_users": top_users,
                "system": {"cost": _cost_float(system_cost), "calls": int(system_calls)},
                "endpoints": endpoints,
            }
        )
    finally:
        session.close()


@app.route("/api/admin/skill-mismatches", methods=["GET"])
def admin_skill_mismatches():
    """Per-lesson item-type mismatches across ALL non-email lessons.

    Returns {lesson_id: [{item_id, type, skill_type, kind}, ...]} for every
    non-email lesson (draft or approved) that contains an item not allowed in its
    skill_area — keyed on skill_area, so grammar/listening/speaking lessons on
    their own tracks are checked too, not just track == "maritime". Drives the
    read-only warning indicator in the admin — existing offenders are surfaced,
    never modified. Clean lessons are omitted.
    """
    try:
        verify_admin(request)
    except AuthError as exc:
        return jsonify({"error": str(exc)}), exc.status_code

    session = SessionLocal()
    try:
        lessons = session.query(Lesson).filter(Lesson.track != "email").all()
        result = {}
        for lesson in lessons:
            bad = lesson_skill_mismatches(lesson, lesson.items)
            if bad:
                result[lesson.lesson_id] = [
                    {
                        "item_id": item.item_id,
                        "type": item.type,
                        "skill_type": item.skill_type,
                        "kind": kind,
                    }
                    for item, kind in bad
                ]
        return jsonify(result)
    finally:
        session.close()


def _vocab_term(item):
    """Extract (term, meaning_el, phonetic) from a vocabulary item, or None.

    Vocabulary items hold the English word/phrase in english.text and its Greek
    meaning in english.answer (mirrored in explanations.el.translation). Items
    without an English term are skipped.
    """
    data = item.data or {}
    english = data.get("english") or {}
    term = (english.get("text") or "").strip()
    if not term:
        return None
    meaning = (english.get("answer") or "").strip()
    if not meaning:
        el = (data.get("explanations") or {}).get("el") or {}
        meaning = (el.get("translation") or "").strip()
    return {
        "term": term,
        "meaning_el": meaning,
        "phonetic": (english.get("phonetic") or "").strip(),
    }


@app.route("/api/admin/vocabulary-bank", methods=["GET"])
def admin_vocabulary_bank():
    """READ-ONLY: the vocabulary already taught in approved maritime lessons.

    For the Hermes content agent — so it can avoid duplicating words and build
    on existing terms. Scope: approved items of skill_type "vocabulary" inside
    approved, maritime-track lessons (drafts and the email track are excluded).
    Other item kinds (teaching titles, fill_gap/word_order sentences) are not
    single terms and are left out.

    Returns both views: per-lesson term lists, and a de-duplicated term list
    (case-insensitive) with the lessons each term appears in.
    """
    try:
        verify_admin(request)
    except AuthError as exc:
        return jsonify({"error": str(exc)}), exc.status_code

    session = SessionLocal()
    try:
        rows = (
            session.query(Item, Lesson)
            .join(Lesson, Item.lesson_id == Lesson.lesson_id)
            .filter(
                Item.status == "approved",
                Lesson.status == "approved",
                Lesson.track == "maritime",
            )
            .order_by(Lesson.cefr_level, Lesson.order_index, Item.order_index)
            .all()
        )

        lessons = {}  # lesson_id -> lesson payload (insertion order preserved)
        unique = {}  # term.lower() -> de-duplicated term payload
        for item, lesson in rows:
            if item_skill_kind(item) != "vocabulary":
                continue
            term = _vocab_term(item)
            if term is None:
                continue

            entry = lessons.get(lesson.lesson_id)
            if entry is None:
                entry = {
                    "lesson_id": lesson.lesson_id,
                    "title": lesson.title,
                    "cefr_level": lesson.cefr_level,
                    "skill_area": lesson.skill_area,
                    "terms": [],
                }
                lessons[lesson.lesson_id] = entry
            entry["terms"].append(term)

            key = term["term"].lower()
            agg = unique.get(key)
            if agg is None:
                agg = {
                    "term": term["term"],
                    "meaning_el": term["meaning_el"],
                    "phonetic": term["phonetic"],
                    "occurrences": [],
                }
                unique[key] = agg
            agg["occurrences"].append(
                {
                    "lesson_id": lesson.lesson_id,
                    "title": lesson.title,
                    "cefr_level": lesson.cefr_level,
                    "skill_area": lesson.skill_area,
                }
            )

        terms = sorted(unique.values(), key=lambda t: t["term"].lower())
        return jsonify(
            {
                "lesson_count": len(lessons),
                "term_count": len(terms),
                "lessons": list(lessons.values()),
                "terms": terms,
            }
        )
    finally:
        session.close()


# Canonical display order for the curriculum overview.
_CEFR_ORDER = ("A2", "B1", "B2", "C1", "C2")
_SKILL_ORDER = ("vocabulary", "grammar", "listening", "speaking")


def _lesson_teaches(lesson, items):
    """A short, scannable list of what a lesson teaches.

    Vocabulary lessons → the English terms (reusing _vocab_term, same source as
    the vocabulary bank). Other skills → the titles of the teaching items (the
    concept each one introduces, e.g. "Present Simple"); when a lesson has no
    teaching item we fall back to its own title so the entry is never empty.
    """
    if lesson.skill_area == "vocabulary":
        out = []
        for item in items:
            if item_skill_kind(item) != "vocabulary":
                continue
            term = _vocab_term(item)
            if term:
                out.append(term["term"])
        return out

    concepts = []
    for item in items:
        if item_skill_kind(item) != "teaching":
            continue
        text = ((item.data or {}).get("english") or {}).get("text") or ""
        text = text.strip()
        if text:
            concepts.append(text)
    if concepts:
        return concepts
    return [lesson.title] if lesson.title else []


@app.route("/api/admin/curriculum-overview", methods=["GET"])
def admin_curriculum_overview():
    """READ-ONLY: approved maritime-path lessons grouped by level and skill.

    For the Hermes content agent — to plan new material without overlapping what
    already exists ("A2 Grammar already has X, Y, Z"). Scope: every approved,
    non-email lesson — the same set the home shows on the "Ναυτικά Αγγλικά" path
    (so grammar-track lessons are included, not just track == "maritime"). Each
    lesson lists what it teaches: vocabulary terms for vocabulary lessons,
    teaching-item concept titles for grammar/listening/speaking (see
    _lesson_teaches).

    Shape: { lesson_count, levels: [ { cefr_level, skills: [ { skill_area,
    lesson_count, lessons: [ { lesson_id, title, cefr_level, skill_area,
    order_index, teaches: [...] } ] } ] } ] }.
    """
    try:
        verify_admin(request)
    except AuthError as exc:
        return jsonify({"error": str(exc)}), exc.status_code

    session = SessionLocal()
    try:
        # "Maritime path" = every approved, non-email lesson — the SAME criterion
        # the home uses to show a lesson on the "Ναυτικά Αγγλικά" path
        # (track !== "email"). NOT track == "maritime": grammar-skill lessons live
        # on the separate "grammar" track and would be wrongly excluded otherwise.
        lessons = (
            session.query(Lesson)
            .filter(Lesson.status == "approved", Lesson.track != "email")
            .all()
        )
        items_by_lesson = {}
        rows = (
            session.query(Item)
            .join(Lesson, Item.lesson_id == Lesson.lesson_id)
            .filter(
                Item.status == "approved",
                Lesson.status == "approved",
                Lesson.track != "email",
            )
            .order_by(Item.order_index)
            .all()
        )
        for item in rows:
            items_by_lesson.setdefault(item.lesson_id, []).append(item)

        # level -> skill_area -> [lesson payloads]
        grouped = {}
        for lesson in lessons:
            entry = {
                "lesson_id": lesson.lesson_id,
                "title": lesson.title,
                "cefr_level": lesson.cefr_level,
                "skill_area": lesson.skill_area,
                "order_index": lesson.order_index,
                "teaches": _lesson_teaches(lesson, items_by_lesson.get(lesson.lesson_id, [])),
            }
            grouped.setdefault(lesson.cefr_level, {}).setdefault(lesson.skill_area, []).append(entry)

        def lesson_sort(l):
            return (l["order_index"] if l["order_index"] is not None else 1_000_000, l["title"] or "")

        def ordered_keys(keys, canonical):
            present = [k for k in canonical if k in keys]
            extras = sorted((k for k in keys if k not in canonical), key=lambda k: (k is None, k or ""))
            return present + extras

        levels_out = []
        for level in ordered_keys(grouped.keys(), _CEFR_ORDER):
            skills_map = grouped[level]
            skills_out = []
            for skill in ordered_keys(skills_map.keys(), _SKILL_ORDER):
                ls = sorted(skills_map[skill], key=lesson_sort)
                skills_out.append(
                    {"skill_area": skill, "lesson_count": len(ls), "lessons": ls}
                )
            levels_out.append({"cefr_level": level, "skills": skills_out})

        return jsonify({"lesson_count": len(lessons), "levels": levels_out})
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

        gaps = analyze_lesson_gaps(items, lesson.skill_area)
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

    payload = request.get_json(silent=True) or {}
    force = bool(payload.get("force"))  # admin chose to publish despite warnings

    session = SessionLocal()
    try:
        lesson = session.query(Lesson).filter_by(lesson_id=lesson_id).one_or_none()
        if lesson is None:
            logger.warning("Approve failed: lesson %s not found", lesson_id)
            return jsonify({"error": f"Lesson '{lesson_id}' not found."}), 404

        # Validate item types against the lesson's skill_area (maritime only).
        # Fail-closed: refuse to publish a mismatch unless the admin overrides
        # with force=true, and spell out exactly which item is wrong and why.
        all_items = (
            session.query(Item).filter_by(lesson_id=lesson_id).order_by(Item.order_index).all()
        )
        mismatches = lesson_skill_mismatches(lesson, all_items)
        if mismatches and not force:
            allowed = sorted(SKILL_AREA_ITEM_TYPES.get(lesson.skill_area, set()))
            logger.warning(
                "Approve blocked: lesson %s has %d item(s) outside skill_area %r",
                lesson_id,
                len(mismatches),
                lesson.skill_area,
            )
            return (
                jsonify(
                    {
                        "error": (
                            f"{len(mismatches)} άσκηση(εις) δεν ταιριάζει στη δεξιότητα "
                            f"'{lesson.skill_area}'. Επιτρεπτοί τύποι: {', '.join(allowed)}."
                        ),
                        "skill_area": lesson.skill_area,
                        "allowed_types": allowed,
                        "mismatches": [
                            {
                                "item_id": item.item_id,
                                "type": item.type,
                                "skill_type": item.skill_type,
                                "kind": kind,
                            }
                            for item, kind in mismatches
                        ],
                    }
                ),
                422,
            )

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
            # The admin edits the Greek text; keep the language-keyed sibling
            # in sync without dropping any other language's entry.
            i18n = dict(lesson.description_i18n or {})
            i18n[DEFAULT_LANG] = payload["description"]
            lesson.description_i18n = i18n
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
        if "cefr_level" in payload:
            # Allow clearing (None/empty) or a valid A2-C2 band.
            value = payload["cefr_level"] or None
            if value is not None and value not in ALLOWED_CEFR_LEVELS:
                return jsonify({"error": f"Invalid cefr_level: '{value}'."}), 400
            lesson.cefr_level = value
        if "skill_area" in payload:
            value = payload["skill_area"] or None
            if value is not None and value not in ALLOWED_SKILL_AREAS:
                return jsonify({"error": f"Invalid skill_area: '{value}'."}), 400
            lesson.skill_area = value
        if "order_index" in payload:
            raw = payload["order_index"]
            if raw in (None, ""):
                lesson.order_index = None
            else:
                try:
                    lesson.order_index = max(0, int(raw))
                except (TypeError, ValueError):
                    return jsonify({"error": "order_index must be an integer."}), 400

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
