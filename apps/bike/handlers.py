"""
Handlers and business logic for the Bike Ride Tracker mini app.

Tracks daily school bike rides (morning / evening), calculates cab-fare savings,
and shows streak stats. Bike purchased 24 Jan 2026 for $360; cab cost = $17/ride.

Morning savings apply Tue/Wed/Thu only (no cab on Mon/Fri mornings).
Evening savings apply every school day.
"""

import logging
from datetime import date, datetime, timedelta

from sqlalchemy import select
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
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
from core.ui import e as _e, BOX_TOP, BOX_MID, BOX_BOT, DOT
from apps.bike.models import BikeDay

logger = logging.getLogger(__name__)

# ============================================================
# Constants
# ============================================================

BIKE_PURCHASE_DATE = date(2026, 1, 24)
BIKE_COST = 660
CAB_COST = 17

SG_PUBLIC_HOLIDAYS = {
    date(2026, 1, 1),    # New Year's Day
    date(2026, 2, 17),   # CNY Day 1
    date(2026, 2, 18),   # CNY Day 2
    date(2026, 3, 20),   # Hari Raya Puasa (approx)
    date(2026, 3, 27),   # Good Friday
    date(2026, 5, 1),    # Labour Day
    date(2026, 5, 27),   # Vesak Day (approx)
    date(2026, 7, 27),   # Hari Raya Haji (approx)
    date(2026, 8, 9),    # National Day (Sunday)
    date(2026, 8, 10),   # National Day observed (Monday)
    date(2026, 10, 27),  # Deepavali (approx)
    date(2026, 12, 25),  # Christmas
}

REASON_LABELS = {
    "school_closure": "School Closure",
    "rain": "Rain",
    "vacation": "Vacation",
    "custom": "Custom",
}

# Conversation state for custom skip reason
CUSTOM_REASON_STATE = 0


# ============================================================
# Business Logic
# ============================================================

def is_public_holiday(d: date) -> bool:
    return d in SG_PUBLIC_HOLIDAYS


def is_school_day(d: date) -> bool:
    """Mon–Fri, not a Singapore public holiday."""
    return d.weekday() < 5 and not is_public_holiday(d)


def day_saves_morning(d: date) -> bool:
    """Morning ride saves $17 cab fare only on Tue/Wed/Thu."""
    return d.weekday() in {1, 2, 3}


def calculate_savings(day: BikeDay) -> int:
    """Dollar savings this day contributes."""
    savings = 0
    if day.morning_rode and day_saves_morning(day.date):
        savings += CAB_COST
    if day.evening_rode:
        savings += CAB_COST
    return savings


def session_streak_status(rode, reason, date_is_public_holiday: bool, is_today: bool = False) -> str:
    """
    Returns one of: "rode", "excused", "break".

    - Public holiday → excused (neutral)
    - Rode → rode (+1)
    - Skipped with any reason → excused (neutral)
    - Skipped with no reason → break (resets streak)
    - Unrecorded today → excused (give benefit of the doubt)
    - Unrecorded past → break
    """
    if date_is_public_holiday:
        return "excused"
    if rode is True:
        return "rode"
    if rode is False and reason:
        return "excused"
    if rode is False and not reason:
        return "break"
    # rode is None (unrecorded)
    if is_today:
        return "excused"
    return "break"


def calculate_streaks(days: list) -> tuple[int, int]:
    """Return (current_streak, longest_streak) counted in sessions."""
    today = date.today()
    day_map = {d.date: d for d in days}

    # Build all (morning, evening) session statuses for each school day
    statuses = []
    current = BIKE_PURCHASE_DATE
    while current <= today:
        if is_school_day(current):
            row = day_map.get(current)
            is_today_flag = current == today
            is_ph = is_public_holiday(current)  # always False here (filtered by is_school_day)

            morning_rode = row.morning_rode if row else None
            morning_reason = row.morning_reason if row else None
            evening_rode = row.evening_rode if row else None
            evening_reason = row.evening_reason if row else None

            statuses.append(session_streak_status(morning_rode, morning_reason, is_ph, is_today_flag))
            statuses.append(session_streak_status(evening_rode, evening_reason, is_ph, is_today_flag))

        current += timedelta(days=1)

    # Longest streak: scan forward, +1 on "rode", skip "excused", reset on "break"
    longest = 0
    run = 0
    for s in statuses:
        if s == "rode":
            run += 1
            if run > longest:
                longest = run
        elif s == "excused":
            pass
        else:
            run = 0

    # Current streak: scan backward, count "rode", skip "excused", stop on "break"
    current_streak = 0
    for s in reversed(statuses):
        if s == "rode":
            current_streak += 1
        elif s == "excused":
            pass
        else:
            break

    return current_streak, longest


def calculate_summary(days: list) -> dict:
    total_savings = sum(calculate_savings(d) for d in days)
    total_rides = sum(
        (1 if d.morning_rode else 0) + (1 if d.evening_rode else 0)
        for d in days
    )
    current_streak, longest_streak = calculate_streaks(days)
    return {
        "total_savings": total_savings,
        "net_savings": total_savings - BIKE_COST,
        "total_rides": total_rides,
        "current_streak": current_streak,
        "longest_streak": longest_streak,
    }


# ============================================================
# DB Helpers
# ============================================================

async def _get_or_create_day(db, user_id: int, d: date) -> BikeDay:
    result = await db.execute(
        select(BikeDay).where(BikeDay.user_id == user_id, BikeDay.date == d)
    )
    day = result.scalar_one_or_none()
    if not day:
        day = BikeDay(user_id=user_id, date=d)
        db.add(day)
        await db.flush()
    return day


async def _get_all_days(db, user_id: int) -> list:
    result = await db.execute(
        select(BikeDay).where(BikeDay.user_id == user_id).order_by(BikeDay.date)
    )
    return result.scalars().all()


# ============================================================
# Formatting Helpers
# ============================================================

def _fmt_date(d: date) -> str:
    """'Tue, 03 Mar 2026'"""
    return d.strftime("%a, %d %b %Y")


def _fmt_date_short(d: date) -> str:
    """'Mon 02 Feb'"""
    return d.strftime("%a %d %b")


def _fmt_session_text(rode, reason, custom) -> str:
    """Human-readable session status (HTML-safe)."""
    if rode is True:
        return "🚲 Rode"
    if rode is False:
        if reason == "custom" and custom:
            return f"✗ {_e(custom)}"
        label = REASON_LABELS.get(reason, "Skipped") if reason else "Unexcused"
        return f"✗ {label}"
    return "?"


def _fmt_session_icon(rode) -> str:
    if rode is True:
        return "🚲"
    if rode is False:
        return "—"
    return "?"


def _fmt_log_session(rode, reason, custom) -> str:
    """Session display for the log view: X for unexcused, italicised reason for excused."""
    if rode is True:
        return "🚲"
    if rode is False:
        if not reason:
            return "✗"
        label = _e(custom) if reason == "custom" and custom else _e(REASON_LABELS.get(reason, reason))
        return f"— <i>{label}</i>"
    return "?"


def _fmt_status_icon(rode, reason) -> str:
    """Compact icon for dashboard displays: rode=🚲, excused=—, unexcused=✗, unknown=?"""
    if rode is True:
        return "🚲"
    if rode is False:
        return "—" if reason else "✗"
    return "?"


def _fmt_reason_label(reason, custom) -> str:
    """Short plain-text label for buttons."""
    if reason == "custom" and custom:
        return custom[:20]
    return REASON_LABELS.get(reason, "Skip") if reason else "Skip"


# ============================================================
# Save / Clear Helpers
# ============================================================

async def _save_session(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    date_str: str,
    session: str,
    rode,
    reason,
    custom,
) -> None:
    user = context.user_data["current_user"]
    d = date.fromisoformat(date_str)

    async with async_session_factory() as db:
        day = await _get_or_create_day(db, user.id, d)
        if session == "morning":
            day.morning_rode = rode
            day.morning_reason = reason
            day.morning_custom = custom
        else:
            day.evening_rode = rode
            day.evening_reason = reason
            day.evening_custom = custom
        await db.commit()

    await _show_date_view(update, context, d, edit=True)


async def _save_both_sessions(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    date_str: str,
    rode,
    reason,
    custom,
) -> None:
    user = context.user_data["current_user"]
    d = date.fromisoformat(date_str)

    async with async_session_factory() as db:
        day = await _get_or_create_day(db, user.id, d)
        day.morning_rode = rode
        day.morning_reason = reason
        day.morning_custom = custom
        day.evening_rode = rode
        day.evening_reason = reason
        day.evening_custom = custom
        await db.commit()

    await _show_date_view(update, context, d, edit=True)


async def _clear_session(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    date_str: str,
    session: str,
) -> None:
    user = context.user_data["current_user"]
    d = date.fromisoformat(date_str)

    async with async_session_factory() as db:
        result = await db.execute(
            select(BikeDay).where(BikeDay.user_id == user.id, BikeDay.date == d)
        )
        day = result.scalar_one_or_none()
        if day:
            if session == "morning":
                day.morning_rode = None
                day.morning_reason = None
                day.morning_custom = None
            else:
                day.evening_rode = None
                day.evening_reason = None
                day.evening_custom = None
            await db.commit()

    await _show_date_view(update, context, d, edit=True)


async def _clear_both_sessions(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    date_str: str,
) -> None:
    user = context.user_data["current_user"]
    d = date.fromisoformat(date_str)

    async with async_session_factory() as db:
        result = await db.execute(
            select(BikeDay).where(BikeDay.user_id == user.id, BikeDay.date == d)
        )
        day = result.scalar_one_or_none()
        if day:
            day.morning_rode = None
            day.morning_reason = None
            day.morning_custom = None
            day.evening_rode = None
            day.evening_reason = None
            day.evening_custom = None
            await db.commit()

    await _show_date_view(update, context, d, edit=True)


# ============================================================
# UI: Main Menu
# ============================================================

async def _show_main_menu(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    edit: bool = False,
) -> None:
    user = context.user_data["current_user"]
    today = date.today()

    async with async_session_factory() as db:
        days = await _get_all_days(db, user.id)

    day_map = {d.date: d for d in days}
    today_row = day_map.get(today)
    s = calculate_summary(days)

    net_prefix = "+" if s["net_savings"] >= 0 else "−"
    net_abs = abs(s["net_savings"])
    today_label = _e(today.strftime("%a, %d %b"))

    if is_school_day(today):
        m_rode = today_row.morning_rode if today_row else None
        m_reason = today_row.morning_reason if today_row else None
        e_rode = today_row.evening_rode if today_row else None
        e_reason = today_row.evening_reason if today_row else None
        today_block = (
            f"{BOX_TOP} <b>Today</b>  {DOT}  {today_label}\n"
            f"{BOX_MID}  Morning  {DOT}  {_fmt_status_icon(m_rode, m_reason)}\n"
            f"{BOX_MID}  Evening  {DOT}  {_fmt_status_icon(e_rode, e_reason)}\n"
            f"{BOX_BOT}\n"
        )
    else:
        today_block = (
            f"{BOX_TOP} <b>Today</b>  {DOT}  {today_label}\n"
            f"{BOX_MID}  <i>No school today</i>\n"
            f"{BOX_BOT}\n"
        )

    text = (
        "🚲 <b>Bike Tracker</b>\n"
        "\n"
        + today_block +
        f"{BOX_TOP} <b>Stats</b>\n"
        f"{BOX_MID}  Streak  {DOT}  {s['current_streak']} sessions\n"
        f"{BOX_MID}  Net     {DOT}  {net_prefix}${net_abs}\n"
        f"{BOX_BOT}"
    )
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Record Today", callback_data="bike:today"),
            InlineKeyboardButton("Pick a Date", callback_data="bike:retro:0"),
        ],
        [
            InlineKeyboardButton("Summary", callback_data="bike:summary"),
            InlineKeyboardButton("View Log", callback_data="bike:log"),
        ],
    ])

    if edit and update.callback_query:
        try:
            await update.callback_query.edit_message_text(
                text, reply_markup=keyboard, parse_mode="HTML"
            )
        except Exception as ex:
            if "Message is not modified" not in str(ex):
                raise
    else:
        await update.effective_chat.send_message(
            text, reply_markup=keyboard, parse_mode="HTML"
        )


# ============================================================
# UI: Date Recording View
# ============================================================

async def _show_date_view(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    d: date,
    edit: bool = False,
) -> None:
    user = context.user_data["current_user"]
    date_str = d.strftime("%Y-%m-%d")
    ph = is_public_holiday(d)

    async with async_session_factory() as db:
        result = await db.execute(
            select(BikeDay).where(BikeDay.user_id == user.id, BikeDay.date == d)
        )
        day = result.scalar_one_or_none()

    morning_rode = day.morning_rode if day else None
    morning_reason = day.morning_reason if day else None
    morning_custom = day.morning_custom if day else None
    evening_rode = day.evening_rode if day else None
    evening_reason = day.evening_reason if day else None
    evening_custom = day.evening_custom if day else None

    # Build text header
    text = f"📅 <b>{_e(_fmt_date(d))}</b>"
    if ph:
        text += "\n⚠️ <i>Public holiday — excused in streak</i>"
    text += "\n"

    # Session status box
    m_status = _fmt_session_text(morning_rode, morning_reason, morning_custom) if morning_rode is not None else "<i>tap to record</i>"
    e_status = _fmt_session_text(evening_rode, evening_reason, evening_custom) if evening_rode is not None else "<i>tap to record</i>"
    m_note = "  <i>no savings</i>" if is_school_day(d) and not day_saves_morning(d) else ""
    text += (
        "\n"
        f"{BOX_TOP} <b>Sessions</b>\n"
        f"{BOX_MID}  Morning  {DOT}  {m_status}{m_note}\n"
        f"{BOX_MID}  Evening  {DOT}  {e_status}\n"
        f"{BOX_BOT}"
    )

    # Build keyboard
    keyboard_rows = []

    # Morning row
    if morning_rode is None:
        keyboard_rows.append([
            InlineKeyboardButton("🚲 Morning", callback_data=f"bike:morning:{date_str}"),
            InlineKeyboardButton("✗ Skip Morning", callback_data=f"bike:skip:{date_str}:morning"),
        ])
    else:
        m_label = "🚲 Rode" if morning_rode else f"✗ {_fmt_reason_label(morning_reason, morning_custom)}"
        keyboard_rows.append([
            InlineKeyboardButton(f"Morning: {m_label}", callback_data="bike:noop"),
            InlineKeyboardButton("↩ Change", callback_data=f"bike:clear:{date_str}:morning"),
        ])

    # Evening row
    if evening_rode is None:
        keyboard_rows.append([
            InlineKeyboardButton("🚲 Evening", callback_data=f"bike:evening:{date_str}"),
            InlineKeyboardButton("✗ Skip Evening", callback_data=f"bike:skip:{date_str}:evening"),
        ])
    else:
        e_label = "🚲 Rode" if evening_rode else f"✗ {_fmt_reason_label(evening_reason, evening_custom)}"
        keyboard_rows.append([
            InlineKeyboardButton(f"Evening: {e_label}", callback_data="bike:noop"),
            InlineKeyboardButton("↩ Change", callback_data=f"bike:clear:{date_str}:evening"),
        ])

    # DNB / Unmark row
    both_recorded = morning_rode is not None and evening_rode is not None
    if both_recorded:
        keyboard_rows.append([
            InlineKeyboardButton("🔄 Unmark Both", callback_data=f"bike:undnb:{date_str}"),
        ])
    else:
        keyboard_rows.append([
            InlineKeyboardButton("🚫 Did Not Bike (both)", callback_data=f"bike:dnb:{date_str}"),
        ])

    keyboard_rows.append([
        InlineKeyboardButton("← Back", callback_data="bike:main"),
    ])

    keyboard = InlineKeyboardMarkup(keyboard_rows)

    if edit and update.callback_query:
        try:
            await update.callback_query.edit_message_text(
                text, reply_markup=keyboard, parse_mode="HTML"
            )
        except Exception as ex:
            if "Message is not modified" not in str(ex):
                raise
    else:
        await update.effective_chat.send_message(
            text, reply_markup=keyboard, parse_mode="HTML"
        )


async def _show_skip_reason(update: Update, date_str: str, session: str) -> None:
    """Inline reason picker for skipping a single session."""
    d = date.fromisoformat(date_str)
    session_label = "Morning" if session == "morning" else "Evening"

    text = (
        f"✗ <b>Skip {session_label} — {_e(_fmt_date(d))}</b>\n"
        "\n"
        "Reason for not riding:"
    )
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("School Closure", callback_data=f"bike:skip_reason:{date_str}:{session}:school_closure"),
            InlineKeyboardButton("Rain", callback_data=f"bike:skip_reason:{date_str}:{session}:rain"),
        ],
        [
            InlineKeyboardButton("Vacation", callback_data=f"bike:skip_reason:{date_str}:{session}:vacation"),
            InlineKeyboardButton("Custom…", callback_data=f"bike:skip_reason:{date_str}:{session}:custom"),
        ],
        [
            InlineKeyboardButton("No Reason", callback_data=f"bike:skip_reason:{date_str}:{session}:none"),
        ],
        [
            InlineKeyboardButton("← Back", callback_data=f"bike:date:{date_str}"),
        ],
    ])
    await update.callback_query.edit_message_text(text, reply_markup=keyboard, parse_mode="HTML")


async def _show_dnb_reason(update: Update, date_str: str) -> None:
    """Inline reason picker for Did Not Bike (both sessions)."""
    d = date.fromisoformat(date_str)

    text = (
        f"🚫 <b>Did Not Bike — {_e(_fmt_date(d))}</b>\n"
        "\n"
        "Reason for not biking:"
    )
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("School Closure", callback_data=f"bike:dnb_reason:{date_str}:school_closure"),
            InlineKeyboardButton("Rain", callback_data=f"bike:dnb_reason:{date_str}:rain"),
        ],
        [
            InlineKeyboardButton("Vacation", callback_data=f"bike:dnb_reason:{date_str}:vacation"),
            InlineKeyboardButton("Custom…", callback_data=f"bike:dnb_reason:{date_str}:custom"),
        ],
        [
            InlineKeyboardButton("No Reason", callback_data=f"bike:dnb_reason:{date_str}:none"),
        ],
        [
            InlineKeyboardButton("← Back", callback_data=f"bike:date:{date_str}"),
        ],
    ])
    await update.callback_query.edit_message_text(text, reply_markup=keyboard, parse_mode="HTML")


# ============================================================
# UI: Retrospective Date Picker
# ============================================================

async def _show_retro_list(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    page: int,
    edit: bool = False,
) -> None:
    user = context.user_data["current_user"]
    yesterday = date.today() - timedelta(days=1)

    async with async_session_factory() as db:
        result = await db.execute(
            select(BikeDay).where(BikeDay.user_id == user.id)
        )
        existing = result.scalars().all()

    # Days where both sessions are recorded (non-None)
    fully_recorded = {
        d.date for d in existing
        if d.morning_rode is not None and d.evening_rode is not None
    }

    # All school days from purchase date to yesterday that are not fully recorded
    unrecorded = []
    current = BIKE_PURCHASE_DATE
    while current <= yesterday:
        if is_school_day(current) and current not in fully_recorded:
            unrecorded.append(current)
        current += timedelta(days=1)

    # Most recent first
    unrecorded.sort(reverse=True)

    PAGE_SIZE = 8
    total = len(unrecorded)
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    page_days = unrecorded[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]

    if total == 0:
        text = "📋 <b>Unrecorded School Days</b>\n\n✅ All school days are fully recorded!"
    else:
        text = (
            f"📋 <b>Unrecorded School Days</b>\n"
            f"\n"
            f"Page {page + 1} of {total_pages} {DOT} {total} days to record"
        )

    keyboard_rows = []

    # Date buttons in rows of 2
    for i in range(0, len(page_days), 2):
        row = []
        for d in page_days[i : i + 2]:
            row.append(InlineKeyboardButton(
                _fmt_date_short(d),
                callback_data=f"bike:date:{d.strftime('%Y-%m-%d')}",
            ))
        keyboard_rows.append(row)

    # Pagination
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("← Prev", callback_data=f"bike:retro:{page - 1}"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton("Next →", callback_data=f"bike:retro:{page + 1}"))
    if nav_row:
        keyboard_rows.append(nav_row)

    keyboard_rows.append([InlineKeyboardButton("← Back", callback_data="bike:main")])
    keyboard = InlineKeyboardMarkup(keyboard_rows)

    if edit and update.callback_query:
        try:
            await update.callback_query.edit_message_text(
                text, reply_markup=keyboard, parse_mode="HTML"
            )
        except Exception as ex:
            if "Message is not modified" not in str(ex):
                raise
    else:
        await update.effective_chat.send_message(
            text, reply_markup=keyboard, parse_mode="HTML"
        )


# ============================================================
# UI: Summary
# ============================================================

async def _show_summary(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    edit: bool = False,
) -> None:
    user = context.user_data["current_user"]

    async with async_session_factory() as db:
        days = await _get_all_days(db, user.id)

    s = calculate_summary(days)
    net_prefix = "+" if s["net_savings"] >= 0 else "−"
    net_abs = abs(s["net_savings"])

    text = (
        "🚲 <b>Bike Tracker — Summary</b>\n"
        "\n"
        f"{BOX_TOP} <b>Savings</b>\n"
        f"{BOX_MID}  Cab savings  {DOT}  ${s['total_savings']}\n"
        f"{BOX_MID}  Bike cost    {DOT}  ${BIKE_COST}\n"
        f"{BOX_MID}  Net          {DOT}  {net_prefix}${net_abs}\n"
        f"{BOX_BOT}\n"
        f"{BOX_TOP} <b>Rides</b>\n"
        f"{BOX_MID}  Sessions   {DOT}  {s['total_rides']}\n"
        f"{BOX_MID}  Distance   {DOT}  {s['total_rides'] * 5.5:.1f} km\n"
        f"{BOX_MID}  Streak     {DOT}  {s['current_streak']}  <i>(best: {s['longest_streak']})</i>\n"
        f"{BOX_BOT}"
    )
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("← Back", callback_data="bike:main"),
    ]])

    if edit and update.callback_query:
        try:
            await update.callback_query.edit_message_text(
                text, reply_markup=keyboard, parse_mode="HTML"
            )
        except Exception as ex:
            if "Message is not modified" not in str(ex):
                raise
    else:
        await update.effective_chat.send_message(
            text, reply_markup=keyboard, parse_mode="HTML"
        )


# ============================================================
# UI: Recent Log
# ============================================================

async def _show_log(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    page: int = 0,
    edit: bool = False,
) -> None:
    user = context.user_data["current_user"]
    today = date.today()

    async with async_session_factory() as db:
        days = await _get_all_days(db, user.id)

    day_map = {d.date: d for d in days}

    # All school days from purchase date to today, most recent first
    all_school_days = []
    current = today
    while current >= BIKE_PURCHASE_DATE:
        if is_school_day(current):
            all_school_days.append(current)
        current -= timedelta(days=1)

    PAGE_SIZE = 14
    total_pages = max(1, (len(all_school_days) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))

    # Slice: page 0 = most recent 14, page 1 = next older 14, etc.
    page_days = all_school_days[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]
    # Show oldest first within the page
    school_days = list(reversed(page_days))

    page_label = f"Page {page + 1} of {total_pages}" if total_pages > 1 else ""
    lines = [f"📋 <b>Ride Log</b>{('  · ' + page_label) if page_label else ''}", ""]

    for d in school_days:
        row = day_map.get(d)
        date_label = _e(d.strftime("%a %d %b"))

        m_rode = row.morning_rode if row else None
        m_reason = row.morning_reason if row else None
        m_custom = row.morning_custom if row else None
        e_rode = row.evening_rode if row else None
        e_reason = row.evening_reason if row else None
        e_custom = row.evening_custom if row else None

        # Show combined line if both sessions share the same excused skip reason
        both_same_skip = (
            m_rode is False and e_rode is False
            and m_reason and m_reason == e_reason
        )
        if both_same_skip:
            if m_reason == "custom" and m_custom:
                reason_label = _e(m_custom)
            else:
                reason_label = _e(REASON_LABELS.get(m_reason, m_reason))
            lines.append(f"{date_label} · 🚫 <i>{reason_label}</i>")
        else:
            am = _fmt_log_session(m_rode, m_reason, m_custom)
            pm = _fmt_log_session(e_rode, e_reason, e_custom)
            lines.append(f"{date_label} · AM {am} · PM {pm}")

    lines.append("")
    lines.append(f"<i>🚲 rode  {DOT}  — excused  {DOT}  ✗ unexcused  {DOT}  ? unrecorded</i>")
    lines.append("")
    lines.append("<i>Tap a date below to edit it.</i>")

    # Date buttons for editing — rows of 3
    keyboard_rows = []
    for i in range(0, len(school_days), 3):
        row = []
        for d in school_days[i : i + 3]:
            row.append(InlineKeyboardButton(
                _fmt_date_short(d),
                callback_data=f"bike:date:{d.strftime('%Y-%m-%d')}",
            ))
        keyboard_rows.append(row)

    # Pagination: older pages have higher page numbers
    nav_row = []
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton("← Older", callback_data=f"bike:log:{page + 1}"))
    if page > 0:
        nav_row.append(InlineKeyboardButton("Newer →", callback_data=f"bike:log:{page - 1}"))
    if nav_row:
        keyboard_rows.append(nav_row)

    keyboard_rows.append([InlineKeyboardButton("← Back", callback_data="bike:main")])

    keyboard = InlineKeyboardMarkup(keyboard_rows)

    if edit and update.callback_query:
        try:
            await update.callback_query.edit_message_text(
                "\n".join(lines), reply_markup=keyboard, parse_mode="HTML"
            )
        except Exception as ex:
            if "Message is not modified" not in str(ex):
                raise
    else:
        await update.effective_chat.send_message(
            "\n".join(lines), reply_markup=keyboard, parse_mode="HTML"
        )


# ============================================================
# Command Handler
# ============================================================

@require_auth
async def bike_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /bike — show the main menu."""
    await _show_main_menu(update, context)


# ============================================================
# Callback Router
# ============================================================

@require_auth
async def bike_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Route all bike: callback queries."""
    query = update.callback_query
    await query.answer()
    data = query.data
    parts = data.split(":")

    action = parts[1] if len(parts) > 1 else ""

    # bike:main
    if data == "bike:main":
        await _show_main_menu(update, context, edit=True)

    # bike:today
    elif data == "bike:today":
        await _show_date_view(update, context, date.today(), edit=True)

    # bike:retro:N
    elif action == "retro":
        page = int(parts[2]) if len(parts) >= 3 else 0
        await _show_retro_list(update, context, page, edit=True)

    # bike:date:YYYY-MM-DD
    elif action == "date" and len(parts) >= 3:
        await _show_date_view(update, context, date.fromisoformat(parts[2]), edit=True)

    # bike:summary
    elif data == "bike:summary":
        await _show_summary(update, context, edit=True)

    # bike:log / bike:log:N
    elif action == "log":
        page = int(parts[2]) if len(parts) >= 3 else 0
        await _show_log(update, context, page, edit=True)

    # bike:noop — display-only buttons
    elif data == "bike:noop":
        await query.answer("Tap ↩ Change to update this session", show_alert=False)

    # bike:morning:YYYY-MM-DD
    elif action == "morning" and len(parts) >= 3:
        await _save_session(update, context, parts[2], "morning", rode=True, reason=None, custom=None)

    # bike:evening:YYYY-MM-DD
    elif action == "evening" and len(parts) >= 3:
        await _save_session(update, context, parts[2], "evening", rode=True, reason=None, custom=None)

    # bike:skip:YYYY-MM-DD:morning|evening
    elif action == "skip" and len(parts) >= 4:
        await _show_skip_reason(update, parts[2], parts[3])

    # bike:skip_reason:YYYY-MM-DD:morning|evening:reason
    # ("custom" is intercepted by ConversationHandler before reaching here)
    elif action == "skip_reason" and len(parts) >= 5:
        date_str, session, reason_key = parts[2], parts[3], parts[4]
        if reason_key == "none":
            await _save_session(update, context, date_str, session, rode=False, reason=None, custom=None)
        else:
            await _save_session(update, context, date_str, session, rode=False, reason=reason_key, custom=None)

    # bike:dnb:YYYY-MM-DD
    elif action == "dnb" and len(parts) >= 3:
        await _show_dnb_reason(update, parts[2])

    # bike:dnb_reason:YYYY-MM-DD:reason
    # ("custom" is intercepted by ConversationHandler before reaching here)
    elif action == "dnb_reason" and len(parts) >= 4:
        date_str, reason_key = parts[2], parts[3]
        if reason_key == "none":
            await _save_both_sessions(update, context, date_str, rode=False, reason=None, custom=None)
        else:
            await _save_both_sessions(update, context, date_str, rode=False, reason=reason_key, custom=None)

    # bike:undnb:YYYY-MM-DD
    elif action == "undnb" and len(parts) >= 3:
        await _clear_both_sessions(update, context, parts[2])

    # bike:clear:YYYY-MM-DD:morning|evening
    elif action == "clear" and len(parts) >= 4:
        await _clear_session(update, context, parts[2], parts[3])

    else:
        logger.warning("Unhandled bike callback: %s", data)


# ============================================================
# Custom Reason Conversation
# ============================================================

@require_auth
async def _custom_reason_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Entry point: user tapped Custom… on a skip/dnb reason picker."""
    query = update.callback_query
    await query.answer()
    data = query.data
    parts = data.split(":")

    if parts[1] == "skip_reason":
        # bike:skip_reason:YYYY-MM-DD:morning|evening:custom
        date_str, session = parts[2], parts[3]
        session_label = "Morning" if session == "morning" else "Evening"
        context.user_data["bike_pending_custom"] = (date_str, session)
        prompt = f"Type your custom reason for skipping <b>{session_label}</b> on {_e(date_str)}:"
    else:
        # bike:dnb_reason:YYYY-MM-DD:custom
        date_str = parts[2]
        context.user_data["bike_pending_custom"] = (date_str, "both")
        prompt = f"Type your custom reason for not biking on {_e(date_str)}:"

    await query.edit_message_text(
        f"✏️ <b>Custom Reason</b>\n\n{prompt}\n\n/cancel to go back",
        parse_mode="HTML",
    )
    return CUSTOM_REASON_STATE


@require_auth
async def _receive_custom_reason(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Received the typed custom reason. Save and show date view."""
    user = context.user_data["current_user"]
    custom_text = update.message.text.strip()
    pending = context.user_data.pop("bike_pending_custom", None)

    if not pending:
        await update.message.reply_text("Something went wrong. Try again with /bike.")
        return ConversationHandler.END

    date_str, session = pending
    d = date.fromisoformat(date_str)

    async with async_session_factory() as db:
        day = await _get_or_create_day(db, user.id, d)
        if session in ("both", "morning"):
            day.morning_rode = False
            day.morning_reason = "custom"
            day.morning_custom = custom_text
        if session in ("both", "evening"):
            day.evening_rode = False
            day.evening_reason = "custom"
            day.evening_custom = custom_text
        await db.commit()

    # New message (we received text, no callback message to edit)
    await _show_date_view(update, context, d, edit=False)
    return ConversationHandler.END


async def _cancel_custom(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop("bike_pending_custom", None)
    await update.message.reply_text("Cancelled. Use /bike to continue.")
    return ConversationHandler.END


def get_custom_reason_conversation_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(
                _custom_reason_start,
                pattern=r"^bike:skip_reason:.+:(morning|evening):custom$",
            ),
            CallbackQueryHandler(
                _custom_reason_start,
                pattern=r"^bike:dnb_reason:.+:custom$",
            ),
        ],
        states={
            CUSTOM_REASON_STATE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, _receive_custom_reason),
            ],
        },
        fallbacks=[CommandHandler("cancel", _cancel_custom)],
        conversation_timeout=120,
        per_message=False,
    )
