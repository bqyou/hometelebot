"""Export all data from local DB to JSON for migration."""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import text
from core.database import async_session_factory

TABLES = [
    "users",
    "sessions",
    "user_app_settings",
    "bike_days",
    "inventory_items",
    "grocery_lists",
    "grocery_list_members",
    "grocery_items",
    "menu_weeks",
    "menu_items",
]


async def main():
    data = {}
    async with async_session_factory() as db:
        for table in TABLES:
            try:
                result = await db.execute(text(f"SELECT * FROM {table}"))
                rows = [dict(r._mapping) for r in result.fetchall()]
                data[table] = rows
                print(f"  {table}: {len(rows)} rows")
            except Exception as e:
                print(f"  {table}: skipped ({e})")
                data[table] = []

    with open("export.json", "w") as f:
        json.dump(data, f, indent=2, default=str)
    print("\nSaved export.json")


asyncio.run(main())
