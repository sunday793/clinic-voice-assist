"""Тесты контроля загруженности операторов."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.services.load_manager import (
    MAX_CONCURRENT_CALLS,
    REDIS_KEY,
    LoadManager,
)


@pytest.fixture
def fake_redis():
    """Фейковый Redis-клиент для тестов."""
    store: dict[str, int] = {}

    class FakeRedis:
        async def incr(self, key: str) -> int:
            store[key] = store.get(key, 0) + 1
            return store[key]

        async def decr(self, key: str) -> int:
            store[key] = max(0, store.get(key, 0) - 1)
            return store[key]

        async def get(self, key: str) -> str:
            return str(store.get(key, 0))

        async def set(self, key: str, val) -> None:
            store[key] = int(val)

        async def expire(self, key: str, ttl: int) -> None:
            pass

    return FakeRedis()


async def test_load_manager_allows_calls(fake_redis):
    """Пока лимит не превышен — слоты выделяются."""
    lm = LoadManager()
    with patch("app.services.load_manager.get_redis", AsyncMock(return_value=fake_redis)):
        for _ in range(MAX_CONCURRENT_CALLS):
            assert await lm.acquire() is True
        assert await lm.is_overloaded() is True


async def test_load_manager_blocks_when_overloaded(fake_redis):
    """После превышения лимита — новый звонок отклоняется."""
    lm = LoadManager()
    with patch("app.services.load_manager.get_redis", AsyncMock(return_value=fake_redis)):
        # Занимаем все слоты
        for _ in range(MAX_CONCURRENT_CALLS):
            await lm.acquire()

        # Следующий должен быть отклонен
        assert await lm.acquire() is False
        assert await lm.is_overloaded() is True


async def test_load_manager_release(fake_redis):
    """Освобождение слота уменьшает счетчик."""
    lm = LoadManager()
    with patch("app.services.load_manager.get_redis", AsyncMock(return_value=fake_redis)):
        await lm.acquire()
        await lm.acquire()
        await lm.release()

        assert await fake_redis.get(REDIS_KEY) == "1"


async def test_load_manager_graceful_degradation():
    """Если Redis недоступен — менеджер не падает, считает слот доступным."""
    async def broken_redis():
        raise ConnectionError("Redis is down")

    lm = LoadManager()
    with patch("app.services.load_manager.get_redis", broken_redis):
        # Не должно упасть
        assert await lm.acquire() is True
        # И не считает перегрузкой
        assert await lm.is_overloaded() is False