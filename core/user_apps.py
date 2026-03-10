"""
User app access management.

Functions for querying and modifying which apps each user has access to.
App metadata (descriptions, icons, types, commands) is populated by main.py
after registry discovery via set_app_registry_data().
"""

import logging

from sqlalchemy import select
from telegram import BotCommand, BotCommandScopeChat

from core.database import async_session_factory
from core.models import UserAppSetting

logger = logging.getLogger(__name__)

# ============================================================
# App metadata — populated by main.py after registry discovery
# ============================================================

# Names of apps with app_type == "common"
_common_app_names: list[str] = []

# {name: {"description": str, "icon": str}} for common apps
_common_apps_info: dict[str, dict] = {}

# {name: {"description": str, "icon": str, "app_type": str}} for all apps
_all_apps_info: dict[str, dict] = {}

# {name: [{"command": str, "description": str}]} for all apps
_app_commands: dict[str, list[dict]] = {}

# Commands shown in the per-user menu when logged in (system commands only)
_LOGGED_IN_SYSTEM_CMDS = [
    {"command": "apps",   "description": "Manage your apps"},
    {"command": "logout", "description": "End your session"},
    {"command": "help",   "description": "Show all available commands"},
]

# Commands shown globally (before login)
_GLOBAL_CMDS = [
    {"command": "register", "description": "Create a new account"},
    {"command": "login",    "description": "Sign in with your PIN"},
    {"command": "help",     "description": "Show all available commands"},
]


def set_app_registry_data(
    common_apps: dict[str, dict],
    all_apps: dict[str, dict],
    app_commands: dict[str, list[dict]] | None = None,
) -> None:
    """Populate app metadata. Called from main.py after registry.discover_apps().

    Args:
        common_apps:  {name: {description, icon}} for apps with app_type=="common"
        all_apps:     {name: {description, icon, app_type}} for every discovered app
        app_commands: {name: [{command, description}]} for every discovered app
    """
    global _common_app_names, _common_apps_info, _all_apps_info, _app_commands
    _common_app_names = list(common_apps.keys())
    _common_apps_info = dict(common_apps)
    _all_apps_info    = dict(all_apps)
    _app_commands     = dict(app_commands) if app_commands else {}


def get_common_app_names() -> list[str]:
    return list(_common_app_names)


def get_common_apps_info() -> dict[str, dict]:
    """Returns {name: {description, icon}} for all common apps."""
    return dict(_common_apps_info)


def get_all_apps_info() -> dict[str, dict]:
    """Returns {name: {description, icon, app_type}} for all apps."""
    return dict(_all_apps_info)


# ============================================================
# DB helpers
# ============================================================

async def user_has_app(user_id: int, app_name: str) -> bool:
    """Check if a user has access to the named app."""
    async with async_session_factory() as db:
        result = await db.execute(
            select(UserAppSetting).where(
                UserAppSetting.user_id == user_id,
                UserAppSetting.app_name == app_name,
                UserAppSetting.is_enabled == True,
            )
        )
        return result.scalar_one_or_none() is not None


async def get_user_app_names(user_id: int) -> list[str]:
    """Return names of all enabled apps for a user."""
    async with async_session_factory() as db:
        result = await db.execute(
            select(UserAppSetting.app_name).where(
                UserAppSetting.user_id == user_id,
                UserAppSetting.is_enabled == True,
            )
        )
        return [row[0] for row in result.fetchall()]


async def has_any_apps(user_id: int) -> bool:
    """True if user has at least one UserAppSetting row (enabled or not)."""
    async with async_session_factory() as db:
        result = await db.execute(
            select(UserAppSetting.id)
            .where(UserAppSetting.user_id == user_id)
            .limit(1)
        )
        return result.scalar_one_or_none() is not None


async def add_user_app(user_id: int, app_name: str) -> None:
    """Enable an app for a user. Creates or re-enables the setting row."""
    async with async_session_factory() as db:
        result = await db.execute(
            select(UserAppSetting).where(
                UserAppSetting.user_id == user_id,
                UserAppSetting.app_name == app_name,
            )
        )
        existing = result.scalar_one_or_none()
        if existing:
            existing.is_enabled = True
        else:
            db.add(UserAppSetting(user_id=user_id, app_name=app_name, is_enabled=True))
        await db.commit()


async def remove_user_app(user_id: int, app_name: str) -> None:
    """Disable an app for a user."""
    async with async_session_factory() as db:
        result = await db.execute(
            select(UserAppSetting).where(
                UserAppSetting.user_id == user_id,
                UserAppSetting.app_name == app_name,
            )
        )
        existing = result.scalar_one_or_none()
        if existing:
            existing.is_enabled = False
            await db.commit()


async def initialize_user_apps(user_id: int, app_names: list[str]) -> None:
    """Bulk-create enabled app settings for a new user."""
    if not app_names:
        return
    async with async_session_factory() as db:
        for app_name in app_names:
            db.add(UserAppSetting(user_id=user_id, app_name=app_name, is_enabled=True))
        await db.commit()


async def ensure_user_has_apps(user_id: int) -> None:
    """Auto-assign all common apps to a user who has none.

    Called after login to handle legacy users created before the per-user
    app system was introduced.
    """
    if _common_app_names and not await has_any_apps(user_id):
        await initialize_user_apps(user_id, _common_app_names)


# ============================================================
# Per-user Telegram command menu
# ============================================================

async def update_user_command_menu(bot, chat_id: str | int, user_id: int) -> None:
    """Set a personalised command menu for a logged-in user.

    Shows only the slash commands for their enabled apps plus system commands.
    Called after login and whenever app access changes.
    """
    try:
        enabled = set(await get_user_app_names(user_id))
        commands: list[dict] = []
        for name in sorted(enabled):
            commands.extend(_app_commands.get(name, []))
        commands.extend(_LOGGED_IN_SYSTEM_CMDS)

        bot_commands = [BotCommand(c["command"], c["description"]) for c in commands]
        await bot.set_my_commands(
            bot_commands,
            scope=BotCommandScopeChat(chat_id=int(chat_id)),
        )
    except Exception as exc:
        logger.warning(f"Could not update command menu for chat {chat_id}: {exc}")


async def reset_user_command_menu(bot, chat_id: str | int) -> None:
    """Remove the per-user command scope so the global menu takes over.

    Called on logout.
    """
    try:
        await bot.delete_my_commands(scope=BotCommandScopeChat(chat_id=int(chat_id)))
    except Exception as exc:
        logger.warning(f"Could not reset command menu for chat {chat_id}: {exc}")
