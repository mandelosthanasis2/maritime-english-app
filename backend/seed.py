"""Seed the database from a lesson content JSON file.

Idempotent: running it repeatedly upserts by ``lesson_id`` / ``item_id`` rather
than creating duplicates, so it is safe to re-run after content updates.

Usage (locally or on Railway):

    python seed.py                       # uses the default lesson file
    LESSON_FILE=/path/to/lesson.json python seed.py
"""

import json
import logging
import os

from db import SessionLocal, init_db
from models import Item, Lesson

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Resolve the lesson file relative to this script so it works regardless of the
# current working directory (e.g. /app on Railway). The content lives inside the
# backend folder so the service is self-contained.
BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CONTENT_PATH = os.path.join(
    BACKEND_DIR, "content", "engine_orders_lesson01_v2.json"
)

# Lesson-level fields copied into structured columns. Everything item-level is
# kept verbatim in the JSONB `data` column.
LESSON_FIELDS = (
    "track",
    "module",
    "title",
    "description",
    "source",
    "interface_language",
    "target_language",
    "version",
)

# Map source `type` onto the editorial skill_type vocabulary.
SKILL_TYPE_MAP = {"dialogue": "roleplay", "translation": "speaking"}


def skill_type_for(item):
    """Resolve skill_type from an explicit override or the item's type."""
    explicit = item.get("skill_type")
    if explicit:
        return explicit
    item_type = item.get("type")
    return SKILL_TYPE_MAP.get(item_type, item_type)


def seed(path=None):
    path = path or os.environ.get("LESSON_FILE", DEFAULT_CONTENT_PATH)

    # Make sure the tables exist before we try to write to them.
    init_db()

    with open(path, "r", encoding="utf-8") as fh:
        payload = json.load(fh)

    lesson_id = payload["lesson_id"]
    items = payload.get("items", [])

    session = SessionLocal()
    try:
        lesson = session.query(Lesson).filter_by(lesson_id=lesson_id).one_or_none()
        if lesson is None:
            lesson = Lesson(lesson_id=lesson_id)
            session.add(lesson)
            logger.info("Creating lesson '%s'.", lesson_id)
        else:
            logger.info("Updating existing lesson '%s'.", lesson_id)

        for field in LESSON_FIELDS:
            setattr(lesson, field, payload.get(field))

        # Ensure the lesson row is present so item foreign keys resolve.
        session.flush()

        created, updated = 0, 0
        for index, item in enumerate(items):
            item_id = item["id"]
            row = session.query(Item).filter_by(item_id=item_id).one_or_none()
            if row is None:
                row = Item(item_id=item_id)
                session.add(row)
                created += 1
                # Editorial fields are set only on insert so curated values are
                # never overwritten when re-seeding content.
                row.difficulty = item.get("difficulty", "B1")
                row.status = item.get("status", "approved")
                row.skill_type = skill_type_for(item)
            else:
                updated += 1

            row.lesson_id = lesson_id
            row.type = item.get("type")
            row.level = item.get("level")
            row.order_index = index
            row.data = item

        session.commit()
        logger.info(
            "Seeded lesson '%s': %d item(s) created, %d updated.",
            lesson_id,
            created,
            updated,
        )
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


if __name__ == "__main__":
    seed()
