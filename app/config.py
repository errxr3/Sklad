"""Настройки приложения. Читаются из переменных окружения и файла .env."""

from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Простейший разбор .env: КЛЮЧ=значение, строки с # игнорируются.

    Отдельная библиотека не нужна, формат у нас примитивный.
    Уже заданные переменные окружения приоритетнее файла.
    """
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


_load_dotenv(BASE_DIR / ".env")


def _db_path() -> Path:
    raw = os.environ.get("SKLAD_DB_PATH", "data/sklad.db")
    path = Path(raw)
    if not path.is_absolute():
        path = BASE_DIR / path
    return path


DB_PATH = _db_path()
DATABASE_URL = os.environ.get("SKLAD_DATABASE_URL") or f"sqlite+pysqlite:///{DB_PATH}"

# Ключ подписи сессионных cookie. В бою задаётся через .env, иначе работа
# продолжается со временным ключом (при перезапуске все выйдут из системы).
SECRET_KEY = os.environ.get("SKLAD_SECRET_KEY") or ""

SESSION_MINUTES = int(os.environ.get("SKLAD_SESSION_MINUTES", "720"))

# Часовой пояс для отображения и печати (смещение от UTC в часах).
TZ_OFFSET_HOURS = int(os.environ.get("SKLAD_TZ_OFFSET_HOURS", "3"))

# Сколько этикеток разрешено печатать за один раз.
MAX_BATCH_QTY = int(os.environ.get("SKLAD_MAX_BATCH_QTY", "200"))
