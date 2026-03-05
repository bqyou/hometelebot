"""
CLI script to create new bot users.

Usage:
    python scripts/create_user.py --username alice --pin 1234
    python scripts/create_user.py --username alice --pin 1234 --admin
    python scripts/create_user.py --username alice --pin 1234 --name "Alice Tan"

The PIN is hashed with bcrypt before storage. The plaintext PIN
is never stored anywhere.
"""

import argparse
import asyncio
import sys
import os

# Add project root to path so imports work
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select
from core.database import init_db, async_session_factory
from core.models import User
from core.auth import hash_pin


async def create_user(
    username: str,
    pin: str,
    display_name: str | None = None,
    is_admin: bool = False,
) -> None:
    """Create a new user in the database."""
    await init_db()

    async with async_session_factory() as db:
        # Check if username already exists
        result = await db.execute(
            select(User).where(User.username == username)
        )
        existing = result.scalar_one_or_none()

        if existing:
            print(f"ERROR: Username '{username}' already exists (id={existing.id}).")
            print("Use a different username or delete the existing user first.")
            return

        # Hash the PIN
        pin_hashed = hash_pin(pin)

        # Create the user
        user = User(
            username=username.lower().strip(),
            pin_hash=pin_hashed,
            display_name=display_name,
            is_admin=is_admin,
        )
        db.add(user)
        await db.commit()

        print(f"User created successfully:")
        print(f"  ID:       {user.id}")
        print(f"  Username: {user.username}")
        print(f"  Name:     {user.display_name or '(not set)'}")
        print(f"  Admin:    {user.is_admin}")
        print(f"\nThe user can now /login on Telegram with this username and PIN.")


def main():
    parser = argparse.ArgumentParser(description="Create a new TeleBot user")
    parser.add_argument("--username", "-u", required=True, help="Login username")
    parser.add_argument("--pin", "-p", required=True, help="4-6 digit PIN")
    parser.add_argument("--name", "-n", default=None, help="Display name (optional)")
    parser.add_argument("--admin", action="store_true", help="Grant admin privileges")

    args = parser.parse_args()

    # Validate PIN
    if not args.pin.isdigit() or len(args.pin) < 4 or len(args.pin) > 6:
        print("ERROR: PIN must be 4-6 digits (numbers only).")
        sys.exit(1)

    asyncio.run(create_user(
        username=args.username,
        pin=args.pin,
        display_name=args.name,
        is_admin=args.admin,
    ))


if __name__ == "__main__":
    main()
