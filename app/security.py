"""Проверка PIN-кодов сотрудников.

PIN хранится только в виде хеша PBKDF2-SHA256 со случайной солью.
Сам PIN в базу и в репозиторий не попадает.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

ALGORITHM = "pbkdf2_sha256"
ITERATIONS = 240_000
MIN_PIN_LENGTH = 4


class WeakPinError(ValueError):
    pass


def hash_pin(pin: str) -> str:
    """Собрать строку вида pbkdf2_sha256$итерации$соль$хеш."""
    pin = (pin or "").strip()
    if len(pin) < MIN_PIN_LENGTH or not pin.isdigit():
        raise WeakPinError(
            f"PIN должен состоять только из цифр, минимум {MIN_PIN_LENGTH} цифры"
        )
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", pin.encode(), bytes.fromhex(salt), ITERATIONS)
    return f"{ALGORITHM}${ITERATIONS}${salt}${digest.hex()}"


def verify_pin(pin: str, stored: str) -> bool:
    """Сверить введённый PIN с хешем. Сравнение без утечки времени."""
    try:
        algorithm, iterations, salt, expected = (stored or "").split("$")
        if algorithm != ALGORITHM:
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", (pin or "").strip().encode(), bytes.fromhex(salt), int(iterations)
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest.hex(), expected)


def generate_pin(length: int = 4) -> str:
    """Случайный PIN — для заведения тестовых сборщиков."""
    return "".join(secrets.choice("0123456789") for _ in range(length))
