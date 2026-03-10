"""
/apps command — lets users view, add, and remove common apps.

Personal apps (bike, food_menu) are shown but cannot be self-managed;
they are assigned by an admin via scripts/grant_app.py.
"""

import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler

from core.auth import get_active_session
from core.user_apps import (
    get_user_app_names,
    get_all_apps_info,
    get_common_app_names,
    add_user_app,
    remove_user_app,
    update_user_command_menu,
)

logger = logging.getLogger(__name__)

CALLBACK_PREFIX = "apps"


# ============================================================
# Message builder
# ============================================================

async def _build_apps_message(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    """Build the /apps display message and inline keyboard for a user."""
    enabled   = set(await get_user_app_names(user_id))
    all_info  = get_all_apps_info()
    common    = set(get_common_app_names())

    lines  = ["\U0001f4f1 <b>Your Apps</b>", ""]
    buttons: list[list[InlineKeyboardButton]] = []

    # --- Enabled apps ---
    if enabled:
        for name in sorted(enabled):
            info = all_info.get(name)
            if not info:
                continue
            icon     = info["icon"]
            desc     = info["description"]
            is_common = info["app_type"] == "common"
            if is_common:
                lines.append(f"\u2705 {icon} {desc}")
                buttons.append([
                    InlineKeyboardButton(
                        f"\u274c Remove {icon} {desc}",
                        callback_data=f"{CALLBACK_PREFIX}:rem:{name}",
                    )
                ])
            else:
                lines.append(f"\U0001f512 {icon} {desc} <i>(personal)</i>")
    else:
        lines.append("<i>No apps enabled yet</i>")

    # --- Available common apps not yet added ---
    available = sorted(common - enabled)
    if available:
        lines.append("")
        lines.append("<b>Available to add:</b>")
        for name in available:
            info = all_info.get(name)
            if not info:
                continue
            icon = info["icon"]
            desc = info["description"]
            lines.append(f"\u2b1c {icon} {desc}")
            buttons.append([
                InlineKeyboardButton(
                    f"\u2795 Add {icon} {desc}",
                    callback_data=f"{CALLBACK_PREFIX}:add:{name}",
                )
            ])

    if not available and enabled:
        lines.append("")
        lines.append("<i>All available apps are enabled.</i>")

    # Refresh button at the bottom
    buttons.append([InlineKeyboardButton("\U0001f504 Refresh", callback_data=f"{CALLBACK_PREFIX}:refresh")])

    return "\n".join(lines), InlineKeyboardMarkup(buttons)


# ============================================================
# Handlers
# ============================================================

async def apps_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /apps — show the user's app management panel."""
    chat_id = str(update.effective_chat.id)
    session_data = await get_active_session(chat_id)
    if not session_data:
        await update.message.reply_text(
            "\U0001f512 Not logged in \u00b7 use /login to authenticate",
            parse_mode="HTML",
        )
        return

    _, user = session_data
    text, keyboard = await _build_apps_message(user.id)
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)


async def apps_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle apps:add:<name>, apps:rem:<name>, apps:refresh callbacks."""
    query = update.callback_query
    await query.answer()

    chat_id = str(update.effective_chat.id)
    session_data = await get_active_session(chat_id)
    if not session_data:
        await query.edit_message_text(
            "\U0001f512 Session expired \u00b7 use /login to authenticate",
            parse_mode="HTML",
        )
        return

    _, user = session_data
    parts = query.data.split(":", 2)
    action = parts[1] if len(parts) > 1 else ""
    app_name = parts[2] if len(parts) > 2 else ""

    all_info = get_all_apps_info()
    common   = set(get_common_app_names())

    if action == "add" and app_name:
        if app_name not in common:
            await query.answer("That app cannot be self-added.", show_alert=True)
            return
        await add_user_app(user.id, app_name)
        await update_user_command_menu(context.bot, chat_id, user.id)

    elif action == "rem" and app_name:
        if app_name not in common:
            await query.answer("Personal apps can only be removed by an admin.", show_alert=True)
            return
        await remove_user_app(user.id, app_name)
        await update_user_command_menu(context.bot, chat_id, user.id)

    # Refresh the display
    text, keyboard = await _build_apps_message(user.id)
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)


# ============================================================
# Handler registration helpers (called from main.py)
# ============================================================

def get_apps_command_handler() -> CommandHandler:
    return CommandHandler("apps", apps_command)


def get_apps_callback_handler() -> CallbackQueryHandler:
    return CallbackQueryHandler(apps_callback, pattern=r"^apps:")
