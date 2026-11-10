"""Тесты для DialogEngine (маршрутизация и переключение сценариев)."""

import pytest
from app.services.dialog.engine import DialogEngine
from app.services.dialog.state import (
    STEP_GREETING,
    STEP_ASK_NAME,
    STEP_BOOKED,
    STEP_ESCALATED,
    STEP_ASK_SPECIALTY,
)
from app.services.mis_client_mock import MISClientMock
from app.services.schedule import ScheduleService
from app.schemas.mis import Doctor


@pytest.fixture
def mis():
    mock = MISClientMock()
    mock.seed_doctors([
        Doctor(id=1, full_name="Иванова Мария Петровна", specialty="Терапевт", is_active=True)
    ])
    return mock


@pytest.fixture
def schedule():
    class MockSchedule(ScheduleService):
        async def get_free_slots(self, specialty=None, limit=3, **kwargs):
            from app.services.schedule import Slot
            return [
                Slot(slot_id="1", date="20.10.2026", doctor_excel_id="D001", doctor_name="Иванова Мария Петровна",
                     specialty="Терапевт", start="10:00", status="Свободен")
            ]

        async def resolve_doctor_id(self, doctor_excel_id, mis_client):
            return 1

        async def book_slot(self, doctor_excel_id, scheduled_at):
            pass

    return MockSchedule()


@pytest.fixture
def engine(mis, schedule):
    return DialogEngine(mis=mis, schedule=schedule, today="14.10.2026")


# ─────────────────────────────────────────────────────────────
# 1. Тесты экстренных ситуаций (безопасность)
# ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_engine_danger_terrorism(engine):
    """Угроза терроризма моментально переводит на оператора (Danger)."""
    response = await engine.handle("call-1", "Я заложил бомбу в поликлинике!")

    # Движок должен распознать это как Danger, а не как жалобу или запись
    assert response.step == STEP_ESCALATED
    assert response.escalated is True
    assert "112" in response.reply or "полицию" in response.reply


@pytest.mark.asyncio
async def test_engine_emergency_red_flag(engine):
    """Красный флаг (боль в груди) переводит на 103 (Emergency)."""
    response = await engine.handle("call-2", "У меня сильно давит в груди и не хватает воздуха")

    assert response.step == STEP_ESCALATED
    assert response.escalated is True
    assert "103" in response.reply or "скорую" in response.reply.lower()


# ─────────────────────────────────────────────────────────────
# 2. Тесты маршрутизации по интентам
# ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_engine_routes_to_booking(engine):
    """Фраза 'Запишите меня' отправляет в BookingScenario."""
    response = await engine.handle("call-3", "Хочу записаться к терапевту")

    # Сценарий записи начинается с приветствия и запроса ФИО
    assert response.step == STEP_ASK_NAME
    assert "фамилию" in response.reply.lower()


@pytest.mark.asyncio
async def test_engine_routes_to_information(engine):
    """Вопрос про график без интента записи уходит в Information."""
    response = await engine.handle("call-4", "А когда вы работаете в субботу?")

    # Справочная не просит ФИО, а сразу отвечает
    assert "субботу" in response.reply.lower() or "работает" in response.reply.lower()
    assert response.step != STEP_ASK_NAME


# ─────────────────────────────────────────────────────────────
# 3. Тесты эстафеты (переключения сценариев на лету)
# ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_engine_switch_from_information_to_booking(engine):
    """Если пациент в справочной передумал и просит записать — движок переключает сценарий."""
    # 1. Заходим в справочную
    r1 = await engine.handle("call-5", "Когда вы работаете?")
    assert "работает" in r1.reply.lower()

    # 2. Просим записать
    r2 = await engine.handle("call-5", "А запишите меня к терапевту")

    # Движок должен подхватить смену сценария и запросить ФИО
    assert r2.step == STEP_ASK_NAME
    assert "фамилию" in r2.reply.lower()


@pytest.mark.asyncio
async def test_engine_terminal_state_reset(engine):
    """После успешной записи новый интент сбрасывает состояние."""
    # 1. Успешно записываемся (имитируем прохождение всех шагов)
    await engine.handle("call-6", "Запишите к терапевту")
    await engine.handle("call-6", "Иванов Иван 01.01.1990")
    await engine.handle("call-6", "Терапевт")
    await engine.handle("call-6", "Первый")
    r_booked = await engine.handle("call-6", "Да")

    assert r_booked.step == STEP_BOOKED
    assert r_booked.booked is True

    # 2. Пытаемся начать новый диалог
    r_new = await engine.handle("call-6", "А теперь отмените мою запись")

    # Движок должен сбросить STEP_BOOKED и запустить сценарий отмены с самого начала
    assert r_new.step == STEP_ASK_NAME
    assert "отменить" in r_new.reply.lower()