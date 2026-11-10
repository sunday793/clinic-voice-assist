"""Тесты справочной с учётом загруженности."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.core.constants import OPERATORS_BUSY_PHRASE
from app.services.dialog.scenarios.information_desk import InformationScenario
from app.services.dialog.state import DialogState, STEP_ESCALATED
from app.services.mis_client_mock import MISClientMock


@pytest.fixture
def mis() -> MISClientMock:
    return MISClientMock()


@pytest.fixture
def scenario(mis) -> InformationScenario:
    return InformationScenario(mis=mis)


@pytest.fixture
def state() -> DialogState:
    st = DialogState(call_id="test-info")
    st.step = "info_menu"  # уже в меню
    st.client_id = 1
    return st


async def test_info_operator_busy(scenario, state):
    """Если заняты — callback, не переводим."""
    with patch("app.services.dialog.scenarios.information_desk.get_load_manager") as mock_lm:
        mock_instance = AsyncMock()
        mock_instance.is_overloaded = AsyncMock(return_value=True)
        mock_lm.return_value = mock_instance

        response = await scenario.handle(state, raw_text="Соедините с человеком")

        assert response.escalated is False
        assert response.step == "callback_pending"
        assert response.reply == OPERATORS_BUSY_PHRASE


async def test_info_operator_free(scenario, state):
    """Если свободны — переводим."""
    with patch("app.services.dialog.scenarios.information_desk.get_load_manager") as mock_lm:
        mock_instance = AsyncMock()
        mock_instance.is_overloaded = AsyncMock(return_value=False)
        mock_instance.acquire = AsyncMock(return_value=True)
        mock_lm.return_value = mock_instance

        response = await scenario.handle(state, raw_text="Соедините с человеком")

        assert response.escalated is True
        assert response.step == STEP_ESCALATED