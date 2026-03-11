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
from core.database import init_db, async_session_factory
from core.auth import get_login_handler, logout_command
from core.registration import get_registration_handler
from core.apps_manager import get_apps_command_handler, get_apps_callback_handler
from core.registry import MiniAppRegistry
from core.user_apps import set_app_registry_data

# ============================================================
# Logging Setup
# ============================================================

logging.basicConfig(
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
)
logger = logging.getLogger(__name__)

# Reduce noise from httpx, telegram, and APScheduler internals
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram.ext").setLevel(logging.WARNING)
logging.getLogger("apscheduler").setLevel(logging.WARNING)


# ============================================================
# Global Registry (so /help can access it)
# ============================================================

registry = MiniAppRegistry()


# ============================================================
# System Command Handlers
# ============================================================

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log errors. Transient network errors are expected with long polling and logged quietly."""
    from telegram.error import NetworkError, TimedOut
    if isinstance(context.error, (NetworkError, TimedOut)):
        logger.debug(f"Transient network error (auto-retry): {context.error}")
    else:
        logger.error(f"Unhandled exception: {context.error}", exc_info=context.error)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start -- welcome message."""
    await update.message.reply_text(
        "\U0001f916 <b>Welcome to TeleBot</b>\n"
        "\n"
        "Your personal command center for\n"
        "inventory, meal planning, groceries & more.\n"
        "\n"
        "\u250c <b>Get started</b>\n"
        "\u2502  /register \u00b7 Create a new account\n"
        "\u2502  /login    \u00b7 Sign in with your PIN\n"
        "\u2502  /help     \u00b7 See all commands\n"
        "\u2514",
        parse_mode="HTML",
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /help -- show commands filtered to the user's enabled apps."""
    from core.auth import get_active_session
    from core.user_apps import get_user_app_names

    chat_id = str(update.effective_chat.id)
    session_data = await get_active_session(chat_id)
    if session_data:
        _, user = session_data
        enabled = set(await get_user_app_names(user.id))
        help_text = registry.get_help_text(enabled_apps=enabled)
    else:
        help_text = registry.get_help_text()
    await update.message.reply_text(help_text, parse_mode="HTML")


# ============================================================
# Post-Init: Set Bot Commands in Telegram Menu
# ============================================================

async def post_init(app: Application) -> None:
    """Called after the bot application is initialized."""
    # Set the global (unauthenticated) command menu — minimal commands only.
    # Logged-in users get a personalised per-chat menu via update_user_command_menu().
    global_commands = [
        BotCommand("register", "Create a new account"),
        BotCommand("login",    "Sign in with your PIN"),
        BotCommand("help",     "Show all available commands"),
    ]
    await app.bot.set_my_commands(global_commands)
    logger.info(f"Set global command menu ({len(global_commands)} commands)")

    # Push personalised menus to every currently active session.
    # This handles users who were already logged in before a restart.
    await _sync_active_session_menus(app.bot)

    await registry.startup_all()


async def _sync_active_session_menus(bot) -> None:
    """On startup, update the Telegram command menu for every active session.

    Without this, users logged in before a bot restart would still see the
    global (unauthenticated) menu until they log out and back in.
    """
    from datetime import datetime, timezone
    from sqlalchemy import select
    from core.models import Session, User
    from core.user_apps import update_user_command_menu

    try:
        async with async_session_factory() as db:
            result = await db.execute(
                select(Session.telegram_chat_id, Session.user_id)
                .where(
                    Session.is_active == True,
                    Session.expires_at > datetime.now(timezone.utc).replace(tzinfo=None),
                )
                .distinct()
            )
            active = result.fetchall()

        count = 0
        for chat_id, user_id in active:
            await update_user_command_menu(bot, chat_id, user_id)
            count += 1

        if count:
            logger.info(f"Synced command menus for {count} active session(s)")
    except Exception as exc:
        logger.warning(f"Could not sync active session menus: {exc}")


# ============================================================
# Main
# ============================================================

def main() -> None:
    """Build and run the bot."""

    # --- Discover mini apps ---
    logger.info("Discovering mini apps...")
    registry.discover_apps("apps")

    # --- Populate app metadata for user_apps module ---
    common_apps_dict: dict[str, dict] = {}
    all_apps_dict: dict[str, dict] = {}
    app_commands_dict: dict[str, list[dict]] = {}
    for name, app in registry.apps.items():
        icon = registry.APP_ICONS.get(name, "\u25ab")
        info = {"description": app.description, "icon": icon, "app_type": app.app_type}
        all_apps_dict[name] = info
        app_commands_dict[name] = app.commands
        if app.app_type == "common":
            common_apps_dict[name] = {"description": app.description, "icon": icon}
    set_app_registry_data(common_apps_dict, all_apps_dict, app_commands_dict)
    logger.info(
        f"App registry: {len(common_apps_dict)} common, "
        f"{len(all_apps_dict) - len(common_apps_dict)} personal"
    )

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
    app.add_handler(get_registration_handler())
    app.add_handler(get_apps_command_handler())
    app.add_handler(get_apps_callback_handler())

    # --- Register error handler ---
    app.add_error_handler(error_handler)

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
