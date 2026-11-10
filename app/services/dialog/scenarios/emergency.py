
"""Сценарий C-14 «Экстренная ситуация»."""
from __future__ import annotations

import logging
from typing import Any

from app.core.constants import RED_FLAG_CATEGORY_A
from app.schemas.mis import EscalationCreate
from app.services.dialog.base import ScenarioProtocol
from app.services.dialog.state import (
    DialogResponse,
    DialogState,
    STEP_ESCALATED,
)
from app.services.mis_client import MISClientProtocol
from app.services.nlu import ParsedUtterance
from app.services.red_flags import RedFlag
from app.services.transfer import (
    build_emergency_card,
    build_urgent_card,
)

logger = logging.getLogger(__name__)

EMERGENCY_CARD_SAVED_KEY = "emergency_card_saved"

DEFAULT_EMERGENCY_REPLY = (
    "Ситуация требует немедленного внимания. "
    "Если существует непосредственная угроза жизни или здоровью, "
    "позвоните в экстренную службу по номеру 112. "
    "Передаю информацию ответственному сотруднику. Оставайтесь на линии."
)

DEFAULT_TRANSFER_REPLY = (
    "Передаю ваш вызов ответственному сотруднику. "
    "Пожалуйста, оставайтесь на линии."
)


class EmergencyScenario(ScenarioProtocol):
    """Обрабатывает выявленные экстренные ситуации."""

    name = "emergency"

    def __init__(self, mis: MISClientProtocol) -> None:
        self._mis = mis

    async def handle(
        self,
        state: DialogState,
        parsed: ParsedUtterance | None = None,
        red_flag: RedFlag | None = None,
        raw_text: str = "",
    ) -> DialogResponse:
        # Устанавливаем состояние сразу, чтобы даже при ошибке
        # формирования карточки диалог не продолжил обычный сценарий.
        state.step = STEP_ESCALATED

        if red_flag is None:
            logger.critical(
                "Сценарий экстренной ситуации вызван без red_flag; "
                "call_id=%s",
                state.call_id,
            )
            return DialogResponse(
                reply=DEFAULT_TRANSFER_REPLY,
                step=state.step,
                escalated=True,
            )

        state.red_flag = red_flag

        if not state.full_name:
            name_parts = (
                state.last_name,
                state.first_name,
                state.middle_name,
            )
            state.full_name = " ".join(
                part.strip()
                for part in name_parts
                if part and part.strip()
            ) or None

        logger.critical(
            "Выявлена экстренная ситуация; call_id=%s; "
            "category=%s; group=%s",
            state.call_id,
            red_flag.category,
            red_flag.group,
        )

        # Если карточка уже успешно создана в рамках этого состояния,
        # не отправляем её повторно при повторном входе в сценарий.
        card_saved = bool(state.extra.get(EMERGENCY_CARD_SAVED_KEY))

        if not card_saved:
            card = self._build_card(state, red_flag)

            if card is None:
                logger.exception(
                    "Не удалось сформировать карточку экстренной ситуации; "
                    "call_id=%s",
                    state.call_id,
                )
            elif state.client_id is None:
                logger.error(
                    "Карточка экстренной ситуации сформирована, "
                    "но не сохранена: отсутствует client_id; call_id=%s",
                    state.call_id,
                )
            else:
                try:
                    await self._mis.create_escalation(
                        EscalationCreate(
                            client_id=state.client_id,
                            description=card.to_escalation_description(),
                        )
                    )
                    state.extra[EMERGENCY_CARD_SAVED_KEY] = True
                    card_saved = True

                    logger.info(
                        "Карточка экстренной ситуации сохранена в MIS; "
                        "call_id=%s; category=%s",
                        state.call_id,
                        red_flag.category,
                    )
                except Exception:
                    logger.exception(
                        "Не удалось сохранить карточку экстренной ситуации "
                        "в MIS; call_id=%s; category=%s",
                        state.call_id,
                        red_flag.category,
                    )

        reply = (red_flag.phrase or "").strip()

        if not reply:
            reply = DEFAULT_EMERGENCY_REPLY

        if not card_saved:
            # Не утверждаем, что сведения записаны в MIS.
            reply = (
                f"{reply} "
                "Не удалось подтвердить регистрацию обращения в системе. "
                "Оставайтесь на линии для связи с ответственным сотрудником."
            )

        return DialogResponse(
            reply=reply,
            step=state.step,
            escalated=True,
            red_flag_category=red_flag.category,
        )

    @staticmethod
    def _build_card(
        state: DialogState,
        red_flag: RedFlag,
    ) -> Any | None:
        """Формирует карточку нужного типа; возвращает None при ошибке."""

        try:
            builder = (
                build_emergency_card
                if red_flag.category == RED_FLAG_CATEGORY_A
                else build_urgent_card
            )

            return builder(
                call_id=state.call_id,
                started_at=state.started_at,
                red_flag_group=red_flag.group,
                matched=red_flag.matched,
                patient_name=state.full_name,
                patient_id=state.client_id,
            )
        except Exception:
            logger.exception(
                "Ошибка формирования карточки; call_id=%s; category=%s",
                state.call_id,
                red_flag.category,
            )
            return None
