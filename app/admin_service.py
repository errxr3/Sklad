"""Операции админ-панели: сотрудники, артикулы, зеркала, сводка.

Журнал движений остаётся неизменяемым и здесь: исправление статуса
админом не правит прошлые записи, а добавляет новую, с пометкой.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from . import roles, statuses
from .articles import group_letters, match_key, normalize_code, normalize_number
from .models import Article, Employee, Mirror, Movement, PrintBatch, Site
from .security import WeakPinError, hash_pin
from .services import ServiceError
from .timeutil import now_utc


# --- Сотрудники -------------------------------------------------------------


def all_employees(session: Session) -> list[Employee]:
    return list(session.scalars(select(Employee).order_by(Employee.name)))


def create_employee(session: Session, *, name: str, pin: str, role: str) -> Employee:
    name = (name or "").strip()
    if not name:
        raise ServiceError("Укажите имя сотрудника.")
    if not roles.is_valid(role):
        raise ServiceError("Неизвестная роль.")
    if session.scalar(select(Employee).where(Employee.name == name)):
        raise ServiceError(f"Сотрудник «{name}» уже заведён.")
    try:
        pin_hash = hash_pin(pin)
    except WeakPinError as exc:
        raise ServiceError(str(exc)) from exc

    employee = Employee(name=name, pin_hash=pin_hash, role=role)
    session.add(employee)
    session.commit()
    return employee


def change_role(session: Session, employee_id: int, role: str) -> Employee:
    if not roles.is_valid(role):
        raise ServiceError("Неизвестная роль.")
    employee = session.get(Employee, employee_id)
    if employee is None:
        raise ServiceError("Сотрудник не найден.")
    employee.role = role
    session.commit()
    return employee


def change_pin(session: Session, employee_id: int, pin: str) -> Employee:
    employee = session.get(Employee, employee_id)
    if employee is None:
        raise ServiceError("Сотрудник не найден.")
    try:
        employee.pin_hash = hash_pin(pin)
    except WeakPinError as exc:
        raise ServiceError(str(exc)) from exc
    session.commit()
    return employee


def toggle_employee(session: Session, employee_id: int, *, by: Employee) -> Employee:
    employee = session.get(Employee, employee_id)
    if employee is None:
        raise ServiceError("Сотрудник не найден.")
    if employee.id == by.id:
        raise ServiceError("Нельзя отключить самого себя.")
    if employee.is_active and _last_active_admin(session, employee):
        raise ServiceError(
            "Это последний администратор — отключать его нельзя, "
            "иначе в систему будет не войти."
        )
    employee.is_active = not employee.is_active
    session.commit()
    return employee


def _last_active_admin(session: Session, employee: Employee) -> bool:
    if employee.role != roles.ADMIN:
        return False
    count = session.scalar(
        select(func.count())
        .select_from(Employee)
        .where(Employee.role == roles.ADMIN, Employee.is_active.is_(True))
    ) or 0
    return count <= 1


# --- Артикулы ---------------------------------------------------------------


def add_article(
    session: Session,
    *,
    code: str,
    sheet: str = "",
    section: str = "",
) -> Article:
    """Добавить артикул вручную из админки."""
    code = normalize_code(code)
    if not code:
        raise ServiceError("Укажите артикул.")
    key = match_key(code)
    existing = session.scalar(select(Article).where(Article.code_key == key))
    if existing is not None:
        raise ServiceError(
            f"Артикул «{existing.code}» уже есть в справочнике"
            + ("" if existing.is_active else " (сейчас отключён)")
            + "."
        )

    sheet = (sheet or "").strip() or "Добавлено вручную"
    section = (section or "").strip() or sheet
    # Ставим в конец своего раздела.
    last = session.scalar(
        select(func.max(Article.sort_order)).where(Article.source_sheet == sheet)
    ) or 0

    article = Article(
        code=code,
        code_key=key,
        group_letters=group_letters(code),
        source_sheet=sheet,
        section=section,
        sort_order=last + 1,
    )
    session.add(article)
    session.commit()
    return article


@dataclass
class BulkResult:
    added: list[str]
    restored: list[str]
    already: list[str]

    @property
    def summary(self) -> str:
        parts = [f"добавлено {len(self.added)}"]
        if self.restored:
            parts.append(f"включено обратно {len(self.restored)}")
        if self.already:
            parts.append(f"уже было {len(self.already)}")
        return ", ".join(parts)


def add_articles_bulk(
    session: Session,
    *,
    text: str,
    sheet: str = "",
    section: str = "",
) -> BulkResult:
    """Добавить сразу список артикулов — по одному в строке.

    Строку можно записать и с разделом через точку с запятой:
    «Ambient 110 круг; БЕЗРАМНЫЕ КРУГЛЫЕ».
    """
    result = BulkResult([], [], [])
    default_sheet = (sheet or "").strip() or "Добавлено вручную"
    default_section = (section or "").strip()

    known = {
        article.code_key: article
        for article in session.scalars(select(Article)).all()
    }
    last_order = {
        row_sheet: value
        for row_sheet, value in session.execute(
            select(Article.source_sheet, func.max(Article.sort_order)).group_by(
                Article.source_sheet
            )
        ).all()
    }

    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [normalize_code(piece) for piece in line.split(";")]
        code = parts[0]
        if not code:
            continue
        row_section = parts[1] if len(parts) > 1 and parts[1] else default_section
        row_sheet = parts[2] if len(parts) > 2 and parts[2] else default_sheet

        key = match_key(code)
        article = known.get(key)
        if article is not None:
            if not article.is_active:
                article.is_active = True
                result.restored.append(article.code)
            else:
                result.already.append(article.code)
            continue

        position = (last_order.get(row_sheet) or 0) + 1
        last_order[row_sheet] = position
        article = Article(
            code=code,
            code_key=key,
            group_letters=group_letters(code),
            source_sheet=row_sheet,
            section=row_section or row_sheet,
            sort_order=position,
        )
        session.add(article)
        known[key] = article
        result.added.append(code)

    if not (result.added or result.restored or result.already):
        raise ServiceError("Список пуст — вставьте артикулы, по одному в строке.")

    session.commit()
    return result


def toggle_article(session: Session, article_id: int) -> Article:
    article = session.get(Article, article_id)
    if article is None:
        raise ServiceError("Артикул не найден.")
    article.is_active = not article.is_active
    session.commit()
    return article


def find_articles_admin(
    session: Session, query: str = "", limit: int = 200, offset: int = 0
) -> tuple[list[Article], int]:
    stmt = select(Article)
    count_stmt = select(func.count()).select_from(Article)
    needle = match_key(query)
    if needle:
        stmt = stmt.where(Article.code_key.like(f"%{needle}%"))
        count_stmt = count_stmt.where(Article.code_key.like(f"%{needle}%"))
    total = session.scalar(count_stmt) or 0
    items = list(
        session.scalars(
            stmt.order_by(Article.source_sheet, Article.sort_order)
            .limit(limit)
            .offset(offset)
        )
    )
    return items, total


def sheets(session: Session) -> list[str]:
    return [
        row
        for (row,) in session.execute(
            select(Article.source_sheet)
            .where(Article.source_sheet.is_not(None))
            .group_by(Article.source_sheet)
            .order_by(Article.source_sheet)
        ).all()
    ]


# --- Зеркала ----------------------------------------------------------------


def find_mirrors(
    session: Session,
    *,
    query: str = "",
    status: str = "",
    site_id: int | None = None,
    employee_id: int | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[Mirror], int]:
    stmt = select(Mirror).join(Article, Article.id == Mirror.article_id)
    count_stmt = (
        select(func.count()).select_from(Mirror).join(Article, Article.id == Mirror.article_id)
    )

    text = (query or "").strip()
    if text:
        number = normalize_number(text)
        condition = or_(
            Mirror.number.like(f"%{number}%"),
            Article.code_key.like(f"%{match_key(text)}%"),
        )
        stmt = stmt.where(condition)
        count_stmt = count_stmt.where(condition)
    if status:
        stmt = stmt.where(Mirror.status == status)
        count_stmt = count_stmt.where(Mirror.status == status)
    if site_id:
        stmt = stmt.where(Mirror.site_id == site_id)
        count_stmt = count_stmt.where(Mirror.site_id == site_id)
    if employee_id:
        stmt = stmt.where(Mirror.assembler_id == employee_id)
        count_stmt = count_stmt.where(Mirror.assembler_id == employee_id)

    total = session.scalar(count_stmt) or 0
    items = list(
        session.scalars(stmt.order_by(Mirror.id.desc()).limit(limit).offset(offset))
    )
    return items, total


def correct_status(
    session: Session,
    *,
    mirror_id: int,
    to_status: str,
    by: Employee,
    reason: str = "",
) -> Mirror:
    """Исправить статус зеркала вручную.

    Нужно, когда зеркало отсканировали по ошибке. Прошлые записи журнала
    не трогаем — добавляем новую с пометкой, кто и почему исправил.
    """
    if to_status not in statuses.ALL:
        raise ServiceError("Неизвестный статус.")
    mirror = session.get(Mirror, mirror_id)
    if mirror is None:
        raise ServiceError("Зеркало не найдено.")
    if mirror.status == to_status:
        raise ServiceError(
            f"Зеркало {mirror.number} уже в статусе «{statuses.title(to_status)}»."
        )

    note = f"Исправление администратором: {by.name}"
    if reason.strip():
        note += f". Причина: {reason.strip()}"

    moment = now_utc()
    session.add(
        Movement(
            mirror_id=mirror.id,
            from_status=mirror.status,
            to_status=to_status,
            employee_id=by.id,
            site_id=mirror.site_id,
            note=note,
            created_at=moment,
        )
    )
    mirror.status = to_status
    mirror.status_at = moment
    session.commit()
    return mirror


# --- Сводка -----------------------------------------------------------------


@dataclass
class Overview:
    by_status: dict[str, int]
    by_site: dict[str, int]
    employees: int
    articles: int
    movements: int
    mirrors: int
    batches: int


def overview(session: Session) -> Overview:
    by_status = {
        status: count
        for status, count in session.execute(
            select(Mirror.status, func.count()).group_by(Mirror.status)
        ).all()
    }
    by_site = {
        name: count
        for name, count in session.execute(
            select(Site.name, func.count(Mirror.id))
            .join(Mirror, Mirror.site_id == Site.id)
            .where(Mirror.status == statuses.IN_STOCK)
            .group_by(Site.name)
        ).all()
    }
    return Overview(
        by_status=by_status,
        by_site=by_site,
        employees=session.scalar(select(func.count()).select_from(Employee)) or 0,
        articles=session.scalar(select(func.count()).select_from(Article)) or 0,
        movements=session.scalar(select(func.count()).select_from(Movement)) or 0,
        mirrors=session.scalar(select(func.count()).select_from(Mirror)) or 0,
        batches=session.scalar(select(func.count()).select_from(PrintBatch)) or 0,
    )


def journal_filtered(
    session: Session,
    *,
    number: str = "",
    employee_id: int | None = None,
    status: str = "",
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[Movement], int]:
    stmt = select(Movement).join(Mirror, Mirror.id == Movement.mirror_id)
    count_stmt = (
        select(func.count()).select_from(Movement).join(Mirror, Mirror.id == Movement.mirror_id)
    )
    needle = normalize_number(number)
    if needle:
        stmt = stmt.where(Mirror.number.like(f"%{needle}%"))
        count_stmt = count_stmt.where(Mirror.number.like(f"%{needle}%"))
    if employee_id:
        stmt = stmt.where(Movement.employee_id == employee_id)
        count_stmt = count_stmt.where(Movement.employee_id == employee_id)
    if status:
        stmt = stmt.where(Movement.to_status == status)
        count_stmt = count_stmt.where(Movement.to_status == status)

    total = session.scalar(count_stmt) or 0
    items = list(
        session.scalars(stmt.order_by(Movement.id.desc()).limit(limit).offset(offset))
    )
    return items, total
