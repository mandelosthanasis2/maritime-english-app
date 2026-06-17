"""Idempotent migration: add editorial fields to the `items` table.

Adds three columns with safe defaults, without touching existing data:
  - difficulty  (CEFR: A1 | A2 | B1 | B2 | C1) — backfilled from each item's
                existing `level` (A1->A1, A2->A2, ...), fallback 'B1'
  - status      (draft | approved)             — default 'approved'
  - skill_type  (vocabulary | listening | fill_gap | word_order | speaking | roleplay)

Also adds the placement-test columns to `user_progress` (cefr_level,
maritime_level), both nullable — NULL means "placement not taken yet", and the
new lesson-architecture columns to `lessons` (cefr_level A2-C2, skill_area,
order_index), backfilled once from each lesson's own items, plus
`user_lesson_completions.best_score` for the skill-tree unlock.

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


# --- Lesson CEFR / skill_area backfill ----------------------------------------
#
# Derives each lesson's organizing dimensions from its own items, once, when the
# columns are first added. Kept in Python (not raw SQL) because the skill_area
# heuristic is a per-lesson majority vote that is awkward to express portably.

# Item CEFR bands on the difficulty scale (A1-C1); lesson levels are A2-C2.
_ITEM_BANDS = ("A1", "A2", "B1", "B2", "C1")
# Lesson CEFR bands. C2 is never produced by the backfill (items max out at C1);
# it exists for content the generator/admin levels up explicitly.
_LESSON_BANDS = ("A2", "B1", "B2", "C1", "C2")

# How a single item's skill_type/type maps to one of the 4 lesson skill areas.
# fill_gap / word_order are production drills counted as vocabulary for the
# (non-grammar) maritime path; grammar-track lessons are forced to "grammar"
# wholesale below, so this mapping only decides among the maritime skills.
_SKILL_AREA_FROM_ITEM = {
    "vocabulary": "vocabulary",
    "fill_gap": "vocabulary",
    "word_order": "vocabulary",
    "listening": "listening",
    "speaking": "speaking",
    "roleplay": "speaking",
}


def _cefr_from_items(rows):
    """Lesson CEFR (A2-C2) = rounded mean of its items' difficulty, A1 lifted to A2."""
    indices = [_ITEM_BANDS.index(d) for d, _s, _t in rows if d in _ITEM_BANDS]
    if not indices:
        return "A2"  # no gradable items yet — start everyone at the floor
    band = _ITEM_BANDS[round(sum(indices) / len(indices))]
    return "A2" if band == "A1" else band


def _skill_area_from_items(track, rows):
    """Lesson skill area via majority vote over item skill_types (grammar wins by track)."""
    if track == "grammar":
        return "grammar"
    votes = {"vocabulary": 0, "listening": 0, "speaking": 0}
    for _d, skill, item_type in rows:
        area = _SKILL_AREA_FROM_ITEM.get((skill or item_type or "").lower())
        if area in votes:
            votes[area] += 1
    # Highest vote wins; ties (and all-teaching lessons) fall back to vocabulary.
    best = max(votes, key=lambda k: votes[k])
    return best if votes[best] else "vocabulary"


def _backfill_lesson_dimensions(conn, do_cefr, do_skill):
    """Set cefr_level / skill_area for each lesson from its items (NULL rows only)."""
    lessons = conn.execute(text("SELECT lesson_id, track FROM lessons")).fetchall()
    updated = 0
    for lesson_id, track in lessons:
        # Email lessons are a separate path — leave their dimensions NULL.
        if track == "email":
            continue
        rows = conn.execute(
            text("SELECT difficulty, skill_type, type FROM items WHERE lesson_id = :lid"),
            {"lid": lesson_id},
        ).fetchall()
        sets, params = [], {"lid": lesson_id}
        if do_cefr:
            sets.append("cefr_level = :cefr")
            params["cefr"] = _cefr_from_items(rows)
        if do_skill:
            sets.append("skill_area = :skill")
            params["skill"] = _skill_area_from_items(track, rows)
        if not sets:
            continue
        result = conn.execute(
            text(
                f"UPDATE lessons SET {', '.join(sets)} "
                "WHERE lesson_id = :lid AND (cefr_level IS NULL OR skill_area IS NULL)"
            ),
            params,
        )
        updated += result.rowcount or 0
    logger.info(
        "Backfilled lesson dimensions (cefr=%s, skill=%s) for %d lesson(s).",
        do_cefr,
        do_skill,
        updated,
    )


def _mean_difficulty(rows):
    """Mean item-difficulty index (A1=0 … C1=4); a high default sinks item-less lessons last."""
    indices = [_ITEM_BANDS.index(d) for d, in rows if d in _ITEM_BANDS]
    return sum(indices) / len(indices) if indices else len(_ITEM_BANDS)


def _backfill_order_index(conn):
    """Sequence lessons within each (cefr_level, skill_area) section by difficulty.

    Easier/more fundamental first (lower mean item difficulty), ties broken by
    creation order (id) for stability. Email lessons and rows that already have
    an order_index are left untouched, so later manual ordering survives re-runs.
    """
    lessons = conn.execute(
        text(
            "SELECT lesson_id, cefr_level, skill_area, id FROM lessons "
            "WHERE track != 'email' AND order_index IS NULL"
        )
    ).fetchall()

    groups = {}
    for lesson_id, cefr, skill, row_id in lessons:
        rows = conn.execute(
            text("SELECT difficulty FROM items WHERE lesson_id = :lid"),
            {"lid": lesson_id},
        ).fetchall()
        groups.setdefault((cefr, skill), []).append(
            (_mean_difficulty(rows), row_id, lesson_id)
        )

    updated = 0
    for members in groups.values():
        members.sort()  # (mean difficulty, id) — easiest first, stable by creation
        for position, (_diff, _row_id, lesson_id) in enumerate(members):
            conn.execute(
                text("UPDATE lessons SET order_index = :pos WHERE lesson_id = :lid"),
                {"pos": position, "lid": lesson_id},
            )
            updated += 1
    logger.info("Backfilled lessons.order_index for %d lesson(s).", updated)


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

        # New lesson architecture: per-lesson CEFR band (A2-C2) and skill area
        # (vocabulary | grammar | listening | speaking). Both nullable; backfilled
        # ONCE from each lesson's own items right after the column is created, so
        # later editorial curation is never overwritten on re-runs. Email-track
        # lessons are left NULL (the email path doesn't use these dimensions).
        added_cefr = "cefr_level" not in lesson_columns
        if added_cefr:
            conn.execute(text("ALTER TABLE lessons ADD COLUMN cefr_level VARCHAR"))
            logger.info("Added lessons.cefr_level.")
        else:
            logger.info("lessons.cefr_level already exists — skipping.")
        added_skill = "skill_area" not in lesson_columns
        if added_skill:
            conn.execute(text("ALTER TABLE lessons ADD COLUMN skill_area VARCHAR"))
            logger.info("Added lessons.skill_area.")
        else:
            logger.info("lessons.skill_area already exists — skipping.")
        if added_cefr or added_skill:
            _backfill_lesson_dimensions(conn, do_cefr=added_cefr, do_skill=added_skill)

        # Skill-tree ordering: position within the (cefr_level, skill_area)
        # section. Backfilled once from item difficulty (see _backfill_order_index),
        # after cefr_level/skill_area exist (added just above on a fresh DB).
        if "order_index" not in lesson_columns:
            conn.execute(text("ALTER TABLE lessons ADD COLUMN order_index INTEGER"))
            logger.info("Added lessons.order_index.")
            _backfill_order_index(conn)
        else:
            logger.info("lessons.order_index already exists — skipping.")

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

        # Lesson completions: best_score (0-100) drives the skill-tree unlock.
        # NULL = score never measured (legacy completions / lessons without
        # auto-graded items) and is grandfathered as "passed".
        if insp.has_table("user_lesson_completions"):
            completion_columns = {
                c["name"] for c in insp.get_columns("user_lesson_completions")
            }
            if "best_score" not in completion_columns:
                conn.execute(
                    text("ALTER TABLE user_lesson_completions ADD COLUMN best_score INTEGER")
                )
                logger.info("Added user_lesson_completions.best_score.")
            else:
                logger.info("user_lesson_completions.best_score already exists — skipping.")
        else:
            logger.info(
                "user_lesson_completions table not present yet — skipping (create_all adds it)."
            )

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
