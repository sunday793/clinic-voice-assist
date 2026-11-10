"""Тесты C-14 «Экстренная ситуация» по контрольным диалогам Приложения Д."""

from __future__ import annotations

import pytest

from app.config import settings
from app.core.constants import RED_FLAG_CATEGORY_A, RED_FLAG_CATEGORY_B
from app.schemas.mis import Doctor
from app.services.dialog import DialogEngine, STEP_ESCALATED
from app.services.mis_client_mock import MISClientMock
from app.services.red_flags import detect
from app.services.schedule import ScheduleService


# ─────────────────────────────────────────────────────────────
# Unit: детектор
# ─────────────────────────────────────────────────────────────


class TestRedFlagDetector:
    """Проверяем детектор красных флагов."""

    @pytest.mark.parametrize(
        "text",
        [
            "давит в груди",
            "боль в груди",
            "отдаёт в левую руку",
            "не может дышать",
            "задыхается",
            "потерял сознание",
            "перекосило лицо",
            "отнялась рука",
            "сильное кровотечение",
            "укусила змея",
            "откает горло",
            "ребёнок посинел",
            "отошли воды",
            "не хочу жить",
        ],
    )
    def test_category_a_detected(self, text: str) -> None:
        flag = detect(text)
        assert flag is not None, f"Не найден красный флаг: {text}"
        assert flag.category == RED_FLAG_CATEGORY_A

    @pytest.mark.parametrize(
        "text",
        [
            "очень сильная боль",
            "невыносимо болит живот",
            "температура сорок",
            "температура не сбивается",
            "стало намного хуже",
            "резко поднялось давление",
        ],
    )
    def test_category_b_detected(self, text: str) -> None:
        flag = detect(text)
        assert flag is not None, f"Не найден красный флаг: {text}"
        assert flag.category == RED_FLAG_CATEGORY_B

    @pytest.mark.parametrize(
        "text",
        [
            "запишите меня к терапевту",
            "хочу отменить запись",
            "когда принимает хирург",
            "готовы ли анализы",
            "сколько стоит УЗИ",
            "у меня болит голова",
        ],
    )
    def test_no_red_flag(self, text: str) -> None:
        assert detect(text) is None, f"Ложное срабатывание: {text}"


# ─────────────────────────────────────────────────────────────
# Integration: прерывание сценария
# ─────────────────────────────────────────────────────────────


@pytest.fixture
def engine() -> DialogEngine:
    mis = MISClientMock()
    mis.seed_doctors([
        Doctor(
            id=1,
            full_name="Верещагин Андрей Михайлович",
            specialty="Терапевт",
            is_active=True,
        ),
    ])
    schedule = ScheduleService()
    return DialogEngine(
        mis=mis, schedule=schedule, today=settings.SCHEDULE_TODAY
    )


async def test_red_flag_interrupts_booking(engine: DialogEngine) -> None:
    """Приложение Д, тест 1: красный флаг прерывает запись."""
    call_id = "test-emergency-1"

    r1 = await engine.handle(call_id, "Запишите меня к терапевту")
    assert r1.step != STEP_ESCALATED

    r2 = await engine.handle(
        call_id, "ой, у меня вообще-то сильно давит в груди"
    )
    assert r2.step == STEP_ESCALATED
    assert r2.escalated is True
    assert r2.red_flag_category == RED_FLAG_CATEGORY_A
    assert "сто три" in r2.reply or "сто двенадцать" in r2.reply


async def test_emergency_from_first_message(engine: DialogEngine) -> None:
    """С-14: «Мужу плохо, давит в груди»."""
    call_id = "test-emergency-2"
    r = await engine.handle(
        call_id, "Мужу плохо, говорит, давит в груди, весь бледный"
    )
    assert r.step == STEP_ESCALATED
    assert "сто три" in r.reply or "сто двенадцать" in r.reply


async def test_urgent_category_b(engine: DialogEngine) -> None:
    """Категория Б: острая боль — перевод P1."""
    call_id = "test-urgent-1"
    r = await engine.handle(call_id, "Очень сильная боль в животе, невыносимо")
    assert r.step == STEP_ESCALATED
    assert r.red_flag_category == RED_FLAG_CATEGORY_B


async def test_normal_dialog_not_escalated(engine: DialogEngine) -> None:
    """Обычный диалог не должен вызывать эскалацию."""
    call_id = "test-normal-1"
    r = await engine.handle(call_id, "Запишите меня к терапевту")
    assert r.step != STEP_ESCALATED
    assert r.escalated is False