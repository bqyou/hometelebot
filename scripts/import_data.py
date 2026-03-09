"""Import JSON export into target database (PostgreSQL on Railway).

Usage:
  python scripts/import_data.py postgresql+asyncpg://postgres:PASSWORD@HOST:PORT/railway
"""
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

if len(sys.argv) < 2:
    print("Usage: python scripts/import_data.py <DATABASE_URL>")
    print("Example: python scripts/import_data.py postgresql+asyncpg://postgres:pass@host:port/railway")
    sys.exit(1)

# Override before any app imports so pydantic-settings picks it up
os.environ["DATABASE_URL"] = sys.argv[1]

from sqlalchemy import text
from core.database import async_session_factory, engine

print(f"Connecting to: {engine.url}")

# Tables in dependency order (parents before children)
TABLE_ORDER = [
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
    with open("export.json") as f:
        data = json.load(f)

    async with async_session_factory() as db:
        for table in TABLE_ORDER:
            rows = data.get(table, [])
            if not rows:
                print(f"  {table}: empty, skipping")
                continue

            cols = list(rows[0].keys())
            col_str = ", ".join(f'"{c}"' for c in cols)
            val_str = ", ".join(f":{c}" for c in cols)

            inserted = 0
            for row in rows:
                try:
                    await db.execute(
                        text(
                            f'INSERT INTO "{table}" ({col_str}) VALUES ({val_str})'
                            f" ON CONFLICT DO NOTHING"
                        ),
                        row,
                    )
                    inserted += 1
                except Exception as e:
                    print(f"  WARN row in {table}: {e}")

            await db.commit()
            print(f"  {table}: {inserted}/{len(rows)} rows imported")

    print("\nDone.")


asyncio.run(main())
