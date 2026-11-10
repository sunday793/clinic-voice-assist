
from __future__ import annotations

import redis.asyncio as redis
from redis.asyncio import Redis

from app.config import settings


redis_client: Redis = redis.from_url(
    settings.REDIS_URL,
    decode_responses=True,
    socket_connect_timeout=5,
    socket_timeout=5,
    health_check_interval=30,
)


async def get_redis() -> Redis:
    """Вернуть общий Redis-клиент приложения."""
    return redis_client


async def check_redis_connection() -> None:
    """Проверить доступность Redis."""
    await redis_client.ping()


async def close_redis() -> None:
    """Закрыть Redis-соединение при остановке приложения."""
    await redis_client.aclose()
