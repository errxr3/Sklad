"""Сквозной счётчик номеров."""

from app import numbering, services
from tests.conftest import make_article


def test_счётчик_начинается_с_единицы(db):
    assert numbering.current_value(db) == 0
    assert numbering.allocate(db, 1) == [1]
    db.commit()
    assert numbering.current_value(db) == 1


def test_выделяется_подряд_идущий_диапазон(db):
    assert numbering.allocate(db, 3) == [1, 2, 3]
    db.commit()
    assert numbering.allocate(db, 2) == [4, 5]
    db.commit()


def test_счётчик_общий_на_все_группы(db, worker, site):
    """Буквы разные, а цифры идут подряд — счётчик один."""
    zerkalo = make_article(db, "Zerkalo 600x800")
    alum = make_article(db, "Alum 400x1200")
    ambient = make_article(db, "Ambient 700x500")

    numbers = []
    for article in (zerkalo, alum, ambient, zerkalo):
        batch = services.create_print_batch(
            db, article_id=article.id, qty=1, employee=worker, site=site
        )
        numbers.extend(m.number for m in services.batch_mirrors(db, batch.id))

    assert numbers == ["Z0000001", "A0000002", "AT0000003", "Z0000004"]


def test_номера_не_повторяются_при_нескольких_партиях(db, worker, site, article):
    all_numbers = []
    for _ in range(5):
        batch = services.create_print_batch(
            db, article_id=article.id, qty=4, employee=worker, site=site
        )
        all_numbers.extend(m.number for m in services.batch_mirrors(db, batch.id))
    assert len(all_numbers) == 20
    assert len(set(all_numbers)) == 20
