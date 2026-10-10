"""Список последних сканов для экранов приёмки и отгрузки.

Храним в памяти процесса, а не в cookie: тексты отказов длинные,
и десяток сканов легко переполняет cookie-сессию (её предел 4096 байт).
Переполненную cookie браузер отбрасывает целиком — сотрудника
выбрасывало бы из системы прямо посреди смены.

Список нужен только для подсказки на экране. Пропадает при
перезапуске сервера, и это не потеря: настоящая история движений
лежит в базе, в журнале.
"""

from __future__ import annotations

import threading
from collections import OrderedDict, deque
from dataclasses import dataclass

# Сколько сканов показываем и сколько рабочих мест помним одновременно.
PER_SCREEN = 8
MAX_SESSIONS = 500


@dataclass
class ScanLine:
    ok: bool
    number: str
    text: str
    time: str


_lock = threading.Lock()
_store: OrderedDict[str, deque[ScanLine]] = OrderedDict()


def _key(session_id: str, screen: str) -> str:
    return f"{session_id}:{screen}"


def add(session_id: str, screen: str, line: ScanLine) -> None:
    key = _key(session_id, screen)
    with _lock:
        lines = _store.get(key)
        if lines is None:
            lines = deque(maxlen=PER_SCREEN)
            _store[key] = lines
        lines.appendleft(line)
        _store.move_to_end(key)
        while len(_store) > MAX_SESSIONS:
            _store.popitem(last=False)


def get(session_id: str, screen: str) -> list[ScanLine]:
    with _lock:
        return list(_store.get(_key(session_id, screen), ()))


def clear(session_id: str) -> None:
    """Очистить оба экрана при входе и выходе."""
    with _lock:
        for screen in ("receive", "ship"):
            _store.pop(_key(session_id, screen), None)
