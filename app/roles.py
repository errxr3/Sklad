"""Роли сотрудников и права на операции.

Сборщик делает зеркала, грузчик отгружает, бригадир проверяет сборку
и может подменить обоих, администратор видит и может всё.
"""

from __future__ import annotations

ASSEMBLER = "assembler"
LOADER = "loader"
FOREMAN = "foreman"
ADMIN = "admin"

TITLES = {
    ASSEMBLER: "Сборщик",
    LOADER: "Грузчик",
    FOREMAN: "Бригадир",
    ADMIN: "Администратор",
}

# Порядок для списков в админке: от простой роли к полной.
ALL = (ASSEMBLER, LOADER, FOREMAN, ADMIN)

# Права на операции.
MAKE = "make"        # печать этикеток, «Сделал зеркало»
RECEIVE = "receive"  # приёмка на склад сканом
SHIP = "ship"        # отгрузка сканом
ADMIN_PANEL = "admin_panel"

# Бригадир принимает зеркала за сборщиком — это и есть проверка сборки,
# и отгружает за грузчика. Сборщик к отгрузке не допускается,
# грузчик не печатает этикетки.
PERMISSIONS = {
    ASSEMBLER: frozenset({MAKE, RECEIVE}),
    LOADER: frozenset({SHIP}),
    FOREMAN: frozenset({MAKE, RECEIVE, SHIP}),
    ADMIN: frozenset({MAKE, RECEIVE, SHIP, ADMIN_PANEL}),
}


def title(role: str) -> str:
    return TITLES.get(role, role)


def is_valid(role: str) -> bool:
    return role in PERMISSIONS


def can(role: str, permission: str) -> bool:
    return permission in PERMISSIONS.get(role, frozenset())
