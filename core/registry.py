"""
Mini App Plugin System.

BaseMiniApp: Abstract base class that every mini app must inherit from.
MiniAppRegistry: Auto-discovers all mini apps in the apps/ directory at startup.

To add a new mini app:
1. Create a folder under apps/ (e.g., apps/my_feature/)
2. Create app.py with a class inheriting BaseMiniApp
3. Implement the required properties and methods
4. Restart the bot. The registry finds it automatically.
"""

import importlib
import logging
import pkgutil
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from telegram.ext import Application

logger = logging.getLogger(__name__)


class BaseMiniApp(ABC):
    """Abstract base class for all mini apps.
    
    Every mini app must implement:
    - name: unique string identifier (e.g., "inventory")
    - description: one-line text shown in /help
    - commands: list of dicts with "command" and "description" keys
    - register_handlers(): hook Telegram handlers into the bot
    
    Optionally override:
    - get_models(): return SQLAlchemy model classes for auto table creation
    - get_scheduled_jobs(): return dicts describing periodic tasks
    - on_startup(): called once after all handlers are registered
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique identifier for this mini app (e.g., 'inventory')."""
        ...

    @property
    @abstractmethod
    def description(self) -> str:
        """One-line description shown in the /help listing."""
        ...

    @property
    @abstractmethod
    def commands(self) -> list[dict[str, str]]:
        """Commands this app registers.
        
        Returns a list like:
        [
            {"command": "inv", "description": "Open inventory manager"},
            {"command": "inv_add", "description": "Quick-add an item"},
        ]
        """
        ...

    @abstractmethod
    def register_handlers(self, app: Application) -> None:
        """Register all Telegram handlers (CommandHandler, CallbackQueryHandler, etc.)."""
        ...

    def get_models(self) -> list[Any]:
        """Return SQLAlchemy model classes for database table creation.
        
        Override this if your app needs database tables.
        Example: return [InventoryItem]
        """
        return []

    def get_scheduled_jobs(self) -> list[dict]:
        """Return a list of scheduled jobs to register.
        
        Each dict must have:
        - "callback": async callable(context) to run
        - "interval": seconds between runs (int)
        - "name": unique job name (str)
        
        Optional:
        - "first": seconds until first run (int, default 10)
        """
        return []

    async def on_startup(self) -> None:
        """Called once after all handlers are registered. Use for initialization."""
        pass


class MiniAppRegistry:
    """Discovers and registers all mini apps found in the apps/ package."""

    def __init__(self) -> None:
        self._apps: dict[str, BaseMiniApp] = {}

    @property
    def apps(self) -> dict[str, BaseMiniApp]:
        """Read-only access to registered apps."""
        return dict(self._apps)

    def discover_apps(self, apps_package: str = "apps") -> None:
        """Scan the apps/ directory for modules containing BaseMiniApp subclasses.
        
        Discovery logic:
        1. Walk all sub-packages of the apps_package
        2. For each module named "app" (e.g., apps.inventory.app), import it
        3. Find any class that is a subclass of BaseMiniApp
        4. Instantiate it and register it by name
        """
        package = importlib.import_module(apps_package)
        package_path = Path(package.__file__).parent

        for finder, module_name, is_pkg in pkgutil.walk_packages(
            [str(package_path)], prefix=f"{apps_package}."
        ):
            # Only look at modules named "app" inside each sub-package
            if not module_name.endswith(".app"):
                continue

            try:
                module = importlib.import_module(module_name)
            except Exception as exc:
                logger.error(f"Failed to import {module_name}: {exc}")
                continue

            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if (
                    isinstance(attr, type)
                    and issubclass(attr, BaseMiniApp)
                    and attr is not BaseMiniApp
                ):
                    try:
                        instance = attr()
                        if instance.name in self._apps:
                            logger.warning(
                                f"Duplicate mini app name '{instance.name}' "
                                f"from {module_name}. Skipping."
                            )
                            continue
                        self._apps[instance.name] = instance
                        logger.info(
                            f"Discovered mini app: {instance.name} "
                            f"({len(instance.commands)} commands)"
                        )
                    except Exception as exc:
                        logger.error(f"Failed to instantiate {attr_name} from {module_name}: {exc}")

        logger.info(f"Total mini apps discovered: {len(self._apps)}")

    def register_all(self, telegram_app: Application) -> None:
        """Register all discovered apps' handlers and scheduled jobs with the Telegram bot."""
        for name, app in self._apps.items():
            try:
                app.register_handlers(telegram_app)
                logger.info(f"Registered handlers for '{name}'")
            except Exception as exc:
                logger.error(f"Failed to register handlers for '{name}': {exc}")
                continue

            # Register scheduled jobs
            for job in app.get_scheduled_jobs():
                first_run = job.get("first", 10)
                telegram_app.job_queue.run_repeating(
                    job["callback"],
                    interval=job["interval"],
                    first=first_run,
                    name=job["name"],
                )
                logger.info(
                    f"Scheduled job '{job['name']}' for '{name}' "
                    f"(every {job['interval']}s)"
                )

    async def startup_all(self) -> None:
        """Call on_startup() for every registered mini app."""
        for name, app in self._apps.items():
            try:
                await app.on_startup()
            except Exception as exc:
                logger.error(f"Startup failed for '{name}': {exc}")

    # Emoji hint per app name — extend as new apps are added
    _APP_ICONS: dict[str, str] = {
        "inventory": "\U0001f4e6",   # 📦
        "food_menu": "\U0001f371",   # 🍱
        "grocery":   "\U0001f6d2",   # 🛒
    }

    def get_help_text(self) -> str:
        """Generate the /help text dynamically from all registered apps."""
        lines = ["\U0001f4cb <b>Commands</b>", ""]

        for name, app in sorted(self._apps.items()):
            icon = self._APP_ICONS.get(name, "\u25ab")  # ▫ fallback
            lines.append(f"\u250c {icon} <b>{app.description}</b>")
            for cmd in app.commands:
                lines.append(f"\u2502  /{cmd['command']} \u00b7 {cmd['description']}")
            lines.append("\u2514")
            lines.append("")

        lines.append(f"\u250c \u2699\ufe0f <b>System</b>")
        lines.append(f"\u2502  /login \u00b7 Sign in with your PIN")
        lines.append(f"\u2502  /logout \u00b7 End your session")
        lines.append(f"\u2502  /help \u00b7 Show this message")
        lines.append(f"\u2502  /cancel \u00b7 Cancel current operation")
        lines.append("\u2514")

        return "\n".join(lines)

    def get_all_commands(self) -> list[dict[str, str]]:
        """Return a flat list of all commands across all apps.
        
        Used to set the bot's command menu via set_my_commands().
        """
        all_commands = []
        for name, app in sorted(self._apps.items()):
            all_commands.extend(app.commands)
        # Add system commands
        all_commands.extend([
            {"command": "login", "description": "Log in with username and PIN"},
            {"command": "logout", "description": "End your session"},
            {"command": "help", "description": "Show all available commands"},
        ])
        return all_commands
