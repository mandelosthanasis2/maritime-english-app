"""SQLAlchemy data models for lessons and their items.

The design is intentionally lightweight: structured columns hold the fields we
expect to query or filter on, while the full, rich item object from the source
JSON is kept verbatim in a JSONB ``data`` column so nothing is lost and the
schema does not need to change as lesson content evolves.
"""

from sqlalchemy import JSON, Column, ForeignKey, Integer, String, Text
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
    description = Column(Text)
    source = Column(Text)
    interface_language = Column(String)
    target_language = Column(String)
    version = Column(Integer)

    items = relationship(
        "Item",
        back_populates="lesson",
        cascade="all, delete-orphan",
        order_by="Item.order_index",
    )


class Item(Base):
    __tablename__ = "items"

    id = Column(Integer, primary_key=True)
    lesson_id = Column(
        String,
        ForeignKey("lessons.lesson_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    item_id = Column(String, unique=True, nullable=False, index=True)
    type = Column(String)
    level = Column(String)
    order_index = Column(Integer)
    # The full original item object (english, explanations, pronunciation_focus,
    # tags, ...) stored exactly as it appears in the source JSON.
    data = Column(JSONType, nullable=False)

    lesson = relationship("Lesson", back_populates="items")
