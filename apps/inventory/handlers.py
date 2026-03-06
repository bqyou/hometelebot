"""
Telegram command and callback handlers for the Inventory Tracker.

Interaction model:
- /inv          -> Show all items grouped by category, with inline buttons
- [Add Item]    -> Conversational flow: name -> quantity -> unit -> threshold
- [Edit]        -> Pick an item, then adjust quantity or edit details
- [Delete]      -> Pick an item to remove (with confirmation)
- /inv_add NAME QTY UNIT -> Quick-add shortcut (e.g., /inv_add "Toilet Paper" 12 rolls)
"""

import logging
from typing import Any

from sqlalchemy import select, delete, func
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

from core.auth import require_auth
from core.database import async_session_factory
from apps.inventory.models import InventoryItem

logger = logging.getLogger(__name__)

from core.ui import e as _e, BOX_TOP, BOX_MID, BOX_BOT, DOT

# Conversation states for adding an item
ADD_NAME = 0
ADD_QTY = 1
ADD_UNIT = 2
ADD_THRESHOLD = 3
ADD_CATEGORY = 4
ADD_THRESHOLD_CUSTOM = 5

# Conversation states for editing
EDIT_SELECT_FIELD = 10
EDIT_NEW_VALUE = 11

UNIT_OPTIONS = ["pcs", "rolls", "boxes", "kg", "liters", "bottles", "packs"]
CATEGORY_OPTIONS = ["Kids", "General", "Other"]


# ============================================================
# Main Inventory View
# ============================================================

@require_auth
async def inventory_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /inv -- display all inventory items grouped by category."""
    user = context.user_data["current_user"]
    await _show_inventory(update, context, user.id)


async def _show_inventory(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    edit_message: bool = False,
) -> None:
    """Build and send/edit the inventory list message."""
    async with async_session_factory() as db:
        result = await db.execute(
            select(InventoryItem)
            .where(InventoryItem.user_id == user_id)
            .order_by(InventoryItem.category, InventoryItem.name)
        )
        items = result.scalars().all()

    if not items:
        text = (
            "\U0001f4e6 <b>Inventory</b>\n"
            "\n"
            "No items yet.\n"
            "\n"
            f"{BOX_TOP} <b>Quick start</b>\n"
            f"{BOX_MID}  Tap <b>\uff0b Add</b> below, or\n"
            f"{BOX_MID}  <code>/inv_add Rice 5 kg</code>\n"
            f"{BOX_BOT}"
        )
    else:
        categories: dict[str, list[InventoryItem]] = {}
        for item in items:
            cat = item.category or "General"
            categories.setdefault(cat, []).append(item)

        lines = ["\U0001f4e6 <b>Inventory</b>", ""]
        for cat_name, cat_items in sorted(categories.items()):
            lines.append(f"{BOX_TOP} <b>{_e(cat_name)}</b>")
            for item in cat_items:
                low = "  \u26a0\ufe0f low" if item.is_low_stock else ""
                lines.append(f"{BOX_MID}  {_e(item.name)} {DOT} {item.quantity} {_e(item.unit)}{low}")
            lines.append(BOX_BOT)
            lines.append("")

        low_stock_count = sum(1 for i in items if i.is_low_stock)
        if low_stock_count > 0:
            lines.append(f"\u26a0\ufe0f {low_stock_count} item(s) low on stock")

        text = "\n".join(lines)

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("\uff0b Add", callback_data="inv:add"),
            InlineKeyboardButton("\u270f\ufe0f Edit", callback_data="inv:edit_select"),
        ],
        [
            InlineKeyboardButton("\U0001f5d1 Delete", callback_data="inv:del_select"),
            InlineKeyboardButton("\U0001f504 Refresh", callback_data="inv:refresh"),
        ],
    ])

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
# Callback Router
# ============================================================

@require_auth
async def inventory_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Route all inv: callback queries to the appropriate handler."""
    query = update.callback_query
    await query.answer()
    data = query.data
    user = context.user_data["current_user"]

    if data == "inv:refresh":
        await _show_inventory(update, context, user.id, edit_message=True)

    elif data == "inv:edit_select":
        await _show_item_picker(update, user.id, action="edit")

    elif data == "inv:del_select":
        await _show_item_picker(update, user.id, action="del")

    elif data.startswith("inv:edit:"):
        item_id = int(data.split(":")[2])
        await _show_edit_options(update, item_id)

    elif data.startswith("inv:del_confirm:"):
        item_id = int(data.split(":")[2])
        await _show_delete_confirm(update, item_id)

    elif data.startswith("inv:del:"):
        item_id = int(data.split(":")[2])
        await _delete_item(update, context, item_id, user.id)

    elif data.startswith("inv:qty:"):
        # Format: inv:qty:ITEM_ID:DELTA (e.g., inv:qty:5:1 or inv:qty:5:-1)
        parts = data.split(":")
        item_id = int(parts[2])
        delta = int(parts[3])
        await _adjust_quantity(update, context, item_id, delta, user.id)


async def _show_item_picker(update: Update, user_id: int, action: str) -> None:
    """Show a list of items as buttons for selecting which to edit/delete."""
    async with async_session_factory() as db:
        result = await db.execute(
            select(InventoryItem)
            .where(InventoryItem.user_id == user_id)
            .order_by(InventoryItem.name)
        )
        items = result.scalars().all()

    if not items:
        await update.callback_query.edit_message_text("No items to select from.")
        return

    # Build a grid of item buttons (2 per row)
    # For delete, route to the confirm screen first rather than deleting immediately
    cb_action = "del_confirm" if action == "del" else action
    buttons = []
    for item in items:
        label = f"{item.name} ({item.quantity})"
        buttons.append(InlineKeyboardButton(label, callback_data=f"inv:{cb_action}:{item.id}"))

    rows = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
    rows.append([InlineKeyboardButton("\u2190 Back", callback_data="inv:refresh")])

    action_label = "edit" if action == "edit" else "delete"
    icon = "\u270f\ufe0f" if action == "edit" else "\U0001f5d1"
    await update.callback_query.edit_message_text(
        f"{icon} Select an item to {action_label}:",
        reply_markup=InlineKeyboardMarkup(rows),
    )


async def _show_delete_confirm(update: Update, item_id: int) -> None:
    """Ask the user to confirm before deleting an item."""
    async with async_session_factory() as db:
        result = await db.execute(
            select(InventoryItem).where(InventoryItem.id == item_id)
        )
        item = result.scalar_one_or_none()

    if not item:
        await update.callback_query.edit_message_text("Item not found.")
        return

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("\u2713 Yes, delete", callback_data=f"inv:del:{item_id}"),
            InlineKeyboardButton("\u2190 Cancel", callback_data="inv:del_select"),
        ]
    ])
    await update.callback_query.edit_message_text(
        f"\U0001f5d1 Delete <b>{_e(item.name)}</b>?\n"
        f"{item.quantity} {_e(item.unit)} {DOT} {_e(item.category or 'General')}",
        reply_markup=keyboard,
        parse_mode="HTML",
    )


async def _show_edit_options(update: Update, item_id: int) -> None:
    """Show quick quantity adjustment buttons for a specific item."""
    async with async_session_factory() as db:
        result = await db.execute(
            select(InventoryItem).where(InventoryItem.id == item_id)
        )
        item = result.scalar_one_or_none()

    if not item:
        await update.callback_query.edit_message_text("Item not found.")
        return

    threshold_text = str(item.low_stock_threshold) if item.low_stock_threshold else "None"
    text = (
        f"\u270f\ufe0f <b>{_e(item.name)}</b>\n"
        f"\n"
        f"{BOX_TOP} <b>Details</b>\n"
        f"{BOX_MID}  Qty {DOT} {item.quantity} {_e(item.unit)}\n"
        f"{BOX_MID}  Category {DOT} {_e(item.category or 'General')}\n"
        f"{BOX_MID}  Alert at {DOT} \u2264 {threshold_text}\n"
        f"{BOX_BOT}\n"
        f"\n"
        f"Adjust quantity:"
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("-5", callback_data=f"inv:qty:{item_id}:-5"),
            InlineKeyboardButton("-1", callback_data=f"inv:qty:{item_id}:-1"),
            InlineKeyboardButton("+1", callback_data=f"inv:qty:{item_id}:1"),
            InlineKeyboardButton("+5", callback_data=f"inv:qty:{item_id}:5"),
        ],
        [
            InlineKeyboardButton("-10", callback_data=f"inv:qty:{item_id}:-10"),
            InlineKeyboardButton("+10", callback_data=f"inv:qty:{item_id}:10"),
        ],
        [InlineKeyboardButton("\u2190 Back", callback_data="inv:refresh")],
    ])

    await update.callback_query.edit_message_text(
        text=text, reply_markup=keyboard, parse_mode="HTML"
    )


async def _adjust_quantity(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    item_id: int,
    delta: int,
    user_id: int,
) -> None:
    """Increment or decrement an item's quantity, then refresh the edit view."""
    async with async_session_factory() as db:
        result = await db.execute(
            select(InventoryItem).where(
                InventoryItem.id == item_id,
                InventoryItem.user_id == user_id,
            )
        )
        item = result.scalar_one_or_none()
        if item:
            item.quantity = max(0, item.quantity + delta)
            await db.commit()

    await _show_edit_options(update, item_id)


async def _delete_item(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    item_id: int,
    user_id: int,
) -> None:
    """Delete an inventory item and refresh the main list."""
    async with async_session_factory() as db:
        await db.execute(
            delete(InventoryItem).where(
                InventoryItem.id == item_id,
                InventoryItem.user_id == user_id,
            )
        )
        await db.commit()

    await _show_inventory(update, context, user_id, edit_message=True)


# ============================================================
# Add Item Conversation
# ============================================================

@require_auth
async def add_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle the [Add Item] button press. Start the add-item conversation."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("\uff0b <b>Add Item</b>\n\nType the item name:", parse_mode="HTML")
    return ADD_NAME


@require_auth
async def add_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Received item name. Ask for quantity."""
    name = update.message.text.strip()
    if not name:
        await update.message.reply_text("Name cannot be empty. Try again:")
        return ADD_NAME

    context.user_data["inv_add_name"] = name
    await update.message.reply_text(f"How many <b>{_e(name)}</b> do you have?", parse_mode="HTML")
    return ADD_QTY


@require_auth
async def add_qty(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Received quantity. Show unit picker."""
    text = update.message.text.strip()
    try:
        qty = int(text)
        if qty < 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("Please enter a valid non-negative number:")
        return ADD_QTY

    context.user_data["inv_add_qty"] = qty

    # Show unit options as inline buttons
    buttons = [
        [InlineKeyboardButton(u, callback_data=f"inv:unit:{u}") for u in UNIT_OPTIONS[:4]],
        [InlineKeyboardButton(u, callback_data=f"inv:unit:{u}") for u in UNIT_OPTIONS[4:]],
    ]
    await update.message.reply_text(
        "What unit?",
        reply_markup=InlineKeyboardMarkup(buttons),
    )
    return ADD_UNIT


@require_auth
async def add_unit_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Received unit selection. Ask for low stock threshold."""
    query = update.callback_query
    await query.answer()

    unit = query.data.split(":")[2]
    context.user_data["inv_add_unit"] = unit

    buttons = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("1", callback_data="inv:thresh:1"),
            InlineKeyboardButton("2", callback_data="inv:thresh:2"),
            InlineKeyboardButton("5", callback_data="inv:thresh:5"),
            InlineKeyboardButton("Skip", callback_data="inv:thresh:0"),
        ],
        [
            InlineKeyboardButton("Custom\u2026", callback_data="inv:thresh:custom"),
        ],
    ])
    await query.edit_message_text(
        "Set a low stock alert threshold?\n<i>You\u2019ll see a warning when stock reaches this level</i>",
        reply_markup=buttons,
        parse_mode="HTML",
    )
    return ADD_THRESHOLD


@require_auth
async def add_threshold_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Received threshold button press. If 'custom', ask user to type a number."""
    query = update.callback_query
    await query.answer()

    value = query.data.split(":")[2]

    if value == "custom":
        await query.edit_message_text(
            "Type your threshold number:",
        )
        return ADD_THRESHOLD_CUSTOM

    context.user_data["inv_add_threshold"] = int(value)
    return await _ask_category(query)


@require_auth
async def add_threshold_custom(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Received a typed threshold value. Validate and proceed to category."""
    text = update.message.text.strip()
    try:
        value = int(text)
        if value < 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("Please enter a valid non-negative whole number:")
        return ADD_THRESHOLD_CUSTOM

    context.user_data["inv_add_threshold"] = value
    # Send a fresh message since we're coming from a text reply
    buttons = InlineKeyboardMarkup([[
        InlineKeyboardButton(cat, callback_data=f"inv:cat:{cat}")
        for cat in CATEGORY_OPTIONS
    ]])
    await update.message.reply_text("Which category?", reply_markup=buttons)
    return ADD_CATEGORY


async def _ask_category(query) -> int:
    """Edit the current message to show the category picker."""
    buttons = InlineKeyboardMarkup([[
        InlineKeyboardButton(cat, callback_data=f"inv:cat:{cat}")
        for cat in CATEGORY_OPTIONS
    ]])
    await query.edit_message_text("Which category?", reply_markup=buttons)
    return ADD_CATEGORY


@require_auth
async def add_category_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Received category. Save the item to the database."""
    query = update.callback_query
    await query.answer()

    category  = query.data.split(":")[2]
    user      = context.user_data["current_user"]
    name      = context.user_data.pop("inv_add_name")
    qty       = context.user_data.pop("inv_add_qty")
    unit      = context.user_data.pop("inv_add_unit")
    threshold = context.user_data.pop("inv_add_threshold")

    async with async_session_factory() as db:
        db.add(InventoryItem(
            user_id=user.id,
            name=name,
            quantity=qty,
            unit=unit,
            low_stock_threshold=threshold,
            category=category,
        ))
        await db.commit()

    # Show the updated inventory directly — closes the loop without forcing the user to type /inv
    await _show_inventory(update, context, user.id, edit_message=True)
    return ConversationHandler.END


# ============================================================
# Quick Add via Command
# ============================================================

@require_auth
async def quick_add_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /inv_add NAME QTY [UNIT] [CATEGORY] — quick-add without the conversation flow.

    Formats accepted:
        /inv_add Rice 5
        /inv_add Rice 5 kg
        /inv_add Rice 5 kg Kids
    Category must be the last word; unit must come before it.
    """
    user = context.user_data["current_user"]
    args = context.args

    if not args or len(args) < 2:
        await update.message.reply_text(
            "Usage: /inv_add &lt;name&gt; &lt;qty&gt; [unit] [category]\n\n"
            "Examples:\n"
            "  /inv_add Toilet Paper 12 rolls\n"
            "  /inv_add Rice 5 kg Kids\n"
            "  /inv_add Batteries 8",
            parse_mode="HTML",
        )
        return

    unit     = "pcs"
    category = "General"
    name     = ""
    qty      = 0
    parsed   = False

    # Try: name qty unit category  (4+ args, args[-3] is qty)
    if len(args) >= 4:
        try:
            qty  = int(args[-3])
            unit = args[-2]
            category = args[-1]
            name = " ".join(args[:-3])
            if name:
                parsed = True
        except (ValueError, IndexError):
            pass

    # Try: name qty unit  (args[-2] is qty)
    if not parsed:
        try:
            qty  = int(args[-2])
            unit = args[-1]
            name = " ".join(args[:-2])
            if name:
                parsed = True
        except (ValueError, IndexError):
            pass

    # Try: name qty  (args[-1] is qty, no unit)
    if not parsed:
        try:
            qty  = int(args[-1])
            name = " ".join(args[:-1])
            if name:
                parsed = True
        except (ValueError, IndexError):
            pass

    if not parsed or not name:
        await update.message.reply_text(
            "Could not parse. Usage: /inv_add &lt;name&gt; &lt;qty&gt; [unit] [category]",
            parse_mode="HTML",
        )
        return

    async with async_session_factory() as db:
        db.add(InventoryItem(
            user_id=user.id,
            name=name,
            quantity=qty,
            unit=unit,
            category=category,
        ))
        await db.commit()

    await update.message.reply_text(
        f"\u2705 Added <b>{_e(name)}</b> {DOT} {qty} {_e(unit)} {DOT} {_e(category)}",
        parse_mode="HTML",
    )


# ============================================================
# Handler Registration (called by the app class)
# ============================================================

def get_add_conversation_handler() -> ConversationHandler:
    """Build the ConversationHandler for the add-item flow."""
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(add_start_callback, pattern="^inv:add$"),
        ],
        states={
            ADD_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_name),
            ],
            ADD_QTY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_qty),
            ],
            ADD_UNIT: [
                CallbackQueryHandler(add_unit_callback, pattern="^inv:unit:"),
            ],
            ADD_THRESHOLD: [
                CallbackQueryHandler(add_threshold_callback, pattern="^inv:thresh:"),
            ],
            ADD_THRESHOLD_CUSTOM: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_threshold_custom),
            ],
            ADD_CATEGORY: [
                CallbackQueryHandler(add_category_callback, pattern="^inv:cat:"),
            ],
        },
        fallbacks=[CommandHandler("cancel", lambda u, c: ConversationHandler.END)],
        conversation_timeout=120,
        per_message=False,
    )
