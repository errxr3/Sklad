"""Разделение ролей: кому что можно."""

import pytest

from app import roles
from tests.conftest import TEST_PIN
from tests.test_web import login_as


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


def test_права_по_ролям():
    assert roles.can(roles.ASSEMBLER, roles.MAKE)
    assert roles.can(roles.ASSEMBLER, roles.RECEIVE)
    assert not roles.can(roles.ASSEMBLER, roles.SHIP)
    assert not roles.can(roles.ASSEMBLER, roles.ADMIN_PANEL)

    assert roles.can(roles.LOADER, roles.SHIP)
    assert not roles.can(roles.LOADER, roles.MAKE)
    assert not roles.can(roles.LOADER, roles.RECEIVE)

    # Бригадир принимает за сборщиком и отгружает за грузчика.
    assert roles.can(roles.FOREMAN, roles.MAKE)
    assert roles.can(roles.FOREMAN, roles.RECEIVE)
    assert roles.can(roles.FOREMAN, roles.SHIP)
    assert not roles.can(roles.FOREMAN, roles.ADMIN_PANEL)

    for permission in (roles.MAKE, roles.RECEIVE, roles.SHIP, roles.ADMIN_PANEL):
        assert roles.can(roles.ADMIN, permission)


def test_сборщику_закрыта_отгрузка(client, db, worker):
    login_as(client, db, worker)
    answer = client.get("/ship")
    assert answer.status_code == 403
    assert "Отгрузка" in answer.text
    assert "Сборщик" in answer.text


def test_грузчику_закрыта_печать_и_приёмка(client, db, loader):
    login_as(client, db, loader)
    assert client.get("/make").status_code == 403
    assert client.get("/receive").status_code == 403
    assert client.get("/ship").status_code == 200


def test_бригадиру_открыто_всё_кроме_админки(client, db, foreman):
    login_as(client, db, foreman)
    assert client.get("/make").status_code == 200
    assert client.get("/receive").status_code == 200
    assert client.get("/ship").status_code == 200
    assert client.get("/admin").status_code == 403


def test_админу_открыто_всё(client, db, admin):
    login_as(client, db, admin)
    for path in ("/make", "/receive", "/ship", "/admin"):
        assert client.get(path).status_code == 200, path


def test_на_главной_видны_только_свои_кнопки(client, db, loader):
    login_as(client, db, loader)
    page = client.get("/").text
    assert "Отгрузка" in page
    assert "Сделал зеркало" not in page
    assert "Приёмка на склад" not in page
    assert "Админ-панель" not in page


def test_бригадир_принимает_а_зеркало_остаётся_за_сборщиком(
    client, db, worker, foreman, article
):
    """Проверка сборки: принимает бригадир, но сборщик в карточке прежний."""
    login_as(client, db, worker)
    client.post("/make", data={"article_id": article.id, "qty": "1"})

    client.post("/logout")
    login_as(client, db, foreman)
    answer = client.post("/receive", data={"code": "Z0000001"})
    assert "принято на склад" in answer.text

    card = client.get("/mirror/Z0000001").text
    assert worker.name in card          # сборщик не изменился
    assert "На складе" in card

    from app.models import Mirror

    mirror = db.query(Mirror).filter_by(number="Z0000001").one()
    db.refresh(mirror)
    assert mirror.assembler_id == worker.id


def test_отгрузку_делает_грузчик_сборщик_в_карточке_прежний(
    client, db, worker, loader, foreman, article
):
    login_as(client, db, worker)
    client.post("/make", data={"article_id": article.id, "qty": "1"})
    client.post("/receive", data={"code": "Z0000001"})
    client.post("/logout")

    login_as(client, db, loader)
    answer = client.post("/ship", data={"code": "Z0000001"})
    assert "отгружено" in answer.text

    from app.models import Mirror

    mirror = db.query(Mirror).filter_by(number="Z0000001").one()
    db.refresh(mirror)
    assert mirror.assembler_id == worker.id
