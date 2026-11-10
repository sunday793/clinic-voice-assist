
"""Константы регламента телефонного ассистента.

Содержит:
- Коды результатов звонка (Приложение Г регламента).
- Приоритеты переводов (раздел 7.5).
- Категории и группы красных флагов (раздел 7.4 регламента РГ-ТЕЛ-01).
- Номера экстренных служб.
- Эталонные фразы для голосовых сценариев.
"""

from __future__ import annotations

from typing import Final


# ---------------------------------------------------------------------
# Коды результатов звонка (Приложение Г регламента)
# ---------------------------------------------------------------------

BOOKED: Final[str] = "BOOKED"
BOOKED_GP: Final[str] = "BOOKED_GP"
BOOKED_PAID: Final[str] = "BOOKED_PAID"
RESCHEDULED: Final[str] = "RESCHEDULED"
CANCELLED: Final[str] = "CANCELLED"
CONFIRMED: Final[str] = "CONFIRMED"
INFO_GIVEN: Final[str] = "INFO_GIVEN"
WAITLISTED: Final[str] = "WAITLISTED"
HOME_VISIT_REQUESTED: Final[str] = "HOME_VISIT_REQUESTED"
ESCALATED: Final[str] = "ESCALATED"
CALLBACK: Final[str] = "CALLBACK"
EMERGENCY: Final[str] = "EMERGENCY"
COMPLAINT: Final[str] = "COMPLAINT"
GRATITUDE: Final[str] = "GRATITUDE"
DROPPED: Final[str] = "DROPPED"


# ---------------------------------------------------------------------
# Приоритеты переводов (раздел 7.5)
# ---------------------------------------------------------------------

P1_EMERGENCY: Final[str] = "P1"
P2_HIGH: Final[str] = "P2"
P3_NORMAL: Final[str] = "P3"


# ---------------------------------------------------------------------
# Красные флаги (раздел 7.4 регламента РГ-ТЕЛ-01)
# ---------------------------------------------------------------------

# Категории
RED_FLAG_CATEGORY_A: Final[str] = "A"  # Угроза жизни → R-01 → 103/112 + P1
RED_FLAG_CATEGORY_B: Final[str] = "B"  # Острое состояние → R-11 → регистратор P1


# Группы категории A
RED_FLAG_CHEST_PAIN: Final[str] = "боль_в_груди"
RED_FLAG_BREATHING: Final[str] = "нарушение_дыхания"
RED_FLAG_CONSCIOUSNESS: Final[str] = "потеря_сознания"
RED_FLAG_STROKE: Final[str] = "инсульт"
RED_FLAG_SEIZURE: Final[str] = "судороги"
RED_FLAG_BLEEDING: Final[str] = "кровотечение"
RED_FLAG_TRAUMA: Final[str] = "травма_отравление"
RED_FLAG_ALLERGY: Final[str] = "аллергия"
RED_FLAG_CHILD: Final[str] = "ребёнок"
RED_FLAG_PREGNANCY: Final[str] = "беременность"
RED_FLAG_SELF_HARM: Final[str] = "угроза_себе_или_другим"


# Группы категории B
RED_FLAG_SEVERE_PAIN: Final[str] = "острая_боль"
RED_FLAG_HIGH_FEVER: Final[str] = "высокая_температура"
RED_FLAG_WORSENING: Final[str] = "резкое_ухудшение"
RED_FLAG_MINOR_TRAUMA: Final[str] = "травма_без_угрозы"


# ---------------------------------------------------------------------
# Номера экстренных служб
# ---------------------------------------------------------------------

EMERGENCY_PHONE_SHORT: Final[str] = "103"
EMERGENCY_PHONE_UNIFIED: Final[str] = "112"


# ---------------------------------------------------------------------
# Эталонные фразы голосового ассистента
# ---------------------------------------------------------------------

# Категория A: угроза жизни (сценарий R-01, эталон С-14).
EMERGENCY_PHRASE: Final[str] = (
    "Пожалуйста, немедленно звоните в скорую помощь: сто три или сто двенадцать. "
    "Повторяю: сто три или сто двенадцать. Не ждите приёма в поликлинике."
)

# Категория B: острое состояние.
URGENT_PHRASE: Final[str] = (
    "Соединяю с регистратором, он организует срочную помощь. "
    "Если станет хуже — сразу звоните сто три."
)

# Все операторы заняты (раздел 7.6).
OPERATORS_BUSY_PHRASE: Final[str] = (
    "Все сотрудники сейчас заняты. Я записал ваш вопрос — "
    "вам перезвонят в течение 15 минут. Оставайтесь на линии, пожалуйста."
)


# ---------------------------------------------------------------------
# Составные справочники для проверок и повторного использования
# ---------------------------------------------------------------------

CALL_RESULT_CODES: Final[frozenset[str]] = frozenset(
    {
        BOOKED,
        BOOKED_GP,
        BOOKED_PAID,
        RESCHEDULED,
        CANCELLED,
        CONFIRMED,
        INFO_GIVEN,
        WAITLISTED,
        HOME_VISIT_REQUESTED,
        ESCALATED,
        CALLBACK,
        EMERGENCY,
        COMPLAINT,
        GRATITUDE,
        DROPPED,
    }
)

TRANSFER_PRIORITIES: Final[frozenset[str]] = frozenset(
    {
        P1_EMERGENCY,
        P2_HIGH,
        P3_NORMAL,
    }
)

RED_FLAG_CATEGORY_A_GROUPS: Final[frozenset[str]] = frozenset(
    {
        RED_FLAG_CHEST_PAIN,
        RED_FLAG_BREATHING,
        RED_FLAG_CONSCIOUSNESS,
        RED_FLAG_STROKE,
        RED_FLAG_SEIZURE,
        RED_FLAG_BLEEDING,
        RED_FLAG_TRAUMA,
        RED_FLAG_ALLERGY,
        RED_FLAG_CHILD,
        RED_FLAG_PREGNANCY,
        RED_FLAG_SELF_HARM,
    }
)

RED_FLAG_CATEGORY_B_GROUPS: Final[frozenset[str]] = frozenset(
    {
        RED_FLAG_SEVERE_PAIN,
        RED_FLAG_HIGH_FEVER,
        RED_FLAG_WORSENING,
        RED_FLAG_MINOR_TRAUMA,
    }
)
