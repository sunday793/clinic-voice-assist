"""Менеджер загруженности операторов.

Использует Redis для подсчёта активных звонков.
Если лимит превышен — предлагает обратный звонок.
"""

from __future__ import annotations

import logging

from app.redis_client import get_redis

logger = logging.getLogger(__name__)

MAX_CONCURRENT_CALLS = 5
REDIS_KEY = "active_operators"


ACQUIRE_SCRIPT = """
local current = tonumber(redis.call('GET', KEYS[1]) or '0')
local maximum = tonumber(ARGV[1])

if current >= maximum then
    return 0
end

redis.call('INCR', KEYS[1])
return 1
"""


RELEASE_SCRIPT = """
local current = tonumber(redis.call('GET', KEYS[1]) or '0')

if current <= 0 then
    redis.call('SET', KEYS[1], 0)
    return 0
end

return redis.call('DECR', KEYS[1])
"""


class LoadManager:
    """Управление нагрузкой на операторов через Redis."""

    async def _get_redis(self):
        return await get_redis()

    async def acquire(self) -> bool:
        """Занять слот, если нагрузка ниже лимита."""
        try:
            redis = await self._get_redis()

            acquired = await redis.eval(
                ACQUIRE_SCRIPT,
                1,
                REDIS_KEY,
                MAX_CONCURRENT_CALLS,
            )

            if not acquired:
                logger.warning(
                    "Лимит одновременных звонков достигнут (%s)",
                    MAX_CONCURRENT_CALLS,
                )
                return False

            logger.info("Слот занят")
            return True

        except Exception:
            logger.exception("Ошибка Redis при занятии слота")
            # Безопасный режим: не разрешаем звонок без проверки лимита.
            return False

    async def release(self) -> None:
        """Освободить ранее занятый слот."""
        try:
            redis = await self._get_redis()

            current = await redis.eval(
                RELEASE_SCRIPT,
                1,
                REDIS_KEY,
            )

            logger.info(
                "Слот освобождён; осталось активных звонков: %s",
                current,
            )

        except Exception:
            logger.exception("Ошибка Redis при освобождении слота")

    async def is_overloaded(self) -> bool:
        """Проверить перегрузку без занятия слота."""
        try:
            redis = await self._get_redis()
            current = int(await redis.get(REDIS_KEY) or 0)
            return current >= MAX_CONCURRENT_CALLS

        except Exception:
            logger.exception("Ошибка Redis при проверке нагрузки")
            # При недоступном Redis состояние нагрузки неизвестно.
            return True


_load_manager: LoadManager | None = None


def get_load_manager() -> LoadManager:
    global _load_manager

    if _load_manager is None:
        _load_manager = LoadManager()

    return _load_manager