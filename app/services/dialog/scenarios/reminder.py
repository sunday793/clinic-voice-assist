
"""Сценарий напоминания о записи (Reminder)."""
from __future__ import annotations

import logging
import re

from app.services.dialog.base import ScenarioProtocol
from app.services.dialog.state import (
    DialogResponse,
    DialogState,
    STEP_ESCALATED,
    STEP_GREETING,
)
from app.services.mis_client import MISClientProtocol
from app.services.nlu import ParsedUtterance
from app.services.red_flags import RedFlag

logger = logging.getLogger(__name__)

STEP_REMINDER_GREETING = "reminder_greeting"
STEP_REMINDER_CONFIRM = "reminder_confirm"
STEP_REMINDER_VISIT = "reminder_visit"
STEP_REMINDER_RESCHEDULE = "reminder_reschedule"
STEP_REMINDER_WRONG_PERSON = "reminder_wrong_person"
STEP_REMINDER_DONE = "reminder_done"

MAX_FAILED_ATTEMPTS = 2


class ReminderScenario(ScenarioProtocol):
    """Напоминает о существующей записи и обрабатывает ответ пациента.

    Ожидаемые данные в state.extra:
        reminder_patient_name: имя, которым можно обратиться к пациенту;
        reminder_scheduled_at: дата и время записи;
        reminder_specialty: специальность врача;
        reminder_doctor_name: ФИО врача.

    Эти данные должны быть получены и проверены до запуска сценария.
    Методы MIS для получения записи здесь намеренно не предполагаются:
    их сигнатуры не представлены.
    """

    name = "reminder"

    def __init__(self, mis: MISClientProtocol) -> None:
        self._mis = mis

    async def handle(
        self,
        state: DialogState,
        parsed: ParsedUtterance | None = None,
        red_flag: RedFlag | None = None,
        raw_text: str = "",
    ) -> DialogResponse:
        # Экстренные ситуации должны иметь приоритет над напоминанием.
        if red_flag is not None:
            state.red_flag = red_flag
            state.step = STEP_ESCALATED
            logger.critical(
                "Экстренный сигнал во время напоминания; call_id=%s; category=%s",
                state.call_id,
                red_flag.category,
            )
            return DialogResponse(
                reply=red_flag.phrase or (
                    "Ситуация требует немедленного внимания. "
                    "Оставайтесь, пожалуйста, на линии."
                ),
                step=state.step,
                escalated=True,
                red_flag_category=red_flag.category,
            )

        if state.step == STEP_ESCALATED:
            return DialogResponse(
                reply="Оставайтесь, пожалуйста, на линии. Вам поможет сотрудник.",
                step=state.step,
                escalated=True,
            )

        if state.step in (STEP_GREETING, "greeting", STEP_REMINDER_GREETING):
            state.failed_attempts = 0
            state.step = STEP_REMINDER_CONFIRM

            patient_name = self._get_patient_name(state)
            if not patient_name:
                logger.warning(
                    "Для напоминания не задано имя пациента; call_id=%s",
                    state.call_id,
                )
                state.step = STEP_REMINDER_DONE
                return DialogResponse(
                    reply=(
                        "Здравствуйте! Звоню из поликлиники по поводу записи. "
                        "Для уточнения информации, пожалуйста, свяжитесь "
                        "с регистратурой."
                    ),
                    step=state.step,
                )

            return DialogResponse(
                reply=(
                    f"Здравствуйте! Это виртуальный помощник поликлиники. "
                    f"Я разговариваю с {patient_name}?"
                ),
                step=state.step,
            )

        if state.step == STEP_REMINDER_CONFIRM:
            answer = self._classify_yes_no(raw_text)

            if answer == "yes":
                appointment_text = self._get_appointment_text(state)

                if appointment_text is None:
                    logger.error(
                        "Нет проверенных данных записи для напоминания; call_id=%s",
                        state.call_id,
                    )
                    state.step = STEP_REMINDER_DONE
                    return DialogResponse(
                        reply=(
                            "Не удалось проверить дату и время вашей записи. "
                            "Пожалуйста, уточните информацию в регистратуре."
                        ),
                        step=state.step,
                    )

                state.failed_attempts = 0
                state.step = STEP_REMINDER_VISIT
                return DialogResponse(
                    reply=(
                        f"Напоминаю: {appointment_text}. "
                        "Вы планируете прийти?"
                    ),
                    step=state.step,
                )

            if answer == "no":
                # Не раскрываем данные записи, если личность не подтверждена.
                state.step = STEP_REMINDER_WRONG_PERSON
                return DialogResponse(
                    reply=(
                        "Извините за беспокойство. До свидания."
                    ),
                    step=state.step,
                )

            return self._ask_again(
                state,
                "Пожалуйста, уточните: я разговариваю с пациентом, которому адресовано напоминание?",
            )

        if state.step == STEP_REMINDER_VISIT:
            answer = self._classify_yes_no(raw_text)

            if answer == "yes":
                state.step = STEP_REMINDER_DONE
                return DialogResponse(
                    reply=(
                        "Спасибо, будем вас ждать. Возьмите с собой "
                        "необходимые документы. До свидания!"
                    ),
                    step=state.step,
                    booked=False,
                )

            if answer == "no":
                state.step = STEP_REMINDER_RESCHEDULE
                return DialogResponse(
                    reply=(
                        "Понимаю. Я не могу подтвердить перенос автоматически. "
                        "Для изменения записи могу переключить вас "
                        "на соответствующий сценарий или регистратуру."
                    ),
                    step=state.step,
                )

            return self._ask_again(
                state,
                "Пожалуйста, уточните: вы планируете прийти на приём?",
            )

        if state.step == STEP_REMINDER_RESCHEDULE:
            # Не выдаём предложение переноса за выполненную операцию.
            # Сам перенос должен выполнить отдельный сценарий с проверкой MIS.
            text = self._normalize(raw_text)

            if any(
                phrase in text
                for phrase in (
                    "перенести",
                    "перенос",
                    "изменить запись",
                    "другой день",
                    "другое время",
                    "перезаписаться",
                )
            ):
                state.is_reschedule = True
                state.old_visit_id = state.visit_id

                scheduled_at = state.extra.get("reminder_scheduled_at")
                if isinstance(scheduled_at, str):
                    state.old_scheduled_at = scheduled_at

                state.scenario = "transfer"
                state.step = STEP_GREETING
                return DialogResponse(
                    reply="Хорошо, переключаю вас на сценарий переноса записи.",
                    step=state.step,
                )

            if any(
                phrase in text
                for phrase in (
                    "не надо",
                    "не нужно",
                    "оставить как есть",
                    "ничего не менять",
                )
            ):
                state.step = STEP_REMINDER_DONE
                return DialogResponse(
                    reply=(
                        "Хорошо, запись не изменена. "
                        "Если планы поменяются, обратитесь в регистратуру."
                    ),
                    step=state.step,
                )

            return self._ask_again(
                state,
                "Вы хотите перенести запись или оставить её без изменений?",
            )

        if state.step in (STEP_REMINDER_WRONG_PERSON, STEP_REMINDER_DONE):
            return DialogResponse(
                reply="Спасибо за ответ. До свидания!",
                step=state.step,
            )

        logger.warning(
            "Неизвестный шаг ReminderScenario: %s; call_id=%s",
            state.step,
            state.call_id,
        )
        state.step = STEP_REMINDER_GREETING
        return DialogResponse(
            reply="Давайте начнём уточнение напоминания заново.",
            step=state.step,
        )

    @staticmethod
    def _get_patient_name(state: DialogState) -> str | None:
        """Получает обращение к пациенту из заранее проверенных данных."""
        value = state.extra.get("reminder_patient_name")

        if isinstance(value, str) and value.strip():
            return value.strip()

        # Не подставляем ФИО автоматически, если оно может быть неполным.
        if state.full_name and state.full_name.strip():
            return state.full_name.strip()

        return None

    @staticmethod
    def _get_appointment_text(state: DialogState) -> str | None:
        """Формирует текст только при наличии даты и времени записи."""
        scheduled_at = state.extra.get("reminder_scheduled_at")
        specialty = state.extra.get("reminder_specialty")
        doctor_name = state.extra.get("reminder_doctor_name")

        if not isinstance(scheduled_at, str) or not scheduled_at.strip():
            return None

        parts = [f"у вас запись {scheduled_at.strip()}"]

        if isinstance(specialty, str) and specialty.strip():
            parts.append(f"специалист: {specialty.strip()}")

        if isinstance(doctor_name, str) and doctor_name.strip():
            parts.append(f"врач: {doctor_name.strip()}")

        return ", ".join(parts)

    @staticmethod
    def _normalize(text: str) -> str:
        text = (text or "").lower().replace("ё", "е")
        text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
        return re.sub(r"\s+", " ", text).strip()

    @classmethod
    def _classify_yes_no(cls, text: str) -> str | None:
        """Консервативно определяет ответ, не принимая любое вхождение «да»."""

        normalized = cls._normalize(text)
        if not normalized:
            return None

        negative_phrases = (
            "нет",
            "неверно",
            "не я",
            "ошиблись",
            "не тот человек",
            "не являюсь",
            "не приду",
            "не смогу",
            "не буду",
            "не планирую",
        )
        positive_phrases = (
            "да",
            "верно",
            "совершенно верно",
            "это я",
            "слушаю",
            "да это я",
            "подтверждаю",
        )

        # Отрицание имеет приоритет, чтобы не принять
        # «да, но не приду» за согласие.
        if any(
            re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", normalized)
            for phrase in negative_phrases
        ):
            return "no"

        if any(
            re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", normalized)
            for phrase in positive_phrases
        ):
            return "yes"

        return None

    @staticmethod
    def _ask_again(
        state: DialogState,
        prompt: str,
    ) -> DialogResponse:
        state.failed_attempts += 1

        if state.failed_attempts > MAX_FAILED_ATTEMPTS:
            state.step = STEP_REMINDER_DONE
            return DialogResponse(
                reply=(
                    "Не удалось однозначно понять ответ. "
                    "Пожалуйста, уточните информацию в регистратуре. До свидания."
                ),
                step=state.step,
            )

        return DialogResponse(
            reply=prompt,
            step=state.step,
        )
