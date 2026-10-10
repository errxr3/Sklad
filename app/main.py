"""Веб-приложение складского учёта зеркал.

Обычные HTML-страницы, формы и переходы. JavaScript используется
в одном месте — чтобы курсор не уходил из поля скана.
"""

from __future__ import annotations

import secrets
from datetime import datetime

from fastapi import Depends, FastAPI, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware
from starlette.status import HTTP_303_SEE_OTHER

from . import __version__, config, labels, services, statuses
from .articles import format_number
from .db import get_session, init_db
from .models import Article, Employee, Site
from .numbering import current_value
from .timeutil import fmt, now_utc, to_local

SCAN_LOG_SIZE = 8

app = FastAPI(title="Склад зеркал", version=__version__, docs_url=None, redoc_url=None)

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


@app.on_event("startup")
def _startup() -> None:
    init_db()


# --- Вспомогательное --------------------------------------------------------


def flash(request: Request, text: str, ok: bool = True) -> None:
    """Сообщение, которое покажется на следующей странице."""
    request.session.setdefault("flash", []).append({"text": text, "ok": ok})


def _take_flashes(request: Request) -> list[dict]:
    messages = request.session.pop("flash", [])
    return messages


def render(request: Request, template: str, **context) -> HTMLResponse:
    context.setdefault("messages", _take_flashes(request))
    context.setdefault("employee", request.state.employee if hasattr(request.state, "employee") else None)
    context.setdefault("site", request.state.site if hasattr(request.state, "site") else None)
    return templates.TemplateResponse(request, template, context)


def redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=HTTP_303_SEE_OTHER)


class NeedLogin(Exception):
    """Сотрудник не вошёл — отправляем на страницу входа."""


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


@app.exception_handler(services.ServiceError)
def _service_error(request: Request, exc: services.ServiceError) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "error.html", {"error": str(exc), "messages": []}, status_code=400
    )


def _push_scan(request: Request, key: str, result: services.ScanResult) -> None:
    """Запомнить скан, чтобы показать последние на экране."""
    log = request.session.get(key, [])
    log.insert(
        0,
        {
            "ok": result.ok,
            "number": result.number,
            "text": result.message,
            "time": to_local(now_utc()).strftime("%H:%M:%S"),
        },
    )
    request.session[key] = log[:SCAN_LOG_SIZE]


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
    request.session.pop("receive_log", None)
    request.session.pop("ship_log", None)
    return redirect("/")


@app.post("/logout")
def logout(request: Request):
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
        articles_total=services.articles_count(session),
    )


# --- Сделал зеркало ---------------------------------------------------------


@app.get("/make", response_class=HTMLResponse)
def make_form(
    request: Request,
    q: str = Query(""),
    article_id: int | None = Query(None),
    who=Depends(current),
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
    who=Depends(current),
):
    employee, site, session = who
    try:
        quantity = int((qty or "").strip())
    except ValueError:
        flash(request, "Укажите количество числом.", ok=False)
        return redirect(f"/make?article_id={article_id}")

    batch = services.create_print_batch(
        session, article_id=article_id, qty=quantity, employee=employee, site=site
    )
    flash(request, f"Выдано номеров: {batch.qty}. Этикетки готовы к печати.")
    return redirect(f"/batch/{batch.id}")


@app.get("/batch/{batch_id}", response_class=HTMLResponse)
def batch_view(request: Request, batch_id: int, who=Depends(current)):
    employee, site, session = who
    batch = services.get_batch(session, batch_id)
    return render(
        request,
        "batch.html",
        batch=batch,
        mirrors=services.batch_mirrors(session, batch_id),
    )


@app.get("/batch/{batch_id}/labels.pdf")
def batch_labels(request: Request, batch_id: int, who=Depends(current)):
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
def receive_form(request: Request, who=Depends(current)):
    employee, site, session = who
    log = request.session.get("receive_log", [])
    return render(
        request,
        "scan.html",
        heading="Приёмка на склад",
        prompt="Отсканируйте этикетку",
        action="/receive",
        log=log,
        done_count=sum(1 for row in log if row["ok"]),
        done_word="принято",
        waiting=services.waiting_for_receive(session, site),
    )


@app.post("/receive")
def receive_scan(request: Request, code: str = Form(""), who=Depends(current)):
    employee, site, session = who
    result = services.scan_receive(session, code, employee, site)
    _push_scan(request, "receive_log", result)
    flash(request, result.message, ok=result.ok)
    return redirect("/receive")


@app.get("/ship", response_class=HTMLResponse)
def ship_form(request: Request, who=Depends(current)):
    employee, site, session = who
    log = request.session.get("ship_log", [])
    return render(
        request,
        "scan.html",
        heading="Отгрузка",
        prompt="Отсканируйте этикетку",
        action="/ship",
        log=log,
        done_count=sum(1 for row in log if row["ok"]),
        done_word="отгружено",
        waiting=None,
    )


@app.post("/ship")
def ship_scan(request: Request, code: str = Form(""), who=Depends(current)):
    employee, site, session = who
    result = services.scan_ship(session, code, employee, site)
    _push_scan(request, "ship_log", result)
    flash(request, result.message, ok=result.ok)
    return redirect("/ship")


# --- Остатки, журнал, карточка зеркала --------------------------------------


@app.get("/stock", response_class=HTMLResponse)
def stock_view(request: Request, q: str = Query(""), who=Depends(current)):
    employee, site, session = who
    sites, rows, totals, grand = services.stock(session, q)
    return render(
        request,
        "stock.html",
        sites=sites,
        rows=rows,
        totals=totals,
        grand=grand,
        query=q,
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
