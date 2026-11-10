"""Тесты сценария жалобы с учетом загруженности операторов."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.core.constants import OPERATORS_BUSY_PHRASE
from app.services.dialog.scenarios.complaint import ComplaintScenario
from app.services.dialog.state import DialogState
from app.services.mis_client_mock import MISClientMock


@pytest.fixture
def mis() -> MISClientMock:
    mis = MISClientMock()
    mis.seed_doctors([])
    return mis


@pytest.fixture
def scenario(mis) -> ComplaintScenario:
    return ComplaintScenario(mis=mis)


@pytest.fixture
def state() -> DialogState:
    st = DialogState(call_id="test-complaint")
    st.client_id = 1  # Имитируем, что пациент уже идентифицирован
    return st


async def test_complaint_when_operators_busy(scenario, state):
    """Если операторы заняты — не переводим, а даем callback."""
    with patch("app.services.dialog.scenarios.complaint.get_load_manager") as mock_lm:
        mock_instance = AsyncMock()
        mock_instance.is_overloaded = AsyncMock(return_value=True)
        mock_lm.return_value = mock_instance

        response = await scenario.handle(state, raw_text="Хочу пожаловаться")

        assert response.escalated is False
        assert response.step == "callback_pending"
        assert response.reply == OPERATORS_BUSY_PHRASE


async def test_complaint_when_operators_free(scenario, state):
    """Если операторы свободны — переводим на старшего администратора."""
    with patch("app.services.dialog.scenarios.complaint.get_load_manager") as mock_lm:
        mock_instance = AsyncMock()
        mock_instance.is_overloaded = AsyncMock(return_value=False)
        mock_instance.acquire = AsyncMock(return_value=True)
        mock_lm.return_value = mock_instance

        response = await scenario.handle(state, raw_text="Хочу пожаловаться")

        assert response.escalated is True
        assert response.step == "escalated"
        assert "старшим администратором" in response.reply