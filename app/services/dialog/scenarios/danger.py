
"""Сценарий «Угроза терроризма или массового насилия» (Danger)."""
from __future__ import annotations

import logging

from app.services.dialog.base import ScenarioProtocol
from app.services.dialog.state import (
    DialogResponse,
    DialogState,
    STEP_ESCALATED,
)
from app.services.nlu import ParsedUtterance
from app.services.red_flags import RedFlag

logger = logging.getLogger(__name__)

DANGER_HANDLED_KEY = "danger_handled"

DANGER_REPLY = (
    "Я воспринимаю ваше сообщение серьёзно. "
    "Если существует непосредственная угроза жизни людей, "
    "немедленно сообщите об этом экстренным службам по номеру 112. "
    "Передаю вызов ответственному сотруднику. Оставайтесь на линии."
)

DANGER_REPEAT_REPLY = (
    "Ваше сообщение отмечено как требующее немедленного внимания. "
    "Оставайтесь, пожалуйста, на линии."
)


class DangerScenario(ScenarioProtocol):
    """Обрабатывает сообщения об угрозе терроризма или массового насилия."""

    name = "danger"

    async def handle(
        self,
        state: DialogState,
        parsed: ParsedUtterance | None = None,
        red_flag: RedFlag | None = None,
        raw_text: str = "",
    ) -> DialogResponse:
        # Не повторяем критическое логирование при повторном входе
        # в сценарий для того же состояния диалога.
        already_handled = bool(state.extra.get(DANGER_HANDLED_KEY))

        state.step = STEP_ESCALATED

        if not already_handled:
            state.extra[DANGER_HANDLED_KEY] = True

            # Не записываем полный текст сообщения: он может содержать
            # персональные данные или подробности опасного события.
            logger.critical(
                "Обнаружена потенциальная угроза терроризма или "
                "массового насилия. call_id=%s; red_flag_detected=%s; "
                "text_length=%d",
                state.call_id,
                red_flag is not None,
                len(raw_text or ""),
            )

        return DialogResponse(
            reply=DANGER_REPEAT_REPLY if already_handled else DANGER_REPLY,
            step=state.step,
            escalated=True,
        )
