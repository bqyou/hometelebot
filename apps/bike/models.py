"""Database models for the Bike Ride Tracker mini app."""

from datetime import date

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)

from core.database import Base, utc_now


class BikeDay(Base):
    """Tracks morning and evening bike sessions for a school day."""

    __tablename__ = "bike_days"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    date = Column(Date, nullable=False)

    # Per-session tracking: None=unrecorded, True=rode, False=skipped
    morning_rode = Column(Boolean, nullable=True)
    morning_reason = Column(String(20), nullable=True)   # school_closure/rain/vacation/custom
    morning_custom = Column(String(200), nullable=True)

    evening_rode = Column(Boolean, nullable=True)
    evening_reason = Column(String(20), nullable=True)
    evening_custom = Column(String(200), nullable=True)

    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)

    __table_args__ = (
        UniqueConstraint("user_id", "date", name="uq_bike_user_date"),
    )

    def __repr__(self) -> str:
        return f"<BikeDay(user={self.user_id}, date={self.date})>"
