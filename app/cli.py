"""Команды управления. Запуск: python -m app.cli <команда> [параметры]

Доступные команды:
  setup                        подготовить всё к первому запуску
  init-db                      создать базу и завести площадки
  gen-secret                   сгенерировать ключ для .env
  add-employee                 завести сотрудника
  set-pin                      сменить PIN сотрудника
  list-employees               показать сотрудников
  import-articles ФАЙЛ         загрузить справочник из TOP.xlsx или CSV
  seed-demo-articles           завести несколько артикулов для проверки
  seed-test-workers            завести тестовых сотрудников на каждую роль
  stats                        сводка по базе
"""

from __future__ import annotations

import argparse
import secrets as pysecrets
import sys
from pathlib import Path

from sqlalchemy import func, select

from . import roles, statuses
from .articles import group_letters, match_key, normalize_code
from .config import BASE_DIR
from .db import SessionLocal, init_db
from .importer import import_articles
from .models import Article, Employee, Mirror, Movement
from .security import generate_pin, hash_pin
from .services import active_sites

SECRETS_DIR = BASE_DIR / "secrets"


def _print_pin(name: str, pin: str) -> None:
    print(f"  Сотрудник: {name}")
    print(f"  PIN-код:   {pin}")
    print("  Запишите PIN — повторно он не показывается (в базе только хеш).")


def _save_pin_note(lines: list[str]) -> Path:
    """Сохранить PIN-коды в папку secrets/, которая не попадает в репозиторий."""
    SECRETS_DIR.mkdir(exist_ok=True)
    path = SECRETS_DIR / "test_pins.txt"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# --- Команды ----------------------------------------------------------------


def cmd_init_db(_args: argparse.Namespace) -> int:
    init_db()
    with SessionLocal() as session:
        sites = active_sites(session)
    print("База готова.")
    print("Площадки: " + ", ".join(s.name for s in sites))
    return 0


def cmd_gen_secret(_args: argparse.Namespace) -> int:
    print(f"SKLAD_SECRET_KEY={pysecrets.token_hex(32)}")
    print("Скопируйте строку в файл .env (в репозиторий он не попадает).")
    return 0


def cmd_add_employee(args: argparse.Namespace) -> int:
    init_db()
    if not roles.is_valid(args.role):
        print(
            f"Неизвестная роль «{args.role}». Доступны: "
            + ", ".join(f"{r} ({roles.title(r)})" for r in roles.ALL),
            file=sys.stderr,
        )
        return 1
    pin = args.pin or generate_pin()
    with SessionLocal() as session:
        exists = session.scalar(select(Employee).where(Employee.name == args.name))
        if exists is not None:
            print(f"Сотрудник «{args.name}» уже заведён.", file=sys.stderr)
            return 1
        session.add(Employee(name=args.name, pin_hash=hash_pin(pin), role=args.role))
        session.commit()
    print(f"Сотрудник заведён, роль: {roles.title(args.role)}.")
    _print_pin(args.name, pin)
    return 0


def cmd_set_pin(args: argparse.Namespace) -> int:
    pin = args.pin or generate_pin()
    with SessionLocal() as session:
        employee = session.scalar(select(Employee).where(Employee.name == args.name))
        if employee is None:
            print(f"Сотрудник «{args.name}» не найден.", file=sys.stderr)
            return 1
        employee.pin_hash = hash_pin(pin)
        session.commit()
    print("PIN изменён.")
    _print_pin(args.name, pin)
    return 0


def cmd_list_employees(_args: argparse.Namespace) -> int:
    with SessionLocal() as session:
        rows = list(session.scalars(select(Employee).order_by(Employee.name)))
    if not rows:
        print("Сотрудников нет.")
        return 0
    for employee in rows:
        state = "активен" if employee.is_active else "отключён"
        print(
            f"  {employee.id:>3}  {employee.name:<30} "
            f"{roles.title(employee.role):<15} {state}"
        )
    return 0


def cmd_import_articles(args: argparse.Namespace) -> int:
    path = Path(args.path)
    if not path.exists():
        print(f"Файл не найден: {path}", file=sys.stderr)
        return 1
    init_db()
    with SessionLocal() as session:
        report = import_articles(session, path)
    print(f"Справочник загружен из {path.name}")
    print(report.describe())
    if report.examples:
        print("Примеры добавленных артикулов:")
        for code in report.examples:
            print(f"  {code}  →  буквы номера {group_letters(code)}")
    return 0


# Небольшой набор артикулов, чтобы можно было проверить работу
# до загрузки настоящего TOP.xlsx. Это НЕ рабочий справочник.
DEMO_ARTICLES = (
    "Zerkalo 400x600",
    "Zerkalo 600x800",
    "Zerkalo 800x1000",
    "bp_Zerkalo 500x700",
    "Alum 400x1200",
    "Alum 600x900",
    "Ambient 700x500",
    "Ambient 900x600 LED",
    "Polka 300",
)


def cmd_seed_demo_articles(_args: argparse.Namespace) -> int:
    init_db()
    added = 0
    with SessionLocal() as session:
        for raw in DEMO_ARTICLES:
            code = normalize_code(raw)
            key = match_key(code)
            if session.scalar(select(Article).where(Article.code_key == key)):
                continue
            session.add(
                Article(
                    code=code,
                    code_key=key,
                    group_letters=group_letters(code),
                    source_sheet="demo",
                )
            )
            added += 1
        session.commit()
    print(f"Добавлено демонстрационных артикулов: {added}")
    print("Это временный набор для проверки. Настоящий справочник загружается так:")
    print("  python -m app.cli import-articles TOP.xlsx")
    return 0


# По одному сотруднику на каждую роль — чтобы было чем проверить разделение.
TEST_WORKERS = (
    ("Тестов Иван", roles.ASSEMBLER),
    ("Пробин Пётр", roles.ASSEMBLER),
    ("Грузов Глеб", roles.LOADER),
    ("Бригадов Борис", roles.FOREMAN),
    ("Админов Антон", roles.ADMIN),
)


def cmd_seed_test_workers(args: argparse.Namespace) -> int:
    init_db()
    lines = ["PIN-коды тестовых сборщиков (файл в репозиторий не попадает)", ""]
    created: list[tuple[str, str]] = []
    with SessionLocal() as session:
        for name, role in TEST_WORKERS:
            if session.scalar(select(Employee).where(Employee.name == name)):
                print(f"Сотрудник «{name}» уже есть, пропускаю.")
                continue
            pin = args.pin or generate_pin()
            session.add(Employee(name=name, pin_hash=hash_pin(pin), role=role))
            created.append((f"{name} — {roles.title(role)}", pin))
        session.commit()

    if not created:
        return 0

    print("Заведены тестовые сборщики:")
    for name, pin in created:
        _print_pin(name, pin)
        lines.append(f"{name}: {pin}")
        print()
    path = _save_pin_note(lines)
    print(f"PIN-коды также записаны в {path.relative_to(BASE_DIR)} (папка в .gitignore).")
    return 0


# Справочник, который лежит в репозитории рядом с кодом.
BUNDLED_CATALOG = BASE_DIR / "docs" / "spravochnik-artikulov.xlsx"


def cmd_setup(args: argparse.Namespace) -> int:
    """Подготовить всё к первому запуску. Повторный вызов безопасен.

    Создаёт базу, при необходимости загружает справочник артикулов
    из репозитория и заводит тестовых сотрудников, если их ещё нет.
    Уже сделанное не трогает.
    """
    init_db()
    print("База готова.")

    with SessionLocal() as session:
        articles = session.scalar(select(func.count()).select_from(Article)) or 0
        employees = session.scalar(select(func.count()).select_from(Employee)) or 0

    if articles:
        print(f"Справочник артикулов уже загружен: {articles} шт.")
    elif BUNDLED_CATALOG.exists():
        with SessionLocal() as session:
            report = import_articles(session, BUNDLED_CATALOG)
        print(f"Загружен справочник артикулов: {report.added} шт.")
    else:
        print("Справочник не найден, пропускаю. Загрузите его в админ-панели.")

    if employees:
        print(f"Сотрудники уже заведены: {employees} чел.")
    else:
        args.pin = args.pin or "1234"
        cmd_seed_test_workers(args)

    print()
    print("Всё готово. Запуск сайта:")
    print("  python -m uvicorn app.main:app --host 0.0.0.0 --port 8000")
    return 0


def cmd_stats(_args: argparse.Namespace) -> int:
    with SessionLocal() as session:
        articles = session.scalar(select(func.count()).select_from(Article)) or 0
        employees = session.scalar(select(func.count()).select_from(Employee)) or 0
        movements = session.scalar(select(func.count()).select_from(Movement)) or 0
        print(f"Артикулов:   {articles}")
        print(f"Сотрудников: {employees}")
        print(f"Движений:    {movements}")
        print("Зеркала по статусам:")
        rows = session.execute(
            select(Mirror.status, func.count()).group_by(Mirror.status)
        ).all()
        if not rows:
            print("  пока нет")
        for status, count in rows:
            print(f"  {statuses.title(status):<12} {count}")
    return 0


# --- Разбор параметров ------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli",
        description="Управление складом зеркал",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init-db", help="создать базу и завести площадки").set_defaults(
        func=cmd_init_db
    )
    subparsers.add_parser("gen-secret", help="сгенерировать ключ для .env").set_defaults(
        func=cmd_gen_secret
    )

    add = subparsers.add_parser("add-employee", help="завести сотрудника")
    add.add_argument("--name", required=True, help="фамилия и имя")
    add.add_argument("--pin", help="PIN-код; если не указан — будет сгенерирован")
    add.add_argument(
        "--role",
        default=roles.ASSEMBLER,
        choices=roles.ALL,
        help="роль: assembler — сборщик, loader — грузчик, "
        "foreman — бригадир, admin — администратор",
    )
    add.set_defaults(func=cmd_add_employee)

    setpin = subparsers.add_parser("set-pin", help="сменить PIN сотрудника")
    setpin.add_argument("--name", required=True)
    setpin.add_argument("--pin", help="новый PIN; если не указан — будет сгенерирован")
    setpin.set_defaults(func=cmd_set_pin)

    subparsers.add_parser("list-employees", help="показать сотрудников").set_defaults(
        func=cmd_list_employees
    )

    imp = subparsers.add_parser("import-articles", help="загрузить справочник артикулов")
    imp.add_argument("path", help="путь к TOP.xlsx или CSV")
    imp.set_defaults(func=cmd_import_articles)

    subparsers.add_parser(
        "seed-demo-articles", help="завести артикулы для проверки"
    ).set_defaults(func=cmd_seed_demo_articles)

    workers = subparsers.add_parser(
        "seed-test-workers", help="завести тестовых сотрудников на каждую роль"
    )
    workers.add_argument("--pin", help="общий PIN для обоих; иначе у каждого свой случайный")
    workers.set_defaults(func=cmd_seed_test_workers)

    setup = subparsers.add_parser(
        "setup", help="подготовить всё к первому запуску (можно повторять)"
    )
    setup.add_argument("--pin", help="PIN тестовых сотрудников, по умолчанию 1234")
    setup.set_defaults(func=cmd_setup)

    subparsers.add_parser("stats", help="сводка по базе").set_defaults(func=cmd_stats)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
