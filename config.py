"""
Application configuration loaded from environment variables.
Uses pydantic-settings for validation and type coercion.
"""

from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    """All configuration is loaded from .env file or environment variables."""

    # --- Telegram ---
    telegram_bot_token: str = Field(description="Bot token from @BotFather")

    # --- Database ---
    database_url: str = Field(
        default="sqlite+aiosqlite:///./telebot.db",
        description="SQLAlchemy async database URL",
    )

    # --- Auth ---
    session_duration_hours: int = Field(default=0, description="Session lifetime in hours. 0 = never expire.")
    max_login_attempts: int = Field(default=5)
    lockout_duration_minutes: int = Field(default=15)

    # --- Scraping ---
    menu_scrape_interval_seconds: int = Field(default=86400)

    # --- Server ---
    bot_mode: str = Field(default="polling", description="'polling' or 'webhook'")
    webhook_url: str = Field(default="")
    webhook_port: int = Field(default=8443)

    # --- Logging ---
    log_level: str = Field(default="INFO")

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": False,
    }


# Singleton instance used throughout the app
settings = Settings()
