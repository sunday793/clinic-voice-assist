
"""Health-check endpoint для проверки доступности приложения."""

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get(
    "/health",
    response_model=dict[str, str],
    summary="Проверить доступность приложения",
    description="Возвращает статус приложения, если HTTP API отвечает.",
)
async def health() -> dict[str, str]:
    """Проверить, что приложение отвечает на HTTP-запросы."""
    return {"status": "healthy"}
