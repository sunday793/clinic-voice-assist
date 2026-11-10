
"""Простой NLU-парсер на regex для MVP + GigaChat."""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from openai import AsyncOpenAI

from app.services.inspections.age import fix_year_century

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Специальности
# ---------------------------------------------------------------------------

SPECIALTIES = [
    "терапевт",
    "педиатр",
    "кардиолог",
    "гастроэнтеролог",
    "невролог",
    "эндокринолог",
    "нефролог",
    "аллерголог-иммунолог",
    "дерматовенеролог",
    "лор",
    "офтальмолог",
    "пульмонолог",
    "ревматолог",
    "уролог",
    "хирург",
    "травматолог",
    "ортопед",
    "флеболог",
    "акушер-гинеколог",
    "гинеколог",
    "репродуктолог",
    "маммолог",
    "стоматолог-терапевт",
    "стоматолог-хирург",
    "стоматолог-ортопед",
    "ортодонт",
]

SPECIALTY_ALIASES: dict[str, str] = {
    "лoр": "лор",  # Вариант с латинской o.
    "лор-врач": "лор",
    "ухо горло нос": "лор",
    "ухо-горло-нос": "лор",
    "гинеколог": "акушер-гинеколог",
    "стоматолог": "стоматолог-терапевт",
    "зубной": "стоматолог-терапевт",
    "зубной врач": "стоматолог-терапевт",
    "глазной": "офтальмолог",
    "глазной врач": "офтальмолог",
    "окулист": "офтальмолог",
    "сердечный": "кардиолог",
    "сердечный врач": "кардиолог",
    "желудок": "гастроэнтеролог",
    "нервы": "невролог",
    "щитовидка": "эндокринолог",
    "почки": "нефролог",
    "аллергия": "аллерголог-иммунолог",
    "кожа": "дерматовенеролог",
    "легкие": "пульмонолог",
    "суставы": "ревматолог",
    "перелом": "травматолог",
    "спина": "ортопед",
    "вены": "флеболог",
    "беременность": "акушер-гинеколог",
    "ребенок": "педиатр",
    "дети": "педиатр",
}

_VALID_INTENTS = {
    "book",
    "cancel",
    "check",
    "greeting",
    "reschedule",
    "complaint",
    "unknown",
}

_MONTHS = {
    "январ": 1,
    "феврал": 2,
    "март": 3,
    "апрел": 4,
    "май": 5,
    "мая": 5,
    "июн": 6,
    "июл": 7,
    "август": 8,
    "сентябр": 9,
    "октябр": 10,
    "ноябр": 11,
    "декабр": 12,
}

_RELATIVE_DAYS = {
    "сегодня": 0,
    "завтра": 1,
    "послезавтра": 2,
}


# ---------------------------------------------------------------------------
# Результат разбора
# ---------------------------------------------------------------------------

@dataclass
class ParsedUtterance:
    intent: str = "unknown"
    last_name: str | None = None
    first_name: str | None = None
    middle_name: str | None = None
    birth_date: str | None = None
    specialty: str | None = None
    desired_date: str | None = None
    desired_time: str | None = None
    raw_text: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Регулярные выражения
# ---------------------------------------------------------------------------

_RE_GREETING = re.compile(
    r"\b(?:здравствуйте|добрый\s+(?:день|вечер|утро)|"
    r"привет|алло|доброе\s+утро)\b",
    re.IGNORECASE,
)

_RE_BOOK = re.compile(
    r"\b(?:запис\w*|запиш\w*|хочу\s+к|нужно\s+к|"
    r"можно\s+к|талон\w*|при[её]м\w*)\b",
    re.IGNORECASE,
)

_RE_CANCEL = re.compile(
    r"\b(?:отмен\w*|не\s+приду|не\s+смогу\s+прийти|"
    r"откажусь\s+от\s+записи)\b",
    re.IGNORECASE,
)

_RE_CHECK = re.compile(
    r"\b(?:провер\w*|узнать|когда\s+моя\s+запись|"
    r"моя\s+запись|статус\s+записи|на\s+когда\s+я\s+записан\w*)\b",
    re.IGNORECASE,
)

_RE_RESCHEDULE = re.compile(
    r"\b(?:перенес\w*|перезапиш\w*|"
    r"другой\s+день|поменять\s+время|"
    r"изменить\s+время|перенести\s+запись)\b",
    re.IGNORECASE,
)

_RE_COMPLAINT = re.compile(
    r"\b(?:жалоб\w*|пожалова\w*|нагруб\w*|безобрази\w*|"
    r"недоволен\w*|недовольн\w*)\b",
    re.IGNORECASE,
)

_RE_NAME_DIRECT = re.compile(
    r"\b([А-ЯЁ][а-яё-]+)\s+"
    r"([А-ЯЁ][а-яё-]+)"
    r"(?:\s+([А-ЯЁ][а-яё-]+))?\b"
)

# Поддерживаются даты с двух- и четырёхзначным годом.
_RE_BIRTH_NUM = re.compile(
    r"(?<!\d)(\d{1,2})[./-](\d{1,2})[./-](\d{2}|\d{4})(?!\d)"
)

_RE_BIRTH_TEXT = re.compile(
    r"\b(\d{1,2})\s+"
    r"(январ\w*|феврал\w*|март\w*|апрел\w*|"
    r"ма[йя]\w*|июн\w*|июл\w*|август\w*|"
    r"сентябр\w*|октябр\w*|ноябр\w*|декабр\w*)"
    r"\s+(?:года?\s*)?(\d{2}|\d{4})(?:\s*года?)?\b",
    re.IGNORECASE,
)

_RE_DATE_NUM = re.compile(
    r"(?<!\d)(\d{1,2})[./-](\d{1,2})[./-](\d{2}|\d{4})(?!\d)"
)

_RE_DATE_TEXT = re.compile(
    r"\b(\d{1,2})\s+"
    r"(январ\w*|феврал\w*|март\w*|апрел\w*|"
    r"ма[йя]\w*|июн\w*|июл\w*|август\w*|"
    r"сентябр\w*|октябр\w*|ноябр\w*|декабр\w*)"
    r"(?:\s+(\d{4}))?\b",
    re.IGNORECASE,
)

_RE_TIME = re.compile(
    r"\b(?:в\s*)?(\d{1,2})[:.](\d{2})\b"
    r"|\bв\s+(\d{1,2})\s*"
    r"(?:час(?:а|ов)?|утра|дня|вечера)\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# GigaChat через OpenAI-совместимый API
# ---------------------------------------------------------------------------

_MODEL_NAME = "ai-sage/GigaChat3-10B-A1.8B"
_llm_client: AsyncOpenAI | None = None

_SYSTEM_PROMPT = """
Ты — парсер реплик пациента поликлиники.

Извлеки из сообщения поля:
- intent: book, cancel, check, greeting, reschedule, complaint или unknown;
- specialty: специальность врача;
- last_name: фамилия;
- first_name: имя;
- middle_name: отчество;
- birth_date: дата рождения в формате ДД.ММ.ГГГГ;
- desired_date: желаемая дата приема в формате ДД.ММ.ГГГГ;
- desired_time: желаемое время в формате ЧЧ:ММ.

Правила:
- Не выдумывай отсутствующие данные. Для неизвестного поля верни null.
- Если пациент хочет отменить запись, intent=cancel.
- Если хочет перенести запись, intent=reschedule.
- Если хочет узнать дату или время своей записи, intent=check.
- Если хочет записаться к врачу, intent=book.
- Если пациент жалуется, intent=complaint.
- Не путай дату рождения с желаемой датой приема.
- Распознавай синонимы специальностей: «окулист» — офтальмолог,
  «сердечный врач» — кардиолог, «ухо-горло-нос» — лор.
- Верни только JSON без Markdown и пояснений.

Пример:
Вход: "Иванов Пётр Сергеевич, 17.03.1985"
Выход:
{"intent":"unknown","last_name":"Иванов","first_name":"Пётр",
 "middle_name":"Сергеевич","birth_date":"17.03.1985"}

Вход: "Хочу к кардиологу завтра в 15:30"
Выход:
{"intent":"book","specialty":"кардиолог",
 "desired_date":"завтра","desired_time":"15:30"}
""".strip()


def _get_llm_client() -> AsyncOpenAI | None:
    """Создать LLM-клиент только при наличии API-ключа."""
    global _llm_client

    api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not api_key:
        return None

    if _llm_client is None:
        kwargs: dict[str, Any] = {
            "api_key": api_key,
            "timeout": 10.0,
            "max_retries": 1,
        }

        base_url = (os.getenv("OPENAI_BASE_URL") or "").strip()
        if base_url:
            kwargs["base_url"] = base_url

        _llm_client = AsyncOpenAI(**kwargs)

    return _llm_client


# ---------------------------------------------------------------------------
# Публичный интерфейс
# ---------------------------------------------------------------------------

async def parse(text: str, today: str) -> ParsedUtterance:
    """Разобрать реплику пациента с помощью GigaChat и regex fallback.

    today должен иметь формат ДД.ММ.ГГГГ.
    При недоступности LLM используется regex-парсер.
    """
    text = (text or "").strip()
    regex_result = _parse_with_regex(text, today)

    if not text:
        return regex_result

    client = _get_llm_client()
    if client is None:
        return regex_result

    try:
        response = await client.chat.completions.create(
            model=_MODEL_NAME,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
            temperature=0.0,
            max_tokens=300,
        )

        raw = (response.choices[0].message.content or "").strip()
        data = _decode_json(raw)

        if not isinstance(data, dict):
            return regex_result

        intent = data.get("intent")
        if not isinstance(intent, str) or intent not in _VALID_INTENTS:
            intent = regex_result.intent

        specialty = _normalize_specialty(data.get("specialty"))
        if specialty is None:
            specialty = regex_result.specialty

        birth_date = _normalize_date(data.get("birth_date"))
        if birth_date is None:
            birth_date = regex_result.birth_date

        desired_date = _normalize_desired_date(
            data.get("desired_date"), today
        )
        if desired_date is None:
            desired_date = regex_result.desired_date

        desired_time = _normalize_time(data.get("desired_time"))
        if desired_time is None:
            desired_time = regex_result.desired_time

        return ParsedUtterance(
            intent=intent,
            last_name=_clean_string(data.get("last_name"))
            or regex_result.last_name,
            first_name=_clean_string(data.get("first_name"))
            or regex_result.first_name,
            middle_name=_clean_string(data.get("middle_name"))
            or regex_result.middle_name,
            birth_date=birth_date,
            specialty=specialty,
            desired_date=desired_date,
            desired_time=desired_time,
            raw_text=text,
            extra={
                key: value
                for key, value in data.items()
                if key not in {
                    "intent",
                    "last_name",
                    "first_name",
                    "middle_name",
                    "birth_date",
                    "specialty",
                    "desired_date",
                    "desired_time",
                }
            },
        )

    except Exception:
        logger.warning(
            "Ошибка NLU через LLM; используется regex fallback",
            exc_info=True,
        )
        return regex_result


def _decode_json(raw: str) -> dict[str, Any] | None:
    """Разобрать JSON, в том числе если модель обернула его в Markdown."""
    raw = raw.strip()

    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"\s*```$", "", raw)

    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        # Дополнительная попытка найти JSON-объект внутри ответа.
        match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
        if not match:
            return None

        try:
            result = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None

    return result if isinstance(result, dict) else None


# ---------------------------------------------------------------------------
# Regex fallback
# ---------------------------------------------------------------------------

def _parse_with_regex(text: str, today: str) -> ParsedUtterance:
    result = ParsedUtterance(raw_text=text)
    lowered = _normalize_text(text)

    # Перенос проверяется раньше обычной записи.
    if _RE_RESCHEDULE.search(lowered):
        result.intent = "reschedule"
    elif _RE_CANCEL.search(lowered):
        result.intent = "cancel"
    elif _RE_COMPLAINT.search(lowered):
        result.intent = "complaint"
    elif _RE_CHECK.search(lowered):
        result.intent = "check"
    elif _RE_BOOK.search(lowered):
        result.intent = "book"
    elif _RE_GREETING.search(lowered):
        result.intent = "greeting"

    result.specialty = _extract_specialty(lowered)

    name = _extract_name(text)
    if name:
        result.last_name, result.first_name, result.middle_name = name

    result.birth_date = _extract_birth_date(text)

    result.desired_date = _extract_desired_date(
        text,
        today,
        exclude=result.birth_date,
    )

    result.desired_time = _extract_time(text)

    return result


def _normalize_text(text: str) -> str:
    """Нормализовать регистр, пробелы и букву ё для поиска."""
    return " ".join(text.casefold().replace("ё", "е").split())


def _normalize_specialty(value: Any) -> str | None:
    if not isinstance(value, str):
        return None

    normalized = _normalize_text(value).strip(" .,!?:;")

    if not normalized:
        return None

    for specialty in SPECIALTIES:
        if _normalize_text(specialty) == normalized:
            return specialty.capitalize()

    for alias, specialty in SPECIALTY_ALIASES.items():
        if _normalize_text(alias) == normalized:
            return specialty.capitalize()

    return None


def _extract_specialty(lowered: str) -> str | None:
    normalized = _normalize_text(lowered)

    # Сначала ищем самые длинные названия, чтобы «стоматолог-терапевт»
    # не распознавался просто как «терапевт».
    specialties = sorted(
        SPECIALTIES,
        key=len,
        reverse=True,
    )

    for specialty in specialties:
        pattern = rf"(?<!\w){re.escape(_normalize_text(specialty))}(?!\w)"
        if re.search(pattern, normalized):
            return specialty.capitalize()

    aliases = sorted(
        SPECIALTY_ALIASES.items(),
        key=lambda item: len(item[0]),
        reverse=True,
    )

    for alias, specialty in aliases:
        normalized_alias = _normalize_text(alias)
        pattern = rf"(?<!\w){re.escape(normalized_alias)}(?!\w)"
        if re.search(pattern, normalized):
            return specialty.capitalize()

    return None


def _extract_name(
    text: str,
) -> tuple[str, str, str | None] | None:
    for last_name, first_name, middle_name in _RE_NAME_DIRECT.findall(text):
        parts = [last_name, first_name]
        if middle_name:
            parts.append(middle_name)

        normalized_parts = [_normalize_text(part) for part in parts]

        # Не принимаем названия специальностей за имя и фамилию.
        if any(
            part in SPECIALTIES or part in SPECIALTY_ALIASES
            for part in normalized_parts
        ):
            continue

        # Отбрасываем распространенные слова, которые не являются ФИО.
        if normalized_parts[0] in {
            "добрый",
            "доброе",
            "здравствуйте",
            "запишите",
            "хочу",
            "мне",
        }:
            continue

        return last_name, first_name, middle_name or None

    return None


def _fix_year(year: str) -> int:
    """Преобразовать год, используя существующую функцию проекта."""
    fixed = fix_year_century(year)
    return int(fixed)


def _extract_birth_date(text: str) -> str | None:
    match = _RE_BIRTH_NUM.search(text)

    if match:
        day, month, year = match.groups()
        try:
            year_int = _fix_year(year)
            return datetime(
                year_int, int(month), int(day)
            ).strftime("%d.%m.%Y")
        except (TypeError, ValueError):
            return None

    match = _RE_BIRTH_TEXT.search(text)

    if match:
        day, month_name, year = match.groups()
        month = _month_number(month_name)

        if month is None:
            return None

        try:
            year_int = _fix_year(year)
            return datetime(
                year_int, month, int(day)
            ).strftime("%d.%m.%Y")
        except (TypeError, ValueError):
            return None

    return None


def _month_number(month_name: str) -> int | None:
    normalized = _normalize_text(month_name)

    for prefix, month in _MONTHS.items():
        if normalized.startswith(prefix):
            return month

    return None


def _parse_today(today: str) -> date:
    try:
        return datetime.strptime(today, "%d.%m.%Y").date()
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"Параметр today должен иметь формат ДД.ММ.ГГГГ, получено: {today!r}"
        ) from exc


def _extract_desired_date(
    text: str,
    today: str,
    exclude: str | None = None,
) -> str | None:
    base_date = _parse_today(today)
    lowered = _normalize_text(text)

    for word, offset in _RELATIVE_DAYS.items():
        if re.search(rf"(?<!\w){word}(?!\w)", lowered):
            candidate = base_date + timedelta(days=offset)
            formatted = candidate.strftime("%d.%m.%Y")
            if formatted != exclude:
                return formatted

    for match in _RE_DATE_NUM.finditer(text):
        day, month, year = match.groups()

        try:
            year_int = _fix_year(year)
            candidate = date(
                year_int, int(month), int(day)
            ).strftime("%d.%m.%Y")
        except (TypeError, ValueError):
            continue

        if candidate == exclude:
            continue

        return candidate

    for match in _RE_DATE_TEXT.finditer(text):
        day, month_name, year = match.groups()
        month = _month_number(month_name)

        if month is None:
            continue

        try:
            year_int = _fix_year(year) if year else base_date.year
            candidate_date = date(year_int, month, int(day))
        except (TypeError, ValueError):
            continue

        # Если год не назван, выбираем ближайшую подходящую дату.
        if year is None and candidate_date < base_date:
            try:
                candidate_date = date(
                    base_date.year + 1, month, int(day)
                )
            except ValueError:
                continue

        candidate = candidate_date.strftime("%d.%m.%Y")

        if candidate == exclude:
            continue

        return candidate

    return None


def _extract_time(text: str) -> str | None:
    match = _RE_TIME.search(text)

    if not match:
        return None

    hour, minute, hour_only = match.groups()

    try:
        if hour is not None and minute is not None:
            h = int(hour)
            m = int(minute)
        elif hour_only is not None:
            h = int(hour_only)
            m = 0
        else:
            return None

        if not 0 <= h <= 23 or not 0 <= m <= 59:
            return None

        return f"{h:02d}:{m:02d}"

    except ValueError:
        return None


def _normalize_date(value: Any) -> str | None:
    """Проверить дату из ответа LLM и привести к ДД.ММ.ГГГГ."""
    if not isinstance(value, str):
        return None

    match = _RE_BIRTH_NUM.fullmatch(value.strip())
    if not match:
        return None

    day, month, year = match.groups()

    try:
        return datetime(
            _fix_year(year), int(month), int(day)
        ).strftime("%d.%m.%Y")
    except (TypeError, ValueError):
        return None


def _normalize_desired_date(value: Any, today: str) -> str | None:
    if not isinstance(value, str):
        return None

    normalized = _normalize_text(value)

    if normalized in _RELATIVE_DAYS:
        try:
            base_date = _parse_today(today)
        except ValueError:
            return None

        return (
            base_date + timedelta(days=_RELATIVE_DAYS[normalized])
        ).strftime("%d.%m.%Y")

    return _normalize_date(value)


def _normalize_time(value: Any) -> str | None:
    if not isinstance(value, str):
        return None

    match = re.fullmatch(r"\s*(\d{1,2})[:.](\d{2})\s*", value)
    if not match:
        return None

    hour, minute = map(int, match.groups())

    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        return None

    return f"{hour:02d}:{minute:02d}"


def _clean_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None

    value = " ".join(value.split())
    return value or None
