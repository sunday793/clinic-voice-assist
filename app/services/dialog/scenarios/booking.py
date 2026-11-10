"""Сценарий C-01 «Запись на приём»."""

from __future__ import annotations

import logging
import re
from typing import Any
from app.core.greetings import get_greeting_reply

from app.schemas.mis import (
    ClientCreate,
    VisitCreate,
    VisitStatus,
    VisitUpdate,
)
from app.services.dialog.base import ScenarioProtocol
from app.services.dialog.state import (
    DialogResponse,
    DialogState,
    STEP_ASK_NAME,
    STEP_ASK_SPECIALTY,
    STEP_BOOKED,
    STEP_CONFIRM,
    STEP_ESCALATED,
    STEP_FALLBACK,
    STEP_GREETING,
    STEP_OFFER_SLOTS,
)
from app.services.inspections.legal_age import LegalAge
from app.services.mis_client import MISClientProtocol
from app.services.nlu import ParsedUtterance
from app.services.red_flags import RedFlag
from app.services.schedule import ScheduleService

logger = logging.getLogger(__name__)

STEP_CHECK_REPRESENTATIVE = "check_representative"

MAX_FAILED_ATTEMPTS = 3


class BookingScenario(ScenarioProtocol):
    """Сценарий записи на приём и выбора нового времени при переносе."""

    name = "booking"

    def __init__(
        self,
        mis: MISClientProtocol,
        schedule: ScheduleService,
    ) -> None:
        self._mis = mis
        self._schedule = schedule

    async def handle(
        self,
        state: DialogState,
        parsed: ParsedUtterance | None = None,
        red_flag: RedFlag | None = None,
        raw_text: str = "",
    ) -> DialogResponse:
        # Не продолжаем оформление после эскалации.
        if state.step == STEP_ESCALATED:
            return DialogResponse(
                reply="Оставайтесь, пожалуйста, на линии.",
                step=STEP_ESCALATED,
                escalated=True,
            )

        if state.step == STEP_GREETING:
            return self._handle_greeting(state)

        if state.step == STEP_CHECK_REPRESENTATIVE:
            return self._handle_representative(state, raw_text)

        if state.step == STEP_ASK_NAME:
            return await self._handle_name(state, parsed)

        if state.step == STEP_ASK_SPECIALTY:
            return await self._handle_specialty(
                state,
                parsed,
                raw_text,
            )

        if state.step == STEP_OFFER_SLOTS:
            return self._handle_slot_choice(state, raw_text)

        if state.step == STEP_CONFIRM:
            return await self._handle_confirm(state, raw_text)

        if state.step == STEP_BOOKED:
            return DialogResponse(
                reply="Этот этап записи уже завершён. Хотите что-нибудь ещё?",
                step=state.step,
                visit_id=state.visit_id,
            )

        state.step = STEP_FALLBACK
        return DialogResponse(
            reply=(
                "Извините, я не поняла, на каком этапе мы остановились. "
                "Пожалуйста, повторите, что вы хотите сделать."
            ),
            step=state.step,
        )

    # ---------------------------------------------------------
    # Приветствие
    # ---------------------------------------------------------

    def _handle_greeting(self, state: DialogState) -> DialogResponse:
        state.failed_attempts = 0
        state.step = STEP_ASK_NAME

        return DialogResponse(
            reply=get_greeting_reply(),
            step=state.step,
        )

    # ---------------------------------------------------------
    # Проверка законного представителя
    # ---------------------------------------------------------

    def _handle_representative(
        self,
        state: DialogState,
        raw_text: str,
    ) -> DialogResponse:
        answer = self._classify_yes_no(raw_text)

        if answer == "yes":
            state.extra["is_child_confirmed"] = True
            state.full_name = " ".join(
                part
                for part in (
                    state.last_name,
                    state.first_name,
                    state.middle_name,
                )
                if part
            )
            state.failed_attempts = 0
            state.step = STEP_ASK_SPECIALTY

            return DialogResponse(
                reply=(
                    f"Хорошо. К какому специалисту записать "
                    f"{state.first_name or 'ребёнка'}?"
                ),
                step=state.step,
            )

        if answer == "no":
            state.step = STEP_BOOKED

            return DialogResponse(
                reply=(
                    "Извините, запись ребёнка по этому сценарию "
                    "возможна только через законного представителя. "
                    "Всего доброго."
                ),
                step=state.step,
            )

        return DialogResponse(
            reply=(
                "Пожалуйста, ответьте «да» или «нет». "
                "Вы являетесь родителем или законным представителем ребёнка?"
            ),
            step=state.step,
        )

    # ---------------------------------------------------------
    # Идентификация пациента
    # ---------------------------------------------------------

    async def _handle_name(
        self,
        state: DialogState,
        parsed: ParsedUtterance | None,
    ) -> DialogResponse:
        if parsed is None:
            return self._failed_attempt(
                state,
                "Не удалось распознать данные. Назовите, пожалуйста, "
                "фамилию, имя и дату рождения.",
            )

        # Позволяем пользователю сообщать данные частями.
        if parsed.last_name:
            state.last_name = parsed.last_name

        if parsed.first_name:
            state.first_name = parsed.first_name

        if parsed.middle_name:
            state.middle_name = parsed.middle_name

        if parsed.birth_date:
            state.birth_date = parsed.birth_date

        if not (
            state.last_name
            and state.first_name
            and state.birth_date
        ):
            return self._failed_attempt(
                state,
                "Пожалуйста, назовите фамилию, имя и дату рождения. "
                "Можно сообщить недостающие данные отдельно.",
            )

        try:
            age_info = LegalAge(state.birth_date)
        except (ValueError, TypeError):
            return self._failed_attempt(
                state,
                "Не удалось распознать дату рождения. "
                "Назовите её, пожалуйста, ещё раз.",
            )

        if not age_info.is_valid:
            return self._failed_attempt(
                state,
                "Дата рождения выглядит некорректно. "
                "Проверьте её и назовите ещё раз.",
            )

        if age_info.is_child:
            state.extra["is_child_confirmed"] = False
            state.step = STEP_CHECK_REPRESENTATIVE

            return DialogResponse(
                reply=(
                    f"Я правильно понимаю, что пациент — "
                    f"{state.first_name}? Запись ребёнка требует "
                    "участия родителя или законного представителя. "
                    "Вы являетесь таким представителем?"
                ),
                step=state.step,
            )

        state.full_name = " ".join(
            part
            for part in (
                state.last_name,
                state.first_name,
                state.middle_name,
            )
            if part
        )
        state.failed_attempts = 0
        state.step = STEP_ASK_SPECIALTY

        return DialogResponse(
            reply="Спасибо. К какому специалисту вы хотите записаться?",
            step=state.step,
        )

    # ---------------------------------------------------------
    # Выбор специальности
    # ---------------------------------------------------------

    async def _handle_specialty(
        self,
        state: DialogState,
        parsed: ParsedUtterance | None,
        raw_text: str,
    ) -> DialogResponse:
        specialty = parsed.specialty if parsed else None

        # При переносе пациент может выбрать прежнего специалиста.
        if state.is_reschedule and self._means_same_doctor(raw_text):
            specialty = state.specialty

        if not specialty:
            if state.is_reschedule:
                return DialogResponse(
                    reply=(
                        f"Вы хотите записаться к прежнему специалисту "
                        f"({state.specialty}) или к другому? "
                        "Если к другому, назовите его специальность."
                    ),
                    step=state.step,
                )

            return DialogResponse(
                reply=(
                    "Уточните, пожалуйста, специальность врача. "
                    "Например: терапевт, педиатр или кардиолог."
                ),
                step=state.step,
            )

        state.specialty = specialty

        try:
            slots = await self._schedule.get_free_slots(
                specialty=specialty,
                limit=3,
            )
        except Exception:
            logger.exception(
                "Ошибка получения свободных слотов для специальности %s",
                specialty,
            )
            return DialogResponse(
                reply=(
                    "Сейчас не удаётся проверить свободное время. "
                    "Попробуйте, пожалуйста, немного позже."
                ),
                step=state.step,
            )

        if not slots:
            return DialogResponse(
                reply=(
                    f"Сейчас я не нашёл свободных вариантов "
                    f"для специалиста «{specialty}». "
                    "Можете выбрать другую специальность."
                ),
                step=state.step,
            )

        state.offered_slots = list(slots)
        state.selected_slot = None
        state.step = STEP_OFFER_SLOTS

        lines = [
            (
                f"{index}. {slot.date} в {slot.start}, "
                f"{slot.doctor_name}"
            )
            for index, slot in enumerate(slots, start=1)
        ]

        return DialogResponse(
            reply=(
                "Вот доступные варианты:\n"
                + "\n".join(lines)
                + "\nКакой вариант вам подходит? Назовите номер."
            ),
            step=state.step,
        )

    # ---------------------------------------------------------
    # Выбор слота
    # ---------------------------------------------------------

    def _handle_slot_choice(
        self,
        state: DialogState,
        raw_text: str,
    ) -> DialogResponse:
        if not state.offered_slots:
            state.step = STEP_ASK_SPECIALTY
            return DialogResponse(
                reply=(
                    "Список вариантов больше недоступен. "
                    "Давайте заново выберем специальность."
                ),
                step=state.step,
            )

        index = self._extract_choice(
            raw_text,
            len(state.offered_slots),
        )

        if index is None:
            return DialogResponse(
                reply=(
                    f"Пожалуйста, назовите номер варианта от 1 "
                    f"до {len(state.offered_slots)}."
                ),
                step=state.step,
            )

        state.selected_slot = state.offered_slots[index]
        state.step = STEP_CONFIRM

        slot = state.selected_slot

        return DialogResponse(
            reply=(
                f"Записать вас к {slot.doctor_name} "
                f"на {slot.date} в {slot.start}? "
                "Скажите «да» или «нет»."
            ),
            step=state.step,
        )

    # ---------------------------------------------------------
    # Подтверждение
    # ---------------------------------------------------------

    async def _handle_confirm(
        self,
        state: DialogState,
        raw_text: str,
    ) -> DialogResponse:
        answer = self._classify_yes_no(raw_text)

        if answer == "no":
            state.selected_slot = None
            state.step = STEP_OFFER_SLOTS

            return DialogResponse(
                reply=(
                    "Хорошо, этот вариант не оформляю. "
                    "Выберите другой номер из предложенного списка."
                ),
                step=state.step,
            )

        if answer != "yes":
            return DialogResponse(
                reply=(
                    "Я не совсем поняла ответ. Вы подтверждаете "
                    "выбранное время? Скажите «да» или «нет»."
                ),
                step=state.step,
            )

        if state.selected_slot is None:
            state.step = STEP_OFFER_SLOTS
            return DialogResponse(
                reply=(
                    "Выбранное время не найдено. "
                    "Пожалуйста, выберите вариант ещё раз."
                ),
                step=state.step,
            )

        try:
            return await self._book(state)
        except Exception:
            # Не предлагаем сразу повторить создание: запрос к МИС мог
            # завершиться тайм-аутом после фактического создания записи.
            logger.exception(
                "Не удалось достоверно завершить оформление записи"
            )
            state.step = STEP_ESCALATED

            return DialogResponse(
                reply=(
                    "Не удалось подтвердить результат операции в медицинской "
                    "системе. Чтобы избежать двойной записи, передаю вопрос "
                    "администратору для проверки."
                ),
                step=state.step,
                escalated=True,
            )

    # ---------------------------------------------------------
    # Создание записи и перенос
    # ---------------------------------------------------------

    async def _book(self, state: DialogState) -> DialogResponse:
        slot = state.selected_slot

        if slot is None:
            raise ValueError("Не выбран слот для записи")

        if not (
            state.last_name
            and state.first_name
            and state.birth_date
        ):
            raise ValueError("Не заполнены обязательные данные пациента")

        is_reschedule = state.is_reschedule

        if is_reschedule and state.old_visit_id is None:
            raise ValueError(
                "Режим переноса активен, но ID исходной записи отсутствует"
            )

        # Если пациент уже найден в МИС при переносе, используем его ID.
        # Иначе находим или регистрируем клиента.
        client_id = state.client_id

        if client_id is None:
            client, _created = await self._mis.register_client(
                ClientCreate(
                    last_name=state.last_name,
                    first_name=state.first_name,
                    middle_name=state.middle_name,
                    birth_date=state.birth_date,
                )
            )
            client_id = client.id
            state.client_id = client_id

        doctor_id = await self._schedule.resolve_doctor_id(
            slot.doctor_excel_id,
            self._mis,
        )

        # Сначала создаём новую запись в МИС.
        visit = await self._mis.create_visit(
            VisitCreate(
                client_id=client_id,
                doctor_id=doctor_id,
                scheduled_at=slot.scheduled_at,
            )
        )

        state.visit_id = visit.id

        # Обновляем локальное расписание. Если обновление не удалось,
        # не повторяем создание визита: он уже создан в МИС.
        try:
            await self._schedule.book_slot(
                slot.doctor_excel_id,
                slot.scheduled_at,
            )
        except Exception:
            logger.exception(
                "Запись %s создана в МИС, но локальное расписание "
                "не удалось обновить",
                visit.id,
            )
            state.step = STEP_ESCALATED

            return DialogResponse(
                reply=(
                    f"Новая запись создана, её номер {visit.id}, "
                    "но возникла техническая проблема с обновлением "
                    "расписания. Передаю вопрос администратору."
                ),
                step=state.step,
                visit_id=visit.id,
                booked=True,
                escalated=True,
            )

        # При переносе отменяем старую запись только после создания новой.
        if is_reschedule:
            try:
                await self._mis.update_visit(
                    visit_id=state.old_visit_id,
                    data=VisitUpdate(
                        status=VisitStatus.CANCELLED,
                    ),
                )
            except Exception:
                logger.exception(
                    "Создана новая запись %s, но не удалось подтвердить "
                    "отмену старой записи %s",
                    visit.id,
                    state.old_visit_id,
                )

                state.step = STEP_ESCALATED

                return DialogResponse(
                    reply=(
                        f"Новая запись создана, её номер {visit.id}. "
                        "Однако мне не удалось подтвердить отмену прежней "
                        "записи. Чтобы исключить две активные записи, "
                        "соединяю вас с администратором для проверки."
                    ),
                    step=state.step,
                    visit_id=visit.id,
                    booked=True,
                    escalated=True,
                )

            logger.info(
                "Перенос завершён: старая запись %s отменена, "
                "новая запись %s создана",
                state.old_visit_id,
                visit.id,
            )

        state.step = STEP_BOOKED
        state.failed_attempts = 0

        if is_reschedule:
            reply_text = (
                f"Перенос выполнен. Вы записаны к {slot.doctor_name} "
                f"на {slot.date} в {slot.start}. "
                f"Номер новой записи: {visit.id}."
            )
        else:
            reply_text = (
                f"Готово! Вы записаны к {slot.doctor_name} "
                f"на {slot.date} в {slot.start}. "
                f"Номер записи: {visit.id}."
            )

        return DialogResponse(
            reply=reply_text + " Ждём вас в поликлинике.",
            step=state.step,
            visit_id=visit.id,
            booked=True,
        )

    # ---------------------------------------------------------
    # Вспомогательные методы
    # ---------------------------------------------------------

    def _failed_attempt(
        self,
        state: DialogState,
        message: str,
    ) -> DialogResponse:
        state.failed_attempts += 1

        if state.failed_attempts >= MAX_FAILED_ATTEMPTS:
            state.step = STEP_ESCALATED

            logger.warning(
                "Превышен лимит попыток ввода при записи на приём"
            )

            return DialogResponse(
                reply=(
                    "Не удалось подтвердить данные после нескольких "
                    "попыток. Передаю вопрос администратору."
                ),
                step=state.step,
                escalated=True,
            )

        return DialogResponse(
            reply=message,
            step=state.step,
        )

    @staticmethod
    def _classify_yes_no(text: str) -> str | None:
        """Определяет однозначный ответ: yes, no или None."""
        normalized = text.lower().replace("ё", "е").strip()
        normalized = re.sub(r"[^\w\s-]", " ", normalized)
        normalized = " ".join(normalized.split())

        yes_phrases = {
            "да",
            "да хочу",
            "да подтверждаю",
            "да согласен",
            "да согласна",
            "хочу",
            "подтверждаю",
            "верно",
            "все верно",
            "ага",
            "хорошо",
            "ок",
            "окей",
            "согласен",
            "согласна",
            "являюсь",
            "я родитель",
            "я мама",
            "я папа",
            "я опекун",
            "я законный представитель",
        }

        no_phrases = {
            "нет",
            "не хочу",
            "не надо",
            "не нужно",
            "не подтверждаю",
            "не согласен",
            "не согласна",
            "я не родитель",
            "я не мама",
            "я не папа",
            "я не опекун",
            "не являюсь",
            "отказываюсь",
            "другое",
            "другой",
            "изменить",
        }

        # Проверяем точные фразы, чтобы «не подтверждаю» не считалось
        # подтверждением из-за содержащегося в нём слова «подтверждаю».
        if normalized in no_phrases:
            return "no"

        if normalized in yes_phrases:
            return "yes"

        # Если ответ содержит одновременно согласие и отрицание,
        # не принимаем решение автоматически.
        padded = f" {normalized} "
        has_yes = any(f" {phrase} " in padded for phrase in yes_phrases)
        has_no = any(f" {phrase} " in padded for phrase in no_phrases)

        if has_yes and has_no:
            return None

        if has_no:
            return "no"

        if has_yes:
            return "yes"

        return None

    @staticmethod
    def _means_same_doctor(text: str) -> bool:
        normalized = text.lower().replace("ё", "е")
        phrases = (
            "к тому же",
            "к тому же врачу",
            "к тому же специалисту",
            "к прежнему",
            "к прежнему врачу",
            "к прежнему специалисту",
            "тот же",
            "того же",
            "к этому же",
        )
        return any(phrase in normalized for phrase in phrases)

    @staticmethod
    def _extract_choice(text: str, max_idx: int) -> int | None:
        normalized = text.lower().replace("ё", "е")

        for token in normalized.split():
            cleaned = token.strip(".,!?;:()")
            if cleaned.isdigit():
                number = int(cleaned)
                if 1 <= number <= max_idx:
                    return number - 1

        word_to_num = {
            "первый": 1,
            "первая": 1,
            "первое": 1,
            "один": 1,
            "одна": 1,
            "второй": 2,
            "вторая": 2,
            "второе": 2,
            "два": 2,
            "две": 2,
            "третий": 3,
            "третья": 3,
            "третье": 3,
            "три": 3,
        }

        words = re.findall(r"[а-яa-z0-9]+", normalized)

        for word in words:
            number = word_to_num.get(word)
            if number is not None and 1 <= number <= max_idx:
                return number - 1

        return None