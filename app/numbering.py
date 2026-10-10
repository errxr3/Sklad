"""Сквозная нумерация зеркал.

Счётчик один на все группы: буквы в номере зависят от артикула,
а цифры идут подряд независимо от группы.
"""

from __future__ import annotations

from sqlalchemy import insert, select, update
from sqlalchemy.orm import Session

from .models import Counter

COUNTER_NAME = "mirror_number"


def allocate(session: Session, count: int) -> list[int]:
    """Выделить count подряд идущих значений счётчика.

    Выполняется одним UPDATE, поэтому две одновременные печати получают
    разные диапазоны и номера не повторяются.
    """
    if count < 1:
        raise ValueError("Количество должно быть не меньше 1")

    result = session.execute(
        update(Counter)
        .where(Counter.name == COUNTER_NAME)
        .values(value=Counter.value + count)
    )
    if result.rowcount == 0:
        # Первый запуск: строки счётчика ещё нет.
        session.execute(insert(Counter).values(name=COUNTER_NAME, value=count))
        last = count
    else:
        last = session.scalar(
            select(Counter.value).where(Counter.name == COUNTER_NAME)
        )
    first = last - count + 1
    return list(range(first, last + 1))


def current_value(session: Session) -> int:
    """Текущее значение счётчика (0, если ещё ничего не печатали)."""
    return session.scalar(select(Counter.value).where(Counter.name == COUNTER_NAME)) or 0
