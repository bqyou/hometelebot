"""
Tingkat Delivery menu scraper.

Scrapes https://tingkatdelivery.com/regular-tingkat/
and parses the weekly menu into structured data.

HTML structure (Elementor-based):
- Each week: div.elementor-toggle-item
  - Date range: a.elementor-toggle-title (e.g. "02 Mar - 06 Mar 2026")
  - Day tabs: div.elementor-tab-title.elementor-tab-desktop-title (Mon/Tue/Wed/Thu/Fri)
  - Day content: div.elementor-tab-content[role="tabpanel"] (one per day)
    - div.menu-row
      - div.menu-col          -> Lunch
      - div.menu-col.right-menu -> Dinner
        - h2: "Lunch" or "Dinner"
        - <p> tags, each: <b>Course Label</b><br/>English Name<br/>Chinese Name
"""

import logging
import re
from datetime import datetime, date

import httpx
from bs4 import BeautifulSoup, Tag

logger = logging.getLogger(__name__)

TINGKAT_URL = "https://tingkatdelivery.com/regular-tingkat/"

DAY_MAP = {
    "mon": "Monday",
    "tue": "Tuesday",
    "wed": "Wednesday",
    "thu": "Thursday",
    "fri": "Friday",
}

COURSE_ORDER = ["soup", "dish_1", "dish_2", "dish_3", "side"]


def _parse_date_range(text: str) -> tuple[date, date] | None:
    """Parse a date range string like '02 Mar - 06 Mar 2026' into (start, end) dates."""
    text = text.strip()
    # Cross-month: DD Mon - DD Mon YYYY
    pattern = r"(\d{1,2})\s+(\w{3})\s*-\s*(\d{1,2})\s+(\w{3})\s+(\d{4})"
    match = re.search(pattern, text)
    if match:
        try:
            start = datetime.strptime(f"{match.group(1)} {match.group(2)} {match.group(5)}", "%d %b %Y").date()
            end = datetime.strptime(f"{match.group(3)} {match.group(4)} {match.group(5)}", "%d %b %Y").date()
            return start, end
        except ValueError:
            pass

    # Same-month: DD - DD Mon YYYY
    pattern2 = r"(\d{1,2})\s*-\s*(\d{1,2})\s+(\w{3})\s+(\d{4})"
    match2 = re.search(pattern2, text)
    if match2:
        try:
            start = datetime.strptime(f"{match2.group(1)} {match2.group(3)} {match2.group(4)}", "%d %b %Y").date()
            end = datetime.strptime(f"{match2.group(2)} {match2.group(3)} {match2.group(4)}", "%d %b %Y").date()
            return start, end
        except ValueError:
            pass

    return None


def _parse_course_key(label: str) -> str | None:
    """Map a course label like 'Dish #1' to the internal key 'dish_1'."""
    label = label.strip().lower()
    if re.match(r"^soup$", label):
        return "soup"
    if re.match(r"^dish\s*#?1$", label):
        return "dish_1"
    if re.match(r"^dish\s*#?2$", label):
        return "dish_2"
    if re.match(r"^dish\s*#?3$", label):
        return "dish_3"
    if re.match(r"^dish without soup\s*#?4$", label):
        return "side"
    return None


def _parse_dish_paragraph(p: Tag) -> dict | None:
    """
    Parse a <p> tag containing a dish entry.

    Expected structure:
      <p><b>Course Label</b><br/>English Name<br/>Chinese Name (optional)</p>

    Returns a dish dict or None if this is not a valid dish paragraph.
    """
    b_tag = p.find("b")
    if not b_tag:
        return None

    course_key = _parse_course_key(b_tag.get_text(strip=True))
    if not course_key:
        return None

    # Collect text segments that appear after the <b> tag, using <br> as separators
    lines = []
    past_b = False
    for child in p.children:
        if not past_b:
            if getattr(child, 'name', None) == 'b':
                past_b = True
            continue
        if getattr(child, 'name', None) == 'br':
            continue  # separator, not content
        if getattr(child, 'name', None) is not None:
            # Nested tag — extract its text
            text = child.get_text(strip=True)
        else:
            text = str(child).strip()
        if text:
            lines.append(text)

    if not lines:
        return None

    name_en = lines[0].lstrip("*").strip()

    # Detect spicy — website uses both （Spicy） fullwidth and (Spicy) regular parens
    is_spicy = bool(re.search(r"[（(]\s*[Ss]picy\s*[）)]", name_en))
    if is_spicy:
        name_en = re.sub(r"\s*[（(]\s*[Ss]picy\s*[）)]\s*", "", name_en).strip()

    if not name_en:
        return None

    # Second line is Chinese if it contains CJK characters
    name_zh = None
    if len(lines) >= 2:
        candidate = lines[1].lstrip("*").strip()
        if re.search(r"[\u4e00-\u9fff]", candidate):
            name_zh = candidate

    return {
        "course": course_key,
        "name_en": name_en,
        "name_zh": name_zh,
        "is_spicy": is_spicy,
    }


def parse_menu_from_html(html: str) -> list[dict]:
    """Parse raw HTML into structured menu data using DOM structure."""
    soup = BeautifulSoup(html, "lxml")
    results = []

    toggle_items = soup.select("div.elementor-toggle-item")
    logger.info(f"Found {len(toggle_items)} week toggle items")

    for toggle_item in toggle_items:
        title_el = toggle_item.select_one("a.elementor-toggle-title")
        if not title_el:
            continue

        date_text = title_el.get_text(strip=True)
        parsed = _parse_date_range(date_text)
        if not parsed:
            logger.warning(f"Could not parse date range: {date_text!r} — skipping")
            continue

        week_start, week_end = parsed
        week_data: dict = {"week_start": week_start, "week_end": week_end, "days": {}}
        logger.info(f"Parsing week: {week_start} to {week_end}")

        # Day tabs: desktop titles give Mon/Tue/etc, tabpanels give content
        tab_titles = toggle_item.select("div.elementor-tab-title.elementor-tab-desktop-title")
        tab_panels = toggle_item.select("div.elementor-tab-content[role='tabpanel']")

        if not tab_titles or not tab_panels:
            logger.warning(f"Week {week_start}: no tab titles or panels found — skipping")
            continue

        if len(tab_titles) != len(tab_panels):
            logger.warning(
                f"Week {week_start}: {len(tab_titles)} tab titles but {len(tab_panels)} panels — "
                f"parsing what we can"
            )

        for tab_title_el, tab_panel in zip(tab_titles, tab_panels):
            day_short = tab_title_el.get_text(strip=True).lower()
            day_name = DAY_MAP.get(day_short)
            if not day_name:
                logger.warning(f"Unknown day tab label: {day_short!r} — skipping")
                continue

            day_data: dict = {"lunch": [], "dinner": []}

            menu_cols = tab_panel.select("div.menu-col")
            for col in menu_cols:
                h2 = col.find("h2")
                if not h2:
                    continue
                meal_label = h2.get_text(strip=True).lower()
                if meal_label not in ("lunch", "dinner"):
                    continue

                dishes = []
                for p in col.find_all("p"):
                    dish = _parse_dish_paragraph(p)
                    if dish:
                        dishes.append(dish)

                day_data[meal_label] = dishes
                logger.debug(f"  {day_name} {meal_label}: {len(dishes)} dishes")

            week_data["days"][day_name] = day_data

        total_dishes = sum(
            len(day[meal])
            for day in week_data["days"].values()
            for meal in ("lunch", "dinner")
        )
        logger.info(f"Week {week_start}: {len(week_data['days'])} days, {total_dishes} dishes")

        if week_data["days"]:
            results.append(week_data)

    return results


async def scrape_menu() -> list[dict]:
    """Scrape the Tingkat Delivery menu page and return structured week data."""
    logger.info(f"Scraping menu from {TINGKAT_URL}")

    async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
        response = await client.get(TINGKAT_URL)
        response.raise_for_status()

    results = parse_menu_from_html(response.text)

    total_items = sum(
        len(meals[m])
        for w in results
        for meals in w["days"].values()
        for m in meals
    )
    logger.info(f"Scrape complete: {len(results)} week(s), {total_items} item(s) parsed")

    return results
