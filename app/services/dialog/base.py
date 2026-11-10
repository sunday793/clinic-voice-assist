
"""Интерфейс сценария диалога."""
from __future__ import annotations

from typing import Protocol

from app.services.dialog.state import DialogResponse, DialogState
from app.services.nlu import ParsedUtterance
from app.services.red_flags import RedFlag


class ScenarioProtocol(Protocol):
    """Интерфейс сценария диалога.

    Каждый сценарий должен иметь атрибут name и реализовывать
    асинхронный метод handle(), возвращающий DialogResponse.
    """

    name: str

    async def handle(
        self,
        state: DialogState,
        parsed: ParsedUtterance | None = None,
        red_flag: RedFlag | None = None,
        raw_text: str = "",
    ) -> DialogResponse:
        """Обрабатывает реплику пользователя и обновляет состояние диалога."""
        ...
