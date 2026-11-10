"""Тесты кэширования справочника врачей в ScheduleService."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.schemas.mis import Doctor
from app.services.mis_client_mock import MISClientMock
from app.services.schedule import ScheduleService


# ─────────────────────────────────────────────────────────────
# Фикстуры
# ─────────────────────────────────────────────────────────────

@pytest.fixture
def fake_redis():
    """Фейковый Redis-клиент (словарь в памяти)."""
    store: dict[str, str] = {}

    class FakeRedis:
        async def get(self, key: str) -> str | None:
            return store.get(key)

        async def set(self, key: str, value, ex: int | None = None) -> None:
            store[key] = str(value)

        def clear(self) -> None:
            store.clear()

    return FakeRedis()


@pytest.fixture
def schedule_with_excel(tmp_path: Path) -> ScheduleService:
    """Создаём временный Excel с одним врачом, чтобы не зависеть от data/."""
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Слоты"

    # Шапка (12 колонок)
    ws.append([
        "ID слота", "Дата", "День", "ID врача", "ФИО врача",
        "Специальность", "Отделение", "Кабинет", "Начало",
        "Окончание", "Мин", "Статус",
    ])
    # Один слот
    ws.append([
        "S00001", "15.10.2026", "Чт", "D001", "Верещагин Андрей Михайлович",
        "Терапевт", "Терапевтическое", 101, "09:00",
        "09:20", 20, "Свободен",
    ])

    xlsx_path = tmp_path / "schedule.xlsx"
    wb.save(xlsx_path)
    wb.close()

    return ScheduleService(str(xlsx_path))


@pytest.fixture
def mis_with_doctor() -> MISClientMock:
    """Мок МИС с одним активным врачом."""
    mis = MISClientMock()
    mis.seed_doctors([
        Doctor(
            id=1,
            full_name="Верещагин Андрей Михайлович",
            specialty="Терапевт",
            is_active=True,
        ),
    ])
    return mis


# ─────────────────────────────────────────────────────────────
# Тесты
# ─────────────────────────────────────────────────────────────

async def test_l1_cache_avoids_mis_call(
    schedule_with_excel: ScheduleService,
    mis_with_doctor: MISClientMock,
    fake_redis,
) -> None:
    """Повторный вызов resolve_doctor_id не идёт в МИС (L1-кэш)."""
    # Патчим get_redis, чтобы использовать фейковый Redis
    with patch("app.redis_client.get_redis", AsyncMock(return_value=fake_redis)):
        # Первый вызов — идёт в МИС
        doctor_id_1 = await schedule_with_excel.resolve_doctor_id("D001", mis_with_doctor)
        assert doctor_id_1 == 1

        # Считаем, сколько раз МИС дёргали
        mis_with_doctor.list_doctors = AsyncMock(wraps=mis_with_doctor.list_doctors)

        # Второй вызов — должен взять из L1-кэша (в память)
        doctor_id_2 = await schedule_with_excel.resolve_doctor_id("D001", mis_with_doctor)
        assert doctor_id_2 == 1

        # МИС не должна быть вызвана повторно
        mis_with_doctor.list_doctors.assert_not_called()


async def test_l2_cache_saves_to_redis(
    schedule_with_excel: ScheduleService,
    mis_with_doctor: MISClientMock,
    fake_redis,
) -> None:
    """После вызова resolve_doctor_id данные сохраняются в Redis."""
    with patch("app.redis_client.get_redis", AsyncMock(return_value=fake_redis)):
        await schedule_with_excel.resolve_doctor_id("D001", mis_with_doctor)

        # Проверяем, что в Redis появился ключ
        cached = await fake_redis.get("doctor_id:D001")
        assert cached == "1"


async def test_l2_cache_hit_skips_mis(
    schedule_with_excel: ScheduleService,
    mis_with_doctor: MISClientMock,
    fake_redis,
) -> None:
    """Если данные есть в Redis, МИС не вызывается."""
    # Предварительно кладём в Redis
    await fake_redis.set("doctor_id:D001", "42")

    with patch("app.redis_client.get_redis", AsyncMock(return_value=fake_redis)):
        doctor_id = await schedule_with_excel.resolve_doctor_id("D001", mis_with_doctor)
        # Должны взять из Redis (42), а не из МИС (1)
        assert doctor_id == 42


async def test_graceful_degradation_on_redis_failure(
    schedule_with_excel: ScheduleService,
    mis_with_doctor: MISClientMock,
) -> None:
    """Если Redis упал, resolve_doctor_id всё равно работает через МИС."""
    # Патчим get_redis, чтобы он бросал исключение
    async def broken_redis():
        raise ConnectionError("Redis is down")

    with patch("app.redis_client.get_redis", broken_redis):
        # Не должно упасть, а должно вернуть ID из МИС
        doctor_id = await schedule_with_excel.resolve_doctor_id("D001", mis_with_doctor)
        assert doctor_id == 1


async def test_clear_doctor_cache(
    schedule_with_excel: ScheduleService,
    mis_with_doctor: MISClientMock,
    fake_redis,
) -> None:
    """clear_doctor_cache очищает L1-кэш."""
    with patch("app.redis_client.get_redis", AsyncMock(return_value=fake_redis)):
        await schedule_with_excel.resolve_doctor_id("D001", mis_with_doctor)
        # В кэше что-то есть
        assert len(schedule_with_excel._doctor_id_cache) > 0

        # Очищаем
        schedule_with_excel.clear_doctor_cache()

        # Кэш пуст
        assert len(schedule_with_excel._doctor_id_cache) == 0