"""Статусы зеркала и допустимые переходы между ними."""

from __future__ import annotations

PRINTED = "printed"
IN_STOCK = "in_stock"
SHIPPED = "shipped"
IN_TRANSIT = "in_transit"
WRITTEN_OFF = "written_off"

# Человеческие названия для экранов и этикеток.
TITLES = {
    PRINTED: "Напечатано",
    IN_STOCK: "На складе",
    SHIPPED: "Отгружено",
    IN_TRANSIT: "В пути",
    WRITTEN_OFF: "Списано",
}

ALL = (PRINTED, IN_STOCK, SHIPPED, IN_TRANSIT, WRITTEN_OFF)

# Полная схема движения зеркала. Статусы «В пути» и «Списано» заведены
# в модели, но в версии 0.1 переходы в них не используются.
TRANSITIONS_FULL = {
    PRINTED: (IN_STOCK,),
    IN_STOCK: (SHIPPED, IN_TRANSIT, WRITTEN_OFF),
    IN_TRANSIT: (IN_STOCK, WRITTEN_OFF),
    SHIPPED: (),
    WRITTEN_OFF: (),
}

# Что реально разрешено в версии 0.1: Напечатано -> На складе -> Отгружено.
TRANSITIONS = {
    PRINTED: (IN_STOCK,),
    IN_STOCK: (SHIPPED,),
    IN_TRANSIT: (),
    SHIPPED: (),
    WRITTEN_OFF: (),
}


def title(status: str) -> str:
    return TITLES.get(status, status)


def can_move(from_status: str, to_status: str) -> bool:
    """Разрешён ли переход в версии 0.1."""
    return to_status in TRANSITIONS.get(from_status, ())
