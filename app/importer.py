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


@dataclass
class ImportReport:
    added: int = 0
    already: int = 0
    skipped: int = 0
    per_sheet: dict[str, int] = field(default_factory=dict)
    examples: list[str] = field(default_factory=list)

    @property
    def total_rows(self) -> int:
        return self.added + self.already + self.skipped

    def describe(self) -> str:
        sheets = ", ".join(f"{k}: {v}" for k, v in self.per_sheet.items()) or "—"
        return (
            f"Добавлено новых: {self.added}\n"
            f"Уже были в справочнике: {self.already}\n"
            f"Пропущено (пусто или заголовок): {self.skipped}\n"
            f"Листы (принято артикулов): {sheets}"
        )


def _is_meaningful(code: str) -> bool:
    if not code:
        return False
    if code.casefold() in HEADER_WORDS:
        return False
    # Должна быть хотя бы одна буква или цифра.
    return any(ch.isalnum() for ch in code)


def read_codes(path: Path) -> list[tuple[str, str]]:
    """Прочитать файл и вернуть пары (артикул, имя листа)."""
    suffix = path.suffix.lower()
    if suffix in {".xlsx", ".xlsm"}:
        return _read_xlsx(path)
    if suffix in {".csv", ".txt"}:
        return _read_csv(path)
    raise ValueError(f"Неизвестный формат файла: {path.suffix}. Нужен .xlsx или .csv")


def _read_xlsx(path: Path) -> list[tuple[str, str]]:
    from openpyxl import load_workbook

    rows: list[tuple[str, str]] = []
    workbook = load_workbook(filename=path, read_only=True, data_only=True)
    try:
        for sheet in workbook.worksheets:
            for (value,) in sheet.iter_rows(min_col=1, max_col=1, values_only=True):
                rows.append((normalize_code(value), sheet.title))
    finally:
        workbook.close()
    return rows


def _read_csv(path: Path) -> list[tuple[str, str]]:
    """CSV или простой текстовый список: артикул — первый столбец."""
    rows: list[tuple[str, str]] = []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        sample = handle.readline()
        handle.seek(0)
        delimiter = ";" if sample.count(";") > sample.count(",") else ","
        for record in csv.reader(handle, delimiter=delimiter):
            if record:
                rows.append((normalize_code(record[0]), path.stem))
    return rows


def import_articles(session: Session, path: Path) -> ImportReport:
    """Загрузить артикулы в справочник. Существующие записи не трогаем."""
    report = ImportReport()
    seen_in_file: set[str] = set()

    existing = {
        key for (key,) in session.execute(select(Article.code_key)).all()
    }

    for code, sheet in read_codes(path):
        if not _is_meaningful(code):
            report.skipped += 1
            continue
        key = match_key(code)
        report.per_sheet[sheet] = report.per_sheet.get(sheet, 0) + 1
        if key in seen_in_file or key in existing:
            report.already += 1
            continue
        seen_in_file.add(key)
        session.add(
            Article(
                code=code,
                code_key=key,
                group_letters=group_letters(code),
                source_sheet=sheet,
            )
        )
        report.added += 1
        if len(report.examples) < 10:
            report.examples.append(code)

    session.commit()
    return report
