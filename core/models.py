"""
Shared database models used by the core system.
Mini apps define their own models in their respective modules.
"""

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Integer,
    String,
    Text,
    ForeignKey,
    UniqueConstraint,
)

from core.database import Base, utc_now


class User(Base):
    """A registered bot user. Created via /register on Telegram or the CLI script."""

    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(50), unique=True, nullable=False, index=True)
    pin_hash = Column(String(128), nullable=False)
    telegram_chat_id = Column(String(50), nullable=True, index=True)
    display_name = Column(String(100), nullable=True)
    is_admin = Column(Boolean, default=False)
    is_active = Column(Boolean, default=True)
    failed_login_attempts = Column(Integer, default=0)
    locked_until = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utc_now)
    last_login = Column(DateTime, nullable=True)

    def __repr__(self) -> str:
        return f"<User(id={self.id}, username='{self.username}')>"


class Session(Base):
    """Active login session. One per user per chat. Expires after configured duration."""

    __tablename__ = "sessions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    telegram_chat_id = Column(String(50), nullable=False, index=True)
    expires_at = Column(DateTime, nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=utc_now)

    def __repr__(self) -> str:
        return f"<Session(user_id={self.user_id}, active={self.is_active})>"


class UserAppSetting(Base):
    """Per-user app access. One row per (user, app) pair."""

    __tablename__ = "user_app_settings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    app_name = Column(String(50), nullable=False)
    is_enabled = Column(Boolean, default=True)
    settings_json = Column(Text, default="{}")

    __table_args__ = (
        UniqueConstraint("user_id", "app_name", name="uq_user_app"),
    )
