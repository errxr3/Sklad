"""Нормализация артикулов, буквы группы и формат номера."""

import pytest

from app.articles import (
    format_number,
    group_letters,
    is_valid_number,
    match_key,
    normalize_code,
    normalize_number,
)


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("  Zerkalo 600x800  ", "Zerkalo 600x800"),
        ("Zerkalo    600x800", "Zerkalo 600x800"),
        ("Zerkalo\t600x800", "Zerkalo 600x800"),
        ("Zerkalo 600x800", "Zerkalo 600x800"),
        (None, ""),
        (600, "600"),
    ],
)
def test_лишние_пробелы_убираются(raw, expected):
    assert normalize_code(raw) == expected


def test_русская_х_приводится_к_латинской():
    assert normalize_code("Zerkalo 600х800") == "Zerkalo 600x800"
    assert normalize_code("ЗЕРКАЛО 600Х800") == "ЗЕРКАЛО 600X800"


def test_русская_и_латинская_х_совпадают_при_сравнении():
    assert match_key("Zerkalo 600х800") == match_key("ZERKALO  600x800 ")


@pytest.mark.parametrize(
    "code, letters",
    [
        ("Zerkalo 600x800", "Z"),
        ("zerkalo 400", "Z"),
        ("bp_Zerkalo 500x700", "Z"),
        ("BP_ZERKALO 500", "Z"),
        ("Alum 400x1200", "A"),
        ("Ambient 700x500", "AT"),
        ("Polka 300", "X"),
        ("", "X"),
    ],
)
def test_буквы_группы(code, letters):
    assert group_letters(code) == letters


def test_номер_это_буквы_и_семь_цифр():
    assert format_number("Z", 123) == "Z0000123"
    assert format_number("AT", 1) == "AT0000001"
    assert format_number("X", 9999999) == "X9999999"


def test_скан_приводится_к_каноническому_виду():
    assert normalize_number(" z0000123 ") == "Z0000123"
    assert normalize_number("Z 0000123") == "Z0000123"


def test_проверка_формата_номера():
    assert is_valid_number("Z0000123")
    assert is_valid_number("AT0000123")
    assert not is_valid_number("Z123")
    assert not is_valid_number("12345678")
    assert not is_valid_number("")
