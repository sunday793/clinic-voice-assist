
"""Настройка логирования приложения в JSON-подобном формате."""

from __future__ import annotations

import logging
from typing import Final


DEFAULT_LOG_LEVEL: Final[str] = "INFO"

LOG_FORMAT: Final[str] = (
    '{"time":"%(asctime)s",'
    '"level":"%(levelname)s",'
    '"logger":"%(name)s",'
    '"msg":"%(message)s"}'
)


def setup_logging(level: str = DEFAULT_LOG_LEVEL) -> None:
    """Настроить базовое логирование приложения.

    Args:
        level: Уровень логирования, например DEBUG, INFO, WARNING или ERROR.

    Raises:
        ValueError: Если указан неизвестный уровень логирования.
    """
    normalized_level = level.strip().upper()

    numeric_level = logging.getLevelNamesMapping().get(normalized_level)
    if not isinstance(numeric_level, int):
        raise ValueError(
            f"Неизвестный уровень логирования: {level!r}. "
            "Допустимые значения: DEBUG, INFO, WARNING, ERROR, CRITICAL."
        )

    logging.basicConfig(
        level=numeric_level,
        format=LOG_FORMAT,
        force=False,
    )
