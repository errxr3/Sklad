"""Служебная проверка: прочитать Code 128 с картинки этикетки.

Нужна только для самопроверки печати — в работе приложения не участвует.
Запуск: python scripts/decode_code128.py label-1.png
"""

from __future__ import annotations

import sys

from PIL import Image
from reportlab.graphics.barcode import code128


def _reverse_table() -> dict[str, int]:
    """Паттерн полос -> код символа. Таблицу берём из reportlab.

    В reportlab ширины закодированы буквами: A/a = 1 модуль, B/b = 2,
    C/c = 3. Переводим в строку цифр вида «212222».
    """
    table: dict[str, int] = {}
    for value, pattern in code128._patterns.items():
        widths = "".join(str(ord(ch.upper()) - ord("A") + 1) for ch in pattern)
        table[widths] = value
    return table


VALUE_BY_PATTERN = _reverse_table()


def _scan_line(image: Image.Image, y: int) -> list[tuple[int, int]]:
    """Длины подряд идущих чёрных/белых пикселей в строке y."""
    pixels = image.convert("L").load()
    width = image.size[0]
    runs: list[tuple[int, int]] = []
    current = 1 if pixels[0, y] < 128 else 0
    length = 0
    for x in range(width):
        value = 1 if pixels[x, y] < 128 else 0
        if value == current:
            length += 1
        else:
            runs.append((current, length))
            current, length = value, 1
    runs.append((current, length))
    return runs


def decode(path: str) -> str:
    image = Image.open(path)
    width, height = image.size
    runs = None
    for y in range(int(height * 0.35), int(height * 0.65)):
        candidate = _scan_line(image, y)
        bars = [r for r in candidate if r[0] == 1]
        if len(bars) > 20:
            runs = candidate
            break
    if runs is None:
        raise SystemExit("Штрихкод на картинке не найден")

    # Отбрасываем белые поля по краям.
    while runs and runs[0][0] == 0:
        runs.pop(0)
    while runs and runs[-1][0] == 0:
        runs.pop()

    module = min(length for _, length in runs)
    widths = [max(1, round(length / module)) for _, length in runs]

    # Символ Code 128 — шесть полос; стоп-символ длиннее.
    symbols: list[int] = []
    index = 0
    while index + 6 <= len(widths):
        chunk = widths[index : index + 6]
        pattern = "".join(str(w) for w in chunk)
        if pattern not in VALUE_BY_PATTERN:
            break
        symbols.append(VALUE_BY_PATTERN[pattern])
        index += 6

    if len(symbols) < 3:
        raise SystemExit(f"Разобрано слишком мало символов: {symbols}")

    start, payload, checksum = symbols[0], symbols[1:-1], symbols[-1]
    expected = (start + sum(v * (i + 1) for i, v in enumerate(payload))) % 103
    if expected != checksum:
        raise SystemExit(f"Контрольная сумма не сошлась: {expected} != {checksum}")

    text = ""
    charset = {103: "A", 104: "B", 105: "C"}.get(start, "B")
    for value in payload:
        if value in (99, 105):
            charset = "C"
            continue
        if value in (100, 104):
            charset = "B"
            continue
        if value in (101, 103):
            charset = "A"
            continue
        if charset == "C":
            text += f"{value:02d}"
        else:
            text += chr(value + 32) if value < 95 else "?"
    return text


if __name__ == "__main__":
    for argument in sys.argv[1:]:
        print(f"{argument}: {decode(argument)!r}")
