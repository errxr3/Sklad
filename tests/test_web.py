"""Сценарии через HTTP: вход, печать, PDF, сканы, остатки, журнал."""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models import Site
from tests.conftest import TEST_PIN, make_article, make_employee


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def login_as(client, db, employee):
    site = db.query(Site).order_by(Site.sort_order).first()
    response = client.post(
        "/login",
        data={"employee_id": employee.id, "pin": TEST_PIN, "site_id": site.id},
        follow_redirects=False,
    )
    assert response.status_code == 303
    return client


@pytest.fixture
def logged_in(client, db, worker):
    """Сборщик: печатает этикетки и принимает на склад."""
    return login_as(client, db, worker)


@pytest.fixture
def as_foreman(client, db, foreman):
    """Бригадир: может всё, кроме админки."""
    return login_as(client, db, foreman)


@pytest.fixture
def as_loader(client, db, loader):
    """Грузчик: только отгрузка."""
    return login_as(client, db, loader)


@pytest.fixture
def as_admin(client, db, admin):
    return login_as(client, db, admin)


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


def test_полный_сценарий_печать_приёмка_отгрузка(as_foreman, db, article):
    logged_in = as_foreman
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


def test_отгрузка_без_приёмки_показывает_отказ(as_foreman, article):
    logged_in = as_foreman
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


def test_cookie_не_разрастается_от_сканов(logged_in, article):
    """Раньше список сканов лежал в cookie-сессии и за десяток зеркал
    перерастал предел 4096 байт. Браузер отбрасывал такую cookie целиком,
    и сборщика выбрасывало из системы прямо посреди смены."""
    logged_in.post("/make", data={"article_id": article.id, "qty": "20"})

    for index in range(1, 21):
        logged_in.post("/receive", data={"code": f"Z{index:07d}"})
        logged_in.post("/receive", data={"code": f"Z{index:07d}"})  # повтор, длинный отказ
        logged_in.post("/receive", data={"code": "абракадабра"})    # ещё один отказ

    cookie = logged_in.cookies.get("session")
    assert cookie is not None, "сессия потерялась"
    assert len(cookie) < 1500, f"cookie разрослась до {len(cookie)} байт"

    # Вход сохранился, и сообщения по-прежнему доходят.
    assert logged_in.get("/", follow_redirects=False).status_code == 200
    answer = logged_in.post("/receive", data={"code": "Z0000001"})
    assert "msg err" in answer.text


def test_список_сканов_показывает_последние_восемь(logged_in, article):
    logged_in.post("/make", data={"article_id": article.id, "qty": "12"})
    for index in range(1, 13):
        logged_in.post("/receive", data={"code": f"Z{index:07d}"})

    page = logged_in.get("/receive")
    assert page.text.count('<li class=') == 8
    assert "Z0000012" in page.text
    assert "Z0000001<" not in page.text  # самые старые вытеснены


def test_список_сканов_очищается_при_смене_сотрудника(logged_in, db, article, second_worker):
    logged_in.post("/make", data={"article_id": article.id, "qty": "1"})
    logged_in.post("/receive", data={"code": "Z0000001"})
    assert "Z0000001" in logged_in.get("/receive").text

    logged_in.post("/logout")
    site = db.query(Site).order_by(Site.sort_order).first()
    logged_in.post(
        "/login",
        data={"employee_id": second_worker.id, "pin": TEST_PIN, "site_id": site.id},
    )
    assert "Z0000001" not in logged_in.get("/receive").text


def test_на_экране_скана_виден_результат_последнего_зеркала(logged_in, article):
    """Сообщение от печати не должно висеть поверх результата скана."""
    logged_in.post("/make", data={"article_id": article.id, "qty": "1"})
    answer = logged_in.post("/receive", data={"code": "Z0000001"})
    assert answer.text.count('class="msg') == 1
    assert "Выдано номеров" not in answer.text
    assert "принято на склад" in answer.text


def test_кнопка_количества_работает_при_пустом_поле_ввода(logged_in, article):
    """Браузер отправляет и нажатую кнопку, и пустое поле «другое количество».
    Раньше у них было одно имя и до сервера доходила пустая строка."""
    created = logged_in.post(
        "/make",
        data={"article_id": article.id, "qty": "3", "qty_custom": ""},
        follow_redirects=True,
    )
    assert "Выдано номеров: 3" in created.text
    assert "Z0000003" in created.text


def test_своё_количество_из_поля_ввода(logged_in, article):
    created = logged_in.post(
        "/make",
        data={"article_id": article.id, "qty": "", "qty_custom": "7"},
        follow_redirects=True,
    )
    assert "Выдано номеров: 7" in created.text
