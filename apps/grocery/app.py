"""Grocery List mini app. Auto-discovered by the registry."""

from telegram.ext import Application, CommandHandler, CallbackQueryHandler

from core.registry import BaseMiniApp
from apps.grocery.models import GroceryList, GroceryListMember, GroceryItem
from apps.grocery.handlers import (
    grocery_command,
    grocery_callback,
    get_add_items_conversation_handler,
)


class GroceryApp(BaseMiniApp):
    """Shared family grocery list with check-off and quick-add."""

    @property
    def name(self) -> str:
        return "grocery"

    @property
    def description(self) -> str:
        return "Grocery List"

    @property
    def commands(self) -> list[dict[str, str]]:
        return [
            {"command": "grocery", "description": "View or manage your grocery list"},
        ]

    def register_handlers(self, app: Application) -> None:
        app.add_handler(CommandHandler("grocery", grocery_command))

        # Add-items conversation (triggered by inline button)
        app.add_handler(get_add_items_conversation_handler())

        # All other grocery callbacks (toggle, clear, refresh)
        app.add_handler(
            CallbackQueryHandler(grocery_callback, pattern=r"^groc:(refresh|clear|toggle:\d+)$")
        )

    def get_models(self) -> list:
        return [GroceryList, GroceryListMember, GroceryItem]
