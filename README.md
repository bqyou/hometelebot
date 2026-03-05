# TeleBot Platform

A modular Telegram bot with pluggable mini apps for personal and household use. Built with Python, designed for free-tier hosting.

## Features

- **Plugin architecture**: add new mini apps by creating a folder, no core changes needed
- **Authentication**: username + PIN login with session management and brute-force protection
- **Built-in mini apps**:
  - **Inventory Tracker**: track household items, quantities, low-stock alerts
  - **Tingkat Food Menu**: auto-scrapes weekly tingkat delivery menu, filterable by day/meal
  - **Grocery List**: shared family shopping list with check-off

## Quick Start

### 1. Create a Telegram Bot

1. Open Telegram and message [@BotFather](https://t.me/BotFather)
2. Send `/newbot` and follow the prompts
3. Copy the bot token (looks like `123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11`)

### 2. Set Up the Project

```bash
# Clone the repo
git clone <your-repo-url>
cd telebot-platform

# Create a virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env          # Linux/macOS
copy .env.example .env        # Windows
# Edit .env and add your TELEGRAM_BOT_TOKEN
```

### 3. Create Your First User

```bash
python scripts/create_user.py --username alice --pin 1234 --name "Alice"
# PIN must be 4-6 digits. Add --admin to grant admin privileges.
```

### 4. Run the Bot

```bash
python main.py
```

### 5. Start Using It

1. Open Telegram and find your bot
2. Send `/start`
3. Send `/login`, enter your username and PIN
4. Send `/help` to see all available commands

## Project Structure

```
telebot-platform/
|-- main.py                     # Entry point
|-- config.py                   # Environment config
|-- core/
|   |-- auth.py                 # Login, sessions, PIN verification
|   |-- database.py             # SQLAlchemy engine and session
|   |-- models.py               # User, Session models
|   |-- registry.py             # Mini app auto-discovery
|-- apps/
|   |-- inventory/              # Inventory tracker mini app
|   |   |-- app.py              # Plugin registration
|   |   |-- models.py           # InventoryItem model
|   |   |-- handlers.py         # Telegram handlers
|   |-- food_menu/              # Tingkat menu mini app
|   |   |-- app.py
|   |   |-- models.py
|   |   |-- scraper.py          # Website scraper
|   |   |-- handlers.py
|   |-- grocery/                # Grocery list mini app
|   |   |-- app.py
|   |   |-- models.py
|   |   |-- handlers.py
|-- scripts/
|   |-- create_user.py          # CLI user creation
|-- Dockerfile
|-- docker-compose.yml
```

## Adding a New Mini App

1. Create a new folder under `apps/`:
   ```bash
   mkdir -p apps/my_feature
   touch apps/my_feature/__init__.py
   touch apps/my_feature/app.py
   touch apps/my_feature/models.py
   touch apps/my_feature/handlers.py
   ```

2. In `app.py`, create a class inheriting from `BaseMiniApp`:
   ```python
   from core.registry import BaseMiniApp
   from telegram.ext import Application, CommandHandler

   class MyFeatureApp(BaseMiniApp):
       @property
       def name(self) -> str:
           return "my_feature"

       @property
       def description(self) -> str:
           return "My Cool Feature"

       @property
       def commands(self) -> list[dict[str, str]]:
           return [
               {"command": "myfeature", "description": "Do something cool"},
           ]

       def register_handlers(self, app: Application) -> None:
           from apps.my_feature.handlers import my_handler
           app.add_handler(CommandHandler("myfeature", my_handler))
   ```

3. Restart the bot. The registry discovers it automatically.

That is it. No changes to main.py, no changes to the registry, no config files.

## Deployment

### Railway (Free Tier)

1. Push your code to GitHub
2. Go to [railway.app](https://railway.app) and create a new project
3. Connect your GitHub repo
4. Add environment variables (TELEGRAM_BOT_TOKEN, DATABASE_URL)
5. Deploy

For PostgreSQL, add a Postgres plugin in Railway or use [Neon.tech](https://neon.tech) free tier.

### Docker

```bash
docker-compose up -d
```

### Fly.io

```bash
fly launch
fly secrets set TELEGRAM_BOT_TOKEN=your-token-here
fly deploy
```

## Bot Commands Reference

| Command | Description |
|---------|-------------|
| `/start` | Welcome message |
| `/login` | Log in with username and PIN |
| `/logout` | End your session |
| `/help` | Show all available commands |
| `/inv` | View inventory |
| `/inv_add` | Quick-add inventory item |
| `/menu` | View tingkat food menu |
| `/menu_refresh` | Force refresh menu from website |
| `/grocery` | View or manage grocery list (add/clear via inline buttons) |
| `/cancel` | Cancel current operation |

## Tech Stack

- **Python 3.11+** with async/await
- **python-telegram-bot v20** for the Telegram interface
- **SQLAlchemy 2.0** (async) for database ORM
- **SQLite** (dev) / **PostgreSQL** (prod)
- **httpx + BeautifulSoup** for web scraping
- **bcrypt** for PIN hashing
