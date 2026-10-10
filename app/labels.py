"""Печать этикеток 58 × 40 мм в PDF.

Разметка (сверху вниз): чёрная шапка «СКЛАД» с площадкой, артикул
крупно, штрихкод Code 128, номер цифрами, внизу сборщик и дата.

Ширина штриха — 3 точки принтера с разрешением 203 dpi. Одна этикетка
на одной странице: термопринтер печатает их подряд.
"""

from __future__ import annotations

import io
from pathlib import Path

from reportlab.graphics.barcode import code128
from reportlab.lib.colors import black, white
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from .models import Mirror
from .timeutil import fmt_date

# --- Размеры -----------------------------------------------------------------

LABEL_WIDTH = 58 * mm
LABEL_HEIGHT = 40 * mm
MARGIN = 2 * mm

# Разрешение термопринтера и ширина штриха в его точках.
PRINTER_DPI = 203
BAR_DOTS = 3
# Точка принтера в пунктах PDF: 1 пункт = 1/72 дюйма.
BAR_WIDTH = BAR_DOTS * 72.0 / PRINTER_DPI

HEADER_HEIGHT = 7 * mm
BARCODE_HEIGHT = 10 * mm

# --- Шрифты ------------------------------------------------------------------

FONT_REGULAR = "SkladSans"
FONT_BOLD = "SkladSans-Bold"

_FONT_DIR = Path(__file__).resolve().parent / "fonts"
_SYSTEM_FONT_DIRS = (
    Path("/usr/share/fonts/truetype/dejavu"),
    Path("/usr/share/fonts/dejavu"),
    Path("C:/Windows/Fonts"),
    Path("/Library/Fonts"),
)
_fonts_ready = False


def _find_font(filename: str) -> Path | None:
    candidate = _FONT_DIR / filename
    if candidate.exists():
        return candidate
    for directory in _SYSTEM_FONT_DIRS:
        candidate = directory / filename
        if candidate.exists():
            return candidate
    return None


def ensure_fonts() -> bool:
    """Зарегистрировать шрифты с кириллицей. True, если получилось."""
    global _fonts_ready
    if _fonts_ready:
        return True
    regular = _find_font("DejaVuSans.ttf")
    bold = _find_font("DejaVuSans-Bold.ttf")
    if regular is None or bold is None:
        return False
    pdfmetrics.registerFont(TTFont(FONT_REGULAR, str(regular)))
    pdfmetrics.registerFont(TTFont(FONT_BOLD, str(bold)))
    _fonts_ready = True
    return True


def _fonts() -> tuple[str, str]:
    """Имена шрифтов. Если кириллических нет — встроенный Helvetica."""
    if ensure_fonts():
        return FONT_REGULAR, FONT_BOLD
    return "Helvetica", "Helvetica-Bold"


# --- Отрисовка ---------------------------------------------------------------


def _fit_font_size(text: str, font: str, max_width: float, start: float, minimum: float) -> float:
    """Подобрать размер шрифта, при котором строка влезает в ширину."""
    size = start
    while size > minimum and pdfmetrics.stringWidth(text, font, size) > max_width:
        size -= 0.5
    return size


def _split_to_fit(text: str, font: str, size: float, max_width: float) -> list[str]:
    """Разбить длинный артикул по пробелам максимум на две строки."""
    if pdfmetrics.stringWidth(text, font, size) <= max_width:
        return [text]
    words = text.split(" ")
    if len(words) < 2:
        return [text]
    best: tuple[str, str] | None = None
    for cut in range(1, len(words)):
        first = " ".join(words[:cut])
        second = " ".join(words[cut:])
        widest = max(
            pdfmetrics.stringWidth(first, font, size),
            pdfmetrics.stringWidth(second, font, size),
        )
        if best is None or widest < max(
            pdfmetrics.stringWidth(best[0], font, size),
            pdfmetrics.stringWidth(best[1], font, size),
        ):
            best = (first, second)
    return list(best) if best else [text]


def draw_label(
    pdf: canvas.Canvas,
    *,
    number: str,
    article: str,
    site_name: str,
    assembler: str,
    date_text: str,
) -> None:
    """Нарисовать одну этикетку на текущей странице."""
    regular, bold = _fonts()
    inner_width = LABEL_WIDTH - 2 * MARGIN

    # Чёрная шапка: слева «СКЛАД», справа площадка.
    pdf.setFillColor(black)
    pdf.rect(0, LABEL_HEIGHT - HEADER_HEIGHT, LABEL_WIDTH, HEADER_HEIGHT, stroke=0, fill=1)
    pdf.setFillColor(white)
    header_baseline = LABEL_HEIGHT - HEADER_HEIGHT + 2.1 * mm
    pdf.setFont(bold, 11)
    pdf.drawString(MARGIN, header_baseline, "СКЛАД")
    site_size = _fit_font_size(
        site_name, regular, inner_width - 16 * mm, start=9, minimum=5.5
    )
    pdf.setFont(regular, site_size)
    pdf.drawRightString(LABEL_WIDTH - MARGIN, header_baseline, site_name)

    pdf.setFillColor(black)

    # Артикул крупно. При необходимости уменьшаем и переносим на две строки.
    article_top = LABEL_HEIGHT - HEADER_HEIGHT - 1.5 * mm
    size = _fit_font_size(article, bold, inner_width, start=15, minimum=7)
    lines = _split_to_fit(article, bold, size, inner_width)
    if len(lines) > 1:
        size = min(size, 11)
        lines = _split_to_fit(article, bold, size, inner_width)
    pdf.setFont(bold, size)
    line_height = size * 1.08
    baseline = article_top - size * 0.86
    for line in lines[:2]:
        pdf.drawCentredString(LABEL_WIDTH / 2, baseline, line)
        baseline -= line_height

    # Штрихкод Code 128 по центру.
    barcode = code128.Code128(
        number,
        barWidth=BAR_WIDTH,
        barHeight=BARCODE_HEIGHT,
        humanReadable=False,
        quiet=False,
    )
    barcode_y = 11.5 * mm
    barcode.drawOn(pdf, (LABEL_WIDTH - barcode.width) / 2, barcode_y)

    # Номер цифрами под штрихкодом — чтобы можно было ввести руками.
    pdf.setFont(bold, 11)
    pdf.drawCentredString(LABEL_WIDTH / 2, barcode_y - 4 * mm, number)

    # Низ: сборщик и дата.
    pdf.setFont(regular, 7)
    footer_y = 2 * mm
    assembler_size = _fit_font_size(
        assembler, regular, inner_width - 20 * mm, start=7, minimum=5
    )
    pdf.setFont(regular, assembler_size)
    pdf.drawString(MARGIN, footer_y, assembler)
    pdf.setFont(regular, 7)
    pdf.drawRightString(LABEL_WIDTH - MARGIN, footer_y, date_text)


def render_labels(mirrors: list[Mirror]) -> bytes:
    """Собрать PDF: одна этикетка на страницу, в порядке номеров."""
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(LABEL_WIDTH, LABEL_HEIGHT))
    pdf.setTitle("Этикетки склада зеркал")
    for mirror in mirrors:
        draw_label(
            pdf,
            number=mirror.number,
            article=mirror.article.code,
            site_name=mirror.site.name,
            assembler=mirror.assembler.name,
            date_text=fmt_date(mirror.created_at),
        )
        pdf.showPage()
    pdf.save()
    return buffer.getvalue()
