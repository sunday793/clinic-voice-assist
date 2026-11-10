"""Интеграционные тесты для сценариев диалога."""

import pytest
from datetime import datetime

from app.services.dialog.scenarios.booking import BookingScenario
from app.services.dialog.scenarios.cancel import CancelScenario
from app.services.dialog.scenarios.transfer_entry import TransferScenario
from app.services.dialog.state import (
    DialogState,
    STEP_GREETING,
    STEP_ASK_NAME,
    STEP_ASK_SPECIALTY,
    STEP_BOOKED,
    STEP_OFFER_SLOTS,
    STEP_CONFIRM,
)
from app.services.mis_client_mock import MISClientMock
from app.services.schedule import ScheduleService
from app.services.nlu import ParsedUtterance
from app.schemas.mis import Doctor


# ─────────────────────────────────────────────────────────────
# Фикстуры
# ─────────────────────────────────────────────────────────────

@pytest.fixture
def mis():
    mock = MISClientMock()
    mock.seed_doctors([
        Doctor(id=1, full_name="Иванова Мария Петровна", specialty="Терапевт", is_active=True)
    ])
    return mock


@pytest.fixture
def schedule():
    # Возвращаем заглушку, так как для тестов сценариев нам не нужен реальный Excel
    # Мы будем мокать метод get_free_slots
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
def booking_scenario(mis, schedule):
    return BookingScenario(mis=mis, schedule=schedule)


@pytest.fixture
def cancel_scenario(mis):
    return CancelScenario(mis=mis)


@pytest.fixture
def transfer_scenario(mis):
    return TransferScenario(mis=mis)


# ─────────────────────────────────────────────────────────────
# Тесты для BookingScenario (Запись)
# ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_booking_adult_success(booking_scenario, mis):
    """Взрослый пациент успешно записывается."""
    state = DialogState(call_id="test1", step=STEP_GREETING)

    # 1. Приветствие
    r1 = await booking_scenario.handle(state, raw_text="Привет")
    assert r1.step == STEP_ASK_NAME

    # 2. Ввод ФИО и ДР (взрослый, 1985 год)
    parsed = ParsedUtterance(last_name="Иванов", first_name="Пётр", birth_date="17.03.1985")
    r2 = await booking_scenario.handle(state, parsed=parsed, raw_text="Иванов Пётр 17.03.1985")
    assert r2.step == STEP_ASK_SPECIALTY
    assert "Спасибо" in r2.reply

    # 3. Выбор специальности
    parsed_spec = ParsedUtterance(specialty="Терапевт")
    r3 = await booking_scenario.handle(state, parsed=parsed_spec, raw_text="К терапевту")
    assert r3.step == STEP_OFFER_SLOTS
    assert "варианты" in r3.reply.lower()

    # 4. Выбор слота
    r4 = await booking_scenario.handle(state, raw_text="Первый")
    assert r4.step == STEP_CONFIRM

    # 5. Подтверждение
    r5 = await booking_scenario.handle(state, raw_text="Да")
    assert r5.step == STEP_BOOKED
    assert r5.booked is True


@pytest.mark.asyncio
async def test_booking_child_needs_representative(booking_scenario):
    """Ребенок (2015 г.р.) должен спросить представителя."""
    state = DialogState(call_id="test2", step=STEP_GREETING)

    # Ввод данных ребенка
    parsed = ParsedUtterance(last_name="Смирнова", first_name="Алиса", birth_date="12.03.2015")
    r = await booking_scenario.handle(state, parsed=parsed, raw_text="Смирнова Алиса 12.03.2015")

    # Проверяем, что бот не пошел дальше, а запросил представителя
    assert state.step == "check_representative"
    assert "представител" in r.reply.lower() or "ребенок" in r.reply.lower()

    # Если представитель говорит "Да"
    r2 = await booking_scenario.handle(state, raw_text="Да, я мама")
    assert r2.step == STEP_ASK_SPECIALTY


# ─────────────────────────────────────────────────────────────
# Тесты для CancelScenario (Отмена)
# ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cancel_offers_reschedule(cancel_scenario, mis):
    """При отмене бот сначала предлагает перенос (по регламенту)."""
    # Создаем клиента и запись в моке
    client, _ = await mis.register_client(ClientCreate(last_name="Петров", first_name="Иван", birth_date="01.01.1990"))
    await mis.create_visit(VisitCreate(client_id=client.id, doctor_id=1, scheduled_at="20.10.2026 10:00"))

    state = DialogState(call_id="test3", step=STEP_GREETING)

    # Ввод ФИО
    parsed = ParsedUtterance(last_name="Петров", first_name="Иван", birth_date="01.01.1990")
    r = await cancel_scenario.handle(state, parsed=parsed, raw_text="Петров Иван 01.01.1990")

    # Бот должен предложить перенос, а не сразу отменить
    assert state.step == "cancel_offer_reschedule"
    assert "перенести" in r.reply.lower()

    # Пациент говорит "Отменить"
    r2 = await cancel_scenario.handle(state, raw_text="Нет, отменить")
    assert state.step == "cancel_confirm"
    assert "подтверждаете" in r2.reply.lower()


# ─────────────────────────────────────────────────────────────
# Тесты для TransferScenario (Перенос)
# ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_transfer_handoff_to_booking(transfer_scenario, mis):
    """При переносе старая запись не отменяется сразу, а передается в booking."""
    # Создаем старую запись
    client, _ = await mis.register_client(ClientCreate(last_name="Сидоров", first_name="Олег", birth_date="05.05.1980"))
    old_visit = await mis.create_visit(VisitCreate(client_id=client.id, doctor_id=1, scheduled_at="20.10.2026 10:00"))

    state = DialogState(call_id="test4", step=STEP_GREETING)

    # Ввод ФИО
    parsed = ParsedUtterance(last_name="Сидоров", first_name="Олег", birth_date="05.05.1980")
    r = await transfer_scenario.handle(state, parsed=parsed, raw_text="Сидоров Олег 05.05.1980")
    assert state.step == "reschedule_confirm"

    # Пациент подтверждает перенос
    r2 = await transfer_scenario.handle(state, raw_text="Да")

    # Проверяем, что сценарий передал эстафету (state.scenario = "booking")
    assert state.scenario == "booking"
    assert state.is_reschedule is True
    assert state.old_visit_id == old_visit.id

    # Проверяем, что старая запись еще НЕ отменена в МИС
    visits = await mis.list_visits(client_id=client.id)
    assert visits[0].status == VisitStatus.PLANNED  # Все еще запланирована!