
"""Сценарий C-15 «Жалоба»."""
from __future__ import annotations

import logging

from app.core.constants import OPERATORS_BUSY_PHRASE
from app.schemas.mis import EscalationCreate
from app.services.dialog.base import ScenarioProtocol
from app.services.dialog.state import (
    DialogResponse,
    DialogState,
    STEP_ESCALATED,
)
from app.services.load_manager import get_load_manager
from app.services.mis_client import MISClientProtocol
from app.services.nlu import ParsedUtterance
from app.services.red_flags import RedFlag
from app.services.transfer import TransferCard

logger = logging.getLogger(__name__)

STEP_CALLBACK_PENDING = "callback_pending"
STEP_COMPLAINT_ESCALATED = "complaint_escalated"

COMPLAINT_INTENT = "I-12 Жалоба"
COMPLAINT_REASON_CODE = "R-03"
COMPLAINT_PRIORITY = "P2"
MAX_REASON_LENGTH = 2000


class ComplaintScenario(ScenarioProtocol):
    """Регистрирует жалобу и инициирует перевод на старшего администратора."""

    name = "complaint"

    def __init__(self, mis: MISClientProtocol) -> None:
        self._mis = mis

    async def handle(
        self,
        state: DialogState,
        parsed: ParsedUtterance | None = None,
        red_flag: RedFlag | None = None,
        raw_text: str = "",
    ) -> DialogResponse:
        if state.step in (STEP_ESCALATED, STEP_COMPLAINT_ESCALATED):
            return DialogResponse(
                reply="Оставайтесь, пожалуйста, на линии. Вам поможет сотрудник клиники.",
                step=state.step,
                escalated=True,
            )

        if state.step == STEP_CALLBACK_PENDING:
            return DialogResponse(
                reply=(
                    "Ваше обращение ожидает обратного звонка. "
                    "Могу ли я помочь ещё чем-нибудь?"
                ),
                step=state.step,
            )

        # Проверяем доступность операторов. Если менеджер нагрузки
        # недоступен, не считаем, что свободный оператор гарантирован.
        try:
            load = get_load_manager()
            overloaded = await load.is_overloaded()
        except Exception:
            logger.exception(
                "Не удалось проверить нагрузку операторов для жалобы; "
                "попытка оформить обратный звонок"
            )
            return await self._schedule_callback(state, raw_text)

        if overloaded:
            return await self._schedule_callback(state, raw_text)

        # Важно: между is_overloaded() и acquire() возможна гонка.
        # Атомарную гарантию доступности может дать только LoadManager
        # с соответствующим API (например, try_acquire()).
        try:
            await load.acquire()
        except Exception:
            logger.exception(
                "Не удалось зарезервировать нагрузку оператора для жалобы"
            )
            return await self._schedule_callback(state, raw_text)

        escalation_saved = await self._create_escalation(
            state=state,
            raw_text=raw_text,
            callback=False,
        )

        state.step = STEP_COMPLAINT_ESCALATED

        if escalation_saved:
            reply = (
                "Мне жаль, что так вышло. Соединяю вас со старшим "
                "администратором. Суть обращения записана, поэтому "
                "повторять её не придётся. Оставайтесь на линии, пожалуйста."
            )
        else:
            reply = (
                "Мне жаль, что так вышло. Соединяю вас со старшим "
                "администратором. Не удалось сохранить описание жалобы "
                "в системе, поэтому, возможно, потребуется кратко повторить "
                "суть обращения. Оставайтесь на линии, пожалуйста."
            )

        return DialogResponse(
            reply=reply,
            step=state.step,
            escalated=True,
        )

    async def _schedule_callback(
        self,
        state: DialogState,
        raw_text: str,
    ) -> DialogResponse:
        """Пытается сохранить обращение для последующего звонка."""

        callback_saved = await self._create_escalation(
            state=state,
            raw_text=raw_text,
            callback=True,
        )

        if callback_saved:
            state.step = STEP_CALLBACK_PENDING
            reply = OPERATORS_BUSY_PHRASE
        else:
            # Не обещаем обратный звонок, если заявку не удалось сохранить.
            state.step = STEP_CALLBACK_PENDING
            reply = (
                "Сейчас операторы недоступны, и мне не удалось "
                "зарегистрировать заявку на обратный звонок. "
                "Пожалуйста, свяжитесь с клиникой повторно или обратитесь "
                "к сотруднику другим доступным способом."
            )

        return DialogResponse(
            reply=reply,
            step=state.step,
            escalated=False,
        )

    async def _create_escalation(
        self,
        state: DialogState,
        raw_text: str,
        *,
        callback: bool,
    ) -> bool:
        """Сохраняет описание жалобы в MIS.

        Возвращает True только при успешном завершении вызова MIS.
        Если client_id отсутствует, заявку через этот интерфейс создать нельзя.
        """

        if not state.client_id:
            logger.warning(
                "Нельзя создать заявку по жалобе: отсутствует client_id; "
                "call_id=%s",
                state.call_id,
            )
            return False

        reason = (raw_text or "").strip()
        if len(reason) > MAX_REASON_LENGTH:
            reason = reason[:MAX_REASON_LENGTH] + "…"

        if not reason:
            reason = "Пациент обратился с жалобой; текст обращения не получен."

        if callback:
            intent = f"{COMPLAINT_INTENT} (callback)"
            summary = "Жалоба пациента — ожидает обратного звонка"
            reason_text = (
                "Обратный звонок по жалобе (операторы заняты): "
                f"{reason}"
            )
        else:
            intent = COMPLAINT_INTENT
            summary = "Жалоба пациента"
            reason_text = f"Жалоба пациента: {reason}"

        try:
            card = TransferCard(
                call_id=state.call_id,
                started_at=state.started_at,
                transferred_at="",
                intent=intent,
                reason_code=COMPLAINT_REASON_CODE,
                reason_text=reason_text,
                priority=COMPLAINT_PRIORITY,
                summary=summary,
            )

            await self._mis.create_escalation(
                EscalationCreate(
                    client_id=state.client_id,
                    description=card.to_escalation_description(),
                )
            )

            logger.info(
                "Жалоба сохранена в MIS; call_id=%s; callback=%s",
                state.call_id,
                callback,
            )
            return True

        except Exception:
            logger.exception(
                "Не удалось сохранить жалобу в MIS; "
                "call_id=%s; callback=%s",
                state.call_id,
                callback,
            )
            return False
