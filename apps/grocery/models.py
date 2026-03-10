"""Database models for the Grocery List mini app.

Supports shared lists so family members can collaborate on the same list.
"""

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)

from core.database import Base


class GroceryList(Base):
    """A named grocery list. Can be shared among multiple users."""

    __tablename__ = "grocery_lists"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False, default="Shopping List")
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    def __repr__(self) -> str:
        return f"<GroceryList(name='{self.name}', owner={self.owner_id})>"


class GroceryListMember(Base):
    """Links users to shared grocery lists. The owner is always a member."""

    __tablename__ = "grocery_list_members"

    id = Column(Integer, primary_key=True, autoincrement=True)
    list_id = Column(Integer, ForeignKey("grocery_lists.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    __table_args__ = (
        UniqueConstraint("list_id", "user_id", name="uq_list_member"),
    )


class GroceryItem(Base):
    """A single item on a grocery list."""

    __tablename__ = "grocery_items"

    id = Column(Integer, primary_key=True, autoincrement=True)
    list_id = Column(Integer, ForeignKey("grocery_lists.id"), nullable=False, index=True)
    name = Column(String(100), nullable=False)
    quantity = Column(String(30), nullable=True)  # Free text like "2 packs", "500g"
    is_bought = Column(Boolean, default=False)
    added_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    bought_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    bought_at = Column(DateTime, nullable=True)

    def __repr__(self) -> str:
        status = "bought" if self.is_bought else "pending"
        return f"<GroceryItem(name='{self.name}', {status})>"

    def display_line(self) -> str:
        """Format this item for the grocery list display."""
        check = "[x]" if self.is_bought else "[ ]"
        qty = f" ({self.quantity})" if self.quantity else ""
        return f"{check} {self.name}{qty}"
