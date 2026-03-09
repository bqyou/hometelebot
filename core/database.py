"""
Database engine, session factory, and base model.

Supports both SQLite (dev) and PostgreSQL (prod) transparently.
Switch by changing DATABASE_URL in .env -- no code changes needed.
"""

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from config import settings


# --- Engine ---
# For SQLite, enable WAL mode for better concurrent read performance.
# For PostgreSQL, pool_size and max_overflow handle connection pooling.
if settings.database_url.startswith("sqlite"):
    engine = create_async_engine(
        settings.database_url,
        echo=False,
        connect_args={"check_same_thread": False},
    )
else:
    engine = create_async_engine(
        settings.database_url,
        echo=False,
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,   # test connections before use; drops stale ones silently
        pool_recycle=1800,    # recycle connections after 30 min (well under MySQL wait_timeout)
    )

# --- Session Factory ---
async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


# --- Base Model ---
class Base(DeclarativeBase):
    """All ORM models inherit from this base."""
    pass


async def init_db() -> None:
    """Create all tables that don't exist yet.
    
    In production, use Alembic migrations instead of this function.
    This is a convenience for local development and first-time setup.
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_session() -> AsyncSession:
    """Get a new database session. Use with 'async with' or close manually."""
    return async_session_factory()
