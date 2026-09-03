"""Engine SQLAlchemy, sessão e helpers de transação."""
from __future__ import annotations

import importlib
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from agenda import config

_is_sqlite = config.DATABASE_URL.startswith("sqlite")

engine = create_engine(
    config.DATABASE_URL,
    pool_pre_ping=True,
    future=True,
    connect_args={"check_same_thread": False} if _is_sqlite else {},
)

if _is_sqlite:

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):  # pragma: no cover - infra
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.close()


SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)


class Base(DeclarativeBase):
    pass


@contextmanager
def session_scope() -> Iterator[Session]:
    """Sessão transacional: commit no sucesso, rollback no erro."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def init_db() -> None:
    """Garante o schema em desenvolvimento e testes.

    Em produção o schema é responsabilidade das migrations (`alembic upgrade
    head`, executado no start). Criar tabelas automaticamente em produção
    esconde divergências de schema e impede rollback controlado.
    """
    importlib.import_module("agenda.models")  # registra os modelos no metadata
    if config.IS_PRODUCTION and not config.AUTO_CREATE_TABLES:
        return
    Base.metadata.create_all(engine)
