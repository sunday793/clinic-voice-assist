
"""Детектор красных флагов по регламенту РГ-ТЕЛ-01, раздел 7.4.

Категория A — потенциальная угроза жизни:
    R-01, рекомендация вызвать 103/112, перевод P1.

Категория B — острое состояние:
    R-11, перевод регистратору P1.

Детектор использует регулярные выражения и не является медицинской
диагностикой. При неоднозначности совпадение не отбрасывается автоматически.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.core.constants import (
    EMERGENCY_PHRASE,
    RED_FLAG_ALLERGY,
    RED_FLAG_BLEEDING,
    RED_FLAG_BREATHING,
    RED_FLAG_CATEGORY_A,
    RED_FLAG_CATEGORY_B,
    RED_FLAG_CHEST_PAIN,
    RED_FLAG_CHILD,
    RED_FLAG_CONSCIOUSNESS,
    RED_FLAG_HIGH_FEVER,
    RED_FLAG_MINOR_TRAUMA,
    RED_FLAG_PREGNANCY,
    RED_FLAG_SEIZURE,
    RED_FLAG_SELF_HARM,
    RED_FLAG_SEVERE_PAIN,
    RED_FLAG_STROKE,
    RED_FLAG_TRAUMA,
    RED_FLAG_WORSENING,
    URGENT_PHRASE,
)


@dataclass(frozen=True, slots=True)
class RedFlag:
    """Обнаруженный красный флаг."""

    category: str  # "A" или "B" — согласно значениям в constants.py
    group: str
    matched: str
    phrase: str


def _compile(*patterns: str) -> list[re.Pattern[str]]:
    """Скомпилировать набор регулярных выражений."""
    return [re.compile(pattern, re.IGNORECASE) for pattern in patterns]


# ---------------------------------------------------------------------------
# Категория A — потенциальная угроза жизни
# ---------------------------------------------------------------------------

_CATEGORY_A: dict[str, list[re.Pattern[str]]] = {
    RED_FLAG_CHEST_PAIN: _compile(
        r"\bболь\w*\s+(?:в|за)\s+груд\w*",
        r"\bболит\s+груд\w*",
        r"\bдавит\s+(?:за\s+грудин\w*|в\s+груд\w*)",
        r"\bсдавлени\w*\s+(?:в\s+груд\w*|за\s+грудин\w*)",
        r"\bжж[её]т\s+(?:в\s+груд\w*|за\s+грудин\w*)",
        r"\bжжение\s+(?:в\s+груд\w*|за\s+грудин\w*)",
        r"\bотда[её]т\s+в\s+(?:левую\s+руку|челюсть|спину)",
        r"\bсердечн\w*\s+приступ\w*",
        r"\bинфаркт\w*\b",
    ),
    RED_FLAG_BREATHING: _compile(
        r"\bне\s+может\s+(?:дышать|вдохнуть)",
        r"\bне\s+могу\s+(?:дышать|вдохнуть)",
        r"\bтрудно\s+дышать",
        r"\bтяжело\s+(?:дышать|вдохнуть)",
        r"\bне\s+хватает\s+воздуха",
        r"\bзадыха\w*",
        r"\bудушье\b",
        r"\bпосинел\w*\b",
        r"\bсинеют\s+(?:губы|пальцы|лицо)",
        r"\bхрипит\b",
        r"\bподавил\w*ся\b",
        r"\bпоперхнул\w*ся\b",
    ),
    RED_FLAG_CONSCIOUSNESS: _compile(
        r"\bпотерял\w*\s+сознани\w*",
        r"\bбез\s+сознани\w*",
        r"\bне\s+приходит\s+в\s+себя",
        r"\bне\s+могу\s+разбудить",
        r"\bне\s+реагирует\b",
        r"\bне\s+отзывается\b",
        r"\bупал\w*\s+в\s+обморок",
        r"\bобморок\b",
    ),
    RED_FLAG_STROKE: _compile(
        r"\bперекосил\w*\s+лиц\w*",
        r"\bперекос\s+лиц\w*",
        r"\bотнял\w*\s+(?:ся\s+)?(?:рук\w*|ног\w*)",
        r"\bвнезапн\w*\s+слабост\w*\s+(?:в\s+)?"
        r"(?:рук\w*|ног\w*|половин\w*\s+тел\w*)",
        r"\bне\s+может\s+говорить\b",
        r"\bречь\s+(?:стала\s+)?невнятн\w*",
        r"\bонемел\w*\s+(?:лиц\w*|половин\w*|рук\w*|ног\w*)",
        r"\bвнезапн\w*\s+нарушени\w*\s+речи",
    ),
    RED_FLAG_SEIZURE: _compile(
        r"\bсудорог\w*",
        r"\bтряс[её]т\b",
        r"\bэпилептическ\w*\s+приступ\w*",
        r"\bприпад\w*",
    ),
    RED_FLAG_BLEEDING: _compile(
        r"\bсильн\w*\s+кровотечени\w*",
        r"\bкровь\s+не\s+останавлива\w*",
        r"\bне\s+останавливается\s+кровь",
        r"\bрв[её]т\s+кровью\b",
        r"\bрвот\w*\s+кров\w*",
        r"\bкровь\s+изо?\s*рта\b",
        r"\bобильн\w*\s+кров\w*",
    ),
    RED_FLAG_TRAUMA: _compile(
        r"\bдтп\b",
        r"\b(?:попал\w*|пострадал\w*)\s+в\s+аварию",
        r"\bударил\w*ся\s+голов\w*",
        r"\bудар\w*\s+по\s+голов\w*",
        r"\bупал\w*\s+с\s+высот\w*",
        r"\bбольш\w*\s+ожог\w*",
        r"\bсильн\w*\s+ожог\w*",
        r"\bотравил\w*ся\b",
        r"\bпроглотил\w*\s+(?:таблет\w*|лекарств\w*|хими\w*)",
        r"\bукусил\w*\s+(?:зме[яёйю]|ядовит\w*\s+зме[яёйю])",
        r"\bножев\w*\s+ранени\w*",
        r"\bогнестрельн\w*\s+ранени\w*",
    ),
    RED_FLAG_ALLERGY: _compile(
        r"\bот[её]к\w*\s+(?:горл\w*|язык\w*)",
        r"\bопух\w*\s+(?:язык|горло|губы)\b",
        r"\bне\s+может\s+глотать\b",
        r"\bанафилактическ\w*\s+шок\w*",
        r"\bтяж[её]лая\s+аллергическ\w*\s+реакци\w*",
        r"\bаллерги\w*.{0,40}\b(?:задыха\w*|удушье|от[её]к\s+горл\w*)",
    ),
    RED_FLAG_CHILD: _compile(
        r"\bмладенец.{0,20}\b(?:вял\w*|не\s+реагирует|не\s+дышит)",
        r"\bгрудн\w*\s+реб[её]нок.{0,30}\b"
        r"(?:вял\w*|не\s+дышит|посинел\w*|не\s+реагирует)",
        r"\bреб[её]нок\s+посинел\w*",
        r"\bреб[её]нок\s+проглотил\w*\s+"
        r"(?:таблет\w*|лекарств\w*|хими\w*|батарейк\w*)",
        r"\bреб[её]нок.{0,30}\b(?:судорог\w*|без\s+сознани\w*)",
    ),
    RED_FLAG_PREGNANCY: _compile(
        r"\bберемен\w*.{0,40}\bобильн\w*\s+кровотечени\w*",
        r"\bберемен\w*.{0,40}\bкровь\s+ид[её]т\b",
        r"\bберемен\w*.{0,40}\bсильн\w*\s+боль\w*",
        r"\bотошли\s+воды\b",
        r"\bсхватк\w*",
    ),
    RED_FLAG_SELF_HARM: _compile(
        r"\bне\s+хочу\s+жить\b",
        r"\bхочу\s+умереть\b",
        r"\bпокончить\s+с\s+собой\b",
        r"\bубить\s+себя\b",
        r"\bнаглотал\w*\s+таблет\w*",
        r"\bнаглота\w*ся\s+таблет\w*",
        r"\bпринял\w*\s+слишком\s+много\s+лекарств",
        r"\bугрожает\s+убить\b",
    ),
}


# ---------------------------------------------------------------------------
# Категория B — острое состояние
# ---------------------------------------------------------------------------

_CATEGORY_B: dict[str, list[re.Pattern[str]]] = {
    RED_FLAG_SEVERE_PAIN: _compile(
        r"\bочень\s+сильн\w*\s+боль\w*",
        r"\bневыносим\w*\s+боль\w*",
        r"\bневыносим\w*\b",
        r"\bболь\w*\s+усилива\w*",
        r"\bсильн\w*\s+боль\w*",
    ),
    RED_FLAG_HIGH_FEVER: _compile(
        r"\bтемператур\w*.{0,15}\b40\s*(?:градус\w*)?\b",
        r"\bтемператур\w*.{0,15}\bсорок\b",
        r"\bтемператур\w*.{0,25}\bне\s+сбива\w*",
        r"\bтемператур\w*.{0,25}\bтретий\s+день\b",
        r"\bлихорадк\w*.{0,25}\bне\s+сбива\w*",
    ),
    RED_FLAG_WORSENING: _compile(
        r"\bстало\s+(?:намного|значительно|резко)\s+хуже\b",
        r"\bрезко\s+поднялось\s+давлени\w*",
        r"\bдавлени\w*.{0,20}\b(?:очень\s+высок\w*|резко\s+поднялось)",
        r"\bкружится\s+голова.{0,30}\bне\s+могу\s+встать",
        r"\bрезко\s+ухудшилось\s+состояни\w*",
        r"\bвнезапно\s+стало\s+хуже\b",
    ),
    RED_FLAG_MINOR_TRAUMA: _compile(
        r"\bподвернул\w*\s+ног\w*",
        r"\bглубок\w*\s+порез\w*",
        r"\bглубоко\s+порезал\w*ся\b",
        r"\bсильн\w*\s+ушиб\w*",
    ),
}


def _normalize(text: str) -> str:
    """Нормализовать текст перед поиском."""
    text = (text or "").casefold().replace("ё", "е")
    return " ".join(text.split())


def detect(text: str) -> RedFlag | None:
    """Обнаружить красный флаг в реплике.

    Сначала проверяется категория A, затем категория B.
    Возвращается первое совпадение в порядке объявления групп и паттернов.

    Важно: отсутствие совпадения не означает отсутствие опасного состояния.
    Результат детектора должен передаваться в сценарий безопасности.
    """
    normalized = _normalize(text)

    if not normalized:
        return None

    for category, groups, phrase in (
        (RED_FLAG_CATEGORY_A, _CATEGORY_A, EMERGENCY_PHRASE),
        (RED_FLAG_CATEGORY_B, _CATEGORY_B, URGENT_PHRASE),
    ):
        for group, patterns in groups.items():
            for pattern in patterns:
                match = pattern.search(normalized)
                if match:
                    return RedFlag(
                        category=category,
                        group=group,
                        matched=match.group(0),
                        phrase=phrase,
                    )

    return None
