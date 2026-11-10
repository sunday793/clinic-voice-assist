
"""Pydantic-схемы для REST API тестового стенда «Регистратура».

Заявленный формат контракта:
- Дата: ДД.ММ.ГГГГ.
- Дата и время: ДД.ММ.ГГГГ ЧЧ:ММ.
- Временные значения относятся к GMT+4, смещение не передаётся.
- middle_name и comment могут быть null.
- При частичном обновлении отсутствующие поля не должны считаться
  явно переданными.

Перед изменением схем необходимо сверять их с актуальным OpenAPI:
https://crm-med-mock.dbi-testing.ru/docs/openapi.json
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ---------------------------------------------------------------------
# Форматы дат
# ---------------------------------------------------------------------

_DATE_FORMAT = "%d.%m.%Y"
_DATETIME_FORMAT = "%d.%m.%Y %H:%M"

_DATE_PATTERN = re.compile(r"^\d{2}\.\d{2}\.\d{4}$")
_DATETIME_PATTERN = re.compile(r"^\d{2}\.\d{2}\.\d{4} \d{2}:\d{2}$")


def _validate_date(value: str) -> str:
    """Проверить дату формата ДД.ММ.ГГГГ, сохранив её строкой."""
    if not isinstance(value, str) or not _DATE_PATTERN.fullmatch(value):
        raise ValueError("Дата должна иметь формат ДД.ММ.ГГГГ")

    try:
        datetime.strptime(value, _DATE_FORMAT)
    except ValueError as exc:
        raise ValueError(
            "Указана некорректная календарная дата"
        ) from exc

    return value


def _validate_datetime(value: str) -> str:
    """Проверить дату и время формата ДД.ММ.ГГГГ ЧЧ:ММ."""
    if not isinstance(value, str) or not _DATETIME_PATTERN.fullmatch(value):
        raise ValueError(
            "Дата и время должны иметь формат ДД.ММ.ГГГГ ЧЧ:ММ"
        )

    try:
        datetime.strptime(value, _DATETIME_FORMAT)
    except ValueError as exc:
        raise ValueError(
            "Указана некорректная дата или время"
        ) from exc

    return value


# ---------------------------------------------------------------------
# Базовая модель
# ---------------------------------------------------------------------


class MISBaseModel(BaseModel):
    """Общие настройки схем API."""

    model_config = ConfigDict(extra="ignore")


# ---------------------------------------------------------------------
# Ошибки
# ---------------------------------------------------------------------


class MISErrorCode(str, Enum):
    """Машиночитаемые коды ошибок стенда."""

    UNAUTHORIZED = "НЕ_АВТОРИЗОВАН"
    INVALID_PARAM = "НЕВЕРНЫЙ_ПАРАМЕТР"
    INVALID_REQUEST = "НЕВЕРНЫЙ_ЗАПРОС"
    NOT_FOUND = "НЕ_НАЙДЕНО"
    STATUS_CONFLICT = "КОНФЛИКТ_СТАТУСА"
    RATE_LIMITED = "ПРЕВЫШЕН_ЛИМИТ"
    INTERNAL_ERROR = "ВНУТРЕННЯЯ_ОШИБКА"


class MISErrorBody(MISBaseModel):
    """Тело ответа об ошибке от стенда."""

    code: MISErrorCode
    message: str


# ---------------------------------------------------------------------
# Врачи
# ---------------------------------------------------------------------


class Doctor(MISBaseModel):
    """Врач справочника стенда."""

    id: int = Field(ge=1)
    full_name: str
    specialty: str
    is_active: bool


# ---------------------------------------------------------------------
# Клиенты
# ---------------------------------------------------------------------


class Client(MISBaseModel):
    """Карточка клиента."""

    id: int = Field(ge=1)
    last_name: str
    first_name: str
    middle_name: str | None = None
    birth_date: str
    comment: str | None = None
    created_at: str

    @field_validator("birth_date")
    @classmethod
    def validate_birth_date(cls, value: str) -> str:
        return _validate_date(value)

    @field_validator("created_at")
    @classmethod
    def validate_created_at(cls, value: str) -> str:
        return _validate_datetime(value)


class ClientCreate(MISBaseModel):
    """Тело регистрации клиента."""

    last_name: str = Field(min_length=1, max_length=100)
    first_name: str = Field(min_length=1, max_length=100)
    birth_date: str
    middle_name: str | None = None
    comment: str | None = None

    @field_validator("birth_date")
    @classmethod
    def validate_birth_date(cls, value: str) -> str:
        return _validate_date(value)


class ClientUpdate(MISBaseModel):
    """Тело изменения клиента.

    Все поля необязательны. Если поле не передано, оно отсутствует
    в model_fields_set. При формировании PATCH/PUT-запроса используй
    model_dump(exclude_unset=True), чтобы не подменять отсутствующие
    поля значениями по умолчанию.
    """

    middle_name: str | None = None
    comment: str | None = None


# ---------------------------------------------------------------------
# Записи на приём
# ---------------------------------------------------------------------


class VisitStatus(str, Enum):
    """Статусы записи на приём."""

    PLANNED = "Запланирована"
    CANCELLED = "Отменена"
    COMPLETED = "Завершена"


class Visit(MISBaseModel):
    """Карточка записи на приём."""

    id: int = Field(ge=1)
    client_id: int = Field(ge=1)
    doctor: Doctor
    scheduled_at: str
    status: VisitStatus
    created_at: str
    updated_at: str

    @field_validator("scheduled_at", "created_at", "updated_at")
    @classmethod
    def validate_datetime_fields(cls, value: str) -> str:
        return _validate_datetime(value)


class VisitCreate(MISBaseModel):
    """Тело создания записи на приём."""

    client_id: int = Field(ge=1)
    doctor_id: int = Field(ge=1)
    scheduled_at: str

    @field_validator("scheduled_at")
    @classmethod
    def validate_scheduled_at(cls, value: str) -> str:
        return _validate_datetime(value)


class VisitUpdate(MISBaseModel):
    """Тело изменения записи на приём.

    Все поля необязательны. Отсутствующее поле не меняется.
    Явный null для doctor_id или scheduled_at не принимается,
    поскольку эти поля объявлены как необязательные значения,
    но не как nullable-поля.
    """

    doctor_id: int | None = Field(default=None, ge=1)
    scheduled_at: str | None = None
    status: VisitStatus | None = None

    @field_validator("scheduled_at")
    @classmethod
    def validate_scheduled_at(
        cls,
        value: str | None,
    ) -> str | None:
        if value is None:
            return None
        return _validate_datetime(value)


# ---------------------------------------------------------------------
# Заявки на соединение со специалистом
# ---------------------------------------------------------------------


class Escalation(MISBaseModel):
    """Карточка заявки на соединение со специалистом."""

    id: int = Field(ge=1)
    client_id: int = Field(ge=1)
    description: str
    created_at: str

    @field_validator("created_at")
    @classmethod
    def validate_created_at(cls, value: str) -> str:
        return _validate_datetime(value)


class EscalationCreate(MISBaseModel):
    """Тело создания заявки на соединение со специалистом."""

    client_id: int = Field(ge=1)
    description: str = Field(min_length=1, max_length=1000)
