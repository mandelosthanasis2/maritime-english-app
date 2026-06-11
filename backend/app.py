import logging
import uuid
from datetime import datetime, timedelta, timezone

from flask import Flask, Response, jsonify, request
from flask_cors import CORS
from sqlalchemy import func

from admin import (
    ALLOWED_DIFFICULTY,
    ALLOWED_SKILL_TYPES,
    AdminGenError,
    extract_text_from_pdf,
    generate_items,
)
from auth import AuthError, verify_admin, verify_request
from db import SessionLocal, init_db
from models import Item, Lesson, UserLessonCompletion, UserProgress
from pronunciation import PronunciationError, assess_pronunciation
from roleplay import RoleplayError, chat as roleplay_chat
from transcription import transcribe
from tts import synthesize as synthesize_speech

# XP awards.
XP_FIRST_COMPLETION = 50
XP_REVIEW = 10

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


# --- Serialization helpers ---------------------------------------------------


def serialize_lesson_meta(lesson, item_count):
    """Lesson metadata only (no items)."""
    return {
        "lesson_id": lesson.lesson_id,
        "track": lesson.track,
        "module": lesson.module,
        "title": lesson.title,
        "description": lesson.description,
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
    """Return a {lesson_id: count} map in a single query."""
    rows = session.query(Item.lesson_id, func.count(Item.id)).group_by(Item.lesson_id).all()
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
        lessons = session.query(Lesson).order_by(Lesson.lesson_id).all()
        return jsonify(
            [serialize_lesson_meta(l, counts.get(l.lesson_id, 0)) for l in lessons]
        )
    finally:
        session.close()


@app.route("/api/lessons/<lesson_id>", methods=["GET"])
def get_lesson(lesson_id):
    session = SessionLocal()
    try:
        lesson = session.query(Lesson).filter_by(lesson_id=lesson_id).one_or_none()
        if lesson is None:
            return (
                jsonify({"error": f"Lesson '{lesson_id}' not found."}),
                404,
            )
        payload = serialize_lesson_meta(lesson, len(lesson.items))
        # lesson.items is ordered by order_index via the relationship.
        payload["items"] = [serialize_item(item) for item in lesson.items]
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
            .filter_by(track=track)
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
        last = progress.last_active_date
        if last is None or last < today - timedelta(days=1):
            progress.current_streak = 1  # first activity, or a gap > 1 day
        elif last == today - timedelta(days=1):
            progress.current_streak += 1  # consecutive day
        # last == today -> already counted today, leave streak unchanged
        progress.last_active_date = today

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


def store_generated_item(session, raw, fallback_difficulty, order_index):
    """Persist one generated item dict as a draft Item and return it."""
    data = dict(raw)
    data.pop("audio_url", None)

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
        lesson_id=None,  # drafts aren't attached to a lesson yet
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


@app.route("/api/admin/generate-items", methods=["POST"])
def admin_generate_items():
    try:
        verify_admin(request)
    except AuthError as exc:
        return jsonify({"error": str(exc)}), exc.status_code

    # Accept multipart (PDF + fields) or a JSON body.
    js = request.get_json(silent=True) or {}

    def field(name, default=""):
        return request.form.get(name) or js.get(name) or default

    kind = field("kind", "auto")
    page_range = field("page_range", "")
    source_text = field("source_text", "")
    pdf_file = request.files.get("pdf")

    try:
        if pdf_file is not None:
            pdf_text = extract_text_from_pdf(pdf_file.read(), page_range)
            source_text = (
                f"{pdf_text}\n\n{source_text}".strip() if source_text.strip() else pdf_text
            )
        generated = generate_items(source_text=source_text, kind=kind)
    except AdminGenError as exc:
        return jsonify({"error": str(exc)}), exc.status_code

    fallback_difficulty = "B1"

    session = SessionLocal()
    try:
        base = (
            session.query(func.coalesce(func.max(Item.order_index), -1))
            .filter(Item.lesson_id.is_(None))
            .scalar()
        )
        stored = []
        for offset, raw in enumerate(generated, start=1):
            stored.append(
                store_generated_item(session, raw, fallback_difficulty, base + offset)
            )
        session.commit()
        return jsonify({"items": [serialize_admin_item(i) for i in stored]})
    except Exception:  # pragma: no cover - unexpected failure
        session.rollback()
        logger.exception("Storing generated items failed")
        return jsonify({"error": "Internal error storing generated items."}), 500
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


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
