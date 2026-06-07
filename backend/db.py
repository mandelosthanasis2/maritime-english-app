"""Database setup: engine, session factory, and Base.

Connects to PostgreSQL using the DATABASE_URL environment variable. Railway
sometimes exposes the connection string with the legacy ``postgres://`` prefix,
which SQLAlchemy no longer accepts, so we normalise it to ``postgresql://``.
"""

import os

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker


def get_database_url():
    """Return the normalised database URL, or None if it is not configured."""
    url = os.environ.get("DATABASE_URL")
    if not url:
        return None
    # Railway (and old Heroku) sometimes use the legacy "postgres://" scheme.
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    return url


DATABASE_URL = get_database_url()

# The engine/session are only created when a URL is configured so that importing
# this module never fails (e.g. during local tooling without a database).
engine = create_engine(DATABASE_URL, pool_pre_ping=True) if DATABASE_URL else None
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False) if engine else None

Base = declarative_base()


def init_db():
    """Create all tables if they do not already exist."""
    if engine is None:
        raise RuntimeError("DATABASE_URL is not set; cannot initialize the database.")
    # Import models so they are registered on Base.metadata before create_all.
    import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
