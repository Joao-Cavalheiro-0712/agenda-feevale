"""Camada de banco: engine SQLAlchemy + modelos ORM."""
from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

import config

engine = create_engine(
    config.DATABASE_URL,
    pool_pre_ping=True,
    connect_args={"check_same_thread": False}
    if config.DATABASE_URL.startswith("sqlite")
    else {},
)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class Event(Base):
    """Uma avaliação, prova ou trabalho detectado num cronograma."""

    __tablename__ = "events"
    __table_args__ = (
        UniqueConstraint("title", "date", "discipline", name="uq_event_identity"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(300))
    kind: Mapped[str] = mapped_column(String(40), default="avaliacao")  # prova/trabalho/avaliacao
    date: Mapped[dt.date] = mapped_column(Date, index=True)
    discipline: Mapped[str] = mapped_column(String(200), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    source_file: Mapped[str] = mapped_column(String(300), default="")

    notified_by_day: Mapped[str] = mapped_column(String(120), default="")  # ex: "7,1"
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    def days_notified(self) -> set[int]:
        return {int(x) for x in self.notified_by_day.split(",") if x.strip()}

    def mark_notified(self, day: int) -> None:
        s = self.days_notified()
        s.add(day)
        self.notified_by_day = ",".join(str(d) for d in sorted(s))


class Subscriber(Base):
    """Chats do Telegram que receberão as notificações."""

    __tablename__ = "subscribers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[str] = mapped_column(String(60), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


def init_db() -> None:
    Base.metadata.create_all(engine)
