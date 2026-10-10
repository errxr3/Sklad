"""Небольшие правки схемы для баз, созданных прежними версиями.

Полноценный инструмент миграций для одного файла SQLite избыточен:
достаточно дописать недостающие столбцы при запуске.
"""

from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

# Таблица -> столбец -> описание для ALTER TABLE.
ADDED_COLUMNS: dict[str, dict[str, str]] = {
    "articles": {
        "sort_order": "INTEGER NOT NULL DEFAULT 0",
        "section": "VARCHAR(128)",
    },
}


def apply(engine: Engine) -> list[str]:
    """Дописать недостающие столбцы. Возвращает список изменений."""
    done: list[str] = []
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())

    with engine.begin() as connection:
        for table, columns in ADDED_COLUMNS.items():
            if table not in existing_tables:
                continue
            present = {c["name"] for c in inspector.get_columns(table)}
            for column, definition in columns.items():
                if column in present:
                    continue
                connection.execute(
                    text(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
                )
                done.append(f"{table}.{column}")
    return done
