"""
Кастомные исключения приложения.

Иерархия:
    AppError
    ├── MISError                    — общий предок ошибок MIS-клиента
    │   ├── MISUnauthorized         — 401, недействительный токен
    │   ├── MISValidationError      — 400, неверные данные запроса
    │   ├── MISNotFound             — 404, объект не найден
    │   ├── MISConflict             — 409, недопустимое изменение
    │   ├── MISRateLimited          — 429, превышен лимит запросов
    │   └── MISServerError          — 500, неисправность стенда
    └── DialogError                 — ошибки диалогового движка
"""

from __future__ import annotations


class AppError(Exception):
    """Базовое исключение приложения."""

    def __init__(self, message: str = "") -> None:
        self.message: str = message
        super().__init__(message)


# ---------------------------------------------------------------------
# MIS — интеграция со стендом «Регистратура»
# ---------------------------------------------------------------------


class MISError(AppError):
    """Базовая ошибка MIS-клиента.

    Атрибуты:
        code: Машиночитаемый код ошибки из ответа стенда.
        status_code: HTTP-статус ответа стенда.
        message: Описание ошибки.
    """

    def __init__(
        self,
        message: str = "",
        *,
        code: str | None = None,
        status_code: int | None = None,
    ) -> None:
        if status_code is not None and not 100 <= status_code <= 599:
            raise ValueError("status_code должен быть в диапазоне 100–599")

        self.code: str | None = code
        self.status_code: int | None = status_code

        super().__init__(message)


class MISUnauthorized(MISError):
    """401 — токен команды отсутствует, неизвестен или отозван."""


class MISValidationError(MISError):
    """400 — неверные данные запроса или недопустимый параметр."""


class MISNotFound(MISError):
    """404 — запрошенный объект или метод не найден."""


class MISConflict(MISError):
    """409 — текущее состояние объекта не допускает операцию."""


class MISRateLimited(MISError):
    """429 — превышен лимит запросов.

    retry_after — число секунд до возможного повтора запроса,
    полученное из заголовка Retry-After.
    """

    def __init__(
        self,
        message: str = "",
        *,
        retry_after: int | None = None,
        code: str | None = None,
        status_code: int | None = 429,
    ) -> None:
        if retry_after is not None and retry_after < 0:
            raise ValueError("retry_after не может быть отрицательным")

        self.retry_after: int | None = retry_after

        super().__init__(
            message,
            code=code,
            status_code=status_code,
        )


class MISServerError(MISError):
    """Ошибка сервера MIS или ответ, не классифицированный иначе."""


# ---------------------------------------------------------------------
# Диалоговый движок
# ---------------------------------------------------------------------


class DialogError(AppError):
    """Базовая ошибка диалогового движка."""


class RedFlagDetected(DialogError):
    """Обнаружен красный флаг — требуется эскалация.

    category — категория красного флага согласно регламенту.
    """

    def __init__(
        self,
        message: str = "",
        *,
        category: str | None = None,
    ) -> None:
        self.category: str | None = category
        super().__init__(message)
