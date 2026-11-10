
"""База знаний и триггеры для InformationScenario.

Содержит:
- FAQ с ответами клиники;
- нормализацию пользовательского текста;
- поиск наиболее подходящего FAQ;
- определение триггеров для перехода в другие сценарии.

Важно: это словарный поиск, а не полноценная ML-классификация намерений.
"""
from __future__ import annotations

import re
from collections.abc import Mapping


# Формат:
# ключ — набор ключевых слов или фраз;
# значение — готовый ответ пациенту.
#
# Символ * в конце отдельного слова означает поиск по началу слова.
# Например, "подготов*" совпадает с "подготовка", "подготовиться".
FAQ: dict[tuple[str, ...], str] = {
    # График работы
    (
        "график",
        "режим работы",
        "часы работы",
        "во сколько",
        "суббота",
        "выходной",
        "когда открывает*",
        "когда закрывает*",
    ): (
        "Поликлиника работает по будням с семи тридцати до двадцати, "
        "в субботу с девяти до пятнадцати. Воскресенье — выходной."
    ),

    # Адрес и маршрут
    (
        "адрес",
        "где находитесь",
        "как добраться",
        "как пройти",
        "как доехать",
        "местонахождение",
        "детское отделение",
    ): (
        "Мы находимся по адресу: улица Центральная, дом семь. "
        "Детское отделение — отдельный вход с торца здания."
    ),

    # Анализы и подготовка
    (
        "анализ*",
        "кровь",
        "подготов*",
        "сдать анализ",
        "сдавать анализ",
        "натощак",
        "перед анализом",
        "забор крови",
    ): (
        "Кровь на анализ сдают утром натощак в процедурном кабинете. "
        "По будням с семи тридцати до десяти тридцати, "
        "в субботу с девяти до одиннадцати. "
        "Подробные требования к вашему исследованию уточнит врач."
    ),

    # Цены и платные услуги
    (
        "цен*",
        "стоимость",
        "платно",
        "платная услуга",
        "сколько стоит",
        "сколько стоят",
        "прайс",
        "цена приёма",
        "цена приема",
    ): (
        "Платные услуги можно уточнить в платном отделении. "
        "Например, приём терапевта стоит тысячу пятьсот рублей, "
        "УЗИ щитовидной железы — тысячу триста. "
        "Соединить вас с платным отделением?"
    ),

    # Прикрепление
    (
        "прикреп*",
        "полис",
        "омс",
        "снилс",
        "прикрепиться к поликлинике",
        "как прикрепиться",
    ): (
        "Прикрепиться можно через Госуслуги или в регистратуре, "
        "окно номер один. Нужны паспорт, полис ОМС и СНИЛС."
    ),

    # Документы на приём
    (
        "что взять",
        "взять с собой",
        "какие документы",
        "нужен паспорт",
        "документы на приём",
        "документы на прием",
    ): (
        "На приём возьмите паспорт и полис ОМС. "
        "Если есть — результаты обследований. "
        "Для ребёнка — полис, свидетельство о рождении "
        "и паспорт родителя."
    ),
}


# Триггеры перехода в другие сценарии.
# Звёздочка в конце слова означает поиск по его началу.
TRIGGERS_OPERATOR: tuple[str, ...] = (
    "оператор",
    "человек",
    "живой оператор",
    "соедини",
    "регистратор",
    "сотрудник",
    "администратор",
)

TRIGGERS_BOOKING: tuple[str, ...] = (
    "запиш*",
    "талон",
    "хочу к врачу",
    "приём",
    "прием",
    "попасть к врачу",
    "запись к врачу",
)

TRIGGERS_CANCEL: tuple[str, ...] = (
    "отмен*",
    "не приду",
    "снять запись",
    "отказаться от записи",
)

TRIGGERS_TRANSFER: tuple[str, ...] = (
    "перенес*",
    "перезапис*",
    "другой день",
    "поменять время",
    "изменить время",
    "перенести запись",
)


def normalize_text(text: str) -> str:
    """Приводит текст к единому виду для словарного поиска."""
    if not isinstance(text, str):
        return ""

    normalized = text.lower().replace("ё", "е")
    normalized = re.sub(r"[^\w\s]", " ", normalized, flags=re.UNICODE)
    normalized = re.sub(r"\s+", " ", normalized).strip()

    return normalized


def _matches_keyword(text: str, keyword: str) -> bool:
    """Проверяет фразу или слово; '*' в конце задаёт поиск по основе."""
    normalized_text = normalize_text(text)
    normalized_keyword = normalize_text(keyword)

    if not normalized_text or not normalized_keyword:
        return False

    # Поиск по началу последнего слова основы:
    # "запиш*" -> "записаться", "запишите".
    if normalized_keyword.endswith("*"):
        stem = normalized_keyword[:-1].strip()
        if not stem:
            return False

        return re.search(
            rf"(?<!\w){re.escape(stem)}\w*",
            normalized_text,
            flags=re.UNICODE,
        ) is not None

    # Фраза или целое слово, а не случайное вхождение внутри другого слова.
    return re.search(
        rf"(?<!\w){re.escape(normalized_keyword)}(?!\w)",
        normalized_text,
        flags=re.UNICODE,
    ) is not None


def _match_score(text: str, keywords: tuple[str, ...]) -> int:
    """Оценивает совпадение: учитывает число совпадений и длину фраз."""
    matched = {
        keyword
        for keyword in keywords
        if _matches_keyword(text, keyword)
    }

    score = 0

    for keyword in matched:
        normalized_keyword = normalize_text(keyword).rstrip("*").strip()
        word_count = len(normalized_keyword.split())

        # Совпадение с конкретной фразой весит больше одного общего слова.
        score += 2 + word_count

    return score


def get_faq_answer(text: str) -> str | None:
    """Возвращает ответ по наиболее подходящей категории FAQ.

    При отсутствии совпадений возвращает None.
    При равных оценках сохраняется порядок категорий в FAQ.
    """
    if not normalize_text(text):
        return None

    best_answer: str | None = None
    best_score = 0

    for keywords, answer in FAQ.items():
        score = _match_score(text, keywords)

        if score > best_score:
            best_score = score
            best_answer = answer

    return best_answer


def detect_intent(text: str) -> str | None:
    """Определяет триггер перехода в другой сценарий.

    Возвращает одно из значений:
    'cancel', 'transfer', 'booking', 'operator' или None.

    Приоритеты нужны для запросов, в которых совпадают несколько групп.
    Это словарный классификатор; неоднозначные фразы требуют отдельной
    обработки в NLU или сценарии диалога.
    """
    if not normalize_text(text):
        return None

    trigger_groups: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("cancel", TRIGGERS_CANCEL),
        ("transfer", TRIGGERS_TRANSFER),
        ("operator", TRIGGERS_OPERATOR),
        ("booking", TRIGGERS_BOOKING),
    )

    for intent, keywords in trigger_groups:
        if any(_matches_keyword(text, keyword) for keyword in keywords):
            return intent

    return None


def get_matching_intents(text: str) -> list[str]:
    """Возвращает все совпавшие группы триггеров без выбора приоритета."""
    if not normalize_text(text):
        return []

    trigger_groups: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("cancel", TRIGGERS_CANCEL),
        ("transfer", TRIGGERS_TRANSFER),
        ("operator", TRIGGERS_OPERATOR),
        ("booking", TRIGGERS_BOOKING),
    )

    return [
        intent
        for intent, keywords in trigger_groups
        if any(_matches_keyword(text, keyword) for keyword in keywords)
    ]
