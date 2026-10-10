"""Путь зеркала: печать → приёмка → отгрузка, отказы и журнал."""

import pytest

from app import services, statuses
from app.models import Mirror, Movement
from tests.conftest import make_article


def _print_one(db, article, worker, site):
    batch = services.create_print_batch(
        db, article_id=article.id, qty=1, employee=worker, site=site
    )
    return services.batch_mirrors(db, batch.id)[0]


# --- Печать -----------------------------------------------------------------


def test_печать_создаёт_зеркала_в_статусе_напечатано(db, article, worker, site):
    batch = services.create_print_batch(
        db, article_id=article.id, qty=3, employee=worker, site=site
    )
    mirrors = services.batch_mirrors(db, batch.id)
    assert len(mirrors) == 3
    assert [m.status for m in mirrors] == [statuses.PRINTED] * 3
    assert [m.number for m in mirrors] == ["Z0000001", "Z0000002", "Z0000003"]


def test_сборщик_присваивается_навсегда(db, article, worker, second_worker, site):
    mirror = _print_one(db, article, worker, site)
    assert mirror.assembler_id == worker.id

    # Принимает и отгружает другой сотрудник — сборщик не меняется.
    services.scan_receive(db, mirror.number, second_worker, site)
    services.scan_ship(db, mirror.number, second_worker, site)
    db.refresh(mirror)
    assert mirror.assembler_id == worker.id
    assert mirror.assembler.name == worker.name


def test_печать_нулевого_количества_запрещена(db, article, worker, site):
    with pytest.raises(services.ServiceError):
        services.create_print_batch(
            db, article_id=article.id, qty=0, employee=worker, site=site
        )


def test_печать_слишком_большой_партии_запрещена(db, article, worker, site):
    with pytest.raises(services.ServiceError) as info:
        services.create_print_batch(
            db, article_id=article.id, qty=10_000, employee=worker, site=site
        )
    assert "не больше" in str(info.value)


# --- Приёмка и отгрузка -----------------------------------------------------


def test_полный_путь_зеркала(db, article, worker, site):
    mirror = _print_one(db, article, worker, site)

    received = services.scan_receive(db, mirror.number, worker, site)
    assert received.ok
    db.refresh(mirror)
    assert mirror.status == statuses.IN_STOCK

    shipped = services.scan_ship(db, mirror.number, worker, site)
    assert shipped.ok
    db.refresh(mirror)
    assert mirror.status == statuses.SHIPPED


def test_скан_не_зависит_от_регистра_и_пробелов(db, article, worker, site):
    mirror = _print_one(db, article, worker, site)
    result = services.scan_receive(db, f"  {mirror.number.lower()}  ", worker, site)
    assert result.ok


# --- Понятные отказы --------------------------------------------------------


def test_повторный_скан_при_приёмке(db, article, worker, site):
    mirror = _print_one(db, article, worker, site)
    services.scan_receive(db, mirror.number, worker, site)

    again = services.scan_receive(db, mirror.number, worker, site)
    assert not again.ok
    assert "уже" in again.message
    assert "На складе" in again.message
    assert worker.name in again.message


def test_повторная_отгрузка(db, article, worker, site):
    mirror = _print_one(db, article, worker, site)
    services.scan_receive(db, mirror.number, worker, site)
    services.scan_ship(db, mirror.number, worker, site)

    again = services.scan_ship(db, mirror.number, worker, site)
    assert not again.ok
    assert "уже" in again.message


def test_отгрузка_без_приёмки_запрещена(db, article, worker, site):
    mirror = _print_one(db, article, worker, site)
    result = services.scan_ship(db, mirror.number, worker, site)
    assert not result.ok
    assert "Напечатано" in result.message
    assert "запрещён" in result.message
    db.refresh(mirror)
    assert mirror.status == statuses.PRINTED


def test_приёмка_отгруженного_запрещена(db, article, worker, site):
    mirror = _print_one(db, article, worker, site)
    services.scan_receive(db, mirror.number, worker, site)
    services.scan_ship(db, mirror.number, worker, site)

    result = services.scan_receive(db, mirror.number, worker, site)
    assert not result.ok
    assert "Отгружено" in result.message


def test_неизвестный_номер(db, worker, site):
    result = services.scan_receive(db, "Z0009999", worker, site)
    assert not result.ok
    assert "не найдено" in result.message


def test_мусор_вместо_номера(db, worker, site):
    result = services.scan_receive(db, "абвгд", worker, site)
    assert not result.ok
    assert "не похож на номер" in result.message


def test_пустой_скан(db, worker, site):
    result = services.scan_receive(db, "   ", worker, site)
    assert not result.ok
    assert "Пустой скан" in result.message


def test_скан_на_чужой_площадке(db, article, worker, site, other_site):
    mirror = _print_one(db, article, worker, site)
    result = services.scan_receive(db, mirror.number, worker, other_site)
    assert not result.ok
    assert site.name in result.message
    assert other_site.name in result.message
    db.refresh(mirror)
    assert mirror.status == statuses.PRINTED


# --- Журнал движений --------------------------------------------------------


def test_каждая_смена_статуса_это_отдельная_запись(db, article, worker, site):
    mirror = _print_one(db, article, worker, site)
    services.scan_receive(db, mirror.number, worker, site)
    services.scan_ship(db, mirror.number, worker, site)

    _, movements = services.mirror_history(db, mirror.number)
    assert [(m.from_status, m.to_status) for m in movements] == [
        (None, statuses.PRINTED),
        (statuses.PRINTED, statuses.IN_STOCK),
        (statuses.IN_STOCK, statuses.SHIPPED),
    ]


def test_неудачный_скан_не_пишется_в_журнал(db, article, worker, site):
    mirror = _print_one(db, article, worker, site)
    before = db.query(Movement).count()
    services.scan_ship(db, mirror.number, worker, site)  # отгрузка без приёмки
    services.scan_receive(db, "Z0009999", worker, site)  # неизвестный номер
    assert db.query(Movement).count() == before


def test_журнал_отдаёт_свежие_записи_сверху(db, article, worker, site):
    batch = services.create_print_batch(
        db, article_id=article.id, qty=3, employee=worker, site=site
    )
    mirrors = services.batch_mirrors(db, batch.id)
    services.scan_receive(db, mirrors[0].number, worker, site)

    items, total = services.journal(db, limit=10)
    assert total == 4
    assert items[0].to_status == statuses.IN_STOCK


def test_журнал_ищет_по_номеру(db, article, worker, site):
    batch = services.create_print_batch(
        db, article_id=article.id, qty=3, employee=worker, site=site
    )
    mirrors = services.batch_mirrors(db, batch.id)
    items, total = services.journal(db, number=mirrors[1].number)
    assert total == 1
    assert items[0].mirror.number == mirrors[1].number


# --- Остатки ----------------------------------------------------------------


def test_остатки_считают_только_то_что_на_складе(db, article, worker, site):
    batch = services.create_print_batch(
        db, article_id=article.id, qty=5, employee=worker, site=site
    )
    mirrors = services.batch_mirrors(db, batch.id)

    # Ничего не принято — остаток нулевой.
    _, rows, _, grand = services.stock(db)
    assert grand == 0

    for mirror in mirrors[:4]:
        services.scan_receive(db, mirror.number, worker, site)
    _, rows, _, grand = services.stock(db)
    assert grand == 4

    services.scan_ship(db, mirrors[0].number, worker, site)
    _, rows, totals, grand = services.stock(db)
    assert grand == 3
    assert rows[0].article == article.code
    assert rows[0].by_site[site.id] == 3


def test_остатки_разделяются_по_площадкам(db, article, worker, site, other_site):
    for current_site, count in ((site, 2), (other_site, 3)):
        batch = services.create_print_batch(
            db, article_id=article.id, qty=count, employee=worker, site=current_site
        )
        for mirror in services.batch_mirrors(db, batch.id):
            services.scan_receive(db, mirror.number, worker, current_site)

    _, rows, totals, grand = services.stock(db)
    assert grand == 5
    assert totals[site.id] == 2
    assert totals[other_site.id] == 3


def test_остатки_по_разным_артикулам(db, worker, site):
    zerkalo = make_article(db, "Zerkalo 600x800")
    alum = make_article(db, "Alum 400x1200")
    for article, count in ((zerkalo, 2), (alum, 1)):
        batch = services.create_print_batch(
            db, article_id=article.id, qty=count, employee=worker, site=site
        )
        for mirror in services.batch_mirrors(db, batch.id):
            services.scan_receive(db, mirror.number, worker, site)

    _, rows, _, grand = services.stock(db)
    assert grand == 3
    assert {row.article: row.total for row in rows} == {
        "Zerkalo 600x800": 2,
        "Alum 400x1200": 1,
    }


def test_ждут_приёмки(db, article, worker, site):
    batch = services.create_print_batch(
        db, article_id=article.id, qty=4, employee=worker, site=site
    )
    assert services.waiting_for_receive(db, site) == 4
    services.scan_receive(db, services.batch_mirrors(db, batch.id)[0].number, worker, site)
    assert services.waiting_for_receive(db, site) == 3


# --- Вход -------------------------------------------------------------------


def test_вход_по_пину(db, worker):
    from tests.conftest import TEST_PIN

    assert services.authenticate(db, worker.id, TEST_PIN).id == worker.id
    with pytest.raises(services.ServiceError):
        services.authenticate(db, worker.id, "0000")
    with pytest.raises(services.ServiceError):
        services.authenticate(db, 99999, TEST_PIN)


# --- Поиск артикулов --------------------------------------------------------


def test_поиск_артикула_не_различает_русскую_и_латинскую_х(db):
    make_article(db, "Zerkalo 600x800")
    assert len(services.find_articles(db, "600х800")) == 1
    assert len(services.find_articles(db, "600x800")) == 1
    assert len(services.find_articles(db, "ZERKALO")) == 1
