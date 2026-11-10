
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI

from app.api import demo, health, voice_routes
from app.config import settings
from app.core.logging import setup_logging
from app.redis_client import check_redis_connection, close_redis
from app.voice.audio_server import AudioServer


setup_logging("DEBUG" if settings.DEBUG else "INFO")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Запуск фоновых задач и освобождение ресурсов приложения."""

    # Проверяем Redis до запуска фоновых задач.
    await check_redis_connection()

    audio_task: asyncio.Task[None] | None = None

    try:
        audio_server = AudioServer()
        audio_task = asyncio.create_task(
            audio_server.start(),
            name="audio-server",
        )

        yield

    finally:
        # Останавливаем AudioServer при завершении приложения.
        if audio_task is not None:
            audio_task.cancel()

            try:
                await audio_task
            except asyncio.CancelledError:
                pass
            except Exception:
                # Ошибка фоновой задачи не должна помешать
                # освобождению Redis-соединения.
                pass

        await close_redis()


app = FastAPI(
    title=settings.APP_NAME,
    lifespan=lifespan,
)

app.include_router(health.router)
app.include_router(demo.router)
app.include_router(voice_routes.router)


@app.get("/")
async def root() -> dict[str, str]:
    """Проверка доступности приложения."""
    return {
        "status": "ok",
        "app": settings.APP_NAME,
    }
