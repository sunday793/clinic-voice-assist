
"""Карточка перевода на оператора (Приложение В регламента РГ-ТЕЛ-01).

Формируется голосовым ассистентом при эскалации и передаётся
регистратору вместе с контекстом звонка.
Также может использоваться для задач обратного звонка.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping


MAX_DESCRIPTION_LENGTH = 1000
MAX_SUMMARY_LENGTH = 200


def _current_timestamp() -> str:
    """Текущее локальное время сервера с учётом его часового пояса."""
    return datetime.now().astimezone().strftime("%d.%m.%Y %H:%M:%S")


def _clean_text(value: Any, max_length: int | None = None) -> str:
    """Безопасно привести значение к однострочному тексту."""
    if value is None:
        return ""

    text = " ".join(str(value).split())

    if max_length is not None:
        text = text[:max_length]

    return text


@dataclass
class TransferCard:
    """Карточка перевода оператору."""

    call_id: str
    started_at: str
    transferred_at: str

    patient_id: int | None = None
    patient_name: str | None = None

    intent: str = ""
    collected_params: dict[str, Any] = field(default_factory=dict)

    reason_code: str = ""
    reason_text: str = ""
    priority: str = "P3"

    summary: str = ""
    is_dissatisfied: bool = False

    # None означает, что согласие неизвестно.
    recording_consent: bool | None = None

    def __post_init__(self) -> None:
        self.call_id = _clean_text(self.call_id)
        self.started_at = _clean_text(self.started_at)
        self.transferred_at = _clean_text(self.transferred_at)
        self.patient_name = (
            _clean_text(self.patient_name) or None
        )
        self.intent = _clean_text(self.intent)
        self.reason_code = _clean_text(self.reason_code)
        self.reason_text = _clean_text(self.reason_text)
        self.priority = _clean_text(self.priority).upper() or "P3"
        self.summary = _clean_text(
            self.summary,
            MAX_SUMMARY_LENGTH,
        )

        # Создаём собственный словарь, чтобы не сохранять внешнюю ссылку.
        self.collected_params = dict(self.collected_params or {})

    def to_escalation_description(self) -> str:
        """Сформировать описание для МИС длиной не более 1000 символов.

        Приоритет содержимого:
        1. Код причины и приоритет.
        2. Причина эскалации.
        3. Данные пациента.
        4. Собранные параметры.
        5. Краткое резюме.
        """
        parts: list[str] = []

        if self.reason_code:
            parts.append(f"[{self.reason_code}]")

        if self.priority:
            parts.append(f"Приоритет: {self.priority}.")

        if self.reason_text:
            parts.append(self.reason_text)

        if self.patient_name:
            parts.append(f"Пациент: {self.patient_name}.")

        if self.patient_id is not None:
            parts.append(f"ID пациента: {self.patient_id}.")

        if self.collected_params:
            params = ", ".join(
                f"{_clean_text(key)}={_clean_text(value)}"
                for key, value in self.collected_params.items()
                if _clean_text(key)
            )
            if params:
                parts.append(f"Параметры: {params}.")

        if self.summary:
            parts.append(f"Резюме: {self.summary}")

        description = " ".join(parts).strip()

        if len(description) <= MAX_DESCRIPTION_LENGTH:
            return description

        # Не обрезаем весь текст без разбора: сначала сохраняем
        # идентификатор причины, приоритет и основной текст причины.
        essential_parts: list[str] = []

        if self.reason_code:
            essential_parts.append(f"[{self.reason_code}]")

        if self.priority:
            essential_parts.append(f"Приоритет: {self.priority}.")

        if self.reason_text:
            essential_parts.append(self.reason_text)

        essential = " ".join(essential_parts).strip()

        if len(essential) >= MAX_DESCRIPTION_LENGTH:
            return essential[:MAX_DESCRIPTION_LENGTH]

        remaining = MAX_DESCRIPTION_LENGTH - len(essential)

        optional_parts: list[str] = []

        if self.patient_name:
            optional_parts.append(f"Пациент: {self.patient_name}.")

        if self.patient_id is not None:
            optional_parts.append(f"ID пациента: {self.patient_id}.")

        if self.collected_params:
            params = ", ".join(
                f"{_clean_text(key)}={_clean_text(value)}"
                for key, value in self.collected_params.items()
                if _clean_text(key)
            )
            if params:
                optional_parts.append(f"Параметры: {params}.")

        if self.summary:
            optional_parts.append(f"Резюме: {self.summary}")

        result = essential

        for part in optional_parts:
            addition = f" {part}" if result else part

            if len(addition) <= remaining:
                result += addition
                remaining -= len(addition)
            elif remaining > 0:
                # Не добавляем многоточие, если оно не помещается.
                result += addition[:remaining]
                remaining = 0
                break

            if remaining == 0:
                break

        return result[:MAX_DESCRIPTION_LENGTH]


def _build_red_flag_card(
    *,
    call_id: str,
    started_at: str,
    red_flag_group: str,
    matched: str,
    reason_code: str,
    intent: str,
    category_text: str,
    summary_prefix: str,
    patient_name: str | None = None,
    patient_id: int | None = None,
) -> TransferCard:
    """Общая реализация карточки для эскалации по красному флагу."""
    group = _clean_text(red_flag_group) or "не указана"
    matched_text = _clean_text(matched) or "фраза не указана"

    reason_text = (
        f"{category_text}: {group}. "
        f"Сработало: «{matched_text}»."
    )

    if reason_code == "R-01":
        reason_text += " Рекомендовано немедленно вызвать 103/112."

    return TransferCard(
        call_id=call_id,
        started_at=started_at,
        transferred_at=_current_timestamp(),
        patient_id=patient_id,
        patient_name=patient_name,
        intent=intent,
        reason_code=reason_code,
        reason_text=reason_text,
        priority="P1",
        summary=f"{summary_prefix}: {group}",
    )


def build_emergency_card(
    call_id: str,
    started_at: str,
    red_flag_group: str,
    matched: str,
    patient_name: str | None = None,
    patient_id: int | None = None,
) -> TransferCard:
    """Построить карточку экстренной ситуации (R-01)."""
    return _build_red_flag_card(
        call_id=call_id,
        started_at=started_at,
        red_flag_group=red_flag_group,
        matched=matched,
        reason_code="R-01",
        intent="I-11 Экстренная ситуация",
        category_text="Красный флаг категории А",
        summary_prefix="Экстренная ситуация",
        patient_name=patient_name,
        patient_id=patient_id,
    )


def build_urgent_card(
    call_id: str,
    started_at: str,
    red_flag_group: str,
    matched: str,
    patient_name: str | None = None,
    patient_id: int | None = None,
) -> TransferCard:
    """Построить карточку острого состояния (R-11)."""
    return _build_red_flag_card(
        call_id=call_id,
        started_at=started_at,
        red_flag_group=red_flag_group,
        matched=matched,
        reason_code="R-11",
        intent="I-11 Острое состояние",
        category_text="Красный флаг категории Б",
        summary_prefix="Острое состояние",
        patient_name=patient_name,
        patient_id=patient_id,
    )
