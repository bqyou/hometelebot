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

# Normalise Railway's postgresql:// → postgresql+asyncpg://
_db_url = settings.database_url
if _db_url.startswith("postgresql://") or _db_url.startswith("postgres://"):
    _db_url = _db_url.replace("://", "+asyncpg://", 1)


# --- Engine ---
# For SQLite, enable WAL mode for better concurrent read performance.
# For PostgreSQL, pool_size and max_overflow handle connection pooling.
if _db_url.startswith("sqlite"):
    engine = create_async_engine(
        _db_url,
        echo=False,
        connect_args={"check_same_thread": False},
    )
else:
    engine = create_async_engine(
        _db_url,
        echo=False,
        pool_size=5,
        max_overflow=10,
        pool_recycle=1800,
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
