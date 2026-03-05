"""Database models for the Food Menu (Tingkat) mini app."""

from datetime import datetime, date

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)

from core.database import Base


class MenuWeek(Base):
    """A single week of tingkat menu data (Mon-Fri)."""

    __tablename__ = "menu_weeks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    week_start = Column(Date, nullable=False)
    week_end = Column(Date, nullable=False)
    raw_html = Column(Text, nullable=True)
    scraped_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("week_start", "week_end", name="uq_menu_week_range"),
    )

    def __repr__(self) -> str:
        return f"<MenuWeek({self.week_start} to {self.week_end})>"


class MenuItem(Base):
    """A single dish within a day's meal."""

    __tablename__ = "menu_items"

    id = Column(Integer, primary_key=True, autoincrement=True)
    menu_week_id = Column(Integer, ForeignKey("menu_weeks.id"), nullable=False, index=True)
    day_of_week = Column(String(10), nullable=False)      # Monday, Tuesday, ...
    meal_type = Column(String(10), nullable=False)          # lunch, dinner
    course_type = Column(String(30), nullable=False)        # soup, dish_1, dish_2, dish_3, side
    name_en = Column(String(200), nullable=False)
    name_zh = Column(String(200), nullable=True)
    is_spicy = Column(Boolean, default=False)

    def __repr__(self) -> str:
        return f"<MenuItem({self.day_of_week} {self.meal_type}: {self.name_en})>"

    def display(self) -> str:
        """Format this item for display in a Telegram message."""
        spicy_tag = " (Spicy)" if self.is_spicy else ""
        chinese = f"\n      {self.name_zh}" if self.name_zh else ""
        return f"  {self.name_en}{spicy_tag}{chinese}"
