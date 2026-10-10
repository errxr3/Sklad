"""Веб-приложение складского учёта зеркал.

Обычные HTML-страницы, формы и переходы. JavaScript используется
в одном месте — чтобы курсор не уходил из поля скана.
"""

from __future__ import annotations

import secrets
import tempfile
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware
from starlette.status import HTTP_303_SEE_OTHER

from . import (
    __version__,
    admin_service,
    config,
    importer,
    labels,
    roles,
    scanlog,
    services,
    statuses,
)
from .articles import format_number
from .db import get_session, init_db
from .models import Article, Employee, Site
from .numbering import current_value
from .timeutil import fmt, now_utc, to_local

@asynccontextmanager
async def lifespan(_app: FastAPI):
    """При запуске создаём таблицы и площадки, если их ещё нет."""
    init_db()
    yield


app = FastAPI(
    title="Склад зеркал",
    version=__version__,
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan,
)

_secret = config.SECRET_KEY
if not _secret:
    # Без ключа в .env приложение всё равно работает, но после
    # перезапуска всем придётся войти заново.
    _secret = secrets.token_hex(32)
    print(
        "ВНИМАНИЕ: SKLAD_SECRET_KEY не задан, используется временный ключ. "
        "Задайте его в файле .env, иначе при перезапуске все выйдут из системы."
    )

app.add_middleware(
    SessionMiddleware,
    secret_key=_secret,
    max_age=config.SESSION_MINUTES * 60,
    same_site="lax",
)
app.mount("/static", StaticFiles(directory=config.BASE_DIR / "app" / "static"), name="static")

templates = Jinja2Templates(directory=str(config.BASE_DIR / "app" / "templates"))
templates.env.globals["version"] = __version__
templates.env.globals["fmt"] = fmt
templates.env.globals["status_title"] = statuses.title
templates.env.globals["role_title"] = roles.title
templates.env.globals["perm"] = roles


# --- Вспомогательное --------------------------------------------------------


# В cookie-сессии держим только самое необходимое: её предел 4096 байт,
# а переполненную cookie браузер отбрасывает целиком вместе со входом.
MAX_FLASH = 3


THEME_COOKIE = "sklad_theme"
THEMES = ("light", "dark")


def _theme(request: Request) -> str:
    """Выбранная тема. Храним в отдельной cookie, не в сессии:
    она должна переживать выход из системы."""
    value = request.cookies.get(THEME_COOKIE, "light")
    return value if value in THEMES else "light"


def flash(request: Request, text: str, ok: bool = True, only: bool = False) -> None:
    """Сообщение, которое покажется на следующей странице.

    only=True убирает накопленные сообщения: на экране скана сборщик
    должен видеть результат последнего зеркала, а не прошлого шага.
    """
    messages = [] if only else request.session.get("flash", [])
    messages.append({"text": text, "ok": ok})
    request.session["flash"] = messages[-MAX_FLASH:]


def session_id(request: Request) -> str:
    """Идентификатор рабочего места — по нему храним список сканов."""
    value = request.session.get("sid")
    if not value:
        value = uuid.uuid4().hex
        request.session["sid"] = value
    return value


def _take_flashes(request: Request) -> list[dict]:
    messages = request.session.pop("flash", [])
    return messages


def render(request: Request, template: str, **context) -> HTMLResponse:
    context.setdefault("messages", _take_flashes(request))
    context.setdefault("theme", _theme(request))
    context.setdefault("path", request.url.path)
    context.setdefault("employee", request.state.employee if hasattr(request.state, "employee") else None)
    context.setdefault("site", request.state.site if hasattr(request.state, "site") else None)
    return templates.TemplateResponse(request, template, context)


def redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=HTTP_303_SEE_OTHER)


class NeedLogin(Exception):
    """Сотрудник не вошёл — отправляем на страницу входа."""


class NoAccess(Exception):
    """У роли нет права на эту операцию."""

    def __init__(self, employee, what: str) -> None:
        super().__init__(what)
        self.employee = employee
        self.what = what


def current(request: Request, session: Session = Depends(get_session)):
    """Текущий сотрудник и площадка из cookie-сессии."""
    employee_id = request.session.get("employee_id")
    site_id = request.session.get("site_id")
    employee = session.get(Employee, employee_id) if employee_id else None
    site = session.get(Site, site_id) if site_id else None
    if employee is None or not employee.is_active or site is None:
        request.session.pop("employee_id", None)
        raise NeedLogin
    request.state.employee = employee
    request.state.site = site
    return employee, site, session


@app.exception_handler(NeedLogin)
def _need_login(request: Request, _exc: NeedLogin) -> RedirectResponse:
    return redirect("/login")


@app.exception_handler(NoAccess)
def _no_access(request: Request, exc: NoAccess) -> HTMLResponse:
    text = (
        f"{exc.what} — это не ваша операция. "
        f"Ваша роль: {roles.title(exc.employee.role)}. "
        "Обратитесь к бригадиру или администратору."
    )
    return templates.TemplateResponse(
        request,
        "error.html",
        {"error": text, "messages": [], "employee": exc.employee,
         "site": getattr(request.state, "site", None), "theme": _theme(request)},
        status_code=403,
    )


def require(permission: str, what: str):
    """Зависимость маршрута: пускает только роли с нужным правом."""

    def dependency(request: Request, who=Depends(current)):
        employee, _site, _session = who
        if not roles.can(employee.role, permission):
            raise NoAccess(employee, what)
        return who

    return dependency


@app.exception_handler(services.ServiceError)
def _service_error(request: Request, exc: services.ServiceError) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "error.html", {"error": str(exc), "messages": []}, status_code=400
    )


def _push_scan(request: Request, screen: str, result: services.ScanResult) -> None:
    """Запомнить скан, чтобы показать последние на экране."""
    scanlog.add(
        session_id(request),
        screen,
        scanlog.ScanLine(
            ok=result.ok,
            number=result.number,
            text=result.message,
            time=to_local(now_utc()).strftime("%H:%M:%S"),
        ),
    )


# --- Вход и выход -----------------------------------------------------------


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request, session: Session = Depends(get_session)):
    return render(
        request,
        "login.html",
        employees=services.active_employees(session),
        sites=services.active_sites(session),
        last_employee_id=request.session.get("last_employee_id"),
        last_site_id=request.session.get("last_site_id"),
        employee=None,
        site=None,
    )


@app.post("/login")
def login(
    request: Request,
    employee_id: int = Form(...),
    pin: str = Form(...),
    site_id: int = Form(...),
    session: Session = Depends(get_session),
):
    try:
        employee = services.authenticate(session, employee_id, pin)
    except services.ServiceError as exc:
        flash(request, str(exc), ok=False)
        request.session["last_employee_id"] = employee_id
        request.session["last_site_id"] = site_id
        return redirect("/login")

    site = session.get(Site, site_id)
    if site is None:
        flash(request, "Выберите площадку.", ok=False)
        return redirect("/login")

    request.session["employee_id"] = employee.id
    request.session["site_id"] = site.id
    request.session["last_employee_id"] = employee.id
    request.session["last_site_id"] = site.id
    scanlog.clear(session_id(request))
    request.session["sid"] = uuid.uuid4().hex
    return redirect("/")


@app.post("/theme")
def switch_theme(request: Request, back: str = Form("/")):
    """Переключить светлую и тёмную тему. Выбор живёт в cookie на год."""
    new_theme = "light" if _theme(request) == "dark" else "dark"
    # Возвращаемся только на свой же адрес, чужие ссылки не принимаем.
    target = back if back.startswith("/") and not back.startswith("//") else "/"
    response = redirect(target)
    response.set_cookie(
        THEME_COOKIE,
        new_theme,
        max_age=365 * 24 * 3600,
        samesite="lax",
        httponly=False,
    )
    return response


@app.post("/logout")
def logout(request: Request):
    scanlog.clear(request.session.get("sid", ""))
    last_employee = request.session.get("last_employee_id")
    last_site = request.session.get("last_site_id")
    request.session.clear()
    if last_employee:
        request.session["last_employee_id"] = last_employee
    if last_site:
        request.session["last_site_id"] = last_site
    return redirect("/login")


# --- Главная ----------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
def index(request: Request, who=Depends(current)):
    employee, site, session = who
    return render(
        request,
        "index.html",
        waiting=services.waiting_for_receive(session, site),
        in_stock=services.in_stock_count(session, site),
        articles_total=services.articles_count(session),
    )


# --- Сделал зеркало ---------------------------------------------------------


@app.get("/make", response_class=HTMLResponse)
def make_form(
    request: Request,
    q: str = Query(""),
    article_id: int | None = Query(None),
    who=Depends(require(roles.MAKE, "Печать этикеток")),
):
    employee, site, session = who
    article = None
    next_number = ""
    if article_id:
        article = session.get(Article, article_id)
        if article is not None:
            next_number = format_number(article.group_letters, current_value(session) + 1)

    found = [] if article else services.find_articles(session, q, limit=50)
    return render(
        request,
        "make.html",
        query=q,
        articles=found,
        truncated=len(found) == 50,
        article=article,
        next_number=next_number,
        max_qty=config.MAX_BATCH_QTY,
    )


@app.post("/make")
def make_submit(
    request: Request,
    article_id: int = Form(...),
    qty: str = Form(""),
    qty_custom: str = Form(""),
    who=Depends(require(roles.MAKE, "Печать этикеток")),
):
    """Количество приходит либо нажатой кнопкой-пресетом, либо из поля ввода.

    У кнопок и у поля разные имена: иначе браузер отправляет оба значения
    под одним именем и до обработчика доходит пустая строка из поля.
    """
    employee, site, session = who
    raw = (qty or "").strip() or (qty_custom or "").strip()
    try:
        quantity = int(raw)
    except ValueError:
        flash(request, "Укажите количество числом.", ok=False)
        return redirect(f"/make?article_id={article_id}")

    batch = services.create_print_batch(
        session, article_id=article_id, qty=quantity, employee=employee, site=site
    )
    flash(request, f"Выдано номеров: {batch.qty}. Этикетки готовы к печати.")
    return redirect(f"/batch/{batch.id}")


@app.get("/batch/{batch_id}", response_class=HTMLResponse)
def batch_view(request: Request, batch_id: int, who=Depends(require(roles.MAKE, "Печать этикеток"))):
    employee, site, session = who
    batch = services.get_batch(session, batch_id)
    return render(
        request,
        "batch.html",
        batch=batch,
        mirrors=services.batch_mirrors(session, batch_id),
    )


@app.get("/batch/{batch_id}/labels.pdf")
def batch_labels(request: Request, batch_id: int, who=Depends(require(roles.MAKE, "Печать этикеток"))):
    employee, site, session = who
    batch = services.get_batch(session, batch_id)
    mirrors = services.batch_mirrors(session, batch_id)
    pdf = labels.render_labels(mirrors)
    filename = f"labels-{batch.id}.pdf"
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


# --- Приёмка и отгрузка -----------------------------------------------------


@app.get("/receive", response_class=HTMLResponse)
def receive_form(request: Request, who=Depends(require(roles.RECEIVE, "Приёмка на склад"))):
    employee, site, session = who
    log = scanlog.get(session_id(request), "receive")
    return render(
        request,
        "scan.html",
        heading="Приёмка на склад",
        prompt="Отсканируйте этикетку",
        action="/receive",
        log=log,
        done_count=sum(1 for row in log if row.ok),
        done_word="принято",
        waiting=services.waiting_for_receive(session, site),
    )


@app.post("/receive")
def receive_scan(request: Request, code: str = Form(""), who=Depends(require(roles.RECEIVE, "Приёмка на склад"))):
    employee, site, session = who
    result = services.scan_receive(session, code, employee, site)
    _push_scan(request, "receive", result)
    flash(request, result.message, ok=result.ok, only=True)
    return redirect("/receive")


@app.get("/ship", response_class=HTMLResponse)
def ship_form(request: Request, who=Depends(require(roles.SHIP, "Отгрузка"))):
    employee, site, session = who
    log = scanlog.get(session_id(request), "ship")
    return render(
        request,
        "scan.html",
        heading="Отгрузка",
        prompt="Отсканируйте этикетку",
        action="/ship",
        log=log,
        done_count=sum(1 for row in log if row.ok),
        done_word="отгружено",
        waiting=None,
    )


@app.post("/ship")
def ship_scan(request: Request, code: str = Form(""), who=Depends(require(roles.SHIP, "Отгрузка"))):
    employee, site, session = who
    result = services.scan_ship(session, code, employee, site)
    _push_scan(request, "ship", result)
    flash(request, result.message, ok=result.ok, only=True)
    return redirect("/ship")


# --- Остатки, журнал, карточка зеркала --------------------------------------


@app.get("/stock", response_class=HTMLResponse)
def stock_view(
    request: Request,
    q: str = Query(""),
    only: str = Query(""),
    who=Depends(current),
):
    employee, site, session = who
    only_with_stock = only == "1"
    sites, sections, totals, grand = services.stock(
        session, q, only_with_stock=only_with_stock
    )
    return render(
        request,
        "stock.html",
        sites=sites,
        sections=sections,
        totals=totals,
        grand=grand,
        query=q,
        only_with_stock=only_with_stock,
    )


@app.get("/journal", response_class=HTMLResponse)
def journal_view(
    request: Request,
    number: str = Query(""),
    offset: int = Query(0, ge=0),
    who=Depends(current),
):
    employee, site, session = who
    limit = 50
    items, total = services.journal(session, limit=limit, offset=offset, number=number)
    return render(
        request,
        "journal.html",
        items=items,
        total=total,
        number=number,
        offset=offset,
        limit=limit,
    )


@app.get("/mirror/{number}", response_class=HTMLResponse)
def mirror_view(request: Request, number: str, who=Depends(current)):
    employee, site, session = who
    mirror, movements = services.mirror_history(session, number)
    if mirror is None:
        raise services.ServiceError(f"Зеркало {number} не найдено.")
    return render(request, "mirror.html", mirror=mirror, movements=movements)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok", "version": __version__, "time": datetime.now().isoformat()}


# --- Админ-панель -----------------------------------------------------------

admin_only = require(roles.ADMIN_PANEL, "Админ-панель")


@app.get("/admin", response_class=HTMLResponse)
def admin_home(request: Request, who=Depends(admin_only)):
    employee, site, session = who
    return render(
        request,
        "admin/index.html",
        page="home",
        data=admin_service.overview(session),
        statuses=statuses,
    )


@app.get("/admin/employees", response_class=HTMLResponse)
def admin_employees(request: Request, who=Depends(admin_only)):
    employee, site, session = who
    return render(
        request,
        "admin/employees.html",
        page="employees",
        employees=admin_service.all_employees(session),
        roles_list=roles.ALL,
    )


@app.post("/admin/employees/add")
def admin_employee_add(
    request: Request,
    name: str = Form(...),
    pin: str = Form(...),
    role: str = Form(...),
    who=Depends(admin_only),
):
    employee, site, session = who
    created = admin_service.create_employee(session, name=name, pin=pin, role=role)
    flash(request, f"Заведён сотрудник {created.name} — {created.role_title}.")
    return redirect("/admin/employees")


@app.post("/admin/employees/{employee_id}/role")
def admin_employee_role(
    request: Request, employee_id: int, role: str = Form(...), who=Depends(admin_only)
):
    employee, site, session = who
    changed = admin_service.change_role(session, employee_id, role)
    flash(request, f"{changed.name}: роль изменена на «{changed.role_title}».")
    return redirect("/admin/employees")


@app.post("/admin/employees/{employee_id}/pin")
def admin_employee_pin(
    request: Request, employee_id: int, pin: str = Form(...), who=Depends(admin_only)
):
    employee, site, session = who
    changed = admin_service.change_pin(session, employee_id, pin)
    flash(request, f"{changed.name}: PIN изменён.")
    return redirect("/admin/employees")


@app.post("/admin/employees/{employee_id}/toggle")
def admin_employee_toggle(request: Request, employee_id: int, who=Depends(admin_only)):
    employee, site, session = who
    changed = admin_service.toggle_employee(session, employee_id, by=employee)
    state = "включён" if changed.is_active else "отключён"
    flash(request, f"{changed.name}: {state}.")
    return redirect("/admin/employees")


@app.get("/admin/articles", response_class=HTMLResponse)
def admin_articles(
    request: Request,
    q: str = Query(""),
    offset: int = Query(0, ge=0),
    who=Depends(admin_only),
):
    employee, site, session = who
    limit = 100
    items, total = admin_service.find_articles_admin(
        session, q, limit=limit, offset=offset
    )
    return render(
        request,
        "admin/articles.html",
        page="articles",
        articles=items,
        total=total,
        query=q,
        offset=offset,
        limit=limit,
        sheets=admin_service.sheets(session),
    )


@app.post("/admin/articles/add")
def admin_article_add(
    request: Request,
    code: str = Form(...),
    sheet: str = Form(""),
    section: str = Form(""),
    who=Depends(admin_only),
):
    employee, site, session = who
    article = admin_service.add_article(session, code=code, sheet=sheet, section=section)
    flash(
        request,
        f"Артикул «{article.code}» добавлен, буквы номера {article.group_letters}.",
    )
    return redirect("/admin/articles")


@app.post("/admin/articles/{article_id}/toggle")
def admin_article_toggle(request: Request, article_id: int, who=Depends(admin_only)):
    employee, site, session = who
    article = admin_service.toggle_article(session, article_id)
    state = "включён" if article.is_active else "отключён"
    flash(request, f"Артикул «{article.code}»: {state}.")
    return redirect("/admin/articles")


@app.post("/admin/articles/import")
async def admin_articles_import(
    request: Request,
    upload: UploadFile = File(...),
    who=Depends(admin_only),
):
    employee, site, session = who
    suffix = Path(upload.filename or "").suffix.lower()
    if suffix not in {".xlsx", ".xlsm", ".csv", ".txt"}:
        flash(request, "Нужен файл .xlsx или .csv.", ok=False)
        return redirect("/admin/articles")

    # Загруженный файл кладём во временный, чтобы openpyxl читал его с диска.
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(await upload.read())
        tmp_path = Path(tmp.name)
    try:
        report = importer.import_articles(session, tmp_path)
    finally:
        tmp_path.unlink(missing_ok=True)

    flash(
        request,
        f"Загружено из {upload.filename}: новых {report.added}, "
        f"уже было {report.already}, пропущено {report.skipped}.",
    )
    return redirect("/admin/articles")


@app.get("/admin/mirrors", response_class=HTMLResponse)
def admin_mirrors(
    request: Request,
    q: str = Query(""),
    status: str = Query(""),
    site_id: int = Query(0),
    employee_id: int = Query(0),
    offset: int = Query(0, ge=0),
    who=Depends(admin_only),
):
    employee, site, session = who
    limit = 100
    items, total = admin_service.find_mirrors(
        session,
        query=q,
        status=status,
        site_id=site_id or None,
        employee_id=employee_id or None,
        limit=limit,
        offset=offset,
    )
    return render(
        request,
        "admin/mirrors.html",
        page="mirrors",
        mirrors=items,
        total=total,
        query=q,
        status=status,
        site_id=site_id,
        employee_id=employee_id,
        offset=offset,
        limit=limit,
        sites=services.active_sites(session),
        employees=admin_service.all_employees(session),
        all_statuses=statuses.ALL,
        statuses=statuses,
    )


@app.post("/admin/mirrors/{mirror_id}/status")
def admin_mirror_status(
    request: Request,
    mirror_id: int,
    to_status: str = Form(...),
    reason: str = Form(""),
    who=Depends(admin_only),
):
    employee, site, session = who
    mirror = admin_service.correct_status(
        session, mirror_id=mirror_id, to_status=to_status, by=employee, reason=reason
    )
    flash(
        request,
        f"{mirror.number}: статус исправлен на «{mirror.status_title}». "
        "Запись добавлена в журнал.",
    )
    return redirect(request.headers.get("referer", "/admin/mirrors"))


@app.get("/admin/journal", response_class=HTMLResponse)
def admin_journal(
    request: Request,
    number: str = Query(""),
    employee_id: int = Query(0),
    status: str = Query(""),
    offset: int = Query(0, ge=0),
    who=Depends(admin_only),
):
    employee, site, session = who
    limit = 100
    items, total = admin_service.journal_filtered(
        session,
        number=number,
        employee_id=employee_id or None,
        status=status,
        limit=limit,
        offset=offset,
    )
    return render(
        request,
        "admin/journal.html",
        page="journal",
        items=items,
        total=total,
        number=number,
        employee_id=employee_id,
        status=status,
        offset=offset,
        limit=limit,
        employees=admin_service.all_employees(session),
        all_statuses=statuses.ALL,
        statuses=statuses,
    )
