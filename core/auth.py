"""
Authentication module.

Handles the login conversation flow:
1. User sends /login
2. Bot asks for username
3. Bot asks for PIN (immediately deletes the PIN message for security)
4. Bot verifies against bcrypt hash
5. Creates a session valid for SESSION_DURATION_HOURS

Also provides the require_auth decorator for protecting commands.
"""

import logging
from datetime import datetime, timedelta
from functools import wraps
from typing import Callable, Any

import bcrypt
from sqlalchemy import select, update
from telegram import Update
from telegram.ext import (
    ContextTypes,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    filters,
)

from config import settings
from core.database import async_session_factory
from core.models import User, Session

logger = logging.getLogger(__name__)

# Conversation states for the login flow
USERNAME_STATE = 0
PIN_STATE = 1


# ============================================================
# Password Hashing Utilities
# ============================================================

def hash_pin(pin: str) -> str:
    """Hash a PIN using bcrypt. Returns the hash as a UTF-8 string."""
    return bcrypt.hashpw(pin.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_pin(pin: str, pin_hash: str) -> bool:
    """Verify a PIN against its bcrypt hash."""
    return bcrypt.checkpw(pin.encode("utf-8"), pin_hash.encode("utf-8"))


# ============================================================
# Session Management
# ============================================================

async def create_session(user_id: int, chat_id: str) -> Session:
    """Create a new active session for a user. Deactivates any existing sessions."""
    async with async_session_factory() as db:
        # Deactivate all existing sessions for this user in this chat
        await db.execute(
            update(Session)
            .where(Session.user_id == user_id, Session.telegram_chat_id == chat_id)
            .values(is_active=False)
        )

        # Create new session
        new_session = Session(
            user_id=user_id,
            telegram_chat_id=chat_id,
            expires_at=datetime.utcnow() + timedelta(hours=settings.session_duration_hours),
            is_active=True,
        )
        db.add(new_session)
        await db.commit()
        return new_session


async def get_active_session(chat_id: str) -> tuple[Session, User] | None:
    """Check if there is a valid, non-expired session for this chat.
    
    Returns (Session, User) tuple if authenticated, None otherwise.
    """
    async with async_session_factory() as db:
        result = await db.execute(
            select(Session, User)
            .join(User, Session.user_id == User.id)
            .where(
                Session.telegram_chat_id == chat_id,
                Session.is_active == True,
                Session.expires_at > datetime.utcnow(),
            )
            .order_by(Session.created_at.desc())
            .limit(1)
        )
        row = result.first()
        if row:
            return row[0], row[1]
        return None


async def invalidate_session(chat_id: str) -> None:
    """Deactivate all sessions for a chat (logout)."""
    async with async_session_factory() as db:
        await db.execute(
            update(Session)
            .where(Session.telegram_chat_id == chat_id)
            .values(is_active=False)
        )
        await db.commit()


# ============================================================
# Auth Decorator for Protecting Commands
# ============================================================

def require_auth(handler_func: Callable) -> Callable:
    """Decorator that checks for an active session before running a command handler.
    
    Usage:
        @require_auth
        async def my_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
            user = context.user_data["current_user"]
            ...
    
    The authenticated User object is stored in context.user_data["current_user"].
    """
    @wraps(handler_func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> Any:
        chat_id = str(update.effective_chat.id)
        session_data = await get_active_session(chat_id)

        if session_data is None:
            await update.message.reply_text(
                "You are not logged in. Use /login to authenticate first."
            )
            return

        session_obj, user_obj = session_data
        context.user_data["current_user"] = user_obj
        context.user_data["current_session"] = session_obj
        return await handler_func(update, context)

    return wrapper


# ============================================================
# Login Conversation Flow
# ============================================================

async def login_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Entry point: /login command. Ask for username."""
    chat_id = str(update.effective_chat.id)

    # Check if already logged in
    session_data = await get_active_session(chat_id)
    if session_data:
        _, user = session_data
        await update.message.reply_text(
            f"You are already logged in as {user.username}.\n"
            f"Use /logout to switch accounts."
        )
        return ConversationHandler.END

    await update.message.reply_text("Please enter your username:")
    return USERNAME_STATE


async def login_username(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Received username. Store it and ask for PIN."""
    username = update.message.text.strip().lower()
    context.user_data["login_username"] = username

    await update.message.reply_text(
        "Enter your PIN:\n"
        "(Your message will be deleted immediately for security)"
    )
    return PIN_STATE


async def login_pin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Received PIN. Delete the message, verify credentials, create session."""
    pin = update.message.text.strip()
    chat_id = str(update.effective_chat.id)
    username = context.user_data.get("login_username", "")

    # Immediately delete the PIN message for security
    try:
        await update.message.delete()
    except Exception:
        logger.warning("Could not delete PIN message. Bot may lack delete permissions.")

    async with async_session_factory() as db:
        result = await db.execute(
            select(User).where(User.username == username, User.is_active == True)
        )
        user = result.scalar_one_or_none()

        if user is None:
            await update.effective_chat.send_message("Invalid username or PIN. Try /login again.")
            return ConversationHandler.END

        # Check lockout
        if user.locked_until and user.locked_until > datetime.utcnow():
            remaining = (user.locked_until - datetime.utcnow()).seconds // 60
            await update.effective_chat.send_message(
                f"Account is locked. Try again in {remaining + 1} minutes."
            )
            return ConversationHandler.END

        # Verify PIN
        if not verify_pin(pin, user.pin_hash):
            user.failed_login_attempts += 1

            if user.failed_login_attempts >= settings.max_login_attempts:
                user.locked_until = datetime.utcnow() + timedelta(
                    minutes=settings.lockout_duration_minutes
                )
                await db.commit()
                await update.effective_chat.send_message(
                    f"Too many failed attempts. Account locked for "
                    f"{settings.lockout_duration_minutes} minutes."
                )
            else:
                await db.commit()
                remaining = settings.max_login_attempts - user.failed_login_attempts
                await update.effective_chat.send_message(
                    f"Invalid PIN. {remaining} attempts remaining. Try /login again."
                )
            return ConversationHandler.END

        # Successful login
        user.failed_login_attempts = 0
        user.locked_until = None
        user.last_login = datetime.utcnow()
        user.telegram_chat_id = chat_id
        await db.commit()

    # Create session
    await create_session(user.id, chat_id)

    display = user.display_name or user.username
    await update.effective_chat.send_message(
        f"Welcome back, {display}!\n"
        f"Session valid for {settings.session_duration_hours} hours.\n\n"
        f"Type /help to see available commands."
    )
    return ConversationHandler.END


async def login_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancel the login flow."""
    await update.message.reply_text("Login cancelled.")
    return ConversationHandler.END


async def logout_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /logout -- invalidate the current session."""
    chat_id = str(update.effective_chat.id)
    await invalidate_session(chat_id)
    await update.message.reply_text("You have been logged out. Use /login to sign in again.")


# ============================================================
# Build the ConversationHandler
# ============================================================

def get_login_handler() -> ConversationHandler:
    """Returns the ConversationHandler for the /login flow."""
    return ConversationHandler(
        entry_points=[CommandHandler("login", login_start)],
        states={
            USERNAME_STATE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, login_username),
            ],
            PIN_STATE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, login_pin),
            ],
        },
        fallbacks=[CommandHandler("cancel", login_cancel)],
        conversation_timeout=120,  # 2 minute timeout
    )
