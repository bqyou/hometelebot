"""Bike Ride Tracker mini app. Auto-discovered by the registry."""

from telegram.ext import Application, CommandHandler, CallbackQueryHandler

from core.registry import BaseMiniApp
from apps.bike.models import BikeDay
from apps.bike.handlers import (
    bike_command,
    bike_callback,
    get_custom_reason_conversation_handler,
)


class BikeApp(BaseMiniApp):
    """School bike ride tracker & savings calculator."""

    @property
    def name(self) -> str:
        return "bike"

    @property
    def description(self) -> str:
        return "School bike ride tracker & savings calculator"

    @property
    def commands(self) -> list[dict[str, str]]:
        return [
            {"command": "bike", "description": "Track bike rides & view savings"},
        ]

    def register_handlers(self, app: Application) -> None:
        app.add_handler(CommandHandler("bike", bike_command))

        # Custom reason conversation must be registered before the general callback
        app.add_handler(get_custom_reason_conversation_handler())

        app.add_handler(CallbackQueryHandler(bike_callback, pattern=r"^bike:"))

    def get_models(self) -> list:
        return [BikeDay]
