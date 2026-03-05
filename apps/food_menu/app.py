"""Food Menu (Tingkat) mini app. Auto-discovered by the registry."""

from telegram.ext import Application, CommandHandler, CallbackQueryHandler

from config import settings
from core.registry import BaseMiniApp
from apps.food_menu.models import MenuWeek, MenuItem
from apps.food_menu.handlers import (
    menu_command,
    menu_callback,
    menu_refresh_command,
)


class FoodMenuApp(BaseMiniApp):
    """View the weekly Tingkat Delivery menu. Scraped and cached automatically."""

    @property
    def name(self) -> str:
        return "food_menu"

    @property
    def description(self) -> str:
        return "Tingkat Food Menu"

    @property
    def commands(self) -> list[dict[str, str]]:
        return [
            {"command": "menu", "description": "View this week's tingkat menu"},
            {"command": "menu_refresh", "description": "Force refresh menu from website"},
        ]

    def register_handlers(self, app: Application) -> None:
        app.add_handler(CommandHandler("menu", menu_command))
        app.add_handler(CommandHandler("menu_refresh", menu_refresh_command))
        app.add_handler(
            CallbackQueryHandler(menu_callback, pattern=r"^menu:")
        )

    def get_models(self) -> list:
        return [MenuWeek, MenuItem]

    def get_scheduled_jobs(self) -> list[dict]:
        return [
            {
                "callback": self._scheduled_scrape,
                "interval": settings.menu_scrape_interval_seconds,
                "first": 30,  # First scrape 30 seconds after bot starts
                "name": "tingkat_menu_scrape",
            }
        ]

    async def _scheduled_scrape(self, context) -> None:
        """Scheduled job: scrape the menu website and update the database."""
        from apps.food_menu.scraper import scrape_menu
        from apps.food_menu.models import MenuWeek, MenuItem
        from core.database import async_session_factory
        from sqlalchemy import select, func
        import logging

        logger = logging.getLogger(__name__)
        logger.info("Running scheduled menu scrape...")

        try:
            week_data_list = await scrape_menu()
            if not week_data_list:
                logger.warning("Scrape returned no weeks — skipping DB update")
                return

            inserted_weeks = 0
            inserted_items = 0

            async with async_session_factory() as db:
                for week_data in week_data_list:
                    week_start = week_data["week_start"]
                    week_end = week_data["week_end"]

                    # Check if week row exists
                    existing_result = await db.execute(
                        select(MenuWeek).where(
                            MenuWeek.week_start == week_start,
                            MenuWeek.week_end == week_end,
                        )
                    )
                    week = existing_result.scalar_one_or_none()

                    if week:
                        # Check if items already exist for this week
                        item_count_result = await db.execute(
                            select(func.count()).where(MenuItem.menu_week_id == week.id)
                        )
                        item_count = item_count_result.scalar()
                        if item_count > 0:
                            logger.info(
                                f"Week {week_start} to {week_end} already has "
                                f"{item_count} items — skipping"
                            )
                            continue
                        logger.info(
                            f"Week {week_start} to {week_end} exists but has no items "
                            f"— inserting items now"
                        )
                    else:
                        week = MenuWeek(week_start=week_start, week_end=week_end)
                        db.add(week)
                        await db.flush()
                        logger.info(f"Created new week: {week_start} to {week_end} (id={week.id})")
                        inserted_weeks += 1

                    week_item_count = 0
                    for day, meals in week_data.get("days", {}).items():
                        for meal_type, dishes in meals.items():
                            for dish in dishes:
                                item = MenuItem(
                                    menu_week_id=week.id,
                                    day_of_week=day,
                                    meal_type=meal_type,
                                    course_type=dish.get("course", "dish_1"),
                                    name_en=dish.get("name_en", ""),
                                    name_zh=dish.get("name_zh"),
                                    is_spicy=dish.get("is_spicy", False),
                                )
                                db.add(item)
                                week_item_count += 1

                    logger.info(
                        f"Queued {week_item_count} items for week "
                        f"{week_start} to {week_end}"
                    )
                    inserted_items += week_item_count

                await db.commit()
                logger.info(
                    f"Scrape complete — {inserted_weeks} new week(s), "
                    f"{inserted_items} new item(s) committed to DB"
                )

        except Exception as exc:
            logger.error(f"Scheduled menu scrape failed: {exc}", exc_info=True)
