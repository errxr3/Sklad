"""Бизнес-логика: печать этикеток, приёмка, отгрузка, остатки, журнал."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import numbering, statuses
from .articles import format_number, group_letters, is_valid_number, match_key, normalize_number
from .config import MAX_BATCH_QTY
from .models import Article, Employee, Mirror, Movement, PrintBatch, Site
from .security import verify_pin
from .timeutil import fmt, now_utc


class ServiceError(Exception):
    """Ошибка, текст которой можно показать сотруднику как есть."""


# --- Вход ------------------------------------------------------------------


def authenticate(session: Session, employee_id: int, pin: str) -> Employee:
    employee = session.get(Employee, employee_id)
    if employee is None or not employee.is_active:
        raise ServiceError("Сотрудник не найден. Выберите себя из списка.")
    if not verify_pin(pin, employee.pin_hash):
        raise ServiceError("Неверный PIN-код. Попробуйте ещё раз.")
    return employee


def active_employees(session: Session) -> list[Employee]:
    return list(
        session.scalars(
            select(Employee).where(Employee.is_active.is_(True)).order_by(Employee.name)
        )
    )


def active_sites(session: Session) -> list[Site]:
    return list(
        session.scalars(
            select(Site).where(Site.is_active.is_(True)).order_by(Site.sort_order, Site.name)
        )
    )


# --- Справочник артикулов ---------------------------------------------------


def find_articles(session: Session, query: str = "", limit: int = 50) -> list[Article]:
    """Поиск артикула по части названия. Русская «х» равна латинской «x»."""
    stmt = select(Article).where(Article.is_active.is_(True))
    needle = match_key(query)
    if needle:
        stmt = stmt.where(Article.code_key.like(f"%{needle}%"))
    return list(session.scalars(stmt.order_by(Article.code).limit(limit)))


def articles_count(session: Session) -> int:
    return session.scalar(
        select(func.count()).select_from(Article).where(Article.is_active.is_(True))
    ) or 0


# --- Печать этикеток --------------------------------------------------------


def create_print_batch(
    session: Session,
    *,
    article_id: int,
    qty: int,
    employee: Employee,
    site: Site,
) -> PrintBatch:
    """Завести qty зеркал в статусе «Напечатано» и выдать им номера.

    Сборщик фиксируется здесь и больше не меняется.
    """
    if qty < 1:
        raise ServiceError("Количество должно быть не меньше 1.")
    if qty > MAX_BATCH_QTY:
        raise ServiceError(
            f"За один раз можно напечатать не больше {MAX_BATCH_QTY} этикеток."
        )

    article = session.get(Article, article_id)
    if article is None or not article.is_active:
        raise ServiceError("Артикул не найден. Выберите его из справочника.")

    letters = article.group_letters or group_letters(article.code)
    numbers = numbering.allocate(session, qty)
    moment = now_utc()

    batch = PrintBatch(
        article_id=article.id,
        employee_id=employee.id,
        site_id=site.id,
        qty=qty,
        created_at=moment,
    )
    session.add(batch)
    session.flush()

    for seq in numbers:
        mirror = Mirror(
            number=format_number(letters, seq),
            seq=seq,
            group_letters=letters,
            article_id=article.id,
            assembler_id=employee.id,
            site_id=site.id,
            batch_id=batch.id,
            status=statuses.PRINTED,
            created_at=moment,
            status_at=moment,
        )
        session.add(mirror)
        session.flush()
        session.add(
            Movement(
                mirror_id=mirror.id,
                from_status=None,
                to_status=statuses.PRINTED,
                employee_id=employee.id,
                site_id=site.id,
                note="Напечатана этикетка",
                created_at=moment,
            )
        )

    session.commit()
    return batch


def get_batch(session: Session, batch_id: int) -> PrintBatch:
    batch = session.get(PrintBatch, batch_id)
    if batch is None:
        raise ServiceError("Партия печати не найдена.")
    return batch


def batch_mirrors(session: Session, batch_id: int) -> list[Mirror]:
    return list(
        session.scalars(
            select(Mirror).where(Mirror.batch_id == batch_id).order_by(Mirror.seq)
        )
    )


# --- Сканирование -----------------------------------------------------------


@dataclass
class ScanResult:
    ok: bool
    message: str
    number: str = ""
    mirror: Mirror | None = None


def _find_mirror(session: Session, number: str) -> Mirror | None:
    return session.scalar(select(Mirror).where(Mirror.number == number))


def _move(
    session: Session,
    mirror: Mirror,
    to_status: str,
    employee: Employee,
    site: Site,
    note: str,
) -> None:
    moment = now_utc()
    movement = Movement(
        mirror_id=mirror.id,
        from_status=mirror.status,
        to_status=to_status,
        employee_id=employee.id,
        site_id=site.id,
        note=note,
        created_at=moment,
    )
    mirror.status = to_status
    mirror.status_at = moment
    session.add(movement)
    session.commit()


def _last_movement(session: Session, mirror: Mirror, to_status: str) -> Movement | None:
    return session.scalar(
        select(Movement)
        .where(Movement.mirror_id == mirror.id, Movement.to_status == to_status)
        .order_by(Movement.id.desc())
        .limit(1)
    )


def _scan(
    session: Session,
    raw: str,
    *,
    expected_status: str,
    to_status: str,
    employee: Employee,
    site: Site,
    note: str,
    success_text: str,
) -> ScanResult:
    number = normalize_number(raw)
    if not number:
        return ScanResult(False, "Пустой скан. Поднесите сканер к штрихкоду.")
    if not is_valid_number(number):
        return ScanResult(
            False,
            f"«{number}» не похож на номер зеркала. "
            "Ожидается формат вида Z0000123. Отсканируйте этикетку заново.",
            number,
        )

    mirror = _find_mirror(session, number)
    if mirror is None:
        return ScanResult(
            False,
            f"Зеркало {number} в базе не найдено. "
            "Проверьте, та ли это этикетка, или сообщите мастеру.",
            number,
        )

    # Перемещений между площадками в версии 0.1 нет: сканировать можно
    # только там, где зеркало лежит.
    if mirror.site_id != site.id:
        return ScanResult(
            False,
            f"Зеркало {number} относится к площадке «{mirror.site.name}», "
            f"а вы работаете на площадке «{site.name}». "
            "Перемещения между площадками пока не поддерживаются.",
            number,
            mirror,
        )

    if mirror.status == to_status:
        # Повторный скан — самая частая ситуация, объясняем подробно.
        done = _last_movement(session, mirror, to_status)
        who = done.employee.name if done else "—"
        when = fmt(done.created_at) if done else "—"
        return ScanResult(
            False,
            f"Зеркало {number} уже в статусе «{statuses.title(to_status)}» — "
            f"{who}, {when}. Повторный скан не нужен.",
            number,
            mirror,
        )

    if mirror.status != expected_status:
        return ScanResult(
            False,
            f"Зеркало {number} сейчас в статусе «{mirror.status_title}», "
            f"а для этой операции нужен статус «{statuses.title(expected_status)}». "
            f"Переход «{mirror.status_title}» → «{statuses.title(to_status)}» запрещён.",
            number,
            mirror,
        )

    if not statuses.can_move(mirror.status, to_status):
        return ScanResult(
            False,
            f"Переход «{mirror.status_title}» → «{statuses.title(to_status)}» "
            "не предусмотрен.",
            number,
            mirror,
        )

    _move(session, mirror, to_status, employee, site, note)
    return ScanResult(
        True,
        f"{number}: {success_text} ({mirror.article.code})",
        number,
        mirror,
    )


def scan_receive(session: Session, raw: str, employee: Employee, site: Site) -> ScanResult:
    """Приёмка на склад: Напечатано → На складе."""
    return _scan(
        session,
        raw,
        expected_status=statuses.PRINTED,
        to_status=statuses.IN_STOCK,
        employee=employee,
        site=site,
        note="Приёмка на склад сканом",
        success_text="принято на склад",
    )


def scan_ship(session: Session, raw: str, employee: Employee, site: Site) -> ScanResult:
    """Отгрузка: На складе → Отгружено."""
    return _scan(
        session,
        raw,
        expected_status=statuses.IN_STOCK,
        to_status=statuses.SHIPPED,
        employee=employee,
        site=site,
        note="Отгрузка сканом",
        success_text="отгружено",
    )


# --- Остатки и журнал -------------------------------------------------------


def waiting_for_receive(session: Session, site: Site) -> int:
    """Сколько этикеток напечатано, но ещё не принято на склад."""
    return session.scalar(
        select(func.count())
        .select_from(Mirror)
        .where(Mirror.status == statuses.PRINTED, Mirror.site_id == site.id)
    ) or 0


def in_stock_count(session: Session, site: Site) -> int:
    """Сколько зеркал лежит на складе этой площадки."""
    return session.scalar(
        select(func.count())
        .select_from(Mirror)
        .where(Mirror.status == statuses.IN_STOCK, Mirror.site_id == site.id)
    ) or 0


@dataclass
class StockRow:
    article: str
    by_site: dict[int, int]
    total: int


@dataclass
class StockSection:
    """Раздел исходной таблицы: «ПОЛОТНА», «БЕЗРАМНЫЕ КРУГЛЫЕ» и так далее."""

    title: str
    sheet: str
    rows: list[StockRow]
    totals: dict[int, int]
    total: int


def stock(
    session: Session,
    query: str = "",
    only_with_stock: bool = False,
) -> tuple[list[Site], list[StockSection], dict[int, int], int]:
    """Остатки артикул × площадка, разложенные по разделам справочника.

    Порядок артикулов и разделов повторяет исходную таблицу.
    Считаются зеркала в статусе «На складе».
    """
    sites = active_sites(session)

    counts: dict[int, dict[int, int]] = {}
    for article_id, site_id, count in session.execute(
        select(Mirror.article_id, Mirror.site_id, func.count(Mirror.id))
        .where(Mirror.status == statuses.IN_STOCK)
        .group_by(Mirror.article_id, Mirror.site_id)
    ).all():
        counts.setdefault(article_id, {})[site_id] = count

    stmt = select(Article).where(Article.is_active.is_(True))
    needle = match_key(query)
    if needle:
        stmt = stmt.where(Article.code_key.like(f"%{needle}%"))
    # Разделы идут в том же порядке, что и в файле: сначала по листу,
    # затем по положению строки внутри листа.
    stmt = stmt.order_by(Article.id, Article.sort_order)

    sections: list[StockSection] = []
    index: dict[tuple[str, str], StockSection] = {}
    totals = {site.id: 0 for site in sites}
    grand = 0

    for article in session.scalars(stmt):
        by_article = counts.get(article.id, {})
        row_total = sum(by_article.values())
        if only_with_stock and row_total == 0:
            continue

        sheet = article.source_sheet or "Без листа"
        title = article.section or sheet
        key = (sheet, title)
        section = index.get(key)
        if section is None:
            section = StockSection(
                title=title,
                sheet=sheet,
                rows=[],
                totals={site.id: 0 for site in sites},
                total=0,
            )
            index[key] = section
            sections.append(section)

        row = StockRow(
            article=article.code,
            by_site={site.id: by_article.get(site.id, 0) for site in sites},
            total=row_total,
        )
        section.rows.append(row)
        for site_id, count in row.by_site.items():
            section.totals[site_id] += count
            totals[site_id] += count
        section.total += row_total
        grand += row_total

    return sites, sections, totals, grand


def journal(
    session: Session,
    *,
    limit: int = 100,
    offset: int = 0,
    number: str = "",
) -> tuple[list[Movement], int]:
    """Журнал движений, самые свежие сверху."""
    stmt = select(Movement).join(Mirror, Mirror.id == Movement.mirror_id)
    count_stmt = select(func.count()).select_from(Movement).join(
        Mirror, Mirror.id == Movement.mirror_id
    )

    needle = normalize_number(number)
    if needle:
        stmt = stmt.where(Mirror.number.like(f"%{needle}%"))
        count_stmt = count_stmt.where(Mirror.number.like(f"%{needle}%"))

    total = session.scalar(count_stmt) or 0
    items = list(
        session.scalars(stmt.order_by(Movement.id.desc()).limit(limit).offset(offset))
    )
    return items, total


def mirror_history(session: Session, number: str) -> tuple[Mirror | None, list[Movement]]:
    mirror = _find_mirror(session, normalize_number(number))
    if mirror is None:
        return None, []
    movements = list(
        session.scalars(
            select(Movement)
            .where(Movement.mirror_id == mirror.id)
            .order_by(Movement.id)
        )
    )
    return mirror, movements
