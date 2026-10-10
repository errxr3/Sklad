"""Загрузка справочника артикулов из TOP.xlsx.

Артикулы берутся из столбца A каждого листа. Пустые строки и строки
с заголовком пропускаются. Повторы внутри файла и уже загруженные
артикулы не дублируются: ключ сравнения — нормализованный код
без учёта регистра, с русской «х», приведённой к латинской «x».
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .articles import group_letters, match_key, normalize_code
from .models import Article

# Подписи столбца, которые считаем заголовком, а не артикулом.
HEADER_WORDS = {
    "артикул",
    "артикулы",
    "наименование",
    "номенклатура",
    "код",
    "article",
    "sku",
    "name",
    "item",
}

# Заголовки разделов внутри листа: «ПОЛОТНА», «АЛЮМИНИЙ», «БЕЗРАМНЫЕ».
# Они написаны прописными и не содержат ни цифр, ни латиницы,
# в отличие от артикулов вида «Zerkalo_100х200».
def is_section_title(code: str) -> bool:
    """Заголовок раздела: «ПОЛОТНА», «ПОЛОТНА без креплений», «АЛЮМИНИЙ на ноге».

    Отличается от артикула тем, что начинается со слова капсом и не
    содержит цифр, тогда как артикулы — «Zerkalo_100x200», «Alum rama
    200x100 black» — всегда с размерами и без капса в начале.
    """
    if any(ch.isdigit() for ch in code):
        return False
    words = code.split()
    if not words:
        return False
    first = [ch for ch in words[0] if ch.isalpha()]
    # Одной заглавной буквы мало: так начинается любое слово в начале строки.
    return len(first) > 1 and all(ch.isupper() for ch in first)


@dataclass
class ImportReport:
    added: int = 0
    already: int = 0
    restored: int = 0
    skipped: int = 0
    per_sheet: dict[str, int] = field(default_factory=dict)
    examples: list[str] = field(default_factory=list)
    ignored: list[str] = field(default_factory=list)

    @property
    def total_rows(self) -> int:
        return self.added + self.already + self.skipped

    def describe(self) -> str:
        sheets = ", ".join(f"{k}: {v}" for k, v in self.per_sheet.items()) or "—"
        return (
            f"Добавлено новых: {self.added}\n"
            f"Уже были в справочнике: {self.already}\n"
            + (f"Включено обратно: {self.restored}\n" if self.restored else "")
            +
            f"Пропущено (пусто или заголовок): {self.skipped}\n"
            f"Листы (принято артикулов): {sheets}"
        )


def _is_meaningful(code: str) -> bool:
    """Отличить артикул от шапки, заголовка раздела и строки с итогами."""
    if not code:
        return False
    if code.casefold() in HEADER_WORDS:
        return False
    if is_section_title(code):
        return False
    # Строки итогов вида «0(0)//0//0» — цифры и знаки, без букв.
    if not any(ch.isalpha() for ch in code):
        return False
    # Дата в первой строке листа.
    if code[:4].isdigit() and "-" in code:
        return False
    return True


def read_codes(path: Path) -> list[tuple[str, str, str]]:
    """Прочитать файл и вернуть тройки (артикул, лист, раздел).

    Раздел пустой, если файл его не задаёт: тогда берётся заголовок,
    встреченный выше по листу.
    """
    suffix = path.suffix.lower()
    if suffix in {".xlsx", ".xlsm"}:
        return _read_xlsx(path)
    if suffix in {".csv", ".txt"}:
        return _read_csv(path)
    raise ValueError(f"Неизвестный формат файла: {path.suffix}. Нужен .xlsx или .csv")


def _read_xlsx(path: Path) -> list[tuple[str, str, str]]:
    from openpyxl import load_workbook

    rows: list[tuple[str, str, str]] = []
    workbook = load_workbook(filename=path, read_only=True, data_only=True)
    try:
        for sheet in workbook.worksheets:
            for (value,) in sheet.iter_rows(min_col=1, max_col=1, values_only=True):
                rows.append((normalize_code(value), sheet.title, ""))
    finally:
        workbook.close()
    return rows


def _read_csv(path: Path) -> list[tuple[str, str, str]]:
    """CSV или простой список.

    Первый столбец — артикул. Если есть второй и третий, это раздел
    и лист: так справочник можно хранить плоским файлом, не теряя
    группировку для таблицы остатков.
    """
    rows: list[tuple[str, str, str]] = []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        sample = handle.readline()
        handle.seek(0)
        delimiter = ";" if sample.count(";") > sample.count(",") else ","
        for record in csv.reader(handle, delimiter=delimiter):
            if not record:
                continue
            code = normalize_code(record[0])
            section = normalize_code(record[1]) if len(record) > 1 else ""
            sheet = normalize_code(record[2]) if len(record) > 2 else path.stem
            rows.append((code, sheet or path.stem, section))
    return rows


def import_articles(session: Session, path: Path) -> ImportReport:
    """Загрузить артикулы в справочник. Существующие записи не трогаем."""
    report = ImportReport()
    seen_in_file: set[str] = set()

    existing = {
        article.code_key: article
        for article in session.scalars(select(Article)).all()
    }

    order_in_sheet: dict[str, int] = {}
    # Заголовок раздела действует до следующего заголовка на том же листе.
    section_of_sheet: dict[str, str] = {}

    for code, sheet, explicit_section in read_codes(path):
        if is_section_title(code):
            section_of_sheet[sheet] = code
            report.skipped += 1
            continue
        if not _is_meaningful(code):
            report.skipped += 1
            if code and len(report.ignored) < 10 and code not in report.ignored:
                report.ignored.append(code)
            continue
        key = match_key(code)
        report.per_sheet[sheet] = report.per_sheet.get(sheet, 0) + 1
        if key in seen_in_file:
            report.already += 1
            continue
        seen_in_file.add(key)
        position = order_in_sheet.get(sheet, 0) + 1
        order_in_sheet[sheet] = position

        known = existing.get(key)
        if known is not None:
            # Артикул уже заведён: освежаем раздел и порядок, а если он
            # был отключён — возвращаем в строй, раз файл его содержит.
            known.source_sheet = sheet
            known.section = explicit_section or section_of_sheet.get(sheet)
            known.sort_order = position
            if not known.is_active:
                known.is_active = True
                report.restored += 1
            else:
                report.already += 1
            continue
        session.add(
            Article(
                code=code,
                code_key=key,
                group_letters=group_letters(code),
                source_sheet=sheet,
                section=explicit_section or section_of_sheet.get(sheet),
                sort_order=position,
            )
        )
        report.added += 1
        if len(report.examples) < 10:
            report.examples.append(code)

    session.commit()
    return report
