"""
Telegram handlers for the Grocery List mini app.

Interaction model:
- /grocery                   -> Show current shopping list
- /grocery add milk, eggs    -> Quick-add multiple items (comma-separated)
- /grocery clear             -> Clear all bought items from the list
- Tap an item                -> Toggle bought/unbought
- [Add Items] button         -> Enter items to add
- [Clear Bought] button      -> Remove all checked-off items
"""

import logging
from datetime import datetime, timezone

from sqlalchemy import select, delete, update
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    ContextTypes,
    CommandHandler,
    CallbackQueryHandler,
    ConversationHandler,
    MessageHandler,
    filters,
)

from core.auth import require_auth, require_app_access
from core.database import async_session_factory
from apps.grocery.models import GroceryList, GroceryListMember, GroceryItem

logger = logging.getLogger(__name__)

from core.ui import e as _e, BOX_TOP, BOX_MID, BOX_BOT, DOT

# Conversation state for adding items
ADDING_ITEMS = 0


# ============================================================
# Helper: Get or create the user's default list
# ============================================================

async def _get_or_create_default_list(user_id: int) -> int:
    """Get the user's active grocery list ID, creating one if it doesn't exist."""
    async with async_session_factory() as db:
        # Check for an existing active list (owned or member)
        result = await db.execute(
            select(GroceryList.id)
            .join(GroceryListMember, GroceryList.id == GroceryListMember.list_id)
            .where(
                GroceryListMember.user_id == user_id,
                GroceryList.is_active == True,
            )
            .limit(1)
        )
        list_id = result.scalar_one_or_none()

        if list_id:
            return list_id

        # Create a new list
        new_list = GroceryList(name="Shopping List", owner_id=user_id)
        db.add(new_list)
        await db.flush()

        # Add the owner as a member
        db.add(GroceryListMember(list_id=new_list.id, user_id=user_id))
        await db.commit()
        return new_list.id


# ============================================================
# Main Grocery View
# ============================================================

@require_app_access("grocery")
async def grocery_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /grocery -- show the list, or /grocery add item1, item2."""
    user = context.user_data["current_user"]
    args = context.args

    if args and args[0].lower() == "add":
        # Quick add: /grocery add milk, eggs, bread
        items_text = " ".join(args[1:])
        if not items_text:
            await update.message.reply_text(
                "Usage: /grocery add milk, eggs, bread\n"
                "(Separate items with commas)",
                parse_mode="HTML",
            )
            return
        await _quick_add_items(update, user.id, items_text)
        return

    if args and args[0].lower() == "clear":
        await _clear_bought(update, user.id)
        return

    await _show_grocery_list(update, context, user.id)


async def _show_grocery_list(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    edit_message: bool = False,
) -> None:
    """Build and send the grocery list display."""
    list_id = await _get_or_create_default_list(user_id)

    async with async_session_factory() as db:
        result = await db.execute(
            select(GroceryItem)
            .where(GroceryItem.list_id == list_id)
            .order_by(GroceryItem.is_bought, GroceryItem.created_at)
        )
        items = result.scalars().all()

    if not items:
        text = (
            "\U0001f6d2 <b>Shopping List</b>\n"
            "\n"
            "Your list is empty.\n"
            "\n"
            f"{BOX_TOP} <b>Quick start</b>\n"
            f"{BOX_MID}  Tap <b>\uff0b Add</b> below, or\n"
            f"{BOX_MID}  <code>/grocery add milk, eggs, bread</code>\n"
            f"{BOX_BOT}"
        )
    else:
        pending = [i for i in items if not i.is_bought]
        bought  = [i for i in items if i.is_bought]

        lines = ["\U0001f6d2 <b>Shopping List</b>", ""]

        if pending:
            lines.append(f"{BOX_TOP} <b>To Buy</b>  ({len(pending)})")
            for item in pending:
                qty = f"  <i>({_e(item.quantity)})</i>" if item.quantity else ""
                lines.append(f"{BOX_MID}  \u25cb {_e(item.name)}{qty}")
            lines.append(BOX_BOT)
            lines.append("")

        if bought:
            lines.append(f"{BOX_TOP} <b>Bought</b>  ({len(bought)})")
            for item in bought:
                qty = f"  <i>({_e(item.quantity)})</i>" if item.quantity else ""
                lines.append(f"{BOX_MID}  \u2713 {_e(item.name)}{qty}")
            lines.append(BOX_BOT)
            lines.append("")

        lines.append(f"{len(pending)} to buy {DOT} {len(bought)} bought")
        text = "\n".join(lines)

    # Build item toggle buttons (show pending items as tappable)
    keyboard_rows = []

    pending_items = [i for i in items if not i.is_bought]
    if pending_items:
        # Show items in rows of 2
        for i in range(0, len(pending_items), 2):
            row = []
            for item in pending_items[i : i + 2]:
                row.append(
                    InlineKeyboardButton(
                        f"\u25cb {item.name}",
                        callback_data=f"groc:toggle:{item.id}",
                    )
                )
            keyboard_rows.append(row)

    # Show bought items that can be unchecked
    bought_items = [i for i in items if i.is_bought]
    if bought_items:
        for i in range(0, min(len(bought_items), 4), 2):
            row = []
            for item in bought_items[i : i + 2]:
                row.append(
                    InlineKeyboardButton(
                        f"\u2713 {item.name}",
                        callback_data=f"groc:toggle:{item.id}",
                    )
                )
            keyboard_rows.append(row)

    # Action buttons
    keyboard_rows.append([
        InlineKeyboardButton("\uff0b Add", callback_data="groc:add"),
        InlineKeyboardButton("\U0001f9f9 Clear Bought", callback_data="groc:clear"),
    ])
    keyboard_rows.append([
        InlineKeyboardButton("\U0001f504 Refresh", callback_data="groc:refresh"),
    ])

    keyboard = InlineKeyboardMarkup(keyboard_rows)

    if edit_message and update.callback_query:
        try:
            await update.callback_query.edit_message_text(
                text=text, reply_markup=keyboard, parse_mode="HTML"
            )
        except Exception as e:
            if "Message is not modified" in str(e):
                pass  # Nothing changed — silently ignore
            else:
                raise
    else:
        await update.effective_chat.send_message(
            text=text, reply_markup=keyboard, parse_mode="HTML"
        )


# ============================================================
# Quick Add
# ============================================================

async def _quick_add_items(update: Update, user_id: int, items_text: str) -> None:
    """Parse comma-separated items and add them to the list."""
    list_id = await _get_or_create_default_list(user_id)

    raw_items = [i.strip() for i in items_text.split(",") if i.strip()]
    if not raw_items:
        await update.message.reply_text("No items to add. Separate items with commas.")
        return

    async with async_session_factory() as db:
        for raw in raw_items:
            # Try to extract quantity: "2x milk", "500g rice", "3 packs tissue"
            item = GroceryItem(
                list_id=list_id,
                name=raw,
                added_by_user_id=user_id,
            )
            db.add(item)
        await db.commit()

    names = ", ".join(_e(n) for n in raw_items)
    await update.message.reply_text(
        f"\u2705 Added {len(raw_items)} item(s): {names}\n"
        f"/grocery to view your list",
        parse_mode="HTML",
    )


# ============================================================
# Callback Router
# ============================================================

@require_auth
async def grocery_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Route all groc: callback queries."""
    query = update.callback_query
    await query.answer()
    data = query.data
    user = context.user_data["current_user"]

    if data == "groc:refresh":
        await _show_grocery_list(update, context, user.id, edit_message=True)

    elif data == "groc:clear":
        await _clear_bought_callback(update, context, user.id)

    elif data.startswith("groc:toggle:"):
        item_id = int(data.split(":")[2])
        await _toggle_item(update, context, item_id, user.id)


async def _toggle_item(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    item_id: int,
    user_id: int,
) -> None:
    """Toggle an item between bought and not bought."""
    async with async_session_factory() as db:
        result = await db.execute(
            select(GroceryItem).where(GroceryItem.id == item_id)
        )
        item = result.scalar_one_or_none()
        if item:
            item.is_bought = not item.is_bought
            if item.is_bought:
                item.bought_by_user_id = user_id
                item.bought_at = datetime.now(timezone.utc)
            else:
                item.bought_by_user_id = None
                item.bought_at = None
            await db.commit()

    await _show_grocery_list(update, context, user_id, edit_message=True)


async def _clear_bought(update: Update, user_id: int) -> None:
    """Clear all bought items from the list."""
    list_id = await _get_or_create_default_list(user_id)

    async with async_session_factory() as db:
        result = await db.execute(
            delete(GroceryItem).where(
                GroceryItem.list_id == list_id,
                GroceryItem.is_bought == True,
            )
        )
        await db.commit()
        count = result.rowcount

    await update.message.reply_text(
        f"\U0001f9f9 Cleared {count} bought item(s)\n"
        f"/grocery to view your list",
        parse_mode="HTML",
    )


async def _clear_bought_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
) -> None:
    """Clear bought items (triggered by inline button)."""
    list_id = await _get_or_create_default_list(user_id)

    async with async_session_factory() as db:
        result = await db.execute(
            delete(GroceryItem).where(
                GroceryItem.list_id == list_id,
                GroceryItem.is_bought == True,
            )
        )
        await db.commit()

    await _show_grocery_list(update, context, user_id, edit_message=True)


# ============================================================
# Add Items Conversation (from button)
# ============================================================

@require_auth
async def add_items_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle [Add Items] button. Ask user to type items."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "\uff0b <b>Add Items</b>\n"
        "\n"
        "Type items separated by commas:\n"
        "<i>milk, eggs, bread, 2x butter</i>",
        parse_mode="HTML",
    )
    return ADDING_ITEMS


@require_auth
async def add_items_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Received the items text. Parse and save."""
    user = context.user_data["current_user"]
    await _quick_add_items(update, user.id, update.message.text)
    return ConversationHandler.END


def get_add_items_conversation_handler() -> ConversationHandler:
    """Build the conversation handler for adding items via button."""
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(add_items_start, pattern="^groc:add$"),
        ],
        states={
            ADDING_ITEMS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_items_text),
            ],
        },
        fallbacks=[CommandHandler("cancel", lambda u, c: ConversationHandler.END)],
        conversation_timeout=120,
        per_message=False,
    )
