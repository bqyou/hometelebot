"""
Telegram handlers for the Food Menu (Tingkat) mini app.

Callback data format:  menu:SCOPE:FILTER
  SCOPE   — today | week | next
  FILTER  — lunch | dinner | all
"""

import logging
from datetime import date, timedelta

from sqlalchemy import select, func
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from core.auth import require_auth
from core.database import async_session_factory
from apps.food_menu.models import MenuWeek, MenuItem

logger = logging.getLogger(__name__)

DAYS_OF_WEEK = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
DAY_SHORT = ["Mon", "Tue", "Wed", "Thu", "Fri"]  # matches DAYS_OF_WEEK index

COURSE_LABELS = {
    "soup": "Soup",
    "dish_1": "Dish 1",
    "dish_2": "Dish 2",
    "dish_3": "Dish 3",
    "side": "Side",
}

from core.ui import e as _e, BOX_TOP, BOX_MID, BOX_BOT, DOT


# ============================================================
# DB helpers
# ============================================================

async def _get_week_menu(target_date: date) -> tuple[MenuWeek | None, list[MenuItem]]:
    """Return the MenuWeek containing target_date and all its items."""
    async with async_session_factory() as db:
        result = await db.execute(
            select(MenuWeek).where(
                MenuWeek.week_start <= target_date,
                MenuWeek.week_end >= target_date,
            )
        )
        week = result.scalar_one_or_none()
        if week is None:
            return None, []

        items_result = await db.execute(
            select(MenuItem)
            .where(MenuItem.menu_week_id == week.id)
            .order_by(MenuItem.day_of_week, MenuItem.meal_type, MenuItem.course_type)
        )
        return week, items_result.scalars().all()


def _get_today_day_name() -> str:
    """Today's weekday name; defaults to Monday on weekends."""
    idx = date.today().weekday()
    return DAYS_OF_WEEK[idx] if idx < 5 else "Monday"


def _day_date(week_start: date, day_name: str) -> date:
    """Return the calendar date for a named weekday within the given week."""
    idx = DAYS_OF_WEEK.index(day_name) if day_name in DAYS_OF_WEEK else 0
    return week_start + timedelta(days=idx)


def _fmt_date(d: date, include_year: bool = False) -> str:
    """Format a date as '5 Mar' or '5 Mar 2026' without leading zero."""
    base = f"{d.day} {d.strftime('%b')}"
    return f"{base} {d.year}" if include_year else base



# ============================================================
# Message formatters
# ============================================================

def _format_meal_block(items: list[MenuItem], day: str, meal_type: str) -> list[str]:
    """Lines for one meal section using box-drawing characters."""
    icon = "\U0001f31e" if meal_type == "lunch" else "\U0001f319"
    label = "Lunch" if meal_type == "lunch" else "Dinner"
    meal_items = [i for i in items if i.day_of_week == day and i.meal_type == meal_type]

    lines = [f"{BOX_TOP} {icon} <b>{label}</b>"]

    if not meal_items:
        lines.append(f"{BOX_MID}  <i>No data</i>")
        lines.append(BOX_BOT)
        return lines

    for course_key in ("soup", "dish_1", "dish_2", "dish_3", "side"):
        dish = next((i for i in meal_items if i.course_type == course_key), None)
        if not dish:
            continue
        spicy = " \U0001f336\ufe0f" if dish.is_spicy else ""
        zh = f"  <i>{_e(dish.name_zh)}</i>" if dish.name_zh else ""
        lines.append(f"{BOX_MID}  {COURSE_LABELS[course_key]} {DOT} {_e(dish.name_en)}{spicy}{zh}")

    lines.append(BOX_BOT)
    return lines


def _format_day_text(
    day: str, items: list[MenuItem], meal_filter: str,
    date_range: str, week_start: date,
) -> str:
    """Full day view with date and box-drawn meal sections."""
    d = _day_date(week_start, day)
    lines = [f"\U0001f4c5 <b>{day}, {_fmt_date(d, include_year=True)}</b>", ""]

    if meal_filter in ("all", "lunch"):
        lines.extend(_format_meal_block(items, day, "lunch"))
        lines.append("")

    if meal_filter in ("all", "dinner"):
        lines.extend(_format_meal_block(items, day, "dinner"))

    return "\n".join(lines).rstrip()


def _format_week_text(
    items: list[MenuItem], meal_filter: str,
    date_range: str, is_next: bool, week_start: date,
) -> str:
    """Full week view — each day with box-drawn meal sections."""
    week_label = "Next Week" if is_next else "This Week"
    lines = [f"\U0001f4c6 <b>{week_label}</b>  ({_e(date_range)})", ""]

    for day in DAYS_OF_WEEK:
        d = _day_date(week_start, day)
        lines.append(f"\U0001f4c5 <b>{day}, {_fmt_date(d)}</b>")
        lines.append("")

        if meal_filter in ("all", "lunch"):
            lines.extend(_format_meal_block(items, day, "lunch"))
            lines.append("")

        if meal_filter in ("all", "dinner"):
            lines.extend(_format_meal_block(items, day, "dinner"))
            lines.append("")

    return "\n".join(lines).rstrip()


# ============================================================
# Keyboard builders
# ============================================================

def _filter_row(scope: str, current_filter: str) -> list[InlineKeyboardButton]:
    """Meal-filter toggle row. Active filter gets a ✓ prefix."""
    def btn(label: str, f: str) -> InlineKeyboardButton:
        prefix = "✓ " if f == current_filter else ""
        return InlineKeyboardButton(f"{prefix}{label}", callback_data=f"menu:{scope}:{f}")

    return [btn("Lunch", "lunch"), btn("Dinner", "dinner"), btn("Both", "all")]


def _day_keyboard(meal_filter: str) -> InlineKeyboardMarkup:
    """Keyboard for today's day view — filter toggle + week navigation."""
    return InlineKeyboardMarkup([
        _filter_row("today", meal_filter),
        [
            InlineKeyboardButton("This Week", callback_data=f"menu:week:{meal_filter}"),
            InlineKeyboardButton("Next Week", callback_data=f"menu:next:{meal_filter}"),
        ],
    ])


def _week_keyboard(meal_filter: str, is_next: bool) -> InlineKeyboardMarkup:
    """Keyboard for a week overview — filter toggle + day shortcuts + navigation."""
    scope = "next" if is_next else "week"
    prefix = "nday_" if is_next else "day_"

    # One button per weekday so users can drill in without scrolling
    day_row = [
        InlineKeyboardButton(short, callback_data=f"menu:{prefix}{short}:{meal_filter}")
        for short in DAY_SHORT
    ]

    if is_next:
        nav = [
            InlineKeyboardButton("Today", callback_data=f"menu:today:{meal_filter}"),
            InlineKeyboardButton("This Week", callback_data=f"menu:week:{meal_filter}"),
        ]
    else:
        nav = [
            InlineKeyboardButton("Today", callback_data=f"menu:today:{meal_filter}"),
            InlineKeyboardButton("Next Week", callback_data=f"menu:next:{meal_filter}"),
        ]
    return InlineKeyboardMarkup([_filter_row(scope, meal_filter), day_row, nav])


def _specific_day_keyboard(meal_filter: str, is_next: bool) -> InlineKeyboardMarkup:
    """Keyboard for a specific-day drill-in — filter toggle + back to week."""
    back_scope = "next" if is_next else "week"
    back_label = "Next Week" if is_next else "This Week"
    return InlineKeyboardMarkup([
        _filter_row("today", meal_filter),  # reuse today filter scope for day view
        [
            InlineKeyboardButton(f"\u2190 {back_label}", callback_data=f"menu:{back_scope}:{meal_filter}"),
            InlineKeyboardButton("Today", callback_data=f"menu:today:{meal_filter}"),
        ],
    ])


# ============================================================
# /menu command
# ============================================================

@require_auth
async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /menu — show today's menu."""
    week, items = await _get_week_menu(date.today())

    if week is None:
        await update.message.reply_text(
            "\U0001f371 <b>Menu</b>\n\nNo data for this week \u00b7 /menu_refresh to fetch",
            parse_mode="HTML",
        )
        return

    date_range = f"{week.week_start.day} {week.week_start.strftime('%b')} – {week.week_end.day} {week.week_end.strftime('%b %Y')}"
    day_name = _get_today_day_name()

    await update.message.reply_text(
        text=_format_day_text(day_name, items, "all", date_range, week.week_start),
        reply_markup=_day_keyboard("all"),
        parse_mode="HTML",
    )


# ============================================================
# Callback handler
# ============================================================

@require_auth
async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Route all menu: callback queries."""
    query = update.callback_query
    await query.answer()

    parts = query.data.split(":")
    if len(parts) != 3:
        return
    _, scope, meal_filter = parts

    # Resolve which week to load and whether this is a specific-day drill-in
    is_next = scope.startswith("nday_") or scope == "next"
    target = date.today() + timedelta(days=7) if is_next else date.today()

    week, items = await _get_week_menu(target)
    if week is None:
        await query.edit_message_text("\u274c No menu data for that period")
        return

    date_range = f"{week.week_start.day} {week.week_start.strftime('%b')} – {week.week_end.day} {week.week_end.strftime('%b %Y')}"

    if scope == "today":
        day_name = _get_today_day_name()
        await query.edit_message_text(
            text=_format_day_text(day_name, items, meal_filter, date_range, week.week_start),
            reply_markup=_day_keyboard(meal_filter),
            parse_mode="HTML",
        )

    elif scope in ("week", "next"):
        await query.edit_message_text(
            text=_format_week_text(items, meal_filter, date_range, is_next=is_next, week_start=week.week_start),
            reply_markup=_week_keyboard(meal_filter, is_next=is_next),
            parse_mode="HTML",
        )

    elif scope.startswith("day_") or scope.startswith("nday_"):
        # e.g. "day_Mon" → "Mon" → "Monday"
        short = scope.split("_", 1)[1]
        if short not in DAY_SHORT:
            return
        day_name = DAYS_OF_WEEK[DAY_SHORT.index(short)]
        await query.edit_message_text(
            text=_format_day_text(day_name, items, meal_filter, date_range, week.week_start),
            reply_markup=_specific_day_keyboard(meal_filter, is_next=is_next),
            parse_mode="HTML",
        )


# ============================================================
# /menu_refresh command
# ============================================================

@require_auth
async def menu_refresh_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /menu_refresh — force a re-scrape of the Tingkat website."""
    from apps.food_menu.scraper import scrape_menu

    await update.message.reply_text("\U0001f504 Fetching latest menu\u2026")

    try:
        week_data_list = await scrape_menu()
        if not week_data_list:
            await update.message.reply_text(
                "\u26a0\ufe0f No menu data found \u00b7 website format may have changed"
            )
            return

        inserted_weeks = 0
        inserted_items = 0

        async with async_session_factory() as db:
            for week_data in week_data_list:
                week_start = week_data["week_start"]
                week_end = week_data["week_end"]

                existing = (await db.execute(
                    select(MenuWeek).where(
                        MenuWeek.week_start == week_start,
                        MenuWeek.week_end == week_end,
                    )
                )).scalar_one_or_none()

                if existing:
                    item_count = (await db.execute(
                        select(func.count()).where(MenuItem.menu_week_id == existing.id)
                    )).scalar()
                    if item_count > 0:
                        logger.info(f"Week {week_start} already has {item_count} items — skipping")
                        continue
                    week_obj = existing
                else:
                    week_obj = MenuWeek(week_start=week_start, week_end=week_end)
                    db.add(week_obj)
                    await db.flush()
                    inserted_weeks += 1

                for day, meals in week_data.get("days", {}).items():
                    for meal_type, dishes in meals.items():
                        for dish in dishes:
                            db.add(MenuItem(
                                menu_week_id=week_obj.id,
                                day_of_week=day,
                                meal_type=meal_type,
                                course_type=dish.get("course", "dish_1"),
                                name_en=dish.get("name_en", ""),
                                name_zh=dish.get("name_zh"),
                                is_spicy=dish.get("is_spicy", False),
                            ))
                            inserted_items += 1

            await db.commit()

        logger.info(f"menu_refresh: {inserted_weeks} new week(s), {inserted_items} new item(s)")
        await update.message.reply_text(
            f"\u2705 Menu updated\n"
            f"{inserted_weeks} week(s) {DOT} {inserted_items} item(s)\n"
            f"\n"
            f"/menu to view"
        )

    except Exception as exc:
        logger.error(f"Menu scrape failed: {exc}", exc_info=True)
        await update.message.reply_text(
            f"\u274c Scrape failed: {_e(str(exc))}",
            parse_mode="HTML",
        )
