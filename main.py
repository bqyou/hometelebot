"""
TeleBot Platform - Main Entry Point

Initializes the database, discovers all mini apps, registers handlers,
and starts the bot in either polling or webhook mode.
"""

import asyncio
import logging

from telegram import BotCommand, Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

from config import settings
from core.database import init_db
from core.auth import get_login_handler, logout_command
from core.registry import MiniAppRegistry

# ============================================================
# Logging Setup
# ============================================================

logging.basicConfig(
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
)
logger = logging.getLogger(__name__)

# Reduce noise from httpx and telegram library internals
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram.ext").setLevel(logging.WARNING)


# ============================================================
# Global Registry (so /help can access it)
# ============================================================

registry = MiniAppRegistry()


# ============================================================
# System Command Handlers
# ============================================================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start -- welcome message for new users."""
    await update.message.reply_text(
        "Welcome to TeleBot!\n\n"
        "This is your personal command center with mini apps "
        "for inventory tracking, meal planning, grocery lists, and more.\n\n"
        "To get started, use /login with your username and PIN.\n"
        "Type /help to see all available commands."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /help -- show all available commands from registered mini apps."""
    help_text = registry.get_help_text()
    await update.message.reply_text(help_text)


# ============================================================
# Post-Init: Set Bot Commands in Telegram Menu
# ============================================================

async def post_init(app: Application) -> None:
    """Called after the bot application is initialized.
    
    Sets the bot command menu in Telegram so users see
    autocomplete suggestions when typing /.
    """
    all_commands = registry.get_all_commands()
    bot_commands = [
        BotCommand(command=cmd["command"], description=cmd["description"])
        for cmd in all_commands
    ]
    # Telegram limits to 100 commands
    bot_commands = bot_commands[:100]

    await app.bot.set_my_commands(bot_commands)
    logger.info(f"Set {len(bot_commands)} bot commands in Telegram menu")

    # Run startup hooks for all mini apps
    await registry.startup_all()


# ============================================================
# Main
# ============================================================

def main() -> None:
    """Build and run the bot."""

    # --- Discover mini apps ---
    # Must run before init_db so all models are imported and registered
    # with Base.metadata before create_all is called.
    logger.info("Discovering mini apps...")
    registry.discover_apps("apps")

    # --- Initialize database ---
    logger.info("Initializing database...")
    asyncio.get_event_loop().run_until_complete(init_db())

    # --- Build Telegram application ---
    logger.info("Building Telegram bot application...")
    builder = Application.builder().token(settings.telegram_bot_token)
    app = builder.post_init(post_init).build()

    # --- Register system handlers ---
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(get_login_handler())
    app.add_handler(CommandHandler("logout", logout_command))

    # --- Register all mini app handlers ---
    registry.register_all(app)

    # --- Start the bot ---
    if settings.bot_mode == "webhook":
        logger.info(f"Starting bot in webhook mode at {settings.webhook_url}")
        app.run_webhook(
            listen="0.0.0.0",
            port=settings.webhook_port,
            url_path="webhook",
            webhook_url=f"{settings.webhook_url}/webhook",
        )
    else:
        logger.info("Starting bot in polling mode...")
        app.run_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True,
        )


if __name__ == "__main__":
    main()
