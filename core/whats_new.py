"""
What's New notification system.

On each deploy with major changes:
1. Add an entry to UPDATES with today's date as the key.
2. Bump CURRENT_VERSION to match.
3. Restart the bot. All active users are notified on next startup or login.

Only include major features or significant overhauls -- not bug fixes.
"""

import logging

from sqlalchemy import select
from telegram import Bot

from core.database import async_session_factory, utc_now
from core.models import WhatsNewSeen

logger = logging.getLogger(__name__)

# Bump this to match the latest UPDATES key when deploying new features
CURRENT_VERSION = "2026-03-16-cook"

UPDATES: dict[str, str] = {
    "2026-03-16-cook": (
        "\U0001f373 <b>Cook App is here!</b>\n"
        "\n"
        "A full kitchen companion has been added. Enable it via /apps.\n"
        "\n"
        "\u250c <b>Kitchen Inventory</b>\n"
        "\u2502  Track raw ingredients with quantities\n"
        "\u2502  Manage sauces \u0026 condiments (tap-to-add from SG suggestions)\n"
        "\u2502  Track kitchen equipment\n"
        "\u2514\n"
        "\n"
        "\u250c <b>Recipe Vault</b>\n"
        "\u2502  Add recipes manually with photos\n"
        "\u2502  Cookbook with inventory cross-reference\n"
        "\u2502  See exactly what you have vs. what you need\n"
        "\u2502  Adjust servings with auto-scaled quantities\n"
        "\u2514\n"
        "\n"
        "\u250c <b>Smart Matching</b>\n"
        "\u2502  \U0001f50d What Can I Cook? \u2014 matches your pantry\n"
        "\u2502  against every saved recipe\n"
        "\u2514\n"
        "\n"
        "\u250c <b>AI Features</b> (if configured)\n"
        "\u2502  \U0001f4dd Import any recipe from pasted text\n"
        "\u2502  \U0001f916 Generate recipes using your inventory\n"
        "\u2514\n"
        "\n"
        "Use /cook to get started."
    ),
}


async def has_seen(user_id: int, version: str) -> bool:
    async with async_session_factory() as db:
        result = await db.execute(
            select(WhatsNewSeen).where(
                WhatsNewSeen.user_id == user_id,
                WhatsNewSeen.version == version,
            )
        )
        return result.scalar_one_or_none() is not None


async def mark_seen(user_id: int, version: str) -> None:
    async with async_session_factory() as db:
        db.add(WhatsNewSeen(user_id=user_id, version=version))
        try:
            await db.commit()
        except Exception:
            await db.rollback()  # already marked (race condition), ignore


async def notify_if_unseen(bot: Bot, chat_id: str, user_id: int) -> None:
    """Send the current what's new message if this user hasn't seen it yet."""
    version = CURRENT_VERSION
    message = UPDATES.get(version)
    if not message:
        return

    if await has_seen(user_id, version):
        return

    try:
        await bot.send_message(
            chat_id=chat_id,
            text="\U0001f4e2 <b>What\u2019s New</b>\n\n" + message,
            parse_mode="HTML",
        )
        await mark_seen(user_id, version)
    except Exception as exc:
        logger.warning(f"Could not send what's new to {chat_id}: {exc}")


async def broadcast_to_active_sessions(bot: Bot) -> None:
    """On startup, notify all currently active sessions about unseen updates."""
    from sqlalchemy import select as sa_select
    from core.models import Session, User

    try:
        async with async_session_factory() as db:
            result = await db.execute(
                sa_select(Session.telegram_chat_id, Session.user_id)
                .where(
                    Session.is_active == True,
                    Session.expires_at > utc_now(),
                )
                .distinct()
            )
            active = result.fetchall()

        count = 0
        for chat_id, user_id in active:
            if not await has_seen(user_id, CURRENT_VERSION):
                await notify_if_unseen(bot, chat_id, user_id)
                count += 1

        if count:
            logger.info(f"Sent what's new to {count} active session(s)")
    except Exception as exc:
        logger.warning(f"What's new broadcast failed: {exc}")
