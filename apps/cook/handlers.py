"""Main menu and kitchen inventory CRUD handlers for the Cook app."""

import logging
import math

from sqlalchemy import select, delete, func
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes,
    CallbackQueryHandler,
    ConversationHandler,
    MessageHandler,
    filters,
)

from core.auth import require_auth, require_app_access
from core.database import async_session_factory
from core.ui import e, BOX_TOP, BOX_MID, BOX_BOT, DOT, header, section
from apps.cook.models import CookRawMaterial, CookSauce, CookEquipment
from apps.cook.constants import UNIT_OPTIONS, SAUCE_SUGGESTIONS, EQUIPMENT_SUGGESTIONS
from apps.cook.llm import is_ai_enabled

logger = logging.getLogger(__name__)

ITEMS_PER_PAGE = 10

# Conversation states
RAW_NAME = 0
RAW_QTY = 1
RAW_UNIT = 2
RAW_UNIT_CUSTOM = 3
SAUCE_NAME = 10
EQUIP_NAME = 20
RAW_EDIT_QTY = 30

SAUCE_EMOJI = "\U0001F9C2"   # 🧂
EQUIP_EMOJI = "\U0001f373"   # 🍳
RAW_EMOJI   = "\U0001f96c"   # 🥬

# Shared cancel button for each inventory conversation
_RAW_CANCEL_ROW  = [InlineKeyboardButton("\u2715 Cancel", callback_data="cook:raw:cancel")]
_SC_CANCEL_ROW   = [InlineKeyboardButton("\u2715 Cancel", callback_data="cook:sc:cancel")]
_EQ_CANCEL_ROW   = [InlineKeyboardButton("\u2715 Cancel", callback_data="cook:eq:cancel")]
_BACK_TO_MENU    = [InlineKeyboardButton("\u2190 Back", callback_data="cook:menu")]


# ============================================================
# Main Menu
# ============================================================

@require_app_access("cook")
async def cook_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = context.user_data["current_user"]
    await _show_main_menu(update, context, user.id)


async def _show_main_menu(update, context, user_id, edit_message=False):
    text = header("\U0001f373", "Cook")

    rows = [
        [
            InlineKeyboardButton(f"{RAW_EMOJI} Raw Materials", callback_data="cook:raw"),
            InlineKeyboardButton(f"{SAUCE_EMOJI} Sauces", callback_data="cook:sauce"),
        ],
        [InlineKeyboardButton(f"{EQUIP_EMOJI} Equipment", callback_data="cook:equip")],
        [InlineKeyboardButton("\U0001f4d6 Cookbook", callback_data="cook:book")],
    ]

    if is_ai_enabled():
        rows.append([
            InlineKeyboardButton("\U0001f4dd Import Recipe", callback_data="cook:ai:imp"),
            InlineKeyboardButton("\U0001f916 Generate Recipe", callback_data="cook:ai:gen"),
        ])

    rows.append([InlineKeyboardButton("\U0001f50d What Can I Cook?", callback_data="cook:match")])

    keyboard = InlineKeyboardMarkup(rows)

    if edit_message and update.callback_query:
        try:
            await update.callback_query.edit_message_text(
                text=text, reply_markup=keyboard, parse_mode="HTML"
            )
        except Exception as ex:
            if "Message is not modified" not in str(ex):
                raise
    else:
        await update.effective_chat.send_message(
            text=text, reply_markup=keyboard, parse_mode="HTML"
        )


# ============================================================
# Raw Materials
# ============================================================

async def _show_raw_materials(update, user_id, page=0, send_new=False):
    async with async_session_factory() as db:
        count_result = await db.execute(
            select(func.count()).select_from(CookRawMaterial).where(CookRawMaterial.user_id == user_id)
        )
        total = count_result.scalar()

        result = await db.execute(
            select(CookRawMaterial)
            .where(CookRawMaterial.user_id == user_id)
            .order_by(CookRawMaterial.name)
            .offset(page * ITEMS_PER_PAGE)
            .limit(ITEMS_PER_PAGE)
        )
        items = result.scalars().all()

    total_pages = max(1, math.ceil(total / ITEMS_PER_PAGE))

    if not items and page == 0:
        lines = [header(RAW_EMOJI, "Raw Materials"), "", "No ingredients yet. Tap + Add to start."]
    else:
        lines = [header(RAW_EMOJI, f"Raw Materials ({total} items)"), ""]
        item_lines = []
        for item in items:
            qty_str = f"{item.quantity:g}"
            item_lines.append(f"{e(item.name)} {DOT} {qty_str} {e(item.unit)}")
        lines.extend(section("Ingredients", item_lines))

    if total_pages > 1:
        lines.append(f"\nPage {page + 1}/{total_pages}")

    rows = [
        [
            InlineKeyboardButton("+ Add", callback_data="cook:raw:add"),
            InlineKeyboardButton("\u270f\ufe0f Edit", callback_data="cook:raw:edit"),
            InlineKeyboardButton("\U0001f5d1 Delete", callback_data="cook:raw:del"),
        ],
    ]

    if total_pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("\u2190 Prev", callback_data=f"cook:raw:p:{page - 1}"))
        if page < total_pages - 1:
            nav.append(InlineKeyboardButton("Next \u2192", callback_data=f"cook:raw:p:{page + 1}"))
        rows.append(nav)

    rows.append([InlineKeyboardButton("\u2190 Back", callback_data="cook:menu")])
    keyboard = InlineKeyboardMarkup(rows)
    text = "\n".join(lines)

    if send_new:
        await update.effective_chat.send_message(text=text, reply_markup=keyboard, parse_mode="HTML")
    else:
        await update.callback_query.edit_message_text(text=text, reply_markup=keyboard, parse_mode="HTML")


async def _show_raw_edit_picker(update, user_id):
    async with async_session_factory() as db:
        result = await db.execute(
            select(CookRawMaterial).where(CookRawMaterial.user_id == user_id).order_by(CookRawMaterial.name)
        )
        items = result.scalars().all()

    if not items:
        await update.callback_query.edit_message_text(
            "No ingredients to edit.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("\u2190 Back", callback_data="cook:raw")]]),
        )
        return

    buttons = []
    for item in items:
        qty_str = f"{item.quantity:g}"
        buttons.append(InlineKeyboardButton(f"{item.name} ({qty_str})", callback_data=f"cook:raw:e:{item.id}"))

    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    rows.append([InlineKeyboardButton("\u2190 Back", callback_data="cook:raw")])

    await update.callback_query.edit_message_text(
        "\u270f\ufe0f Select an ingredient to edit:",
        reply_markup=InlineKeyboardMarkup(rows),
    )


async def _show_raw_edit_options(update, item_id):
    async with async_session_factory() as db:
        result = await db.execute(select(CookRawMaterial).where(CookRawMaterial.id == item_id))
        item = result.scalar_one_or_none()

    if not item:
        await update.callback_query.edit_message_text(
            "Item not found.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("\u2190 Back", callback_data="cook:raw")]]),
        )
        return

    qty_str = f"{item.quantity:g}"
    text = (
        f"\u270f\ufe0f <b>{e(item.name)}</b>\n\n"
        f"{BOX_TOP} <b>Details</b>\n"
        f"{BOX_MID}  Qty {DOT} {qty_str} {e(item.unit)}\n"
        f"{BOX_BOT}\n\n"
        f"Adjust quantity:"
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("-5", callback_data=f"cook:raw:q:{item_id}:-5"),
            InlineKeyboardButton("-1", callback_data=f"cook:raw:q:{item_id}:-1"),
            InlineKeyboardButton("+1", callback_data=f"cook:raw:q:{item_id}:1"),
            InlineKeyboardButton("+5", callback_data=f"cook:raw:q:{item_id}:5"),
        ],
        [
            InlineKeyboardButton("-0.5", callback_data=f"cook:raw:q:{item_id}:-0.5"),
            InlineKeyboardButton("+0.5", callback_data=f"cook:raw:q:{item_id}:0.5"),
        ],
        [InlineKeyboardButton("\u2190 Back", callback_data="cook:raw:edit")],
    ])

    await update.callback_query.edit_message_text(text=text, reply_markup=keyboard, parse_mode="HTML")


async def _show_raw_delete_picker(update, user_id):
    async with async_session_factory() as db:
        result = await db.execute(
            select(CookRawMaterial).where(CookRawMaterial.user_id == user_id).order_by(CookRawMaterial.name)
        )
        items = result.scalars().all()

    if not items:
        await update.callback_query.edit_message_text(
            "No ingredients to delete.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("\u2190 Back", callback_data="cook:raw")]]),
        )
        return

    buttons = []
    for item in items:
        buttons.append(InlineKeyboardButton(item.name, callback_data=f"cook:raw:d:{item.id}"))

    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    rows.append([InlineKeyboardButton("\u2190 Back", callback_data="cook:raw")])

    await update.callback_query.edit_message_text(
        "\U0001f5d1 Select an ingredient to delete:",
        reply_markup=InlineKeyboardMarkup(rows),
    )


# ============================================================
# Sauces
# ============================================================

async def _show_sauces(update, user_id, page=0, send_new=False):
    async with async_session_factory() as db:
        count_result = await db.execute(
            select(func.count()).select_from(CookSauce).where(CookSauce.user_id == user_id)
        )
        total = count_result.scalar()

        result = await db.execute(
            select(CookSauce)
            .where(CookSauce.user_id == user_id)
            .order_by(CookSauce.name)
            .offset(page * ITEMS_PER_PAGE)
            .limit(ITEMS_PER_PAGE)
        )
        items = result.scalars().all()

    total_pages = max(1, math.ceil(total / ITEMS_PER_PAGE))

    if not items and page == 0:
        lines = [header(SAUCE_EMOJI, "Sauces"), "", "No sauces yet. Tap + Add to pick from suggestions."]
    else:
        lines = [header(SAUCE_EMOJI, f"Sauces ({total} items)"), ""]
        item_lines = [e(item.name) for item in items]
        lines.extend(section("Sauces & Condiments", item_lines))

    if total_pages > 1:
        lines.append(f"\nPage {page + 1}/{total_pages}")

    rows = [
        [
            InlineKeyboardButton("+ Add", callback_data="cook:sc:add"),
            InlineKeyboardButton("\U0001f5d1 Delete", callback_data="cook:sc:del"),
        ],
    ]

    if total_pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("\u2190 Prev", callback_data=f"cook:sc:p:{page - 1}"))
        if page < total_pages - 1:
            nav.append(InlineKeyboardButton("Next \u2192", callback_data=f"cook:sc:p:{page + 1}"))
        rows.append(nav)

    rows.append([InlineKeyboardButton("\u2190 Back", callback_data="cook:menu")])
    keyboard = InlineKeyboardMarkup(rows)
    text = "\n".join(lines)

    if send_new:
        await update.effective_chat.send_message(text=text, reply_markup=keyboard, parse_mode="HTML")
    else:
        await update.callback_query.edit_message_text(text=text, reply_markup=keyboard, parse_mode="HTML")


async def _show_sauce_add_menu(update, user_id):
    """Show all sauce suggestions (excluding owned) with a Custom option."""
    async with async_session_factory() as db:
        result = await db.execute(
            select(CookSauce.name).where(CookSauce.user_id == user_id)
        )
        owned = {row[0].lower() for row in result.all()}

    available = [(i, s) for i, s in enumerate(SAUCE_SUGGESTIONS) if s.lower() not in owned]

    rows = []
    if available:
        buttons = []
        for idx, name in available:
            buttons.append(InlineKeyboardButton(name, callback_data=f"cook:sc:sa:{idx}"))
        rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]

    rows.append([
        InlineKeyboardButton("\u270f\ufe0f Custom", callback_data="cook:sc:custom"),
        InlineKeyboardButton("\u2190 Back", callback_data="cook:sauce"),
    ])

    title = f"{SAUCE_EMOJI} <b>Add Sauce</b>"
    body = "\n\nTap to add, or enter a custom sauce:" if available else "\n\nAll suggestions already added:"
    await update.callback_query.edit_message_text(
        title + body,
        reply_markup=InlineKeyboardMarkup(rows),
        parse_mode="HTML",
    )


async def _show_sauce_delete_picker(update, user_id):
    async with async_session_factory() as db:
        result = await db.execute(
            select(CookSauce).where(CookSauce.user_id == user_id).order_by(CookSauce.name)
        )
        items = result.scalars().all()

    if not items:
        await update.callback_query.edit_message_text(
            "No sauces to delete.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("\u2190 Back", callback_data="cook:sauce")]]),
        )
        return

    buttons = []
    for item in items:
        buttons.append(InlineKeyboardButton(item.name, callback_data=f"cook:sc:d:{item.id}"))

    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    rows.append([InlineKeyboardButton("\u2190 Back", callback_data="cook:sauce")])

    await update.callback_query.edit_message_text(
        "\U0001f5d1 Select a sauce to delete:",
        reply_markup=InlineKeyboardMarkup(rows),
    )


# ============================================================
# Equipment
# ============================================================

async def _show_equipment(update, user_id, page=0, send_new=False):
    async with async_session_factory() as db:
        count_result = await db.execute(
            select(func.count()).select_from(CookEquipment).where(CookEquipment.user_id == user_id)
        )
        total = count_result.scalar()

        result = await db.execute(
            select(CookEquipment)
            .where(CookEquipment.user_id == user_id)
            .order_by(CookEquipment.name)
            .offset(page * ITEMS_PER_PAGE)
            .limit(ITEMS_PER_PAGE)
        )
        items = result.scalars().all()

    total_pages = max(1, math.ceil(total / ITEMS_PER_PAGE))

    if not items and page == 0:
        lines = [header(EQUIP_EMOJI, "Equipment"), "", "No equipment yet. Tap + Add to pick from suggestions."]
    else:
        lines = [header(EQUIP_EMOJI, f"Equipment ({total} items)"), ""]
        item_lines = [e(item.name) for item in items]
        lines.extend(section("Kitchen Equipment", item_lines))

    if total_pages > 1:
        lines.append(f"\nPage {page + 1}/{total_pages}")

    rows = [
        [
            InlineKeyboardButton("+ Add", callback_data="cook:eq:add"),
            InlineKeyboardButton("\U0001f5d1 Delete", callback_data="cook:eq:del"),
        ],
    ]

    if total_pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("\u2190 Prev", callback_data=f"cook:eq:p:{page - 1}"))
        if page < total_pages - 1:
            nav.append(InlineKeyboardButton("Next \u2192", callback_data=f"cook:eq:p:{page + 1}"))
        rows.append(nav)

    rows.append([InlineKeyboardButton("\u2190 Back", callback_data="cook:menu")])
    keyboard = InlineKeyboardMarkup(rows)
    text = "\n".join(lines)

    if send_new:
        await update.effective_chat.send_message(text=text, reply_markup=keyboard, parse_mode="HTML")
    else:
        await update.callback_query.edit_message_text(text=text, reply_markup=keyboard, parse_mode="HTML")


async def _show_equip_add_menu(update, user_id):
    """Show all equipment suggestions (excluding owned) with a Custom option."""
    async with async_session_factory() as db:
        result = await db.execute(
            select(CookEquipment.name).where(CookEquipment.user_id == user_id)
        )
        owned = {row[0].lower() for row in result.all()}

    available = [(i, eq) for i, eq in enumerate(EQUIPMENT_SUGGESTIONS) if eq.lower() not in owned]

    rows = []
    if available:
        buttons = []
        for idx, name in available:
            buttons.append(InlineKeyboardButton(name, callback_data=f"cook:eq:sa:{idx}"))
        rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]

    rows.append([
        InlineKeyboardButton("\u270f\ufe0f Custom", callback_data="cook:eq:custom"),
        InlineKeyboardButton("\u2190 Back", callback_data="cook:equip"),
    ])

    title = f"{EQUIP_EMOJI} <b>Add Equipment</b>"
    body = "\n\nTap to add, or enter a custom item:" if available else "\n\nAll suggestions already added:"
    await update.callback_query.edit_message_text(
        title + body,
        reply_markup=InlineKeyboardMarkup(rows),
        parse_mode="HTML",
    )


async def _show_equip_delete_picker(update, user_id):
    async with async_session_factory() as db:
        result = await db.execute(
            select(CookEquipment).where(CookEquipment.user_id == user_id).order_by(CookEquipment.name)
        )
        items = result.scalars().all()

    if not items:
        await update.callback_query.edit_message_text(
            "No equipment to delete.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("\u2190 Back", callback_data="cook:equip")]]),
        )
        return

    buttons = []
    for item in items:
        buttons.append(InlineKeyboardButton(item.name, callback_data=f"cook:eq:d:{item.id}"))

    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    rows.append([InlineKeyboardButton("\u2190 Back", callback_data="cook:equip")])

    await update.callback_query.edit_message_text(
        "\U0001f5d1 Select equipment to delete:",
        reply_markup=InlineKeyboardMarkup(rows),
    )


# ============================================================
# Callback Router
# ============================================================

@require_auth
async def cook_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    data = query.data
    user = context.user_data["current_user"]

    # Suggestion taps get a toast; everything else gets a silent ack
    if data.startswith("cook:sc:sa:") or data.startswith("cook:eq:sa:"):
        await query.answer("Added!")
    else:
        await query.answer()

    # Navigation
    if data == "cook:menu":
        await _show_main_menu(update, context, user.id, edit_message=True)

    # Raw materials
    elif data == "cook:raw":
        await _show_raw_materials(update, user.id)
    elif data.startswith("cook:raw:p:"):
        page = int(data.split(":")[3])
        await _show_raw_materials(update, user.id, page)
    elif data == "cook:raw:edit":
        await _show_raw_edit_picker(update, user.id)
    elif data.startswith("cook:raw:e:"):
        item_id = int(data.split(":")[3])
        await _show_raw_edit_options(update, item_id)
    elif data == "cook:raw:del":
        await _show_raw_delete_picker(update, user.id)
    elif data.startswith("cook:raw:d:"):
        item_id = int(data.split(":")[3])
        await _show_raw_delete_confirm(update, item_id)
    elif data.startswith("cook:raw:dd:"):
        item_id = int(data.split(":")[3])
        await _delete_raw_material(update, user.id, item_id)
    elif data.startswith("cook:raw:q:"):
        parts = data.split(":")
        item_id = int(parts[3])
        delta = float(parts[4])
        await _adjust_raw_qty(update, user.id, item_id, delta)

    # Sauces
    elif data == "cook:sauce":
        await _show_sauces(update, user.id)
    elif data.startswith("cook:sc:p:"):
        page = int(data.split(":")[3])
        await _show_sauces(update, user.id, page)
    elif data == "cook:sc:add":
        await _show_sauce_add_menu(update, user.id)
    elif data == "cook:sc:del":
        await _show_sauce_delete_picker(update, user.id)
    elif data.startswith("cook:sc:d:"):
        item_id = int(data.split(":")[3])
        await _show_sauce_delete_confirm(update, item_id)
    elif data.startswith("cook:sc:dd:"):
        item_id = int(data.split(":")[3])
        await _delete_sauce(update, user.id, item_id)
    elif data.startswith("cook:sc:sa:"):
        idx = int(data.split(":")[3])
        await _add_sauce_suggestion(update, user.id, idx)

    # Equipment
    elif data == "cook:equip":
        await _show_equipment(update, user.id)
    elif data.startswith("cook:eq:p:"):
        page = int(data.split(":")[3])
        await _show_equipment(update, user.id, page)
    elif data == "cook:eq:add":
        await _show_equip_add_menu(update, user.id)
    elif data == "cook:eq:del":
        await _show_equip_delete_picker(update, user.id)
    elif data.startswith("cook:eq:d:"):
        item_id = int(data.split(":")[3])
        await _show_equip_delete_confirm(update, item_id)
    elif data.startswith("cook:eq:dd:"):
        item_id = int(data.split(":")[3])
        await _delete_equipment(update, user.id, item_id)
    elif data.startswith("cook:eq:sa:"):
        idx = int(data.split(":")[3])
        await _add_equip_suggestion(update, user.id, idx)


# ============================================================
# Raw Material CRUD Actions
# ============================================================

async def _show_raw_delete_confirm(update, item_id):
    async with async_session_factory() as db:
        result = await db.execute(select(CookRawMaterial).where(CookRawMaterial.id == item_id))
        item = result.scalar_one_or_none()

    if not item:
        await update.callback_query.edit_message_text(
            "Item not found.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("\u2190 Back", callback_data="cook:raw")]]),
        )
        return

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("\u2713 Yes, delete", callback_data=f"cook:raw:dd:{item_id}"),
            InlineKeyboardButton("\u2190 Cancel", callback_data="cook:raw:del"),
        ]
    ])
    await update.callback_query.edit_message_text(
        f"\U0001f5d1 Delete <b>{e(item.name)}</b>?",
        reply_markup=keyboard,
        parse_mode="HTML",
    )


async def _delete_raw_material(update, user_id, item_id):
    async with async_session_factory() as db:
        await db.execute(
            delete(CookRawMaterial).where(CookRawMaterial.id == item_id, CookRawMaterial.user_id == user_id)
        )
        await db.commit()
    await _show_raw_materials(update, user_id)


async def _adjust_raw_qty(update, user_id, item_id, delta):
    async with async_session_factory() as db:
        result = await db.execute(
            select(CookRawMaterial).where(CookRawMaterial.id == item_id, CookRawMaterial.user_id == user_id)
        )
        item = result.scalar_one_or_none()
        if item:
            if item.quantity == 0 and delta < 0:
                await update.callback_query.answer("Already at 0")
                return
            item.quantity = max(0, item.quantity + delta)
            await db.commit()
    await _show_raw_edit_options(update, item_id)


# ============================================================
# Sauce CRUD Actions
# ============================================================

async def _show_sauce_delete_confirm(update, item_id):
    async with async_session_factory() as db:
        result = await db.execute(select(CookSauce).where(CookSauce.id == item_id))
        item = result.scalar_one_or_none()

    if not item:
        await update.callback_query.edit_message_text(
            "Item not found.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("\u2190 Back", callback_data="cook:sauce")]]),
        )
        return

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("\u2713 Yes, delete", callback_data=f"cook:sc:dd:{item_id}"),
            InlineKeyboardButton("\u2190 Cancel", callback_data="cook:sc:del"),
        ]
    ])
    await update.callback_query.edit_message_text(
        f"\U0001f5d1 Delete <b>{e(item.name)}</b>?",
        reply_markup=keyboard,
        parse_mode="HTML",
    )


async def _delete_sauce(update, user_id, item_id):
    async with async_session_factory() as db:
        await db.execute(
            delete(CookSauce).where(CookSauce.id == item_id, CookSauce.user_id == user_id)
        )
        await db.commit()
    await _show_sauces(update, user_id)


async def _add_sauce_suggestion(update, user_id, idx):
    if idx < 0 or idx >= len(SAUCE_SUGGESTIONS):
        return
    name = SAUCE_SUGGESTIONS[idx]
    async with async_session_factory() as db:
        existing = await db.execute(
            select(CookSauce).where(CookSauce.user_id == user_id, func.lower(CookSauce.name) == name.lower())
        )
        if not existing.scalar_one_or_none():
            db.add(CookSauce(user_id=user_id, name=name))
            await db.commit()
    await _show_sauce_add_menu(update, user_id)


# ============================================================
# Equipment CRUD Actions
# ============================================================

async def _show_equip_delete_confirm(update, item_id):
    async with async_session_factory() as db:
        result = await db.execute(select(CookEquipment).where(CookEquipment.id == item_id))
        item = result.scalar_one_or_none()

    if not item:
        await update.callback_query.edit_message_text(
            "Item not found.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("\u2190 Back", callback_data="cook:equip")]]),
        )
        return

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("\u2713 Yes, delete", callback_data=f"cook:eq:dd:{item_id}"),
            InlineKeyboardButton("\u2190 Cancel", callback_data="cook:eq:del"),
        ]
    ])
    await update.callback_query.edit_message_text(
        f"\U0001f5d1 Delete <b>{e(item.name)}</b>?",
        reply_markup=keyboard,
        parse_mode="HTML",
    )


async def _delete_equipment(update, user_id, item_id):
    async with async_session_factory() as db:
        await db.execute(
            delete(CookEquipment).where(CookEquipment.id == item_id, CookEquipment.user_id == user_id)
        )
        await db.commit()
    await _show_equipment(update, user_id)


async def _add_equip_suggestion(update, user_id, idx):
    if idx < 0 or idx >= len(EQUIPMENT_SUGGESTIONS):
        return
    name = EQUIPMENT_SUGGESTIONS[idx]
    async with async_session_factory() as db:
        existing = await db.execute(
            select(CookEquipment).where(CookEquipment.user_id == user_id, func.lower(CookEquipment.name) == name.lower())
        )
        if not existing.scalar_one_or_none():
            db.add(CookEquipment(user_id=user_id, name=name))
            await db.commit()
    await _show_equip_add_menu(update, user_id)


# ============================================================
# Add Raw Material Conversation
# ============================================================

@require_auth
async def raw_add_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "+ <b>Add Ingredient</b>\n\nType the ingredient name:",
        reply_markup=InlineKeyboardMarkup([_RAW_CANCEL_ROW]),
        parse_mode="HTML",
    )
    return RAW_NAME


@require_auth
async def raw_add_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    name = update.message.text.strip()
    if not name:
        await update.message.reply_text("Name cannot be empty. Try again:")
        return RAW_NAME
    context.user_data["cook_raw_name"] = name
    await update.message.reply_text(
        f"How much <b>{e(name)}</b> do you have? (number only, e.g. 500, 1.5)\n<i>You'll pick the unit next.</i>",
        reply_markup=InlineKeyboardMarkup([_RAW_CANCEL_ROW]),
        parse_mode="HTML",
    )
    return RAW_QTY


@require_auth
async def raw_add_qty(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    try:
        qty = float(text)
        if qty < 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text(
            "Please enter a valid non-negative number:",
            reply_markup=InlineKeyboardMarkup([_RAW_CANCEL_ROW]),
        )
        return RAW_QTY

    context.user_data["cook_raw_qty"] = qty

    buttons = []
    row = []
    for u in UNIT_OPTIONS:
        row.append(InlineKeyboardButton(u, callback_data=f"cook:raw:u:{u}"))
        if len(row) == 4:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton("Custom\u2026", callback_data="cook:raw:u:_custom")])
    buttons.append(_RAW_CANCEL_ROW)

    await update.message.reply_text("What unit?", reply_markup=InlineKeyboardMarkup(buttons))
    return RAW_UNIT


@require_auth
async def raw_add_unit_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    unit = query.data.split(":")[3]

    if unit == "_custom":
        await query.edit_message_text(
            "Type a custom unit:",
            reply_markup=InlineKeyboardMarkup([_RAW_CANCEL_ROW]),
        )
        return RAW_UNIT_CUSTOM

    return await _save_raw_material(update, context, unit)


@require_auth
async def raw_add_unit_custom(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    unit = update.message.text.strip()
    if not unit:
        await update.message.reply_text(
            "Unit cannot be empty. Try again:",
            reply_markup=InlineKeyboardMarkup([_RAW_CANCEL_ROW]),
        )
        return RAW_UNIT_CUSTOM
    return await _save_raw_material(update, context, unit)


@require_auth
async def raw_add_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data.pop("cook_raw_name", None)
    context.user_data.pop("cook_raw_qty", None)
    await query.edit_message_text("\u2715 Cancelled.")
    user = context.user_data["current_user"]
    await _show_raw_materials(update, user.id, send_new=True)
    return ConversationHandler.END


async def _save_raw_material(update, context, unit):
    user = context.user_data["current_user"]
    name = context.user_data.pop("cook_raw_name")
    qty = context.user_data.pop("cook_raw_qty")

    async with async_session_factory() as db:
        existing = await db.execute(
            select(CookRawMaterial).where(
                CookRawMaterial.user_id == user.id,
                func.lower(CookRawMaterial.name) == name.lower(),
            )
        )
        if existing.scalar_one_or_none():
            msg = f"\u26a0\ufe0f <b>{e(name)}</b> already exists. Use Edit to change quantity."
            if update.callback_query:
                await update.callback_query.edit_message_text(msg, parse_mode="HTML")
            else:
                await update.message.reply_text(msg, parse_mode="HTML")
            return ConversationHandler.END

        db.add(CookRawMaterial(user_id=user.id, name=name, quantity=qty, unit=unit))
        await db.commit()

    await _show_raw_materials(update, user.id, send_new=True)
    return ConversationHandler.END


# ============================================================
# Add Sauce Conversation (entry: cook:sc:custom)
# ============================================================

@require_auth
async def sauce_add_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "+ <b>Custom Sauce</b>\n\nType the sauce/condiment name:",
        reply_markup=InlineKeyboardMarkup([_SC_CANCEL_ROW]),
        parse_mode="HTML",
    )
    return SAUCE_NAME


@require_auth
async def sauce_add_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    name = update.message.text.strip()
    if not name:
        await update.message.reply_text(
            "Name cannot be empty. Try again:",
            reply_markup=InlineKeyboardMarkup([_SC_CANCEL_ROW]),
        )
        return SAUCE_NAME

    user = context.user_data["current_user"]
    async with async_session_factory() as db:
        existing = await db.execute(
            select(CookSauce).where(CookSauce.user_id == user.id, func.lower(CookSauce.name) == name.lower())
        )
        if existing.scalar_one_or_none():
            await update.message.reply_text(f"\u26a0\ufe0f <b>{e(name)}</b> already exists.", parse_mode="HTML")
            await _show_sauces(update, user.id, send_new=True)
            return ConversationHandler.END

        db.add(CookSauce(user_id=user.id, name=name))
        await db.commit()

    await _show_sauces(update, user.id, send_new=True)
    return ConversationHandler.END


@require_auth
async def sauce_add_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("\u2715 Cancelled.")
    user = context.user_data["current_user"]
    await _show_sauces(update, user.id, send_new=True)
    return ConversationHandler.END


# ============================================================
# Add Equipment Conversation (entry: cook:eq:custom)
# ============================================================

@require_auth
async def equip_add_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "+ <b>Custom Equipment</b>\n\nType the equipment name:",
        reply_markup=InlineKeyboardMarkup([_EQ_CANCEL_ROW]),
        parse_mode="HTML",
    )
    return EQUIP_NAME


@require_auth
async def equip_add_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    name = update.message.text.strip()
    if not name:
        await update.message.reply_text(
            "Name cannot be empty. Try again:",
            reply_markup=InlineKeyboardMarkup([_EQ_CANCEL_ROW]),
        )
        return EQUIP_NAME

    user = context.user_data["current_user"]
    async with async_session_factory() as db:
        existing = await db.execute(
            select(CookEquipment).where(CookEquipment.user_id == user.id, func.lower(CookEquipment.name) == name.lower())
        )
        if existing.scalar_one_or_none():
            await update.message.reply_text(f"\u26a0\ufe0f <b>{e(name)}</b> already exists.", parse_mode="HTML")
            await _show_equipment(update, user.id, send_new=True)
            return ConversationHandler.END

        db.add(CookEquipment(user_id=user.id, name=name))
        await db.commit()

    await _show_equipment(update, user.id, send_new=True)
    return ConversationHandler.END


@require_auth
async def equip_add_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("\u2715 Cancelled.")
    user = context.user_data["current_user"]
    await _show_equipment(update, user.id, send_new=True)
    return ConversationHandler.END


# ============================================================
# Conversation Timeout Handler
# ============================================================

async def _cook_conv_timeout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Notify user when a cook inventory conversation times out."""
    try:
        if update.callback_query:
            await update.effective_chat.send_message(
                "\u23f1 Session timed out. Use /cook to return to the menu."
            )
        elif update.message:
            await update.message.reply_text(
                "\u23f1 Session timed out. Use /cook to return to the menu."
            )
    except Exception:
        pass
    return ConversationHandler.END


# ============================================================
# Conversation Handler Factories
# ============================================================

def get_raw_add_handler() -> ConversationHandler:
    cancel = CallbackQueryHandler(raw_add_cancel, pattern=r"^cook:raw:cancel$")
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(raw_add_start, pattern=r"^cook:raw:add$")],
        states={
            ConversationHandler.TIMEOUT: [
                MessageHandler(filters.ALL, _cook_conv_timeout),
                CallbackQueryHandler(_cook_conv_timeout),
            ],
            RAW_NAME:        [MessageHandler(filters.TEXT & ~filters.COMMAND, raw_add_name), cancel],
            RAW_QTY:         [MessageHandler(filters.TEXT & ~filters.COMMAND, raw_add_qty), cancel],
            RAW_UNIT:        [CallbackQueryHandler(raw_add_unit_callback, pattern=r"^cook:raw:u:"), cancel],
            RAW_UNIT_CUSTOM: [MessageHandler(filters.TEXT & ~filters.COMMAND, raw_add_unit_custom), cancel],
        },
        fallbacks=[cancel],
        conversation_timeout=120,
        per_message=False,
    )


def get_sauce_add_handler() -> ConversationHandler:
    cancel = CallbackQueryHandler(sauce_add_cancel, pattern=r"^cook:sc:cancel$")
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(sauce_add_start, pattern=r"^cook:sc:custom$")],
        states={
            ConversationHandler.TIMEOUT: [
                MessageHandler(filters.ALL, _cook_conv_timeout),
                CallbackQueryHandler(_cook_conv_timeout),
            ],
            SAUCE_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, sauce_add_name), cancel],
        },
        fallbacks=[cancel],
        conversation_timeout=120,
        per_message=False,
    )


def get_equip_add_handler() -> ConversationHandler:
    cancel = CallbackQueryHandler(equip_add_cancel, pattern=r"^cook:eq:cancel$")
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(equip_add_start, pattern=r"^cook:eq:custom$")],
        states={
            ConversationHandler.TIMEOUT: [
                MessageHandler(filters.ALL, _cook_conv_timeout),
                CallbackQueryHandler(_cook_conv_timeout),
            ],
            EQUIP_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, equip_add_name), cancel],
        },
        fallbacks=[cancel],
        conversation_timeout=120,
        per_message=False,
    )
