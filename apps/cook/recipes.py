"""Recipe management handlers for the Cook app.

Includes: manual recipe add, cookbook browsing, recipe detail with inventory
cross-reference, serving adjustment, LLM import/generate, and "What Can I Cook?".
"""

import logging
import math
import re

from sqlalchemy import select, delete, func
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes,
    CallbackQueryHandler,
    ConversationHandler,
    MessageHandler,
    filters,
)

from core.auth import require_auth
from core.database import async_session_factory
from core.ui import e, BOX_TOP, BOX_MID, BOX_BOT, DOT, header, section
from apps.cook.models import (
    CookRecipe, CookRecipeIngredient, CookRecipeEquipment,
    CookRawMaterial, CookSauce, CookEquipment,
)
from apps.cook.constants import CUISINE_TYPES, TIME_OPTIONS
from apps.cook.llm import is_ai_enabled, parse_recipe_from_text, generate_recipe

logger = logging.getLogger(__name__)

RECIPES_PER_PAGE = 10


# ============================================================
# Fuzzy Name Matching (no LLM needed for simple plural/stem)
# ============================================================

def _stem(s: str) -> str:
    """Strip common English plural suffixes for comparison."""
    if s.endswith("ies") and len(s) > 4:
        return s[:-3] + "y"
    if s.endswith("ves") and len(s) > 4:
        return s[:-3] + "f"
    if s.endswith("es") and len(s) > 3:
        return s[:-2]
    if s.endswith("s") and len(s) > 2:
        return s[:-1]
    return s


def _name_matches(a: str, b: str) -> bool:
    """Return True if ingredient names are likely the same item.

    Handles: exact match, one contains the other, singular/plural variations.
    Examples: egg/eggs, tomato/tomatoes, leaf/leaves, berry/berries.
    No LLM needed -- simple string rules cover the vast majority of cases.
    """
    a = a.lower().strip()
    b = b.lower().strip()
    if a == b:
        return True
    # Substring: "chicken breast" matches "chicken", "spring onion" matches "onion"
    if a in b or b in a:
        return True
    # Plural/stem normalization
    if _stem(a) == _stem(b):
        return True
    if _stem(a) == b or a == _stem(b):
        return True
    return False


def _find_in_raw(ing_name: str, user_raw: dict):
    """Find a raw material matching the ingredient name (fuzzy)."""
    norm = ing_name.lower().strip()
    if norm in user_raw:
        return user_raw[norm]
    for key, item in user_raw.items():
        if _name_matches(norm, key):
            return item
    return None


def _in_sauce_set(name: str, sauce_set: set) -> bool:
    """Check if a sauce name matches any entry in the set (fuzzy)."""
    norm = name.lower().strip()
    if norm in sauce_set:
        return True
    return any(_name_matches(norm, s) for s in sauce_set)


def _in_equip_set(name: str, equip_set: set) -> bool:
    """Check if equipment name matches any entry in the set (fuzzy)."""
    norm = name.lower().strip()
    if norm in equip_set:
        return True
    return any(_name_matches(norm, s) for s in equip_set)

# Manual add conversation states
ADD_PHOTO = 0
ADD_NAME = 1
ADD_SERVINGS = 2
ADD_INGREDIENTS = 3
ADD_EQUIPMENT = 4
ADD_STEPS = 5
ADD_CONFIRM = 6

# AI import states
IMP_INPUT = 10
IMP_REVIEW = 11

# AI generate states
GEN_CUISINE = 20
GEN_SERVINGS = 21
GEN_TIME = 22
GEN_SPICY = 23
GEN_DIET = 24
GEN_REVIEW = 25


# ============================================================
# Ingredient Parsing (for manual entry)
# ============================================================

def parse_ingredient_line(line: str) -> dict:
    """Parse '200g chicken breast' -> {qty, unit, name}."""
    line = line.strip()
    if not line:
        return None

    # Try matching: optional qty + optional unit + name
    m = re.match(r'^(\d+(?:\.\d+)?)\s*(g|kg|ml|L|pcs|cups?|tbsp|tsp|bunch|packet|can|bottle)?\s+(.+)$', line, re.IGNORECASE)
    if m:
        return {
            "quantity": float(m.group(1)),
            "unit": m.group(2) or "pcs",
            "name": m.group(3).strip(),
            "is_sauce": False,
        }

    # Try: "qty name" without unit
    m = re.match(r'^(\d+(?:\.\d+)?)\s+(.+)$', line)
    if m:
        return {
            "quantity": float(m.group(1)),
            "unit": "pcs",
            "name": m.group(2).strip(),
            "is_sauce": False,
        }

    # Just a name
    return {"quantity": None, "unit": None, "name": line, "is_sauce": False}


# ============================================================
# Unit Normalization for Matching
# ============================================================

def normalize_to_base(qty: float, unit: str) -> tuple[float, str] | None:
    """Convert to base unit (g or ml) for comparison. Returns None if unknown."""
    if not unit:
        return None
    u = unit.lower().strip()
    if u == "kg":
        return qty * 1000, "g"
    if u == "g":
        return qty, "g"
    if u == "l":
        return qty * 1000, "ml"
    if u == "ml":
        return qty, "ml"
    return qty, u


# ============================================================
# Cookbook List
# ============================================================

@require_auth
async def cookbook_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show cookbook list (called from main menu callback router in handlers.py)."""
    user = context.user_data["current_user"]
    await _show_cookbook(update, user.id)


async def _show_cookbook(update, user_id, page=0, send_new=False):
    async with async_session_factory() as db:
        count_result = await db.execute(
            select(func.count()).select_from(CookRecipe).where(CookRecipe.user_id == user_id)
        )
        total = count_result.scalar()

        result = await db.execute(
            select(CookRecipe)
            .where(CookRecipe.user_id == user_id)
            .order_by(CookRecipe.name)
            .offset(page * RECIPES_PER_PAGE)
            .limit(RECIPES_PER_PAGE)
        )
        recipes = result.scalars().all()

    total_pages = max(1, math.ceil(total / RECIPES_PER_PAGE))

    if not recipes and page == 0:
        lines = [header("\U0001f4d6", "Cookbook"), "", "No recipes yet. Add one manually or use AI."]
    else:
        lines = [header("\U0001f4d6", f"Cookbook ({total} recipes)"), ""]
        item_lines = []
        for r in recipes:
            parts = [e(r.name)]
            if r.cuisine:
                parts.append(e(r.cuisine))
            parts.append(f"{r.servings} servings")
            item_lines.append(f" {DOT} ".join(parts))
        lines.extend(section("Recipes", item_lines))

    if total_pages > 1:
        lines.append(f"\nPage {page + 1}/{total_pages}")

    rows = []
    # Recipe buttons (tap to view)
    if recipes:
        for r in recipes:
            rows.append([InlineKeyboardButton(f"\U0001f4d6 {r.name}", callback_data=f"cook:bk:v:{r.id}")])

    nav = []
    if total_pages > 1:
        if page > 0:
            nav.append(InlineKeyboardButton("\u2190 Prev", callback_data=f"cook:bk:p:{page - 1}"))
        if page < total_pages - 1:
            nav.append(InlineKeyboardButton("Next \u2192", callback_data=f"cook:bk:p:{page + 1}"))
    if nav:
        rows.append(nav)

    rows.append([
        InlineKeyboardButton("+ Add Recipe", callback_data="cook:bk:add"),
        InlineKeyboardButton("\u2190 Back", callback_data="cook:menu"),
    ])

    keyboard = InlineKeyboardMarkup(rows)
    text = "\n".join(lines)
    if send_new:
        await update.effective_chat.send_message(text=text, reply_markup=keyboard, parse_mode="HTML")
    else:
        await update.callback_query.edit_message_text(text=text, reply_markup=keyboard, parse_mode="HTML")


# ============================================================
# Recipe Detail with Inventory Cross-Reference
# ============================================================

async def _show_recipe_detail(update, user_id, recipe_id, servings_override=None):
    async with async_session_factory() as db:
        result = await db.execute(select(CookRecipe).where(CookRecipe.id == recipe_id))
        recipe = result.scalar_one_or_none()
        if not recipe:
            await update.callback_query.edit_message_text("Recipe not found.")
            return

        ing_result = await db.execute(
            select(CookRecipeIngredient)
            .where(CookRecipeIngredient.recipe_id == recipe_id)
            .order_by(CookRecipeIngredient.sort_order)
        )
        ingredients = ing_result.scalars().all()

        eq_result = await db.execute(
            select(CookRecipeEquipment).where(CookRecipeEquipment.recipe_id == recipe_id)
        )
        equipment = eq_result.scalars().all()

        # User inventory
        raw_result = await db.execute(
            select(CookRawMaterial).where(CookRawMaterial.user_id == user_id)
        )
        user_raw = {item.name.lower(): item for item in raw_result.scalars().all()}

        sauce_result = await db.execute(
            select(CookSauce).where(CookSauce.user_id == user_id)
        )
        user_sauces = {item.name.lower() for item in sauce_result.scalars().all()}

        equip_result = await db.execute(
            select(CookEquipment).where(CookEquipment.user_id == user_id)
        )
        user_equip = {item.name.lower() for item in equip_result.scalars().all()}

    display_servings = servings_override or recipe.servings
    scale = display_servings / recipe.servings if recipe.servings else 1

    # Header
    parts = [f"\U0001f4d6 <b>{e(recipe.name)}</b>"]
    meta = []
    if recipe.cuisine:
        meta.append(e(recipe.cuisine))
    meta.append(f"\U0001f37d {display_servings} servings")
    if recipe.cook_time_minutes:
        meta.append(f"\u23f1 {recipe.cook_time_minutes} min")
    parts.append(" {0} ".format(DOT).join(meta))
    parts.append("")

    # Ingredients with cross-reference
    ing_lines = []
    matched_count = 0
    for ing in ingredients:
        scaled_qty = ing.quantity * scale if ing.quantity else None

        if ing.is_sauce:
            has = _in_sauce_set(ing.name, user_sauces)
            if has:
                matched_count += 1
                mark = "\u2705"
                suffix = " \u2713"
            else:
                mark = "\u274c"
                suffix = " (missing)"
            qty_text = e(ing.name)
            if scaled_qty:
                qty_str = f"{scaled_qty:g}"
                qty_text = f"{qty_str}{e(ing.unit or '')} {e(ing.name)}"
            ing_lines.append(f"{mark} {qty_text}{suffix}")
        else:
            raw = _find_in_raw(ing.name, user_raw)
            if raw:
                # Quantity check
                if scaled_qty is not None:
                    needed_norm = normalize_to_base(scaled_qty, ing.unit)
                    have_norm = normalize_to_base(raw.quantity, raw.unit)
                    if needed_norm and have_norm and needed_norm[1] == have_norm[1]:
                        has_enough = have_norm[0] >= needed_norm[0]
                    elif ing.unit and raw.unit and ing.unit.lower() == raw.unit.lower():
                        has_enough = raw.quantity >= scaled_qty
                    else:
                        has_enough = True  # Can't compare units, assume ok
                else:
                    has_enough = True  # No qty required

                if has_enough:
                    matched_count += 1
                    mark = "\u2705"
                    have_str = f"{raw.quantity:g} {e(raw.unit)}"
                    qty_text = ""
                    if scaled_qty:
                        qty_str = f"{scaled_qty:g}"
                        qty_text = f"{qty_str}{e(ing.unit or '')} "
                    ing_lines.append(f"{mark} {qty_text}{e(ing.name)} (have: {have_str})")
                else:
                    mark = "\U0001f7e1"  # 🟡
                    have_str = f"{raw.quantity:g} {e(raw.unit)}"
                    qty_str = f"{scaled_qty:g}"
                    ing_lines.append(f"{mark} {qty_str}{e(ing.unit or '')} {e(ing.name)} (have: {have_str}, need more)")
            else:
                mark = "\u274c"
                qty_text = ""
                if scaled_qty:
                    qty_str = f"{scaled_qty:g}"
                    qty_text = f"{qty_str}{e(ing.unit or '')} "
                ing_lines.append(f"{mark} {qty_text}{e(ing.name)} (missing)")

    if ing_lines:
        parts.extend(section("Ingredients", ing_lines))
    parts.append(f"{matched_count}/{len(ingredients)} ingredients available")
    parts.append("")

    # Equipment
    if equipment:
        eq_lines = []
        for eq in equipment:
            has = _in_equip_set(eq.name, user_equip)
            mark = "\u2705" if has else "\u274c"
            eq_lines.append(f"{mark} {e(eq.name)}")
        parts.extend(section("Equipment", eq_lines))
        parts.append("")

    # Steps
    if recipe.steps:
        step_lines = []
        for i, step in enumerate(recipe.steps.split("\n"), 1):
            step = step.strip()
            if step:
                step_lines.append(f"{i}. {e(step)}")
        if step_lines:
            parts.extend(section("Steps", step_lines))

    rows = [
        [
            InlineKeyboardButton("\U0001f37d Adjust Servings", callback_data=f"cook:bk:s:{recipe_id}"),
            InlineKeyboardButton("\U0001f5d1 Delete", callback_data=f"cook:bk:del:{recipe_id}"),
        ],
        [InlineKeyboardButton("\u2190 Back", callback_data="cook:book")],
    ]

    # Send photo if available, otherwise text
    if recipe.photo_file_id and not servings_override:
        try:
            await update.callback_query.message.delete()
        except Exception:
            pass
        await update.effective_chat.send_photo(
            photo=recipe.photo_file_id,
            caption="\n".join(parts),
            reply_markup=InlineKeyboardMarkup(rows),
            parse_mode="HTML",
        )
    else:
        await update.callback_query.edit_message_text(
            text="\n".join(parts),
            reply_markup=InlineKeyboardMarkup(rows),
            parse_mode="HTML",
        )


async def _show_serving_picker(update, recipe_id):
    buttons = []
    row = []
    for n in [1, 2, 3, 4, 5, 6, 8, 10]:
        row.append(InlineKeyboardButton(str(n), callback_data=f"cook:bk:sv:{recipe_id}:{n}"))
        if len(row) == 4:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton("\u2190 Back", callback_data=f"cook:bk:v:{recipe_id}")])

    await update.callback_query.edit_message_text(
        "\U0001f37d <b>Adjust Servings</b>\n\nSelect number of servings:",
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode="HTML",
    )


async def _delete_recipe_confirm(update, recipe_id):
    async with async_session_factory() as db:
        result = await db.execute(select(CookRecipe).where(CookRecipe.id == recipe_id))
        recipe = result.scalar_one_or_none()

    if not recipe:
        await update.callback_query.edit_message_text("Recipe not found.")
        return

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("\u2713 Yes, delete", callback_data=f"cook:bk:dd:{recipe_id}"),
            InlineKeyboardButton("\u2190 Cancel", callback_data=f"cook:bk:v:{recipe_id}"),
        ]
    ])
    await update.callback_query.edit_message_text(
        f"\U0001f5d1 Delete <b>{e(recipe.name)}</b>?",
        reply_markup=keyboard,
        parse_mode="HTML",
    )


async def _delete_recipe(update, user_id, recipe_id):
    async with async_session_factory() as db:
        await db.execute(
            delete(CookRecipe).where(CookRecipe.id == recipe_id, CookRecipe.user_id == user_id)
        )
        await db.commit()
    await _show_cookbook(update, user_id)


# ============================================================
# "What Can I Cook?" Matching Engine
# ============================================================

async def _show_what_can_i_cook(update, user_id):
    async with async_session_factory() as db:
        # Get all user recipes
        recipe_result = await db.execute(
            select(CookRecipe).where(CookRecipe.user_id == user_id).order_by(CookRecipe.name)
        )
        recipes = recipe_result.scalars().all()

        if not recipes:
            await update.callback_query.edit_message_text(
                header("\U0001f50d", "What Can I Cook?") + "\n\nNo recipes yet. Add some first!",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("\u2190 Back", callback_data="cook:menu")]
                ]),
                parse_mode="HTML",
            )
            return

        # User inventory
        raw_result = await db.execute(
            select(CookRawMaterial).where(CookRawMaterial.user_id == user_id)
        )
        user_raw = {item.name.lower(): item for item in raw_result.scalars().all()}

        sauce_result = await db.execute(
            select(CookSauce).where(CookSauce.user_id == user_id)
        )
        user_sauces = {item.name.lower() for item in sauce_result.scalars().all()}

        equip_result = await db.execute(
            select(CookEquipment).where(CookEquipment.user_id == user_id)
        )
        user_equip = {item.name.lower() for item in equip_result.scalars().all()}

        ready = []
        almost = []

        for recipe in recipes:
            ing_result = await db.execute(
                select(CookRecipeIngredient).where(CookRecipeIngredient.recipe_id == recipe.id)
            )
            ingredients = ing_result.scalars().all()

            eq_result = await db.execute(
                select(CookRecipeEquipment).where(CookRecipeEquipment.recipe_id == recipe.id)
            )
            equipment = eq_result.scalars().all()

            missing = []

            for ing in ingredients:
                if ing.is_sauce:
                    if not _in_sauce_set(ing.name, user_sauces):
                        missing.append(ing.name)
                else:
                    raw = _find_in_raw(ing.name, user_raw)
                    if not raw:
                        missing.append(ing.name)
                    elif ing.quantity is not None:
                        needed_norm = normalize_to_base(ing.quantity, ing.unit)
                        have_norm = normalize_to_base(raw.quantity, raw.unit)
                        if needed_norm and have_norm and needed_norm[1] == have_norm[1]:
                            if have_norm[0] < needed_norm[0]:
                                missing.append(ing.name)
                        elif ing.unit and raw.unit and ing.unit.lower() == raw.unit.lower():
                            if raw.quantity < ing.quantity:
                                missing.append(ing.name)

            for eq in equipment:
                if not _in_equip_set(eq.name, user_equip):
                    missing.append(eq.name)

            if not missing:
                ready.append(recipe)
            elif len(missing) <= 4:
                almost.append((recipe, missing))

    lines = [header("\U0001f50d", "What Can I Cook?"), ""]

    rows = []

    if ready:
        item_lines = []
        for r in ready:
            item_lines.append(f"\u2705 {e(r.name)} {DOT} {r.servings} servings")
        lines.extend(section(f"Ready to Cook ({len(ready)})", item_lines))
        lines.append("")
        for r in ready:
            rows.append([InlineKeyboardButton(f"\u2705 {r.name}", callback_data=f"cook:wc:v:{r.id}")])

    if almost:
        item_lines = []
        for r, miss in almost:
            miss_str = ", ".join(miss[:4])
            item_lines.append(f"\U0001f7e1 {e(r.name)} {DOT} missing: {e(miss_str)} ({len(miss)})")
        lines.extend(section(f"Almost There ({len(almost)})", item_lines))
        lines.append("")
        for r, _ in almost:
            rows.append([InlineKeyboardButton(f"\U0001f7e1 {r.name}", callback_data=f"cook:wc:v:{r.id}")])

    if not ready and not almost:
        lines.append("No recipes match your current inventory.")

    rows.append([InlineKeyboardButton("\u2190 Back", callback_data="cook:menu")])

    await update.callback_query.edit_message_text(
        text="\n".join(lines),
        reply_markup=InlineKeyboardMarkup(rows),
        parse_mode="HTML",
    )


# ============================================================
# Recipe Callback Router
# ============================================================

@require_auth
async def recipe_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data
    user = context.user_data["current_user"]

    if data == "cook:book":
        await _show_cookbook(update, user.id)
    elif data.startswith("cook:bk:p:"):
        page = int(data.split(":")[3])
        await _show_cookbook(update, user.id, page)
    elif data.startswith("cook:bk:v:"):
        recipe_id = int(data.split(":")[3])
        await _show_recipe_detail(update, user.id, recipe_id)
    elif data.startswith("cook:bk:s:"):
        recipe_id = int(data.split(":")[3])
        await _show_serving_picker(update, recipe_id)
    elif data.startswith("cook:bk:sv:"):
        parts = data.split(":")
        recipe_id = int(parts[3])
        servings = int(parts[4])
        await _show_recipe_detail(update, user.id, recipe_id, servings_override=servings)
    elif data.startswith("cook:bk:del:"):
        recipe_id = int(data.split(":")[3])
        await _delete_recipe_confirm(update, recipe_id)
    elif data.startswith("cook:bk:dd:"):
        recipe_id = int(data.split(":")[3])
        await _delete_recipe(update, user.id, recipe_id)
    elif data == "cook:match":
        await _show_what_can_i_cook(update, user.id)
    elif data.startswith("cook:wc:v:"):
        recipe_id = int(data.split(":")[3])
        await _show_recipe_detail(update, user.id, recipe_id)


# ============================================================
# Manual Recipe Add Conversation (7 states)
# ============================================================

@require_auth
async def manual_add_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("Skip (no photo)", callback_data="cook:bk:nophoto")]
    ])
    await query.edit_message_text(
        "+ <b>Add Recipe</b>\n\nSend a photo of the dish, or skip:",
        reply_markup=keyboard,
        parse_mode="HTML",
    )
    return ADD_PHOTO


@require_auth
async def manual_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message and update.message.photo:
        context.user_data["cook_recipe_photo"] = update.message.photo[-1].file_id
    await _ask_name(update, context)
    return ADD_NAME


@require_auth
async def manual_skip_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data["cook_recipe_photo"] = None
    await query.edit_message_text("What's the recipe name?")
    return ADD_NAME


async def _ask_name(update, context):
    if update.callback_query:
        await update.callback_query.edit_message_text("What's the recipe name?")
    else:
        await update.message.reply_text("What's the recipe name?")


@require_auth
async def manual_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    name = update.message.text.strip()
    if not name:
        await update.message.reply_text("Name cannot be empty. Try again:")
        return ADD_NAME
    context.user_data["cook_recipe_name"] = name
    await update.message.reply_text("How many servings does this make? (e.g. 2)")
    return ADD_SERVINGS


@require_auth
async def manual_servings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    try:
        servings = int(text)
        if servings < 1:
            raise ValueError
    except ValueError:
        await update.message.reply_text("Please enter a positive number:")
        return ADD_SERVINGS

    context.user_data["cook_recipe_servings"] = servings
    await update.message.reply_text(
        "List ingredients, one per line.\n"
        "Format: <code>200g chicken breast</code>\n"
        "Or just the name for 'to taste' items.\n\n"
        "Send all ingredients in one message:",
        parse_mode="HTML",
    )
    return ADD_INGREDIENTS


@require_auth
async def manual_ingredients(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    lines = update.message.text.strip().split("\n")
    parsed = []
    for line in lines:
        ing = parse_ingredient_line(line)
        if ing:
            parsed.append(ing)

    if not parsed:
        await update.message.reply_text("Couldn't parse any ingredients. Try again:")
        return ADD_INGREDIENTS

    context.user_data["cook_recipe_ingredients"] = parsed

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("Skip (no equipment)", callback_data="cook:bk:noeq")]
    ])
    await update.message.reply_text(
        "List equipment needed, comma-separated.\n"
        "e.g. <code>Wok, Rice cooker</code>\n\n"
        "Or skip:",
        reply_markup=keyboard,
        parse_mode="HTML",
    )
    return ADD_EQUIPMENT


@require_auth
async def manual_equipment_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    equip = [eq.strip() for eq in text.split(",") if eq.strip()]
    context.user_data["cook_recipe_equipment"] = equip
    await update.message.reply_text(
        "List the cooking steps, one per line.\n"
        "Send all steps in one message:"
    )
    return ADD_STEPS


@require_auth
async def manual_skip_equipment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data["cook_recipe_equipment"] = []
    await query.edit_message_text(
        "List the cooking steps, one per line.\n"
        "Send all steps in one message:"
    )
    return ADD_STEPS


@require_auth
async def manual_steps(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    steps = update.message.text.strip()
    if not steps:
        await update.message.reply_text("Steps cannot be empty. Try again:")
        return ADD_STEPS

    # Clean step numbers if present
    cleaned = []
    for line in steps.split("\n"):
        line = line.strip()
        if not line:
            continue
        line = re.sub(r'^\d+[\.\)]\s*', '', line)
        cleaned.append(line)

    context.user_data["cook_recipe_steps"] = "\n".join(cleaned)

    # Show confirmation
    return await _show_recipe_confirm(update, context)


async def _show_recipe_confirm(update, context):
    data = context.user_data
    name = data["cook_recipe_name"]
    servings = data["cook_recipe_servings"]
    ingredients = data["cook_recipe_ingredients"]
    equipment = data.get("cook_recipe_equipment", [])
    steps = data["cook_recipe_steps"]

    lines = [header("\U0001f4d6", f"Review: {name}"), ""]
    lines.append(f"\U0001f37d {servings} servings")
    lines.append("")

    ing_lines = []
    for ing in ingredients:
        if ing["quantity"]:
            qty_str = f"{ing['quantity']:g}"
            ing_lines.append(f"{qty_str}{ing['unit'] or ''} {ing['name']}")
        else:
            ing_lines.append(ing["name"])
    lines.extend(section("Ingredients", ing_lines))

    if equipment:
        lines.append("")
        lines.extend(section("Equipment", equipment))

    step_list = steps.split("\n")
    lines.append("")
    step_lines = [f"{i+1}. {s}" for i, s in enumerate(step_list) if s.strip()]
    lines.extend(section("Steps", step_lines))

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("\u2705 Save", callback_data="cook:bk:save"),
            InlineKeyboardButton("\u274c Cancel", callback_data="cook:bk:cancel"),
        ]
    ])

    target = update.message if update.message else update.callback_query.message
    await target.reply_text(
        "\n".join(lines),
        reply_markup=keyboard,
        parse_mode="HTML",
    )
    return ADD_CONFIRM


@require_auth
async def manual_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == "cook:bk:cancel":
        _clear_recipe_data(context)
        await query.edit_message_text("\u274c Recipe cancelled.")
        return ConversationHandler.END

    user = context.user_data["current_user"]
    data = context.user_data

    async with async_session_factory() as db:
        recipe = CookRecipe(
            user_id=user.id,
            name=data["cook_recipe_name"],
            photo_file_id=data.get("cook_recipe_photo"),
            servings=data["cook_recipe_servings"],
            steps=data["cook_recipe_steps"],
            source="manual",
        )
        db.add(recipe)
        await db.flush()

        for i, ing in enumerate(data["cook_recipe_ingredients"]):
            db.add(CookRecipeIngredient(
                recipe_id=recipe.id,
                name=ing["name"],
                quantity=ing.get("quantity"),
                unit=ing.get("unit"),
                is_sauce=ing.get("is_sauce", False),
                sort_order=i,
            ))

        for eq_name in data.get("cook_recipe_equipment", []):
            db.add(CookRecipeEquipment(recipe_id=recipe.id, name=eq_name))

        await db.commit()

    saved_name = data.get("cook_recipe_name", "")
    _clear_recipe_data(context)
    await query.edit_message_text(f"\u2705 <b>{e(saved_name)}</b> saved!")
    await _show_cookbook(update, user.id, send_new=True)
    return ConversationHandler.END


def _clear_recipe_data(context):
    for key in list(context.user_data.keys()):
        if key.startswith("cook_recipe_"):
            del context.user_data[key]


# ============================================================
# AI: Import Recipe from Text
# ============================================================

@require_auth
async def import_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "\U0001f4dd <b>Import Recipe</b>\n\n"
        "Paste the recipe text below.\n"
        "I'll structure it for you using AI.",
        parse_mode="HTML",
    )
    return IMP_INPUT


@require_auth
async def import_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw_text = update.message.text.strip()
    if not raw_text:
        await update.message.reply_text("Please paste some recipe text:")
        return IMP_INPUT

    user = context.user_data["current_user"]
    await update.message.reply_text("\u23f3 Parsing recipe with AI...")

    result = await parse_recipe_from_text(user.id, raw_text)

    if result and result.get("error") == "rate_limited":
        await update.message.reply_text(
            "\u26a0\ufe0f Daily AI limit reached (20/day). Try again tomorrow."
        )
        return ConversationHandler.END

    if not result:
        await update.message.reply_text(
            "\u274c Failed to parse recipe. Please try again or add manually."
        )
        return ConversationHandler.END

    context.user_data["cook_ai_recipe"] = result
    return await _show_ai_review(update, context, result, source="text")


@require_auth
async def import_review(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == "cook:ai:discard":
        _clear_ai_data(context)
        await query.edit_message_text("\u274c Recipe discarded.")
        return ConversationHandler.END

    if query.data == "cook:ai:save":
        return await _save_ai_recipe(update, context, source="text")

    return IMP_REVIEW


# ============================================================
# AI: Generate Recipe
# ============================================================

@require_auth
async def generate_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    user = context.user_data["current_user"]

    # Check minimum inventory before allowing recipe generation
    async with async_session_factory() as db:
        raw_count = (await db.execute(
            select(func.count()).select_from(CookRawMaterial).where(CookRawMaterial.user_id == user.id)
        )).scalar()
        sauce_count = (await db.execute(
            select(func.count()).select_from(CookSauce).where(CookSauce.user_id == user.id)
        )).scalar()
        equip_count = (await db.execute(
            select(func.count()).select_from(CookEquipment).where(CookEquipment.user_id == user.id)
        )).scalar()

    missing = []
    if raw_count < 2:
        missing.append(f"\U0001f96c Raw Materials \u2014 {raw_count}/2 added")
    if sauce_count < 2:
        missing.append(f"\U0001F9C2 Sauces \u2014 {sauce_count}/2 added")
    if equip_count < 2:
        missing.append(f"\U0001f373 Equipment \u2014 {equip_count}/2 added")

    if missing:
        lines = [
            "\U0001f916 <b>Generate Recipe</b>",
            "",
            "To generate a recipe tailored to your kitchen, add at least:",
            "",
        ]
        lines.extend(f"\u2022 {m}" for m in missing)
        lines += [
            "",
            "Head back and fill in your kitchen first \u2014 the AI uses your actual inventory to suggest recipes you can cook right now.",
        ]
        await query.edit_message_text(
            "\n".join(lines),
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("\u2190 Back", callback_data="cook:menu")]
            ]),
            parse_mode="HTML",
        )
        return ConversationHandler.END

    buttons = []
    row = []
    for cuisine in CUISINE_TYPES:
        row.append(InlineKeyboardButton(cuisine, callback_data=f"cook:gen:c:{cuisine}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    await query.edit_message_text(
        "\U0001f916 <b>Generate Recipe</b>\n\nPick a cuisine:",
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode="HTML",
    )
    return GEN_CUISINE


@require_auth
async def gen_cuisine(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    cuisine = query.data.split(":")[3]
    context.user_data["cook_gen_cuisine"] = cuisine
    await query.edit_message_text(f"How many servings? (e.g. 2)")
    return GEN_SERVINGS


@require_auth
async def gen_servings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    try:
        servings = int(text)
        if servings < 1:
            raise ValueError
    except ValueError:
        await update.message.reply_text("Please enter a positive number:")
        return GEN_SERVINGS

    context.user_data["cook_gen_servings"] = servings

    buttons = []
    row = []
    for label, mins in TIME_OPTIONS:
        row.append(InlineKeyboardButton(label, callback_data=f"cook:gen:t:{mins}"))
        if len(row) == 3:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    await update.message.reply_text(
        "Max cooking time?",
        reply_markup=InlineKeyboardMarkup(buttons),
    )
    return GEN_TIME


@require_auth
async def gen_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    minutes = int(query.data.split(":")[3])
    context.user_data["cook_gen_time"] = minutes

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Yes can be spicy", callback_data="cook:gen:sp:1"),
            InlineKeyboardButton("No spice please", callback_data="cook:gen:sp:0"),
        ]
    ])
    await query.edit_message_text("Spicy?", reply_markup=keyboard)
    return GEN_SPICY


@require_auth
async def gen_spicy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    spicy = query.data.split(":")[3] == "1"
    context.user_data["cook_gen_spicy"] = spicy

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("No restrictions (skip)", callback_data="cook:gen:diet:skip")]
    ])
    await query.edit_message_text(
        "Any dietary restrictions?\n"
        "e.g. <i>no pork</i>, <i>halal</i>, <i>vegetarian</i>\n\n"
        "Type them or skip:",
        reply_markup=keyboard,
        parse_mode="HTML",
    )
    return GEN_DIET


@require_auth
async def gen_diet_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    dietary = update.message.text.strip()
    context.user_data["cook_gen_diet"] = dietary
    return await _do_generate(update, context)


@require_auth
async def gen_diet_skip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data["cook_gen_diet"] = ""
    return await _do_generate(update, context)


async def _do_generate(update, context):
    user = context.user_data["current_user"]
    data = context.user_data

    # Build inventory context
    async with async_session_factory() as db:
        raw_result = await db.execute(
            select(CookRawMaterial).where(CookRawMaterial.user_id == user.id)
        )
        raw_items = raw_result.scalars().all()

        sauce_result = await db.execute(
            select(CookSauce).where(CookSauce.user_id == user.id)
        )
        sauces = sauce_result.scalars().all()

    inv_parts = []
    if raw_items:
        inv_parts.append("Ingredients: " + ", ".join(f"{i.name} ({i.quantity:g} {i.unit})" for i in raw_items))
    if sauces:
        inv_parts.append("Sauces: " + ", ".join(s.name for s in sauces))
    inventory_text = "\n".join(inv_parts) if inv_parts else "No inventory data available."

    target = update.message or update.callback_query.message
    await target.reply_text("\u23f3 Generating recipe with AI...")

    result = await generate_recipe(
        user_id=user.id,
        cuisine=data["cook_gen_cuisine"],
        servings=data["cook_gen_servings"],
        time_minutes=data["cook_gen_time"],
        spicy=data["cook_gen_spicy"],
        dietary=data.get("cook_gen_diet", ""),
        inventory_text=inventory_text,
    )

    if result and result.get("error") == "rate_limited":
        await target.reply_text(
            "\u26a0\ufe0f Daily AI limit reached (20/day). Try again tomorrow."
        )
        _clear_gen_data(context)
        return ConversationHandler.END

    if not result:
        await target.reply_text(
            "\u274c Failed to generate recipe. Please try again."
        )
        _clear_gen_data(context)
        return ConversationHandler.END

    context.user_data["cook_ai_recipe"] = result
    return await _show_ai_review(update, context, result, source="generated")


async def _show_ai_review(update, context, recipe_data, source="text"):
    name = recipe_data.get("name", "Untitled")
    servings = recipe_data.get("servings", 1)
    cuisine = recipe_data.get("cuisine")
    cook_time = recipe_data.get("cook_time_minutes")
    ingredients = recipe_data.get("ingredients", [])
    equipment = recipe_data.get("equipment", [])
    steps = recipe_data.get("steps", [])

    lines = [header("\U0001f4d6", f"Review: {name}"), ""]
    meta = [f"\U0001f37d {servings} servings"]
    if cuisine:
        meta.append(e(cuisine))
    if cook_time:
        meta.append(f"\u23f1 {cook_time} min")
    lines.append(" {0} ".format(DOT).join(meta))
    lines.append("")

    ing_lines = []
    for ing in ingredients:
        qty = ing.get("quantity")
        unit = ing.get("unit", "")
        n = ing.get("name", "")
        if qty:
            qty_str = f"{qty:g}" if isinstance(qty, (int, float)) else str(qty)
            ing_lines.append(f"{qty_str}{e(unit)} {e(n)}")
        else:
            ing_lines.append(e(n))
    if ing_lines:
        lines.extend(section("Ingredients", ing_lines))

    if equipment:
        lines.append("")
        lines.extend(section("Equipment", [e(eq) for eq in equipment]))

    if steps:
        lines.append("")
        step_lines = [f"{i+1}. {e(s)}" for i, s in enumerate(steps)]
        lines.extend(section("Steps", step_lines))

    context.user_data["cook_ai_source"] = source

    buttons = [
        [
            InlineKeyboardButton("\u2705 Save", callback_data="cook:ai:save"),
            InlineKeyboardButton("\u274c Discard", callback_data="cook:ai:discard"),
        ]
    ]

    target = update.message or update.callback_query.message
    await target.reply_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode="HTML",
    )
    return IMP_REVIEW if source == "text" else GEN_REVIEW


async def _save_ai_recipe(update, context, source="text"):
    query = update.callback_query
    user = context.user_data["current_user"]
    recipe_data = context.user_data.get("cook_ai_recipe", {})

    async with async_session_factory() as db:
        recipe = CookRecipe(
            user_id=user.id,
            name=recipe_data.get("name", "Untitled"),
            servings=recipe_data.get("servings", 1),
            cuisine=recipe_data.get("cuisine"),
            cook_time_minutes=recipe_data.get("cook_time_minutes"),
            steps="\n".join(recipe_data.get("steps", [])),
            source=source,
        )
        db.add(recipe)
        await db.flush()

        for i, ing in enumerate(recipe_data.get("ingredients", [])):
            qty = ing.get("quantity")
            if isinstance(qty, str):
                try:
                    qty = float(qty)
                except ValueError:
                    qty = None
            db.add(CookRecipeIngredient(
                recipe_id=recipe.id,
                name=ing.get("name", ""),
                quantity=qty,
                unit=ing.get("unit"),
                is_sauce=ing.get("is_sauce", False),
                sort_order=i,
            ))

        for eq_name in recipe_data.get("equipment", []):
            db.add(CookRecipeEquipment(recipe_id=recipe.id, name=eq_name))

        await db.commit()

    name = recipe_data.get("name", "Untitled")
    _clear_ai_data(context)
    _clear_gen_data(context)
    await query.edit_message_text(f"\u2705 <b>{e(name)}</b> saved!")
    await _show_cookbook(update, user.id, send_new=True)
    return ConversationHandler.END


def _clear_ai_data(context):
    for key in ["cook_ai_recipe", "cook_ai_source"]:
        context.user_data.pop(key, None)


def _clear_gen_data(context):
    for key in list(context.user_data.keys()):
        if key.startswith("cook_gen_"):
            del context.user_data[key]


# ============================================================
# Conversation Handler Factories
# ============================================================

def get_manual_add_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(manual_add_start, pattern=r"^cook:bk:add$")],
        states={
            ADD_PHOTO: [
                MessageHandler(filters.PHOTO, manual_photo),
                CallbackQueryHandler(manual_skip_photo, pattern=r"^cook:bk:nophoto$"),
            ],
            ADD_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, manual_name)],
            ADD_SERVINGS: [MessageHandler(filters.TEXT & ~filters.COMMAND, manual_servings)],
            ADD_INGREDIENTS: [MessageHandler(filters.TEXT & ~filters.COMMAND, manual_ingredients)],
            ADD_EQUIPMENT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, manual_equipment_text),
                CallbackQueryHandler(manual_skip_equipment, pattern=r"^cook:bk:noeq$"),
            ],
            ADD_STEPS: [MessageHandler(filters.TEXT & ~filters.COMMAND, manual_steps)],
            ADD_CONFIRM: [
                CallbackQueryHandler(manual_confirm, pattern=r"^cook:bk:(save|cancel)$"),
            ],
        },
        fallbacks=[],
        conversation_timeout=120,
        per_message=False,
    )


def get_import_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(import_start, pattern=r"^cook:ai:imp$")],
        states={
            IMP_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, import_text)],
            IMP_REVIEW: [
                CallbackQueryHandler(import_review, pattern=r"^cook:ai:(save|discard)$"),
            ],
        },
        fallbacks=[],
        conversation_timeout=120,
        per_message=False,
    )


def get_generate_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(generate_start, pattern=r"^cook:ai:gen$")],
        states={
            GEN_CUISINE: [CallbackQueryHandler(gen_cuisine, pattern=r"^cook:gen:c:")],
            GEN_SERVINGS: [MessageHandler(filters.TEXT & ~filters.COMMAND, gen_servings)],
            GEN_TIME: [CallbackQueryHandler(gen_time, pattern=r"^cook:gen:t:")],
            GEN_SPICY: [CallbackQueryHandler(gen_spicy, pattern=r"^cook:gen:sp:")],
            GEN_DIET: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, gen_diet_text),
                CallbackQueryHandler(gen_diet_skip, pattern=r"^cook:gen:diet:skip$"),
            ],
            GEN_REVIEW: [
                CallbackQueryHandler(import_review, pattern=r"^cook:ai:(save|discard)$"),
            ],
        },
        fallbacks=[],
        conversation_timeout=120,
        per_message=False,
    )
