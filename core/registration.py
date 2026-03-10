"""
Self-registration conversation flow.

/register → username → PIN → PIN confirm → display name (opt) → app selection → done

Users choose from common apps during registration. Personal apps (bike, food_menu)
are assigned by an admin via scripts/grant_app.py.
"""

import logging

from sqlalchemy import select
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

from core.auth import hash_pin, get_active_session
from core.database import async_session_factory
from core.models import User
from core.user_apps import (
    get_common_apps_info,
    initialize_user_apps,
)

logger = logging.getLogger(__name__)

# Conversation states (high numbers to avoid clashing with app states)
REG_USERNAME    = 20
REG_PIN         = 21
REG_PIN_CONFIRM = 22
REG_NAME        = 23
REG_APPS        = 24


# ============================================================
# Keyboard builder
# ============================================================

def _build_app_keyboard(selected: set[str]) -> InlineKeyboardMarkup:
    """Build toggle keyboard for common app selection."""
    common_apps = get_common_apps_info()
    buttons = []
    for name in sorted(common_apps):
        info = common_apps[name]
        icon = info["icon"]
        check = "\u2705" if name in selected else "\u2b1c"   # ✅ / ⬜
        buttons.append([InlineKeyboardButton(
            f"{check} {icon} {info['description']}",
            callback_data=f"reg:toggle:{name}",
        )])
    buttons.append([InlineKeyboardButton("\u2713 Done", callback_data="reg:done")])
    return InlineKeyboardMarkup(buttons)


# ============================================================
# Conversation handlers
# ============================================================

async def reg_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    chat_id = str(update.effective_chat.id)
    session_data = await get_active_session(chat_id)
    if session_data:
        # Silently ignore — logged-in users cannot register a second account.
        # The /register command is not shown in their command menu either.
        return ConversationHandler.END

    await update.message.reply_text(
        "\U0001f4dd <b>Create Account</b>\n\n"
        "Choose a username (3\u201330 chars, letters/numbers/underscores):",
        parse_mode="HTML",
    )
    return REG_USERNAME


async def reg_username(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    username = update.message.text.strip().lower()

    if not username.replace("_", "").isalnum() or not (3 <= len(username) <= 30):
        await update.message.reply_text(
            "\u274c Username must be 3\u201330 chars, letters/numbers/underscores only.\nTry again:"
        )
        return REG_USERNAME

    async with async_session_factory() as db:
        result = await db.execute(select(User).where(User.username == username))
        if result.scalar_one_or_none():
            await update.message.reply_text(
                f"\u274c <b>{username}</b> is already taken \u00b7 try another:",
                parse_mode="HTML",
            )
            return REG_USERNAME

    context.user_data["reg_username"] = username
    await update.message.reply_text(
        "\U0001f510 Enter a PIN (4\u20136 digits)\n"
        "<i>Your message will be deleted for security</i>",
        parse_mode="HTML",
    )
    return REG_PIN


async def reg_pin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    pin = update.message.text.strip()
    try:
        await update.message.delete()
    except Exception:
        pass

    if not pin.isdigit() or not (4 <= len(pin) <= 6):
        await update.effective_chat.send_message(
            "\u274c PIN must be 4\u20136 digits only \u00b7 try again:\n"
            "<i>Your message will be deleted for security</i>",
            parse_mode="HTML",
        )
        return REG_PIN

    context.user_data["reg_pin"] = pin
    await update.effective_chat.send_message(
        "\U0001f510 Confirm your PIN\n"
        "<i>Your message will be deleted for security</i>",
        parse_mode="HTML",
    )
    return REG_PIN_CONFIRM


async def reg_pin_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    pin_confirm = update.message.text.strip()
    try:
        await update.message.delete()
    except Exception:
        pass

    if pin_confirm != context.user_data.get("reg_pin"):
        await update.effective_chat.send_message(
            "\u274c PINs don\u2019t match \u00b7 enter your PIN again:\n"
            "<i>Your message will be deleted for security</i>",
            parse_mode="HTML",
        )
        return REG_PIN

    # Store hash, discard plaintext
    context.user_data["reg_pin_hash"] = hash_pin(pin_confirm)
    context.user_data.pop("reg_pin", None)

    await update.effective_chat.send_message(
        "\U0001f464 Enter a display name (or /skip to use your username):",
        parse_mode="HTML",
    )
    return REG_NAME


async def reg_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["reg_display_name"] = update.message.text.strip() or None
    return await _show_app_selection(update, context)


async def reg_name_skip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["reg_display_name"] = None
    return await _show_app_selection(update, context)


async def _show_app_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    common_apps = get_common_apps_info()
    if not common_apps:
        return await _complete_registration(update, context, [])

    # Default: all common apps selected
    selected: set[str] = set(common_apps.keys())
    context.user_data["reg_selected_apps"] = selected

    await update.effective_chat.send_message(
        "\U0001f4f1 <b>Choose your apps</b>\n\nTap to toggle, then tap <b>Done</b>:",
        parse_mode="HTML",
        reply_markup=_build_app_keyboard(selected),
    )
    return REG_APPS


async def reg_toggle_app(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    app_name = query.data.split(":", 2)[2]
    selected: set[str] = context.user_data.get("reg_selected_apps", set())

    if app_name in selected:
        selected.discard(app_name)
    else:
        selected.add(app_name)
    context.user_data["reg_selected_apps"] = selected

    await query.edit_message_reply_markup(reply_markup=_build_app_keyboard(selected))
    return REG_APPS


async def reg_done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await query.edit_message_reply_markup(reply_markup=None)

    selected = list(context.user_data.get("reg_selected_apps", set()))
    return await _complete_registration(update, context, selected)


async def _complete_registration(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    app_names: list[str],
) -> int:
    username     = context.user_data["reg_username"]
    pin_hash     = context.user_data["reg_pin_hash"]
    display_name = context.user_data.get("reg_display_name")

    try:
        async with async_session_factory() as db:
            # Re-check uniqueness (race condition guard)
            result = await db.execute(select(User).where(User.username == username))
            if result.scalar_one_or_none():
                await update.effective_chat.send_message(
                    f"\u274c <b>{username}</b> was just taken \u00b7 please /register again.",
                    parse_mode="HTML",
                )
                return ConversationHandler.END

            user = User(
                username=username,
                pin_hash=pin_hash,
                display_name=display_name,
                is_active=True,
            )
            db.add(user)
            await db.flush()
            user_id = user.id
            await db.commit()

        if app_names:
            await initialize_user_apps(user_id, app_names)

        apps_note = (
            f"{len(app_names)} app(s) enabled"
            if app_names
            else "No apps selected \u00b7 use /apps to add some"
        )
        await update.effective_chat.send_message(
            "\U0001f389 <b>Account created!</b>\n\n"
            f"Username: <b>{username}</b>\n"
            f"Apps: {apps_note}\n\n"
            "Use /login to sign in.",
            parse_mode="HTML",
        )

    except Exception as exc:
        logger.error(f"Registration failed for {username}: {exc}", exc_info=True)
        await update.effective_chat.send_message(
            "\u274c Registration failed \u00b7 please try again later."
        )

    # Clean up temp keys
    for key in ("reg_username", "reg_pin_hash", "reg_display_name", "reg_selected_apps"):
        context.user_data.pop(key, None)

    return ConversationHandler.END


async def reg_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    for key in ("reg_username", "reg_pin_hash", "reg_display_name", "reg_selected_apps"):
        context.user_data.pop(key, None)
    await update.message.reply_text("\u2716 Registration cancelled")
    return ConversationHandler.END


# ============================================================
# Build ConversationHandler
# ============================================================

def get_registration_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CommandHandler("register", reg_start)],
        states={
            REG_USERNAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, reg_username),
            ],
            REG_PIN: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, reg_pin),
            ],
            REG_PIN_CONFIRM: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, reg_pin_confirm),
            ],
            REG_NAME: [
                CommandHandler("skip", reg_name_skip),
                MessageHandler(filters.TEXT & ~filters.COMMAND, reg_name),
            ],
            REG_APPS: [
                CallbackQueryHandler(reg_toggle_app, pattern=r"^reg:toggle:.+$"),
                CallbackQueryHandler(reg_done, pattern=r"^reg:done$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", reg_cancel)],
        conversation_timeout=300,
    )
