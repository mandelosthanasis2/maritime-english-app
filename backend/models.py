"""SQLAlchemy data models for lessons and their items.

The design is intentionally lightweight: structured columns hold the fields we
expect to query or filter on, while the full, rich item object from the source
JSON is kept verbatim in a JSONB ``data`` column so nothing is lost and the
schema does not need to change as lesson content evolves.
"""

from sqlalchemy import (
    JSON,
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
    #   skill_type  vocabulary | listening | fill_gap | word_order | speaking | roleplay
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
