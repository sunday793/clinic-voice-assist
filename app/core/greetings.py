
"""Приветствия по времени суток в GMT+4."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Final, Literal

GMT4: Final = timezone(timedelta(hours=4))

TimeOfDay = Literal["morning", "day", "evening", "night"]

GREETINGS: Final[dict[TimeOfDay, str]] = {
    "morning": "Доброе утро",
    "day": "Добрый день",
    "evening": "Добрый вечер",
    "night": "Доброй ночи",
}


def _normalize_datetime(now: datetime | None = None) -> datetime:
    """Вернуть дату и время в часовом поясе GMT+4."""
    if now is None:
        return datetime.now(GMT4)

    if now.tzinfo is None or now.utcoffset() is None:
        # Наивный datetime считаем временем GMT+4.
        return now.replace(tzinfo=GMT4)

    return now.astimezone(GMT4)


def get_time_of_day(now: datetime | None = None) -> TimeOfDay:
    """Вернуть период суток по локальному времени GMT+4."""
    hour = _normalize_datetime(now).hour

    if 5 <= hour < 12:
        return "morning"
    if 12 <= hour < 18:
        return "day"
    if 18 <= hour < 23:
        return "evening"
    return "night"


def get_greeting(now: datetime | None = None) -> str:
    """Вернуть краткое приветствие по времени суток GMT+4."""
    return GREETINGS[get_time_of_day(now)]


def get_greeting_reply(now: datetime | None = None) -> str:
    """Вернуть готовое приветствие для ответа на входящий звонок."""
    tod = get_time_of_day(now)

    if tod == "morning":
        greeting = "Доброе утро"
    elif tod == "day":
        greeting = "Добрый день"
    elif tod == "evening":
        greeting = "Добрый вечер"
    else:
        return (
            "Доброй ночи! Это голосовой ассистент поликлиники номер семь. "
            "Разговор записывается. "
            "Поликлиника сейчас закрыта. "
            "Чем могу помочь?"
        )

    return (
        f"{greeting}! Это голосовой ассистент поликлиники номер семь. "
        "Разговор записывается. "
        "Чем могу помочь?"
    )
