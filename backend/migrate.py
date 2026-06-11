"""Idempotent migration: add editorial fields to the `items` table.

Adds three columns with safe defaults, without touching existing data:
  - difficulty  (CEFR: A1 | A2 | B1 | B2 | C1) — backfilled from each item's
                existing `level` (A1->A1, A2->A2, ...), fallback 'B1'
  - status      (draft | approved)             — default 'approved'
  - skill_type  (vocabulary | listening | fill_gap | word_order | speaking | roleplay)

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


def run():
    if engine is None:
        raise RuntimeError("DATABASE_URL is not set; cannot run the migration.")

    insp = inspect(engine)
    existing = _columns(insp, "items")

    with engine.begin() as conn:
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

    logger.info("Migration complete.")


if __name__ == "__main__":
    run()
