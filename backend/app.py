import logging

from flask import Flask, Response, jsonify, request
from flask_cors import CORS
from sqlalchemy import func

from db import SessionLocal, init_db
from models import Item, Lesson
from pronunciation import PronunciationError, assess_pronunciation
from roleplay import RoleplayError, chat as roleplay_chat
from transcription import transcribe
from tts import synthesize as synthesize_speech

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
        "data": item.data,
    }


def item_counts(session):
    """Return a {lesson_id: count} map in a single query."""
    rows = session.query(Item.lesson_id, func.count(Item.id)).group_by(Item.lesson_id).all()
    return dict(rows)


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


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
