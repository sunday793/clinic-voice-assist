"""Сквозной тест C-01: запись к терапевту.
Включает проверку записи взрослого и защиту от записи ребенка без представителя.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config import settings
from app.services.dialog import (
    DialogEngine,
    STEP_ASK_NAME,
    STEP_ASK_SPECIALTY,
    STEP_BOOKED,
    STEP_CONFIRM,
    STEP_OFFER_SLOTS,
)
from app.services.mis_client_mock import MISClientMock
from app.services.schedule import ScheduleService
from app.schemas.mis import Doctor


pytestmark = pytest.mark.skipif(
    not Path(settings.SCHEDULE_XLSX_PATH).exists(),
    reason=f"Нет файла {settings.SCHEDULE_XLSX_PATH} — тест C-01 пропущен",
)


# Фикстуры
@pytest.fixture
def mis_with_doctors() -> MISClientMock:
    """Мок MIS с врачами из Excel (терапевты)."""
    mis = MISClientMock()
    mis.seed_doctors(
        [
            Doctor(
                id=1,
                full_name="Верещагин Андрей Михайлович",
                specialty="Терапевт",
                is_active=True,
            ),
            Doctor(
                id=2,
                full_name="Ковалёва Людмила Петровна",
                specialty="Терапевт",
                is_active=True,
            ),
            Doctor(
                id=3,
                full_name="Дорохов Сергей Анатольевич",
                specialty="Терапевт",
                is_active=True,
            ),
        ]
    )
    return mis


@pytest.fixture
def schedule() -> ScheduleService:
    svc = ScheduleService()
    svc.load()
    return svc


@pytest.fixture
def engine(
    mis_with_doctors: MISClientMock,
    schedule: ScheduleService,
) -> DialogEngine:
    return DialogEngine(
        mis=mis_with_doctors,
        schedule=schedule,
        today=settings.SCHEDULE_TODAY,
    )


# ТЕСТ 1: Взрослый пациент (успешная запись)
async def test_c01_adult_booking_flow(engine: DialogEngine) -> None:
    call_id = "test-adult-001"

    # 1. Приветствие
    r1 = await engine.handle(call_id, "Здравствуйте")
    assert r1.step == STEP_ASK_NAME
    assert "фамилию" in r1.reply.lower()

    # 2. ФИО + дата рождения (взрослый)
    r2 = await engine.handle(call_id, "Иванов Пётр, 17.03.1985")
    assert r2.step == STEP_ASK_SPECIALTY
    assert "Пётр" in r2.reply or "Иванов" in r2.reply

    # 3. Специальность
    r3 = await engine.handle(call_id, "К терапевту")
    assert r3.step == STEP_OFFER_SLOTS
    assert "вариант" in r3.reply.lower() or "есть" in r3.reply.lower()

    # 4. Выбор слота
    r4 = await engine.handle(call_id, "Первый")
    assert r4.step == STEP_CONFIRM
    assert "подтвержда" in r4.reply.lower()

    # 5. Подтверждение
    r5 = await engine.handle(call_id, "Да")
    assert r5.step == STEP_BOOKED
    assert r5.booked is True
    assert r5.visit_id is not None
    assert "готово" in r5.reply.lower()


# ТЕСТ 2: Ребёнок (должен спросить представителя)
async def test_c01_child_booking_requires_representative(engine: DialogEngine) -> None:
    call_id = "test-child-001"

    # 1. Приветствие
    await engine.handle(call_id, "Здравствуйте")

    # 2. Вводим данные ребёнка (2015 год — ему 10-11 лет)
    r2 = await engine.handle(call_id, "Смирнова Алиса, 12.03.2015")

    # Бот должен спросить про представителя (шаг check_representative)
    assert r2.step == "check_representative"
    assert "представител" in r2.reply.lower() or "ребенок" in r2.reply.lower()

    # 3. Пациент говорит «Да» (я родитель)
    r3 = await engine.handle(call_id, "Да, я мама")

    # После подтверждения бот переходит к выбору специальности
    assert r3.step == STEP_ASK_SPECIALTY
    assert "какому врачу" in r3.reply.lower()

    # 4. Специальность и слот
    r4 = await engine.handle(call_id, "К педиатру")
    assert r4.step == STEP_OFFER_SLOTS

    # 5. Выбор и подтверждение
    r5 = await engine.handle(call_id, "Первый")
    assert r5.step == STEP_CONFIRM

    r6 = await engine.handle(call_id, "Да")
    assert r6.step == STEP_BOOKED
    assert r6.booked is True