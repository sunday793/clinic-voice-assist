
"""Роутер диалогового движка. Делегирует обработку сценариям."""
from __future__ import annotations

import logging
import re
from typing import Any

from app.services.dialog.scenarios.booking import BookingScenario
from app.services.dialog.scenarios.cancel import CancelScenario
from app.services.dialog.scenarios.check import CheckScenario
from app.services.dialog.scenarios.emergency import EmergencyScenario
from app.services.dialog.scenarios.complaint import ComplaintScenario
from app.services.dialog.scenarios.transfer_entry import TransferScenario
from app.services.dialog.scenarios.information_desk import InformationScenario
from app.services.dialog.scenarios.danger import DangerScenario
from app.core.greetings import get_greeting_reply

from app.services.dialog.state import (
    DialogResponse,
    DialogState,
    STEP_BOOKED,
    STEP_ESCALATED,
    STEP_GREETING,
    STEP_ASK_SPECIALTY,
)
from app.services.mis_client import MISClientProtocol
from app.services.nlu import parse
from app.services.red_flags import detect as detect_red_flag
from app.services.schedule import ScheduleService

logger = logging.getLogger(__name__)

# Интенты NLU -> имена зарегистрированных сценариев.
INTENT_TO_SCENARIO: dict[str, str] = {
    "book": "booking",
    "cancel": "cancel",
    "check": "check",
    "reschedule": "transfer",
    "complaint": "complaint",
    "information": "information",
    "emergency": "emergency",
    "danger": "danger",
}

# Это эвристика первичного обнаружения угроз, а не полноценная
# классификация угроз. Окончательная логика зависит от контекста реплики.
DANGER_PATTERNS = (
    r"\bбомб\w*",
    r"\bвзрыв\w*",
    r"\bтеракт\w*",
    r"\bтерракт\w*",
    r"\bзаложник\w*",
    r"\bзахват\w*",
    r"\bстреля\w*",
    r"\bоружи\w*",
    r"\bубью\b",
    r"\bубьют\b",
    r"\bубить\b",
    r"\bмассов\w+\s+убийств\w*",
)


class DialogEngine:
    """Управляет состояниями звонков и направляет реплики сценариям."""

    def __init__(
        self,
        mis: MISClientProtocol,
        schedule: ScheduleService,
        today: str,
    ) -> None:
        self._mis = mis
        self._schedule = schedule
        self._today = today
        self._states: dict[str, DialogState] = {}

        self._scenarios: dict[str, Any] = {
            "booking": BookingScenario(mis=mis, schedule=schedule),
            "emergency": EmergencyScenario(mis=mis),
            "cancel": CancelScenario(mis=mis),
            "check": CheckScenario(mis=mis),
            "complaint": ComplaintScenario(mis=mis),
            "transfer": TransferScenario(mis=mis),
            "information": InformationScenario(mis=mis),
            "danger": DangerScenario(),
        }

        self._validate_scenarios()

    def _validate_scenarios(self) -> None:
        """Проверяет соответствие ключей словаря именам сценариев."""
        for key, scenario in self._scenarios.items():
            scenario_name = getattr(scenario, "name", None)
            if scenario_name != key:
                raise ValueError(
                    f"Сценарий зарегистрирован под ключом {key!r}, "
                    f"но его name={scenario_name!r}."
                )

    def _get_state(self, call_id: str) -> DialogState:
        """Возвращает состояние звонка, создавая его при необходимости."""
        if call_id not in self._states:
            self._states[call_id] = DialogState(call_id=call_id)
        return self._states[call_id]

    def end_call(self, call_id: str) -> None:
        """Удаляет состояние звонка после его завершения."""
        self._states.pop(call_id, None)
        logger.info("Состояние диалога очищено: call_id=%s", call_id)

    async def handle(self, call_id: str, text: str) -> DialogResponse:
        """Обрабатывает реплику пациента и возвращает ответ сценария."""
        state = self._get_state(call_id)
        raw_text = text if isinstance(text, str) else ""
        normalized_text = self._normalize_text(raw_text)

        # 0. Приветствие по времени суток GMT+4 — один раз за звонок.
        #    Произносится до NLU-роутинга, чтобы не зависеть от intent="greeting".
        if not state.greeting_said and state.scenario is None:
            state.greeting_said = True
            return DialogResponse(
                reply=get_greeting_reply(),
                step=STEP_GREETING,
            )

        # 1. Проверка на угрозу. Срабатывание этого блока не означает,
        # что телефонный перевод действительно выполнен: это зависит
        # от обработчика ответа в телефонии.
        if self._is_danger_signal(normalized_text):
            state.scenario = "danger"
            return await self._call_scenario(
                "danger",
                state,
                parsed=None,
                raw_text=raw_text,
            )

        # 2. Медицинские красные флаги проверяются до обычной маршрутизации.
        try:
            red_flag = detect_red_flag(raw_text)
        except Exception:
            logger.exception(
                "Ошибка обнаружения красного флага: call_id=%s",
                call_id,
            )
            red_flag = None

        if red_flag is not None:
            state.scenario = "emergency"
            return await self._call_scenario(
                "emergency",
                state,
                parsed=None,
                red_flag=red_flag,
                raw_text=raw_text,
            )

        # 3. Терминальное состояние: повторный вызов после завершения
        # не должен неожиданно продолжать старый сценарий.
        try:
            parsed = await parse(raw_text, self._today)
        except Exception:
            logger.exception(
                "Ошибка NLU: call_id=%s",
                call_id,
            )
            parsed = None

        if state.step in (STEP_BOOKED, STEP_ESCALATED):
            intent = getattr(parsed, "intent", None)

            if intent in INTENT_TO_SCENARIO:
                self._reset_dialog_state(state)
            else:
                return DialogResponse(
                    reply="Хорошо. Всего доброго, будьте здоровы!",
                    step=state.step,
                )

        # 4. Если NLU не сработал, продолжаем уже выбранный сценарий.
        # Если сценарий ещё не выбран, используем справочную.
        if state.scenario is None:
            intent = getattr(parsed, "intent", None)
            state.scenario = INTENT_TO_SCENARIO.get(
                intent,
                "information",
            )
            state.step = STEP_GREETING

        # 5. Особая передача управления при переносе.
        if (
                state.is_reschedule
                and state.scenario == "transfer"
                and state.step == STEP_ASK_SPECIALTY
        ):
            state.scenario = "booking"

        current_scenario_name = state.scenario
        response = await self._call_scenario(
            current_scenario_name,
            state,
            parsed=parsed,
            raw_text=raw_text,
        )

        # 6. Разрешаем не более одной передачи управления за одну реплику.
        target_scenario_name = state.scenario

        if (
                target_scenario_name != current_scenario_name
                and state.step != STEP_ESCALATED
                and target_scenario_name in self._scenarios
        ):
            logger.info(
                "Передача управления: %s -> %s; call_id=%s",
                current_scenario_name,
                target_scenario_name,
                call_id,
            )
            return await self._call_scenario(
                target_scenario_name,
                state,
                parsed=parsed,
                raw_text=raw_text,
            )

        return response

    async def _call_scenario(
        self,
        scenario_name: str,
        state: DialogState,
        parsed: Any = None,
        red_flag: Any = None,
        raw_text: str = "",
    ) -> DialogResponse:
        """Вызывает сценарий с защитой от неизвестного имени."""
        scenario = self._scenarios.get(scenario_name)

        if scenario is None:
            logger.error(
                "Неизвестный сценарий %r; call_id=%s",
                scenario_name,
                state.call_id,
            )
            state.scenario = "information"
            scenario = self._scenarios["information"]

        try:
            return await scenario.handle(
                state,
                parsed=parsed,
                red_flag=red_flag,
                raw_text=raw_text,
            )
        except Exception:
            logger.exception(
                "Ошибка сценария %s; call_id=%s",
                scenario_name,
                state.call_id,
            )
            # Не продолжаем случайно выполнять другой сценарий после ошибки.
            state.step = STEP_ESCALATED
            return DialogResponse(
                reply=(
                    "Извините, сейчас не удаётся обработать запрос. "
                    "Пожалуйста, обратитесь в регистратуру."
                ),
                step=state.step,
                escalated=True,
            )

    @staticmethod
    def _normalize_text(text: str) -> str:
        """Нормализует текст для простых проверок ключевых слов."""
        normalized = (text or "").lower().replace("ё", "е")
        normalized = re.sub(r"[^\w\s]", " ", normalized, flags=re.UNICODE)
        return re.sub(r"\s+", " ", normalized).strip()

    @staticmethod
    def _is_danger_signal(normalized_text: str) -> bool:
        """Проверяет наличие известных маркеров потенциальной угрозы."""
        if not normalized_text:
            return False

        return any(
            re.search(pattern, normalized_text, flags=re.IGNORECASE)
            for pattern in DANGER_PATTERNS
        )

    @staticmethod
    def _reset_dialog_state(state: DialogState) -> None:
        """Очищает данные предыдущей операции перед началом новой."""
        state.scenario = None
        state.step = STEP_GREETING

        state.last_name = None
        state.first_name = None
        state.middle_name = None
        state.birth_date = None
        state.full_name = None
        state.client_id = None

        state.specialty = None
        state.offered_slots = []
        state.selected_slot = None
        state.visit_id = None

        state.is_reschedule = False
        state.old_visit_id = None
        state.old_scheduled_at = None

        state.failed_attempts = 0
        state.red_flag = None

        # extra используется сценариями для временных данных диалога.
        # Если в вашем проекте там хранятся постоянные метаданные звонка,
        # вместо полной очистки удаляйте только ключи конкретных сценариев.
        state.extra.clear()
