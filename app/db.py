"""Подключение к базе. Сейчас SQLite, позже PostgreSQL.

Вся работа идёт через SQLAlchemy, поэтому переход на PostgreSQL —
это смена SKLAD_DATABASE_URL и перенос данных, без правок запросов.
"""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker

from . import config, migrations
from .models import Base, Site

_is_sqlite = config.DATABASE_URL.startswith("sqlite")

engine = create_engine(
    config.DATABASE_URL,
    future=True,
    # Запросы приходят из разных потоков uvicorn, одного соединения мало.
    connect_args={"check_same_thread": False, "timeout": 15} if _is_sqlite else {},
)


if _is_sqlite:

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_connection, _record):  # pragma: no cover
        cur = dbapi_connection.cursor()
        # Внешние ключи в SQLite по умолчанию выключены.
        cur.execute("PRAGMA foreign_keys=ON")
        # WAL: чтение не блокирует запись — важно, когда сканируют
        # с нескольких телефонов одновременно.
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA busy_timeout=15000")
        cur.close()


SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)

# Площадки версии 0.1. Перемещений между ними пока нет.
DEFAULT_SITES = (
    ("kominterna", "Коминтерна", 1),
    ("uchitelskaya", "Учительская", 2),
)


def init_db() -> None:
    """Создать таблицы и завести площадки, если их ещё нет."""
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(engine)
    migrations.apply(engine)
    with SessionLocal() as session:
        for code, name, order in DEFAULT_SITES:
            exists = session.scalar(select(Site).where(Site.code == code))
            if exists is None:
                session.add(Site(code=code, name=name, sort_order=order))
        session.commit()


def get_session() -> Iterator[Session]:
    """Зависимость FastAPI: сессия БД на один запрос."""
    with SessionLocal() as session:
        yield session
