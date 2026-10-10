"""Общая настройка тестов: отдельная временная база, чистая на каждый тест."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

# Путь к базе задаём до импорта приложения: app.config читает его при загрузке.
_TMP_DIR = Path(tempfile.mkdtemp(prefix="sklad-tests-"))
os.environ["SKLAD_DB_PATH"] = str(_TMP_DIR / "test.db")
os.environ["SKLAD_SECRET_KEY"] = "test-secret-key-not-for-production"
os.environ["SKLAD_TZ_OFFSET_HOURS"] = "3"

from app import roles, security, statuses  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.models import (  # noqa: E402
    Article,
    Counter,
    Employee,
    Mirror,
    Movement,
    PrintBatch,
    Site,
)
from app.articles import group_letters, match_key  # noqa: E402

TEST_PIN = "4321"


@pytest.fixture(autouse=True)
def clean_db():
    """Пересоздать содержимое базы перед каждым тестом."""
    init_db()
    with SessionLocal() as session:
        # Порядок важен: сначала то, что ссылается на другие таблицы.
        for model in (Movement, Mirror, PrintBatch, Article, Employee, Counter):
            session.query(model).delete()
        session.commit()
    yield


@pytest.fixture
def db():
    with SessionLocal() as session:
        yield session


@pytest.fixture
def sites(db):
    rows = db.query(Site).order_by(Site.sort_order).all()
    return rows


@pytest.fixture
def site(sites):
    return sites[0]


@pytest.fixture
def other_site(sites):
    return sites[1]


@pytest.fixture
def worker(db):
    employee = Employee(name="Тестов Иван", pin_hash=security.hash_pin(TEST_PIN))
    db.add(employee)
    db.commit()
    return employee


@pytest.fixture
def second_worker(db):
    employee = Employee(name="Пробин Пётр", pin_hash=security.hash_pin(TEST_PIN))
    db.add(employee)
    db.commit()
    return employee


def make_employee(db, name: str, role: str) -> Employee:
    employee = Employee(name=name, pin_hash=security.hash_pin(TEST_PIN), role=role)
    db.add(employee)
    db.commit()
    return employee


@pytest.fixture
def loader(db):
    """Грузчик: только отгрузка."""
    return make_employee(db, "Грузов Глеб", roles.LOADER)


@pytest.fixture
def foreman(db):
    """Бригадир: печать, приёмка и отгрузка."""
    return make_employee(db, "Бригадов Борис", roles.FOREMAN)


@pytest.fixture
def admin(db):
    return make_employee(db, "Админов Антон", roles.ADMIN)


def make_article(db, code: str) -> Article:
    article = Article(
        code=code,
        code_key=match_key(code),
        group_letters=group_letters(code),
        source_sheet="test",
    )
    db.add(article)
    db.commit()
    return article


@pytest.fixture
def article(db):
    return make_article(db, "Zerkalo 600x800")


@pytest.fixture
def statuses_module():
    return statuses
