"""Модель данных складского учёта зеркал."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from . import statuses


class Base(DeclarativeBase):
    pass


class Site(Base):
    """Площадка: Коминтерна, Учительская."""

    __tablename__ = "sites"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(128))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    def __repr__(self) -> str:  # pragma: no cover - для отладки
        return f"<Site {self.code}>"


class Employee(Base):
    """Сотрудник. В версии 0.1 все сотрудники — сборщики."""

    __tablename__ = "employees"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    pin_hash: Mapped[str] = mapped_column(String(256))
    role: Mapped[str] = mapped_column(String(32), default="assembler")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Employee {self.name}>"


class Article(Base):
    """Артикул из справочника TOP.xlsx."""

    __tablename__ = "articles"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Как показываем и печатаем (нормализованное написание).
    code: Mapped[str] = mapped_column(String(256))
    # Ключ сравнения: без регистра, русская «х» заменена на латинскую.
    code_key: Mapped[str] = mapped_column(String(256), unique=True, index=True)
    group_letters: Mapped[str] = mapped_column(String(4))
    # С какого листа файла загружен — для сверки со справочником.
    source_sheet: Mapped[str | None] = mapped_column(String(128), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Article {self.code}>"


class Counter(Base):
    """Счётчики. Номер зеркала — один сквозной счётчик на все группы."""

    __tablename__ = "counters"

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[int] = mapped_column(Integer, default=0)


class PrintBatch(Base):
    """Партия печати: один нажатый «Напечатать» — одна партия.

    Нужна, чтобы повторно открыть PDF с этикетками, если печать сорвалась.
    """

    __tablename__ = "print_batches"

    id: Mapped[int] = mapped_column(primary_key=True)
    article_id: Mapped[int] = mapped_column(ForeignKey("articles.id"))
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"))
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id"))
    qty: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    article: Mapped[Article] = relationship(lazy="joined")
    employee: Mapped[Employee] = relationship(lazy="joined")
    site: Mapped[Site] = relationship(lazy="joined")
    mirrors: Mapped[list["Mirror"]] = relationship(
        back_populates="batch", order_by="Mirror.id"
    )


class Mirror(Base):
    """Зеркало — единица учёта. Номер присваивается при печати этикетки."""

    __tablename__ = "mirrors"

    id: Mapped[int] = mapped_column(primary_key=True)
    number: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    # Значение сквозного счётчика и буквы группы, из которых собран номер.
    seq: Mapped[int] = mapped_column(Integer, unique=True)
    group_letters: Mapped[str] = mapped_column(String(4))

    article_id: Mapped[int] = mapped_column(ForeignKey("articles.id"), index=True)
    # Сборщик присваивается навсегда и больше не меняется.
    assembler_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    # Площадка, на которой зеркало находится сейчас.
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id"), index=True)
    batch_id: Mapped[int | None] = mapped_column(
        ForeignKey("print_batches.id"), nullable=True
    )

    status: Mapped[str] = mapped_column(String(32), default=statuses.PRINTED, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    # Когда зеркало последний раз меняло статус.
    status_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    article: Mapped[Article] = relationship(lazy="joined")
    assembler: Mapped[Employee] = relationship(lazy="joined")
    site: Mapped[Site] = relationship(lazy="joined")
    batch: Mapped[PrintBatch | None] = relationship(back_populates="mirrors")

    @property
    def status_title(self) -> str:
        return statuses.title(self.status)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Mirror {self.number} {self.status}>"


class Movement(Base):
    """Журнал движений: каждая смена статуса — отдельная запись.

    Таблица только на добавление. Записи не изменяются и не удаляются —
    в приложении нет ни одного места, которое их правит или чистит.
    """

    __tablename__ = "movements"

    id: Mapped[int] = mapped_column(primary_key=True)
    mirror_id: Mapped[int] = mapped_column(ForeignKey("mirrors.id"), index=True)
    # У первой записи (печать этикетки) прежнего статуса нет.
    from_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    to_status: Mapped[str] = mapped_column(String(32))
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id"), index=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), index=True
    )

    mirror: Mapped[Mirror] = relationship(lazy="joined")
    employee: Mapped[Employee] = relationship(lazy="joined")
    site: Mapped[Site] = relationship(lazy="joined")

    __table_args__ = (UniqueConstraint("id", name="uq_movements_id"),)

    @property
    def from_title(self) -> str:
        return statuses.title(self.from_status) if self.from_status else "—"

    @property
    def to_title(self) -> str:
        return statuses.title(self.to_status)
