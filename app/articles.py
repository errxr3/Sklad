"""Артикулы: нормализация написания и буквы группы для номера зеркала."""

from __future__ import annotations

import re

# Русская «х» визуально не отличается от латинской «x», а в выгрузках
# встречаются оба варианта (600х800 и 600x800). Приводим к латинице.
_CYRILLIC_TO_LATIN = {
    "х": "x",
    "Х": "X",
}

# Любые пробельные символы, включая неразрывный пробел из Excel.
_SPACES_RE = re.compile(r"[\s ]+")


def normalize_code(raw: object) -> str:
    """Привести артикул к единому написанию для хранения и показа.

    Убираем пробелы по краям, схлопываем внутренние в один,
    меняем русскую «х» на латинскую «x». Регистр сохраняем.
    """
    if raw is None:
        return ""
    text = str(raw)
    for src, dst in _CYRILLIC_TO_LATIN.items():
        text = text.replace(src, dst)
    return _SPACES_RE.sub(" ", text).strip()


def match_key(raw: object) -> str:
    """Ключ для сравнения и поиска артикулов: без учёта регистра."""
    return normalize_code(raw).casefold()


# Правила выбора букв группы. Порядок важен: проверяем сверху вниз,
# первое совпадение по подстроке и выигрывает. «bp_Zerkalo» содержит
# «zerkalo», поэтому отдельного правила не требует.
GROUP_RULES = (
    ("zerkalo", "Z"),
    ("alum", "A"),
    ("ambient", "AT"),
)

DEFAULT_GROUP_LETTERS = "X"

# Сколько цифр в номере зеркала после букв группы.
NUMBER_DIGITS = 7


def group_letters(code: object) -> str:
    """Буквы группы по артикулу: Zerkalo/bp_Zerkalo — Z, Alum — A,
    Ambient — AT, всё остальное — X."""
    key = match_key(code)
    for needle, letters in GROUP_RULES:
        if needle in key:
            return letters
    return DEFAULT_GROUP_LETTERS


def format_number(letters: str, seq: int) -> str:
    """Собрать номер зеркала: буквы группы плюс 7 цифр. Пример: Z0000123."""
    return f"{letters}{seq:0{NUMBER_DIGITS}d}"


_NUMBER_RE = re.compile(r"^([A-Za-z]{1,4})(\d{%d})$" % NUMBER_DIGITS)


def normalize_number(raw: object) -> str:
    """Привести отсканированный номер к каноническому виду.

    Сканер может отдать номер с пробелами или в нижнем регистре;
    буквы приводим к верхнему. Если формат не распознан — возвращаем
    как есть (в верхнем регистре), решение о поиске принимает вызывающий.
    """
    text = _SPACES_RE.sub("", str(raw or "")).upper()
    for src, dst in _CYRILLIC_TO_LATIN.items():
        text = text.replace(src.upper(), dst.upper())
    return text


def is_valid_number(raw: object) -> bool:
    return bool(_NUMBER_RE.match(normalize_number(raw)))
