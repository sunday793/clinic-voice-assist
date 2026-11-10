"""Состояние диалога и ответы ассистента."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.services.red_flags import RedFlag
from app.services.schedule import Slot


# ---------------------------------------------------------
# Основные шаги диалога
# ---------------------------------------------------------

STEP_GREETING = "greeting"
STEP_ASK_NAME = "ask_name"
STEP_ASK_SPECIALTY = "ask_specialty"
STEP_OFFER_SLOTS = "offer_slots"
STEP_CONFIRM = "confirm"
STEP_BOOKED = "booked"
STEP_FALLBACK = "fallback"
STEP_ESCALATED = "escalated"


# ---------------------------------------------------------
# Дополнительные шаги переноса записи
# ---------------------------------------------------------

STEP_RESCHEDULE_CONFIRM = "reschedule_confirm"
STEP_TRANSFER_CONFIRM_REPRESENTATIVE = "transfer_confirm_representative"
STEP_SELECT_OLD_VISIT = "transfer_select_old_visit"


@dataclass
class DialogState:
    """Состояние диалога одного телефонного звонка."""

    call_id: str

    # Текущий сценарий и шаг
    scenario: str | None = None
    step: str = STEP_GREETING

    # -----------------------------------------------------
    # Данные пациента
    # -----------------------------------------------------

    last_name: str | None = None
    first_name: str | None = None
    middle_name: str | None = None
    birth_date: str | None = None
    full_name: str | None = None
    client_id: int | None = None

    # -----------------------------------------------------
    # Данные записи
    # -----------------------------------------------------

    specialty: str | None = None
    offered_slots: list[Slot] = field(default_factory=list)
    selected_slot: Slot | None = None
    visit_id: int | None = None

    # -----------------------------------------------------
    # Перенос существующей записи
    # -----------------------------------------------------

    is_reschedule: bool = False

    # ID исходной записи, которую переносим.
    # Не затирать до завершения процесса переноса.
    old_visit_id: int | None = None

    # Значение может быть строкой или datetime,
    # в зависимости от модели Visit в МИС.
    old_scheduled_at: datetime | str | None = None

    # -----------------------------------------------------
    # Служебные данные звонка
    # -----------------------------------------------------

    started_at: datetime = field(default_factory=datetime.now)
    red_flag: RedFlag | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    # Количество неудачных попыток распознавания/идентификации.
    failed_attempts: int = 0

    # Приветствие по времени суток произнесено (ровно один раз за звонок).
    greeting_said: bool = False


@dataclass
class DialogResponse:
    """Ответ ассистента на текущую реплику пользователя."""

    reply: str
    step: str

    # ID записи, если сценарий его возвращает.
    visit_id: int | None = None

    # Признак успешного завершения записи.
    booked: bool = False

    # Требуется перевод на оператора/ответственного сотрудника.
    escalated: bool = False

    # Категория обнаруженного красного флага.
    red_flag_category: str | None = None