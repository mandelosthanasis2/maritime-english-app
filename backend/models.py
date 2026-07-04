"""SQLAlchemy data models for lessons and their items.

The design is intentionally lightweight: structured columns hold the fields we
expect to query or filter on, while the full, rich item object from the source
JSON is kept verbatim in a JSONB ``data`` column so nothing is lost and the
schema does not need to change as lesson content evolves.
"""

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from db import Base

# JSONB on PostgreSQL (production on Railway), plain JSON elsewhere (e.g. SQLite
# for local testing).
JSONType = JSON().with_variant(JSONB(), "postgresql")


class Lesson(Base):
    __tablename__ = "lessons"

    id = Column(Integer, primary_key=True)
    lesson_id = Column(String, unique=True, nullable=False, index=True)
    track = Column(String)
    module = Column(String)
    title = Column(String)
    title_el = Column(String)  # Greek title (AI-suggested lessons)
    description = Column(Text)
    source = Column(Text)
    interface_language = Column(String)
    target_language = Column(String)
    version = Column(Integer)
    # draft | approved — AI-suggested lessons start as draft; existing default approved.
    status = Column(String, nullable=False, server_default="approved")
    # Who the lesson is for: engineer | deck | common.
    # "common" = for everyone (safety, grammar, basic communication, SMCP);
    # grammar-track lessons are always common.
    role_category = Column(String, nullable=False, server_default="common")
    # New lesson architecture (organizing dimensions, distinct from items.difficulty):
    #   cefr_level  the lesson's CEFR band: A2 | B1 | B2 | C1 | C2. The home groups
    #               the maritime path BY this level. Nullable until set by the
    #               generator/admin or backfilled from the lesson's own items.
    #   skill_area  the lesson's single skill: vocabulary | grammar | listening |
    #               speaking. The home shows these 4 skill sections within a level.
    # Both are independent of items.difficulty (A1-C1), which placement/adaptive
    # keep using untouched. Email-track lessons leave these NULL (separate path).
    cefr_level = Column(String)  # A2 | B1 | B2 | C1 | C2
    skill_area = Column(String)  # vocabulary | grammar | listening | speaking
    # Position of the lesson within its (cefr_level, skill_area) section. Lower =
    # earlier/more fundamental. Drives the skill-tree sequence and the strict
    # unlock (next lesson opens only after the previous is passed). Nullable until
    # set by the generator/admin or backfilled; email-track lessons leave it NULL.
    order_index = Column(Integer)

    items = relationship(
        "Item",
        back_populates="lesson",
        cascade="all, delete-orphan",
        order_by="Item.order_index",
    )


class Item(Base):
    __tablename__ = "items"

    id = Column(Integer, primary_key=True)
    # Nullable so admin-generated draft items can exist before being assigned to
    # a lesson; real lesson items always set this.
    lesson_id = Column(
        String,
        ForeignKey("lessons.lesson_id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    item_id = Column(String, unique=True, nullable=False, index=True)
    type = Column(String)
    level = Column(String)
    order_index = Column(Integer)
    # Editorial metadata (curated separately from the source JSON):
    #   difficulty  CEFR band: A1 | A2 | B1 | B2 | C1   (default B1)
    #   status      draft | approved                     (default approved)
    #   skill_type  teaching | vocabulary | listening | fill_gap | word_order | speaking | roleplay
    #               ("teaching" = concept card read before the exercises; no answer)
    difficulty = Column(String, nullable=False, server_default="B1")
    status = Column(String, nullable=False, server_default="approved")
    skill_type = Column(String)
    # The full original item object (english, explanations, pronunciation_focus,
    # tags, ...) stored exactly as it appears in the source JSON.
    data = Column(JSONType, nullable=False)

    lesson = relationship("Lesson", back_populates="items")


class UserProgress(Base):
    """Per-user XP and streak, keyed by the Supabase user id."""

    __tablename__ = "user_progress"

    user_id = Column(String, primary_key=True)  # Supabase auth user id (UUID)
    email = Column(String)
    total_xp = Column(Integer, nullable=False, default=0)
    current_streak = Column(Integer, nullable=False, default=0)
    last_active_date = Column(Date)
    # When the progress row was first created — the closest thing we have to a
    # signup date (the row is created on the user's first authenticated call).
    # NULL for accounts that predate the column when no backfill source existed
    # (see migrate.py); retention cohorts skip NULL rows.
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    # Placement test results; NULL until the user takes the placement.
    cefr_level = Column(String)  # A1 | A2 | B1 | B2 | C1
    maritime_level = Column(String)  # none | basic | proficient
    # Onboarding role choice; NULL until chosen in onboarding.
    user_role = Column(String)  # engineer | deck | undecided


class UserLessonCompletion(Base):
    """One row per (user, lesson) — tracks completion and XP from that lesson."""

    __tablename__ = "user_lesson_completions"
    __table_args__ = (UniqueConstraint("user_id", "lesson_id", name="uq_user_lesson"),)

    id = Column(Integer, primary_key=True)
    user_id = Column(String, nullable=False, index=True)
    lesson_id = Column(String, nullable=False, index=True)
    completed_at = Column(DateTime(timezone=True), server_default=func.now())
    times_completed = Column(Integer, nullable=False, default=1)
    xp_earned = Column(Integer, nullable=False, default=0)  # cumulative from this lesson
    # Best lesson score (0-100), used by the skill-tree unlock: a lesson counts
    # as "passed" (unlocks the next) when best_score >= 75 OR is NULL. NULL means
    # the score was never measured — legacy completions and lessons with no
    # auto-graded items — and is grandfathered as passed.
    best_score = Column(Integer)


class UserItemStat(Base):
    """One row per (user, item): the user's answer history for that item.

    Feeds the adaptive engine (adaptive.py). track/skill_type/difficulty are
    denormalized from the item's lesson at write time so per-track / per-skill /
    per-difficulty success rates can be aggregated without joins, and so the
    history survives later item edits. Per-attempt detail isn't kept — counts
    plus the most recent outcome are enough for the current selection logic.
    """

    __tablename__ = "user_item_stats"
    __table_args__ = (UniqueConstraint("user_id", "item_id", name="uq_user_item_stat"),)

    id = Column(Integer, primary_key=True)
    user_id = Column(String, nullable=False, index=True)
    item_id = Column(String, nullable=False, index=True)
    track = Column(String)  # grammar | maritime
    skill_type = Column(String)
    difficulty = Column(String)  # CEFR band A1..C1
    correct_count = Column(Integer, nullable=False, default=0)
    wrong_count = Column(Integer, nullable=False, default=0)
    last_correct = Column(Boolean)  # outcome of the most recent attempt
    last_answered_at = Column(DateTime(timezone=True))  # for spaced repetition


class UserActivityDay(Base):
    """One row per (user, day): a tiny daily activity rollup for beta metrics.

    `activity_date` is the user's LOCAL day (Europe/Athens — the app's
    audience), not UTC. A row means "this user did something that day" (opened
    the app with a valid session, answered, or completed a lesson); `answers`
    counts smart-practice/lesson answers recorded that day. Written from
    get_or_create_progress / record_answer, read only by the admin Users tab
    (actives, retention cohorts, per-user 14-day sparkline). Per-attempt
    detail is intentionally NOT kept — one row per day is enough.
    """

    __tablename__ = "user_activity_days"
    __table_args__ = (
        UniqueConstraint("user_id", "activity_date", name="uq_user_activity_day"),
    )

    id = Column(Integer, primary_key=True)
    user_id = Column(String, nullable=False, index=True)
    activity_date = Column(Date, nullable=False, index=True)
    answers = Column(Integer, nullable=False, default=0)


class UserSectionTest(Base):
    """One row per (user, section) — the user's module-test result for a section.

    A "section" is a (cefr_level, skill_area) pair on the maritime path, e.g.
    "A2 / vocabulary". best_score is the highest module-test score (0-100) the
    user has achieved; the section is "mastered" when best_score >= the pass
    mark. passed_at records the first time it was mastered. Created lazily on the
    first test attempt; absent rows simply mean "not attempted yet".
    """

    __tablename__ = "user_section_tests"
    __table_args__ = (
        UniqueConstraint("user_id", "cefr_level", "skill_area", name="uq_user_section_test"),
    )

    id = Column(Integer, primary_key=True)
    user_id = Column(String, nullable=False, index=True)
    cefr_level = Column(String, nullable=False)
    skill_area = Column(String, nullable=False)
    best_score = Column(Integer)  # 0-100; highest module-test score achieved
    passed_at = Column(DateTime(timezone=True))  # first time best_score >= pass mark


class UserLevelTest(Base):
    """One row per (user, cefr_level) — the user's level-test result.

    The level test spans ALL skill areas of a CEFR level (e.g. "A2"); it unlocks
    once every section of that level is mastered. best_score is the highest
    level-test score (0-100) achieved; the level is "completed" when best_score
    >= the pass mark. passed_at records the first time that happened. Created
    lazily on the first attempt; absent rows mean "not attempted yet".
    """

    __tablename__ = "user_level_tests"
    __table_args__ = (
        UniqueConstraint("user_id", "cefr_level", name="uq_user_level_test"),
    )

    id = Column(Integer, primary_key=True)
    user_id = Column(String, nullable=False, index=True)
    cefr_level = Column(String, nullable=False)
    best_score = Column(Integer)  # 0-100; highest level-test score achieved
    passed_at = Column(DateTime(timezone=True))  # first time best_score >= pass mark


