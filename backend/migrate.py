"""Idempotent migration: add editorial fields to the `items` table.

Adds three columns with safe defaults, without touching existing data:
  - difficulty  (CEFR: A1 | A2 | B1 | B2 | C1) — backfilled from each item's
                existing `level` (A1->A1, A2->A2, ...), fallback 'B1'
  - status      (draft | approved)             — default 'approved'
  - skill_type  (vocabulary | listening | fill_gap | word_order | speaking | roleplay)

Also adds the placement-test columns to `user_progress` (cefr_level,
maritime_level), both nullable — NULL means "placement not taken yet".

`create_all` (used on startup) does NOT add columns to an existing table, so we
run explicit ALTERs here. Safe to run repeatedly: columns are only added when
missing, and `skill_type` is only backfilled where it is still NULL (so any
later editorial curation is preserved).

Usage (locally or on Railway):

    python migrate.py
"""

import logging

from sqlalchemy import inspect, text

from db import engine

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Map the existing `type` values onto the requested skill_type vocabulary.
# (translation isn't in the target enum; it's a spoken production task here.)
SKILL_TYPE_BACKFILL_SQL = """
    UPDATE items
    SET skill_type = CASE type
        WHEN 'dialogue' THEN 'roleplay'
        WHEN 'translation' THEN 'speaking'
        ELSE type
    END
    WHERE skill_type IS NULL
"""

# Backfill difficulty from each item's existing CEFR `level` (A1->A1 etc.),
# falling back to B1 when level is missing or not a known CEFR band. Only run
# when the column was just created, so curated values survive re-runs.
DIFFICULTY_BACKFILL_SQL = """
    UPDATE items
    SET difficulty = CASE
        WHEN level IN ('A1', 'A2', 'B1', 'B2', 'C1') THEN level
        ELSE 'B1'
    END
"""


def _columns(insp, table):
    return {col["name"] for col in insp.get_columns(table)}


# A fixed key for the Postgres advisory lock that serialises concurrent runs
# (e.g. several gunicorn workers applying the migration on startup at once).
_ADVISORY_LOCK_KEY = 91237001


def run():
    if engine is None:
        raise RuntimeError("DATABASE_URL is not set; cannot run the migration.")

    with engine.begin() as conn:
        is_postgres = engine.dialect.name == "postgresql"
        if is_postgres:
            # Transaction-scoped lock: only one worker runs the body at a time;
            # it's released automatically on commit/rollback.
            conn.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _ADVISORY_LOCK_KEY})

        # Inspect AFTER acquiring the lock, on this connection, so we see any
        # changes a concurrent run already committed (and skip them).
        insp = inspect(conn)
        columns = insp.get_columns("items")
        existing = {c["name"] for c in columns}
        lesson_id_not_null = any(
            c["name"] == "lesson_id" and c.get("nullable") is False for c in columns
        )

        if "difficulty" not in existing:
            conn.execute(
                text("ALTER TABLE items ADD COLUMN difficulty VARCHAR NOT NULL DEFAULT 'B1'")
            )
            # Fresh column: copy each item's CEFR level (fallback B1). Done only
            # here so curated values are never overwritten on later runs.
            result = conn.execute(text(DIFFICULTY_BACKFILL_SQL))
            logger.info(
                "Added items.difficulty; backfilled from level for %s row(s).",
                result.rowcount,
            )
        else:
            logger.info("items.difficulty already exists — skipping.")

        if "status" not in existing:
            conn.execute(
                text("ALTER TABLE items ADD COLUMN status VARCHAR NOT NULL DEFAULT 'approved'")
            )
            logger.info("Added items.status (default 'approved').")
        else:
            logger.info("items.status already exists — skipping.")

        if "skill_type" not in existing:
            conn.execute(text("ALTER TABLE items ADD COLUMN skill_type VARCHAR"))
            logger.info("Added items.skill_type.")
        else:
            logger.info("items.skill_type already exists — skipping.")

        # Backfill only rows that don't have a skill_type yet (preserves curation).
        result = conn.execute(text(SKILL_TYPE_BACKFILL_SQL))
        logger.info("Backfilled skill_type for %s row(s).", result.rowcount)

        # Lessons: add status (draft|approved) and an optional Greek title.
        lesson_columns = {c["name"] for c in insp.get_columns("lessons")}
        if "status" not in lesson_columns:
            conn.execute(
                text("ALTER TABLE lessons ADD COLUMN status VARCHAR NOT NULL DEFAULT 'approved'")
            )
            logger.info("Added lessons.status (default 'approved').")
        else:
            logger.info("lessons.status already exists — skipping.")
        if "title_el" not in lesson_columns:
            conn.execute(text("ALTER TABLE lessons ADD COLUMN title_el VARCHAR"))
            logger.info("Added lessons.title_el.")
        else:
            logger.info("lessons.title_el already exists — skipping.")
        # Role category: engineer | deck | common. Existing lessons default to
        # "common"; the admin reclassifies them from the review UI.
        if "role_category" not in lesson_columns:
            conn.execute(
                text(
                    "ALTER TABLE lessons ADD COLUMN role_category VARCHAR "
                    "NOT NULL DEFAULT 'common'"
                )
            )
            logger.info("Added lessons.role_category (default 'common').")
        else:
            logger.info("lessons.role_category already exists — skipping.")

        # User progress: placement results (NULL until the user takes the
        # placement test). The table may not exist yet on a fresh database, in
        # which case create_all builds it with the new columns already present.
        if insp.has_table("user_progress"):
            progress_columns = {c["name"] for c in insp.get_columns("user_progress")}
            for column in ("cefr_level", "maritime_level", "user_role"):
                if column not in progress_columns:
                    conn.execute(
                        text(f"ALTER TABLE user_progress ADD COLUMN {column} VARCHAR")
                    )
                    logger.info("Added user_progress.%s.", column)
                else:
                    logger.info("user_progress.%s already exists — skipping.", column)
        else:
            logger.info("user_progress table not present yet — skipping (create_all adds it).")

        # Allow draft items with no lesson: relax items.lesson_id NOT NULL.
        if lesson_id_not_null:
            if is_postgres:
                conn.execute(text("ALTER TABLE items ALTER COLUMN lesson_id DROP NOT NULL"))
                logger.info("Relaxed items.lesson_id to NULLABLE.")
            else:
                logger.info(
                    "items.lesson_id is NOT NULL but dialect=%s cannot ALTER it "
                    "in place — skipping (fresh DBs already create it nullable).",
                    engine.dialect.name,
                )
        else:
            logger.info("items.lesson_id already nullable — skipping.")

    logger.info("Migration complete.")


if __name__ == "__main__":
    run()
