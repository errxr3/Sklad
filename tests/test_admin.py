"""Админ-панель и переключение темы."""

import io

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from app import roles, statuses
from app.main import app
from app.models import Article, Employee, Mirror, Movement
from tests.conftest import TEST_PIN, make_article
from tests.test_web import login_as


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def as_admin(client, db, admin):
    return login_as(client, db, admin)


@pytest.fixture
def logged_in(client, db, worker):
    return login_as(client, db, worker)


# --- Доступ -----------------------------------------------------------------


def test_админка_закрыта_для_остальных(client, db, worker):
    login_as(client, db, worker)
    for path in ("/admin", "/admin/employees", "/admin/articles",
                 "/admin/mirrors", "/admin/journal"):
        assert client.get(path).status_code == 403, path


def test_страницы_админки_открываются(as_admin):
    for path in ("/admin", "/admin/employees", "/admin/articles",
                 "/admin/mirrors", "/admin/journal"):
        assert as_admin.get(path).status_code == 200, path


# --- Сотрудники -------------------------------------------------------------


def test_админ_заводит_сотрудника(as_admin, db):
    answer = as_admin.post(
        "/admin/employees/add",
        data={"name": "Новиков Новик", "pin": "7777", "role": roles.LOADER},
        follow_redirects=True,
    )
    assert "Заведён сотрудник" in answer.text
    created = db.query(Employee).filter_by(name="Новиков Новик").one()
    assert created.role == roles.LOADER
    # PIN хранится только хешем.
    assert "7777" not in created.pin_hash


def test_новый_сотрудник_может_войти(client, db, admin):
    login_as(client, db, admin)
    client.post(
        "/admin/employees/add",
        data={"name": "Новиков Новик", "pin": "7777", "role": roles.LOADER},
    )
    client.post("/logout")

    created = db.query(Employee).filter_by(name="Новиков Новик").one()
    from app.models import Site

    site = db.query(Site).first()
    client.post(
        "/login",
        data={"employee_id": created.id, "pin": "7777", "site_id": site.id},
    )
    assert client.get("/ship").status_code == 200


def test_слишком_короткий_пин_отклоняется(as_admin, db):
    answer = as_admin.post(
        "/admin/employees/add",
        data={"name": "Коротков", "pin": "12", "role": roles.ASSEMBLER},
    )
    assert answer.status_code == 400
    assert db.query(Employee).filter_by(name="Коротков").count() == 0


def test_смена_роли(as_admin, db, worker):
    as_admin.post(f"/admin/employees/{worker.id}/role", data={"role": roles.FOREMAN})
    db.refresh(worker)
    assert worker.role == roles.FOREMAN


def test_смена_пина(as_admin, db, worker):
    before = worker.pin_hash
    as_admin.post(f"/admin/employees/{worker.id}/pin", data={"pin": "9999"})
    db.refresh(worker)
    assert worker.pin_hash != before

    from app import services

    assert services.authenticate(db, worker.id, "9999").id == worker.id


def test_отключение_сотрудника(as_admin, db, worker):
    as_admin.post(f"/admin/employees/{worker.id}/toggle")
    db.refresh(worker)
    assert worker.is_active is False


def test_нельзя_отключить_себя(as_admin, db, admin):
    answer = as_admin.post(f"/admin/employees/{admin.id}/toggle")
    assert answer.status_code == 400
    assert "самого себя" in answer.text
    db.refresh(admin)
    assert admin.is_active is True


def test_нельзя_отключить_последнего_админа(client, db, admin, worker):
    """Второй админ отключает первого — тогда войти в админку будет некому."""
    from tests.conftest import make_employee

    second = make_employee(db, "Второй Админ", roles.ADMIN)
    login_as(client, db, second)
    client.post(f"/admin/employees/{admin.id}/toggle")
    db.refresh(admin)
    assert admin.is_active is False

    # Теперь второй админ — последний, отключить его нельзя даже чужими руками.
    client.post("/logout")
    db.refresh(admin)
    admin.is_active = True
    admin.role = roles.ADMIN
    db.commit()


# --- Артикулы ---------------------------------------------------------------


def test_админ_добавляет_артикул_вручную(as_admin, db):
    answer = as_admin.post(
        "/admin/articles/add",
        data={"code": "  Ambient 700х500  ", "sheet": "Безрамные", "section": "БЕЗРАМНЫЕ"},
        follow_redirects=True,
    )
    assert "добавлен" in answer.text
    article = db.query(Article).filter_by(code="Ambient 700x500").one()
    assert article.group_letters == "AT"      # буквы определились сами
    assert article.section == "БЕЗРАМНЫЕ"


def test_повторный_артикул_не_добавляется(as_admin, db):
    make_article(db, "Zerkalo 600x800")
    answer = as_admin.post(
        "/admin/articles/add", data={"code": "ZERKALO 600х800"}
    )
    assert answer.status_code == 400
    assert "уже есть" in answer.text
    assert db.query(Article).count() == 1


def test_отключённый_артикул_не_предлагается_при_печати(as_admin, db, article):
    as_admin.post(f"/admin/articles/{article.id}/toggle")
    db.refresh(article)
    assert article.is_active is False
    assert as_admin.get("/make", params={"q": "600"}).text.count("article-pick") == 0


def test_загрузка_справочника_файлом(as_admin, db, tmp_path):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Полотна"
    for row in [["Артикул"], ["ПОЛОТНА"], ["Zerkalo_60x80"], ["Zerkalo_70x90"]]:
        sheet.append(row)
    path = tmp_path / "TOP.xlsx"
    workbook.save(path)

    with path.open("rb") as handle:
        answer = as_admin.post(
            "/admin/articles/import",
            files={"upload": ("TOP.xlsx", handle.read(),
                              "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
            follow_redirects=True,
        )
    assert "новых 2" in answer.text
    assert db.query(Article).count() == 2
    assert db.query(Article).filter_by(code="Zerkalo_60x80").one().section == "ПОЛОТНА"


def test_файл_неверного_формата_отклоняется(as_admin, db):
    answer = as_admin.post(
        "/admin/articles/import",
        files={"upload": ("notes.docx", b"nope", "application/octet-stream")},
        follow_redirects=True,
    )
    assert "Нужен файл" in answer.text
    assert db.query(Article).count() == 0


# --- Зеркала и журнал -------------------------------------------------------


def test_поиск_зеркал_по_номеру_и_артикулу(as_admin, db, article, admin):
    as_admin.post("/admin/articles/add", data={"code": "Alum 400x1200"})
    as_admin.post("/make", data={"article_id": article.id, "qty": "2"})

    page = as_admin.get("/admin/mirrors", params={"q": "Z0000001"})
    assert "Z0000001" in page.text and "Z0000002" not in page.text

    page = as_admin.get("/admin/mirrors", params={"q": "600x800"})
    assert "Z0000001" in page.text and "Z0000002" in page.text


def test_админ_исправляет_статус_и_это_попадает_в_журнал(as_admin, db, article):
    as_admin.post("/make", data={"article_id": article.id, "qty": "1"})
    as_admin.post("/receive", data={"code": "Z0000001"})
    as_admin.post("/ship", data={"code": "Z0000001"})

    mirror = db.query(Mirror).filter_by(number="Z0000001").one()
    before = db.query(Movement).count()

    as_admin.post(
        f"/admin/mirrors/{mirror.id}/status",
        data={"to_status": statuses.IN_STOCK, "reason": "отсканировали по ошибке"},
        follow_redirects=True,
    )
    db.refresh(mirror)
    assert mirror.status == statuses.IN_STOCK

    # Прошлые записи целы, добавилась новая с пометкой.
    assert db.query(Movement).count() == before + 1
    last = db.query(Movement).order_by(Movement.id.desc()).first()
    assert "Исправление администратором" in last.note
    assert "отсканировали по ошибке" in last.note
    assert last.from_status == statuses.SHIPPED
    assert last.to_status == statuses.IN_STOCK


def test_журнал_админки_фильтрует_по_сотруднику(as_admin, db, article, admin, worker):
    as_admin.post("/make", data={"article_id": article.id, "qty": "1"})
    page = as_admin.get("/admin/journal", params={"employee_id": admin.id})
    assert "Z0000001" in page.text
    page = as_admin.get("/admin/journal", params={"employee_id": worker.id})
    assert "Z0000001" not in page.text


# --- Темы -------------------------------------------------------------------


def test_по_умолчанию_светлая_тема(logged_in):
    assert 'data-theme="light"' in logged_in.get("/").text


def test_переключение_на_тёмную_и_обратно(logged_in):
    answer = logged_in.post("/theme", data={"back": "/"}, follow_redirects=True)
    assert 'data-theme="dark"' in answer.text
    assert logged_in.cookies.get("sklad_theme") == "dark"

    # Тема держится при переходах по сайту.
    assert 'data-theme="dark"' in logged_in.get("/stock").text

    answer = logged_in.post("/theme", data={"back": "/"}, follow_redirects=True)
    assert 'data-theme="light"' in answer.text


def test_тема_переживает_выход_из_системы(client, db, worker):
    login_as(client, db, worker)
    client.post("/theme", data={"back": "/"})
    client.post("/logout")
    assert client.cookies.get("sklad_theme") == "dark"
    login_as(client, db, worker)
    assert 'data-theme="dark"' in client.get("/").text


def test_переключатель_не_уводит_на_чужой_сайт(logged_in):
    answer = logged_in.post(
        "/theme", data={"back": "//evil.example.com/"}, follow_redirects=False
    )
    assert answer.headers["location"] == "/"
