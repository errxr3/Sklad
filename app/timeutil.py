"""Время. В базе храним UTC, на экранах и этикетках показываем местное."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from . import config


def now_utc() -> datetime:
    """Текущее время UTC без часового пояса — так его хранит база."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def to_local(moment: datetime | None) -> datetime | None:
    if moment is None:
        return None
    return moment + timedelta(hours=config.TZ_OFFSET_HOURS)


def fmt(moment: datetime | None, with_time: bool = True) -> str:
    """Дата и время для показа: 10.10.2026 14:20."""
    local = to_local(moment)
    if local is None:
        return "—"
    return local.strftime("%d.%m.%Y %H:%M" if with_time else "%d.%m.%Y")


def fmt_date(moment: datetime | None) -> str:
    return fmt(moment, with_time=False)
