"""Database models for the Cook app."""

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)

from core.database import Base, utc_now


class CookRawMaterial(Base):
    __tablename__ = "cook_raw_materials"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    name = Column(String(100), nullable=False)
    quantity = Column(Float, default=0)
    unit = Column(String(20), nullable=False)
    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)

    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_cook_raw_user_name"),)


class CookSauce(Base):
    __tablename__ = "cook_sauces"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    name = Column(String(100), nullable=False)
    created_at = Column(DateTime, default=utc_now)

    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_cook_sauce_user_name"),)


class CookEquipment(Base):
    __tablename__ = "cook_equipment"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    name = Column(String(100), nullable=False)
    created_at = Column(DateTime, default=utc_now)

    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_cook_equip_user_name"),)


class CookRecipe(Base):
    __tablename__ = "cook_recipes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    name = Column(String(200), nullable=False)
    photo_file_id = Column(String(200), nullable=True)
    servings = Column(Integer, default=1)
    steps = Column(Text, nullable=True)
    source = Column(String(20), default="manual")
    cuisine = Column(String(50), nullable=True)
    cook_time_minutes = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)


class CookRecipeIngredient(Base):
    __tablename__ = "cook_recipe_ingredients"

    id = Column(Integer, primary_key=True, autoincrement=True)
    recipe_id = Column(Integer, ForeignKey("cook_recipes.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(100), nullable=False)
    quantity = Column(Float, nullable=True)
    unit = Column(String(20), nullable=True)
    is_sauce = Column(Boolean, default=False)
    sort_order = Column(Integer, default=0)


class CookRecipeEquipment(Base):
    __tablename__ = "cook_recipe_equipment"

    id = Column(Integer, primary_key=True, autoincrement=True)
    recipe_id = Column(Integer, ForeignKey("cook_recipes.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(100), nullable=False)
