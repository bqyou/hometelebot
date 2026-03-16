"""Cook mini app. Kitchen inventory & recipe vault with AI features."""

from telegram.ext import Application, CommandHandler, CallbackQueryHandler

from core.registry import BaseMiniApp
from apps.cook.models import (
    CookRawMaterial, CookSauce, CookEquipment,
    CookRecipe, CookRecipeIngredient, CookRecipeEquipment,
)
from apps.cook.handlers import (
    cook_command,
    cook_callback,
    get_raw_add_handler,
    get_sauce_add_handler,
    get_equip_add_handler,
)
from apps.cook.recipes import (
    recipe_callback,
    get_manual_add_handler,
    get_import_handler,
    get_generate_handler,
)


class CookApp(BaseMiniApp):
    """Kitchen inventory & recipe vault with AI-powered features."""

    @property
    def name(self) -> str:
        return "cook"

    @property
    def description(self) -> str:
        return "Cook - Kitchen & Recipes"

    @property
    def commands(self) -> list[dict[str, str]]:
        return [
            {"command": "cook", "description": "Open kitchen & recipe manager"},
        ]

    def register_handlers(self, app: Application) -> None:
        app.add_handler(CommandHandler("cook", cook_command))

        # Conversation handlers (must be registered before generic callback handlers)
        app.add_handler(get_raw_add_handler())
        app.add_handler(get_sauce_add_handler())
        app.add_handler(get_equip_add_handler())
        app.add_handler(get_manual_add_handler())
        app.add_handler(get_import_handler())
        app.add_handler(get_generate_handler())

        # Generic callback routers for non-conversation callbacks
        app.add_handler(
            CallbackQueryHandler(
                cook_callback,
                pattern=r"^cook:(menu|raw|sauce|equip|raw:|sc:|eq:)",
            )
        )
        app.add_handler(
            CallbackQueryHandler(
                recipe_callback,
                pattern=r"^cook:(book|bk:|match|wc:)",
            )
        )

    def get_models(self) -> list:
        return [
            CookRawMaterial, CookSauce, CookEquipment,
            CookRecipe, CookRecipeIngredient, CookRecipeEquipment,
        ]
