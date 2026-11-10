
"""Сценарий справочной информации (Information Desk)."""
from __future__ import annotations

import logging

from app.core.constants import OPERATORS_BUSY_PHRASE
from app.schemas.mis import EscalationCreate
from app.services.dialog.base import ScenarioProtocol
from app.services.dialog.state import (
    DialogResponse,
    DialogState,
    STEP_ESCALATED,
    STEP_GREETING,
)
from app.services.load_manager import get_load_manager
from app.services.mis_client import MISClientProtocol
from app.services.nlu import ParsedUtterance
from app.services.red_flags import RedFlag
from app.services.dialog.scenarios.faq import (
    detect_intent,
    get_faq_answer,
)

logger = logging.getLogger(__name__)

STEP_INFO_MENU = "info_menu"
STEP_CALLBACK_PENDING = "callback_pending"

SCENARIO_BY_INTENT = {
    "booking": "booking",
    "cancel": "cancel",
    "transfer": "transfer",
}


class InformationScenario(ScenarioProtocol):
    """Отвечает на справочные вопросы и направляет в другие сценарии."""

    name = "information"

    def __init__(self, mis: MISClientProtocol) -> None:
        self._mis = mis

    async def handle(
        self,
        state: DialogState,
        parsed: ParsedUtterance | None = None,
        red_flag: RedFlag | None = None,
        raw_text: str = "",
    ) -> DialogResponse:
        # Опасные ситуации не должны обрабатываться как обычный FAQ.
        # Основной DialogEngine должен направлять их в экстренный сценарий.
        if red_flag is not None:
            state.red_flag = red_flag
            state.step = STEP_ESCALATED
            logger.critical(
                "Red flag передан в InformationScenario; call_id=%s; category=%s",
                state.call_id,
                red_flag.category,
            )
            return DialogResponse(
                reply=red_flag.phrase or (
                    "Ситуация требует немедленного внимания. "
                    "Оставайтесь, пожалуйста, на линии."
                ),
                step=state.step,
                escalated=True,
                red_flag_category=red_flag.category,
            )

        if state.step == STEP_ESCALATED:
            return DialogResponse(
                reply="Оставайтесь, пожалуйста, на линии. Вам поможет сотрудник клиники.",
                step=state.step,
                escalated=True,
            )

        if state.step == STEP_CALLBACK_PENDING:
            return DialogResponse(
                reply=(
                    "Заявка на обратный звонок была зарегистрирована, "
                    "если её сохранение ранее подтвердилось. "
                    "Могу ли я помочь с другим вопросом?"
                ),
                step=state.step,
            )

        # приветствие
        if state.step in (STEP_GREETING, "greeting"):
            state.step = STEP_INFO_MENU
            return DialogResponse(
                reply=(
                    "Вы можете узнать график работы, адрес, правила "
                    "подготовки к анализам или цены. Что вас интересует?"
                ),
                step=state.step,
            )

        if state.step != STEP_INFO_MENU:
            state.step = STEP_INFO_MENU
            return DialogResponse(
                reply=(
                    "Давайте продолжим. Вы можете спросить о графике работы, "
                    "адресе, анализах, ценах или попросить соединить вас "
                    "с сотрудником."
                ),
                step=state.step,
            )

        text = (raw_text or "").strip()
        if not text:
            state.step = STEP_INFO_MENU
            return DialogResponse(
                reply="Пожалуйста, скажите, что вас интересует.",
                step=state.step,
            )

        # Сначала обрабатываем намерения перехода в другие сценарии.
        # detect_intent() должен существовать в faq.py.
        try:
            intent = detect_intent(text)
        except Exception:
            logger.exception(
                "Ошибка определения намерения в InformationScenario; call_id=%s",
                state.call_id,
            )
            intent = None

        if intent == "operator":
            return await self._handle_operator_request(state, text)

        target_scenario = SCENARIO_BY_INTENT.get(intent or "")
        if target_scenario:
            state.scenario = target_scenario
            state.step = STEP_GREETING

            # DialogEngine в текущей архитектуре может сразу вызвать
            # новый сценарий после изменения state.scenario.
            return DialogResponse(
                reply="Хорошо, переключаю вас.",
                step=state.step,
            )

        # Если намерение не распознано как переход, ищем ответ в FAQ.
        try:
            answer = get_faq_answer(text)
        except Exception:
            logger.exception(
                "Ошибка поиска ответа в FAQ; call_id=%s",
                state.call_id,
            )
            answer = None

        if answer:
            return DialogResponse(
                reply=answer,
                step=state.step,
            )

        return DialogResponse(
            reply=(
                "Я не совсем понял ваш вопрос. Вы можете спросить про график "
                "работы, адрес, анализы, цены или прикрепление. "
                "Также могу попробовать соединить вас с сотрудником."
            ),
            step=state.step,
        )

    async def _handle_operator_request(
        self,
        state: DialogState,
        raw_text: str,
    ) -> DialogResponse:
        """Проверяет нагрузку и переводит звонок либо оформляет заявку."""

        try:
            load = get_load_manager()
            overloaded = await load.is_overloaded()
        except Exception:
            logger.exception(
                "Не удалось проверить нагрузку операторов; call_id=%s",
                state.call_id,
            )
            return await self._create_callback(state, raw_text)

        if overloaded:
            return await self._create_callback(state, raw_text)

        try:
            await load.acquire()
        except Exception:
            logger.exception(
                "Не удалось зарезервировать нагрузку оператора; call_id=%s",
                state.call_id,
            )
            return await self._create_callback(state, raw_text)

        state.step = STEP_ESCALATED
        return DialogResponse(
            reply=(
                "Соединяю вас с сотрудником регистратуры. "
                "Пожалуйста, оставайтесь на линии."
            ),
            step=state.step,
            escalated=True,
        )

    async def _create_callback(
        self,
        state: DialogState,
        raw_text: str,
    ) -> DialogResponse:
        """Создаёт заявку на обратный звонок, если это позволяет MIS."""

        if state.client_id is None:
            logger.warning(
                "Нельзя создать callback: отсутствует client_id; call_id=%s",
                state.call_id,
            )
            state.step = STEP_INFO_MENU
            return DialogResponse(
                reply=(
                    "Сейчас сотрудники регистратуры заняты, а я не могу "
                    "зарегистрировать обратный звонок без данных пациента. "
                    "Попробуйте позвонить позднее."
                ),
                step=state.step,
                escalated=False,
            )

        description = (
            "[CALLBACK P3] Пациент просит оператора, "
            "операторы заняты. "
            f"Текст обращения: {(raw_text or '').strip()[:1000]}"
        )

        try:
            await self._mis.create_escalation(
                EscalationCreate(
                    client_id=state.client_id,
                    description=description,
                )
            )
        except Exception:
            logger.exception(
                "Не удалось создать callback в MIS; call_id=%s",
                state.call_id,
            )
            state.step = STEP_INFO_MENU
            return DialogResponse(
                reply=(
                    "Сейчас сотрудники регистратуры заняты, и не удалось "
                    "зарегистрировать обратный звонок. Пожалуйста, "
                    "попробуйте связаться с клиникой позднее."
                ),
                step=state.step,
                escalated=False,
            )

        state.step = STEP_CALLBACK_PENDING
        return DialogResponse(
            reply=OPERATORS_BUSY_PHRASE,
            step=state.step,
            escalated=False,
        )
