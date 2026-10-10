"""Этикетка: размер страницы, содержимое и читаемость штрихкода."""

import subprocess
import sys
from pathlib import Path

import pytest

from app import labels, services
from app.config import BASE_DIR

MM = 72.0 / 25.4


def _print_batch(db, article, worker, site, qty=1):
    batch = services.create_print_batch(
        db, article_id=article.id, qty=qty, employee=worker, site=site
    )
    return services.batch_mirrors(db, batch.id)


def test_ширина_штриха_три_точки_при_203_dpi():
    # Три точки принтера, переведённые в пункты PDF.
    assert labels.BAR_WIDTH == pytest.approx(3 * 72 / 203, abs=1e-6)
    assert labels.BAR_WIDTH == pytest.approx(1.064, abs=0.001)


def test_размер_страницы_58_на_40_мм():
    assert labels.LABEL_WIDTH == pytest.approx(58 * MM, abs=0.01)
    assert labels.LABEL_HEIGHT == pytest.approx(40 * MM, abs=0.01)


def test_шрифт_с_кириллицей_найден():
    assert labels.ensure_fonts(), "Не найден DejaVuSans.ttf — кириллица не напечатается"


def test_одна_этикетка_на_страницу(db, article, worker, site):
    mirrors = _print_batch(db, article, worker, site, qty=3)
    pdf = labels.render_labels(mirrors)
    assert pdf.startswith(b"%PDF")
    assert pdf.count(b"/Type /Page\n") == 3 or pdf.count(b"/Page") >= 3


def test_пустая_партия_даёт_пустой_pdf():
    pdf = labels.render_labels([])
    assert pdf.startswith(b"%PDF")


def test_длинный_артикул_не_ломает_этикетку(db, worker, site):
    from tests.conftest import make_article

    article = make_article(
        db, "Ambient 1200x900 с подсветкой и сенсорным выключателем"
    )
    mirrors = _print_batch(db, article, worker, site)
    pdf = labels.render_labels(mirrors)
    assert pdf.startswith(b"%PDF")


# --- Проверка штрихкода на отрисованной картинке ----------------------------

pdftoppm = pytest.mark.skipif(
    subprocess.run(["which", "pdftoppm"], capture_output=True).returncode != 0,
    reason="нужен pdftoppm для растеризации",
)


@pdftoppm
def test_штрихкод_читается_при_203_dpi(db, article, worker, site, tmp_path):
    """Рисуем этикетку в том же разрешении, в каком печатает принтер,
    и декодируем штрихкод обратно."""
    mirrors = _print_batch(db, article, worker, site, qty=2)
    pdf_path = tmp_path / "labels.pdf"
    pdf_path.write_bytes(labels.render_labels(mirrors))

    subprocess.run(
        ["pdftoppm", "-r", "203", "-png", str(pdf_path), str(tmp_path / "label")],
        check=True,
    )
    images = sorted(tmp_path.glob("label-*.png"))
    assert len(images) == 2

    sys.path.insert(0, str(BASE_DIR / "scripts"))
    from decode_code128 import decode  # noqa: E402

    decoded = [decode(str(image)) for image in images]
    assert decoded == [mirrors[0].number, mirrors[1].number]
