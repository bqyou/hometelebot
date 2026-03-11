"""Database models for the Inventory Tracker mini app."""

from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
)

from core.database import Base


class InventoryItem(Base):
    """A single tracked inventory item belonging to a user."""

    __tablename__ = "inventory_items"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    name = Column(String(100), nullable=False)
    quantity = Column(Integer, default=0)
    unit = Column(String(20), default="pcs")
    low_stock_threshold = Column(Integer, default=5)
    category = Column(String(50), default="General")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None), onupdate=lambda: datetime.now(timezone.utc))

    def __repr__(self) -> str:
        return f"<InventoryItem(name='{self.name}', qty={self.quantity})>"

    @property
    def is_low_stock(self) -> bool:
        """True if quantity is at or below the low stock threshold."""
        return self.quantity <= self.low_stock_threshold

    def display_line(self) -> str:
        """Format this item as a single line for the inventory list."""
        warning = " \u26a0\ufe0f" if self.is_low_stock else ""
        return f"{self.name}: {self.quantity} {self.unit}{warning}"
