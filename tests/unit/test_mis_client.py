"""Unit-тесты MIS-клиента на моке.

Проверяем поведение, приближенное к реальному стенду:
    - регистрация клиента: created=True при первом вызове, False при повторе;
    - поиск клиента по ФИО и дате рождения;
    - создание записи к активному врачу;
    - отказ при записи к деактивированному врачу (400);
    - отказ при изменении отменённой записи (409);
    - отмена записи переводом статуса в «Отменена»;
    - создание заявки на соединение со специалистом.
"""

from __future__ import annotations

import pytest

from app.core.exceptions import MISConflict, MISNotFound, MISValidationError
from app.schemas.mis import (
    ClientCreate,
    Doctor,
    EscalationCreate,
    VisitCreate,
    VisitStatus,
    VisitUpdate,
)
from app.services.mis_client_mock import MISClientMock


# ─────────────────────────────────────────────────────────────
# Фикстуры
# ─────────────────────────────────────────────────────────────


@pytest.fixture
def mock() -> MISClientMock:
    """Пустой мок с одним активным и одним деактивированным врачом."""
    client = MISClientMock()
    client.seed_doctors(
        [
            Doctor(
                id=1,
                full_name="Иванова Мария Петровна",
                specialty="терапевт",
                is_active=True,
            ),
            Doctor(
                id=2,
                full_name="Петров Иван Сергеевич",
                specialty="терапевт",
                is_active=False,
            ),
            Doctor(
                id=3,
                full_name="Сидорова Анна Львовна",
                specialty="кардиолог",
                is_active=True,
            ),
        ]
    )
    return client


@pytest.fixture
async def patient(mock: MISClientMock):
    """Зарегистрированный пациент Иванов Пётр 17.03.1985."""
    client, _ = await mock.register_client(
        ClientCreate(
            last_name="Иванов",
            first_name="Пётр",
            birth_date="17.03.1985",
        )
    )
    return client


# ─────────────────────────────────────────────────────────────
# Врачи
# ─────────────────────────────────────────────────────────────


async def test_list_doctors_all(mock: MISClientMock) -> None:
    doctors = await mock.list_doctors()
    assert len(doctors) == 3
    assert [d.id for d in doctors] == [1, 2, 3]


async def test_list_doctors_by_specialty(mock: MISClientMock) -> None:
    doctors = await mock.list_doctors(specialty="терапевт")
    assert len(doctors) == 2
    assert all(d.specialty == "терапевт" for d in doctors)


async def test_list_doctors_specialty_case_insensitive(mock: MISClientMock) -> None:
    doctors = await mock.list_doctors(specialty="ТЕРАПЕВТ")
    assert len(doctors) == 2


# ─────────────────────────────────────────────────────────────
# Клиенты
# ─────────────────────────────────────────────────────────────


async def test_register_client_created(mock: MISClientMock) -> None:
    client, created = await mock.register_client(
        ClientCreate(
            last_name="Иванов",
            first_name="Пётр",
            birth_date="17.03.1985",
        )
    )
    assert created is True
    assert client.id == 1
    assert client.last_name == "Иванов"


async def test_register_client_duplicate_returns_existing(
    mock: MISClientMock,
) -> None:
    first, created_first = await mock.register_client(
        ClientCreate(
            last_name="Иванов",
            first_name="Пётр",
            birth_date="17.03.1985",
        )
    )
    second, created_second = await mock.register_client(
        ClientCreate(
            last_name="Иванов",
            first_name="Пётр",
            birth_date="17.03.1985",
        )
    )
    assert created_first is True
    assert created_second is False
    assert first.id == second.id


async def test_find_clients_by_name(mock: MISClientMock, patient) -> None:
    found = await mock.find_clients(last_name="Иванов", first_name="Пётр")
    assert len(found) == 1
    assert found[0].id == patient.id


async def test_find_clients_empty(mock: MISClientMock) -> None:
    found = await mock.find_clients(last_name="Несуществующий")
    assert found == []


async def test_get_client_not_found(mock: MISClientMock) -> None:
    with pytest.raises(MISNotFound):
        await mock.get_client(999)


# ─────────────────────────────────────────────────────────────
# Записи на приём
# ─────────────────────────────────────────────────────────────


async def test_create_visit_ok(mock: MISClientMock, patient) -> None:
    visit = await mock.create_visit(
        VisitCreate(
            client_id=patient.id,
            doctor_id=1,
            scheduled_at="20.10.2026 10:30",
        )
    )
    assert visit.id == 1
    assert visit.client_id == patient.id
    assert visit.doctor.id == 1
    assert visit.status == VisitStatus.PLANNED
    assert visit.created_at == visit.updated_at


async def test_create_visit_unknown_client(mock: MISClientMock) -> None:
    with pytest.raises(MISValidationError):
        await mock.create_visit(
            VisitCreate(
                client_id=999,
                doctor_id=1,
                scheduled_at="20.10.2026 10:30",
            )
        )


async def test_create_visit_unknown_doctor(
    mock: MISClientMock,
    patient,
) -> None:
    with pytest.raises(MISValidationError):
        await mock.create_visit(
            VisitCreate(
                client_id=patient.id,
                doctor_id=999,
                scheduled_at="20.10.2026 10:30",
            )
        )


async def test_create_visit_inactive_doctor(
    mock: MISClientMock,
    patient,
) -> None:
    with pytest.raises(MISValidationError, match="деактивированного"):
        await mock.create_visit(
            VisitCreate(
                client_id=patient.id,
                doctor_id=2,
                scheduled_at="20.10.2026 10:30",
            )
        )


async def test_get_visit_not_found(mock: MISClientMock) -> None:
    with pytest.raises(MISNotFound):
        await mock.get_visit(999)


async def test_list_visits_by_client(
    mock: MISClientMock,
    patient,
) -> None:
    await mock.create_visit(
        VisitCreate(
            client_id=patient.id,
            doctor_id=1,
            scheduled_at="20.10.2026 10:30",
        )
    )
    await mock.create_visit(
        VisitCreate(
            client_id=patient.id,
            doctor_id=3,
            scheduled_at="21.10.2026 14:00",
        )
    )
    visits = await mock.list_visits(client_id=patient.id)
    assert len(visits) == 2


async def test_cancel_visit(mock: MISClientMock, patient) -> None:
    visit = await mock.create_visit(
        VisitCreate(
            client_id=patient.id,
            doctor_id=1,
            scheduled_at="20.10.2026 10:30",
        )
    )
    cancelled = await mock.update_visit(
        visit.id, VisitUpdate(status=VisitStatus.CANCELLED)
    )
    assert cancelled.status == VisitStatus.CANCELLED


async def test_cancel_visit_idempotent(
    mock: MISClientMock,
    patient,
) -> None:
    """Повторная отмена уже отменённой записи не падает."""
    visit = await mock.create_visit(
        VisitCreate(
            client_id=patient.id,
            doctor_id=1,
            scheduled_at="20.10.2026 10:30",
        )
    )
    await mock.update_visit(visit.id, VisitUpdate(status=VisitStatus.CANCELLED))
    again = await mock.update_visit(
        visit.id, VisitUpdate(status=VisitStatus.CANCELLED)
    )
    assert again.status == VisitStatus.CANCELLED


async def test_update_cancelled_visit_conflict(
    mock: MISClientMock,
    patient,
) -> None:
    """Перенос отменённой записи → 409."""
    visit = await mock.create_visit(
        VisitCreate(
            client_id=patient.id,
            doctor_id=1,
            scheduled_at="20.10.2026 10:30",
        )
    )
    await mock.update_visit(visit.id, VisitUpdate(status=VisitStatus.CANCELLED))

    with pytest.raises(MISConflict):
        await mock.update_visit(
            visit.id, VisitUpdate(scheduled_at="25.10.2026 11:00")
        )


async def test_update_visit_to_inactive_doctor(
    mock: MISClientMock,
    patient,
) -> None:
    visit = await mock.create_visit(
        VisitCreate(
            client_id=patient.id,
            doctor_id=1,
            scheduled_at="20.10.2026 10:30",
        )
    )
    with pytest.raises(MISValidationError, match="деактивированного"):
        await mock.update_visit(visit.id, VisitUpdate(doctor_id=2))


# ─────────────────────────────────────────────────────────────
# Заявки на соединение со специалистом
# ─────────────────────────────────────────────────────────────


async def test_create_escalation_ok(mock: MISClientMock, patient) -> None:
    esc = await mock.create_escalation(
        EscalationCreate(
            client_id=patient.id,
            description="Боль в груди, пациент просит срочную консультацию",
        )
    )
    assert esc.id == 1
    assert esc.client_id == patient.id
    assert "Боль в груди" in esc.description


async def test_create_escalation_unknown_client(mock: MISClientMock) -> None:
    with pytest.raises(MISValidationError):
        await mock.create_escalation(
            EscalationCreate(client_id=999, description="...")
        )