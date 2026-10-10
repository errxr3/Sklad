"""Сценарии через HTTP: вход, печать, PDF, сканы, остатки, журнал."""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models import Site
from tests.conftest import TEST_PIN, make_article


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def logged_in(client, db, worker):
    site = db.query(Site).order_by(Site.sort_order).first()
    response = client.post(
        "/login",
        data={"employee_id": worker.id, "pin": TEST_PIN, "site_id": site.id},
        follow_redirects=False,
    )
    assert response.status_code == 303
    return client


def test_без_входа_перекидывает_на_страницу_входа(client):
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_страница_входа_показывает_сотрудников_и_площадки(client, worker):
    response = client.get("/login")
    assert response.status_code == 200
    assert worker.name in response.text
    assert "Коминтерна" in response.text
    assert "Учительская" in response.text


def test_неверный_пин_не_пускает(client, db, worker):
    site = db.query(Site).order_by(Site.sort_order).first()
    client.post(
        "/login",
        data={"employee_id": worker.id, "pin": "0000", "site_id": site.id},
        follow_redirects=True,
    )
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 303


def test_главная_после_входа(logged_in, worker):
    response = logged_in.get("/")
    assert response.status_code == 200
    assert "Сделал зеркало" in response.text
    assert worker.name in response.text


def test_выход(logged_in):
    logged_in.post("/logout", follow_redirects=False)
    assert logged_in.get("/", follow_redirects=False).status_code == 303


def test_полный_сценарий_печать_приёмка_отгрузка(logged_in, db, article):
    # Выбор артикула
    page = logged_in.get("/make", params={"q": "600x800"})
    assert article.code in page.text

    # Печать двух этикеток
    created = logged_in.post(
        "/make", data={"article_id": article.id, "qty": "2"}, follow_redirects=False
    )
    assert created.status_code == 303
    batch_url = created.headers["location"]

    page = logged_in.get(batch_url)
    assert "Z0000001" in page.text and "Z0000002" in page.text

    # PDF этикеток
    pdf = logged_in.get(batch_url + "/labels.pdf")
    assert pdf.status_code == 200
    assert pdf.headers["content-type"] == "application/pdf"
    assert pdf.content.startswith(b"%PDF")

    # Приёмка сканом
    logged_in.post("/receive", data={"code": "Z0000001"}, follow_redirects=False)
    page = logged_in.get("/receive")
    assert "принято на склад" in page.text

    # Остатки
    page = logged_in.get("/stock")
    assert article.code in page.text

    # Отгрузка
    logged_in.post("/ship", data={"code": "Z0000001"}, follow_redirects=False)
    page = logged_in.get("/ship")
    assert "отгружено" in page.text

    # Журнал: три записи на зеркало Z0000001 плюс печать второго
    page = logged_in.get("/journal")
    assert "Z0000001" in page.text
    assert "Отгружено" in page.text


def test_повторный_скан_показывает_отказ(logged_in, article):
    logged_in.post("/make", data={"article_id": article.id, "qty": "1"})
    first = logged_in.post("/receive", data={"code": "Z0000001"})
    assert "msg ok" in first.text

    second = logged_in.post("/receive", data={"code": "Z0000001"})
    assert "msg err" in second.text
    assert "уже" in second.text

    # Отказ остаётся виден в списке последних сканов.
    page = logged_in.get("/receive")
    assert "log" in page.text and "уже" in page.text


def test_отгрузка_без_приёмки_показывает_отказ(logged_in, article):
    logged_in.post("/make", data={"article_id": article.id, "qty": "1"})
    logged_in.post("/ship", data={"code": "Z0000001"})
    page = logged_in.get("/ship")
    assert "запрещён" in page.text


def test_неизвестный_номер_на_экране(logged_in):
    logged_in.post("/receive", data={"code": "Z0009999"})
    page = logged_in.get("/receive")
    assert "не найдено" in page.text


def test_количество_не_числом(logged_in, article):
    response = logged_in.post(
        "/make", data={"article_id": article.id, "qty": "пять"}, follow_redirects=True
    )
    assert "количество числом" in response.text


def test_карточка_зеркала(logged_in, article, worker):
    logged_in.post("/make", data={"article_id": article.id, "qty": "1"})
    page = logged_in.get("/mirror/Z0000001")
    assert page.status_code == 200
    assert article.code in page.text
    assert worker.name in page.text
    assert "Напечатано" in page.text


def test_поле_скана_в_фокусе(logged_in):
    page = logged_in.get("/receive")
    assert "autofocus" in page.text
    assert "scan-input" in page.text


def test_страница_подходит_для_телефона(logged_in):
    page = logged_in.get("/")
    assert 'name="viewport"' in page.text
    assert "width=device-width" in page.text


def test_здоровье_сервиса(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
