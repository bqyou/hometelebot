"""Inventory Tracker mini app. Plugs into the TeleBot registry automatically."""

from telegram.ext import Application, CommandHandler, CallbackQueryHandler

from core.registry import BaseMiniApp
from apps.inventory.models import InventoryItem
from apps.inventory.handlers import (
    inventory_command,
    inventory_callback,
    get_add_conversation_handler,
)


class InventoryApp(BaseMiniApp):
    """Track household items, quantities, and get low-stock alerts."""

    @property
    def name(self) -> str:
        return "inventory"

    @property
    def description(self) -> str:
        return "Inventory Tracker"

    @property
    def commands(self) -> list[dict[str, str]]:
        return [
            {"command": "inv", "description": "Open inventory manager"},
        ]

    def register_handlers(self, app: Application) -> None:
        app.add_handler(CommandHandler("inv", inventory_command))

        # Add-item conversation (triggered by inline button)
        app.add_handler(get_add_conversation_handler())

        # All other inventory callbacks (edit, delete, refresh, qty adjust)
        app.add_handler(
            CallbackQueryHandler(inventory_callback, pattern=r"^inv:(refresh|edit_select|del_select|edit:\d+|del_confirm:\d+|del:\d+|qty:\d+:-?\d+)$")
        )

    def get_models(self) -> list:
        return [InventoryItem]
