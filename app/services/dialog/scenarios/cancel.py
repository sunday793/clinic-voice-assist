
"""Сценарий C-04 «Отмена записи»."""

from __future__ import annotations

import logging
import re

from app.schemas.mis import VisitStatus, VisitUpdate
from app.services.dialog.base import ScenarioProtocol
from app.services.dialog.state import (
    DialogResponse,
    DialogState,
    STEP_ASK_NAME,
    STEP_BOOKED,
    STEP_ESCALATED,
    STEP_GREETING,
)
from app.services.inspections.legal_age import LegalAge
from app.services.mis_client import MISClientProtocol
from app.services.nlu import ParsedUtterance
from app.services.red_flags import RedFlag

logger = logging.getLogger(__name__)

STEP_CANCEL_OFFER_RESCHEDULE = "cancel_offer_reschedule"
STEP_CANCEL_CONFIRM = "cancel_confirm"
STEP_CANCEL_CONFIRM_REPRESENTATIVE = "cancel_confirm_representative"

MAX_FAILED_ATTEMPTS = 2


class CancelScenario(ScenarioProtocol):
    """Идентификация пациента, выбор действия и отмена визита."""

    name = "cancel"

    def __init__(self, mis: MISClientProtocol) -> None:
        self._mis = mis

    async def handle(
        self,
        state: DialogState,
        parsed: ParsedUtterance | None = None,
        red_flag: RedFlag | None = None,
        raw_text: str = "",
    ) -> DialogResponse:
        # Не продолжаем сценарий после эскалации.
        if state.step == STEP_ESCALATED:
            return DialogResponse(
                reply="Оставайтесь, пожалуйста, на линии.",
                step=STEP_ESCALATED,
                escalated=True,
            )

        if state.step == STEP_GREETING:
            state.failed_attempts = 0
            state.step = STEP_ASK_NAME
            return DialogResponse(
                reply=(
                    "Здравствуйте! Я помогу отменить вашу запись. "
                    "Назовите, пожалуйста, фамилию, имя и дату рождения."
                ),
                step=state.step,
            )

        if state.step == STEP_ASK_NAME:
            return await self._handle_patient_identification(
                state,
                parsed,
            )

        if state.step == STEP_CANCEL_CONFIRM_REPRESENTATIVE:
            return await self._handle_representative(
                state,
                raw_text,
            )

        if state.step == STEP_CANCEL_OFFER_RESCHEDULE:
            return self._handle_reschedule_offer(state, raw_text)

        if state.step == STEP_CANCEL_CONFIRM:
            return await self._handle_cancel_confirmation(
                state,
                raw_text,
            )

        state.step = STEP_GREETING
        return DialogResponse(
            reply=(
                "Извините, я потерял контекст разговора. "
                "Давайте начнём сначала."
            ),
            step=state.step,
        )

    # ---------------------------------------------------------
    # Идентификация пациента
    # ---------------------------------------------------------

    async def _handle_patient_identification(
        self,
        state: DialogState,
        parsed: ParsedUtterance | None,
    ) -> DialogResponse:
        if parsed is None:
            return self._failed_attempt(
                state,
                "Не удалось распознать данные. "
                "Назовите фамилию, имя и дату рождения.",
            )

        # Сохраняем распознанные поля, даже если пользователь сообщил
        # их частями в нескольких репликах.
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
                "Пожалуйста, назовите её ещё раз.",
            )

        if not age_info.is_valid:
            return self._failed_attempt(
                state,
                "Дата рождения выглядит некорректно. "
                "Проверьте её и повторите.",
            )

        if age_info.is_child:
            state.failed_attempts = 0
            state.step = STEP_CANCEL_CONFIRM_REPRESENTATIVE

            return DialogResponse(
                reply=(
                    "Отмена записи ребёнка по этому сценарию "
                    "возможна через родителя или законного представителя. "
                    "Вы являетесь таким представителем?"
                ),
                step=state.step,
            )

        return await self._find_client_and_visits(state)

    # ---------------------------------------------------------
    # Подтверждение законного представителя
    # ---------------------------------------------------------

    async def _handle_representative(
        self,
        state: DialogState,
        raw_text: str,
    ) -> DialogResponse:
        answer = self._classify_yes_no(raw_text)

        if answer == "no":
            state.step = STEP_BOOKED
            return DialogResponse(
                reply=(
                    "Извините, отмена записи ребёнка возможна "
                    "только через законного представителя. "
                    "Всего доброго."
                ),
                step=state.step,
            )

        if answer != "yes":
            return DialogResponse(
                reply=(
                    "Пожалуйста, ответьте «да» или «нет». "
                    "Вы являетесь родителем или законным представителем?"
                ),
                step=state.step,
            )

        return await self._find_client_and_visits(state)

    # ---------------------------------------------------------
    # Поиск пациента и его активных записей
    # ---------------------------------------------------------

    async def _find_client_and_visits(
        self,
        state: DialogState,
    ) -> DialogResponse:
        if not (
            state.last_name
            and state.first_name
            and state.birth_date
        ):
            state.step = STEP_ASK_NAME
            return DialogResponse(
                reply=(
                    "Для поиска записи мне нужны фамилия, имя "
                    "и дата рождения. Назовите, пожалуйста, эти данные."
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

        try:
            clients = await self._mis.find_clients(
                last_name=state.last_name,
                first_name=state.first_name,
                birth_date=state.birth_date,
            )
        except Exception:
            logger.exception("Ошибка поиска пациента в МИС")
            state.step = STEP_ESCALATED
            return DialogResponse(
                reply=(
                    "Сейчас не удаётся проверить данные в медицинской "
                    "системе. Соединяю вас с сотрудником регистратуры."
                ),
                step=state.step,
                escalated=True,
            )

        if not clients:
            return self._failed_attempt(
                state,
                "Я не нашёл пациента с такими данными. "
                "Проверьте фамилию, имя и дату рождения.",
            )

        # Не выбираем молча первого пациента, если МИС вернула
        # несколько совпадений: это может привести к отмене чужой записи.
        if len(clients) > 1:
            logger.warning(
                "Для отмены найдено несколько пациентов: %s",
                len(clients),
            )
            state.step = STEP_ESCALATED
            return DialogResponse(
                reply=(
                    "В медицинской системе найдено несколько совпадений. "
                    "Чтобы не отменить чужую запись, соединяю вас "
                    "с регистратурой для уточнения данных."
                ),
                step=state.step,
                escalated=True,
            )

        client = clients[0]
        state.client_id = client.id
        state.failed_attempts = 0

        try:
            visits = await self._mis.list_visits(
                client_id=client.id,
                status=VisitStatus.PLANNED,
            )
        except Exception:
            logger.exception(
                "Ошибка получения записей пациента %s",
                client.id,
            )
            state.step = STEP_ESCALATED
            return DialogResponse(
                reply=(
                    "Не удалось проверить ваши активные записи. "
                    "Соединяю вас с регистратурой."
                ),
                step=state.step,
                escalated=True,
            )

        if not visits:
            state.step = STEP_BOOKED
            return DialogResponse(
                reply=(
                    "Я не нашёл активных записей, которые можно отменить. "
                    "Всего доброго!"
                ),
                step=state.step,
            )

        # Не предполагаем, что visits[0] — ближайшая запись.
        # Если записей несколько, уточняем выбор у пациента.
        if len(visits) > 1:
            state.extra["cancel_visits"] = list(visits)
            state.step = STEP_CANCEL_OFFER_RESCHEDULE

            lines = []
            for index, visit in enumerate(visits, start=1):
                doctor_name = getattr(
                    getattr(visit, "doctor", None),
                    "full_name",
                    "специалист не указан",
                )
                scheduled_at = getattr(
                    visit,
                    "scheduled_at",
                    "время не указано",
                )
                lines.append(
                    f"{index}. {doctor_name}, {scheduled_at}"
                )

            state.extra["cancel_select_visit"] = True

            return DialogResponse(
                reply=(
                    "У вас несколько активных записей:\n"
                    + "\n".join(lines)
                    + "\nНазовите номер записи, которую хотите отменить "
                    "или перенести."
                ),
                step=state.step,
            )

        return self._set_visit_and_offer_action(
            state,
            visits[0],
        )

    def _set_visit_and_offer_action(
        self,
        state: DialogState,
        visit,
    ) -> DialogResponse:
        state.visit_id = visit.id
        state.old_visit_id = visit.id
        state.old_scheduled_at = visit.scheduled_at

        doctor = getattr(visit, "doctor", None)
        state.specialty = getattr(doctor, "specialty", None)

        state.extra.pop("cancel_select_visit", None)
        state.extra.pop("cancel_visits", None)

        state.step = STEP_CANCEL_OFFER_RESCHEDULE

        doctor_name = getattr(
            doctor,
            "full_name",
            "специалисту",
        )

        return DialogResponse(
            reply=(
                f"Я вижу вашу запись к {doctor_name} "
                f"на {visit.scheduled_at}. "
                "Вы хотите перенести её на другое время "
                "или отменить?"
            ),
            step=state.step,
        )

    # ---------------------------------------------------------
    # Выбор записи и предложение переноса
    # ---------------------------------------------------------

    def _handle_reschedule_offer(
        self,
        state: DialogState,
        raw_text: str,
    ) -> DialogResponse:
        if state.extra.get("cancel_select_visit"):
            visits = state.extra.get("cancel_visits", [])
            index = self._extract_choice(raw_text, len(visits))

            if index is None:
                return DialogResponse(
                    reply=(
                        f"Пожалуйста, назовите номер записи "
                        f"от 1 до {len(visits)}."
                    ),
                    step=state.step,
                )

            return self._set_visit_and_offer_action(
                state,
                visits[index],
            )

        choice = self._classify_cancel_or_reschedule(raw_text)

        if choice == "reschedule":
            if state.visit_id is None:
                state.step = STEP_ESCALATED
                return DialogResponse(
                    reply=(
                        "Не удалось определить запись для переноса. "
                        "Соединяю вас с регистратурой."
                    ),
                    step=state.step,
                    escalated=True,
                )

            state.is_reschedule = True
            state.old_visit_id = state.visit_id
            state.scenario = "transfer"

            # TransferScenario начнёт собственный сценарий.
            # Идентификационные данные и ID исходного визита сохраняются.
            state.step = STEP_GREETING

            return DialogResponse(
                reply="Хорошо, помогу подобрать новое время.",
                step=state.step,
            )

        if choice == "cancel":
            if state.visit_id is None:
                state.step = STEP_ESCALATED
                return DialogResponse(
                    reply=(
                        "Не удалось определить запись для отмены. "
                        "Соединяю вас с регистратурой."
                    ),
                    step=state.step,
                    escalated=True,
                )

            state.step = STEP_CANCEL_CONFIRM

            return DialogResponse(
                reply=(
                    f"Вы хотите отменить запись на "
                    f"{state.old_scheduled_at}. "
                    "Подтверждаете отмену? Скажите «да» или «нет»."
                ),
                step=state.step,
            )

        return DialogResponse(
            reply=(
                "Пожалуйста, уточните: вы хотите перенести запись "
                "или отменить её?"
            ),
            step=state.step,
        )

    # ---------------------------------------------------------
    # Финальное подтверждение отмены
    # ---------------------------------------------------------

    async def _handle_cancel_confirmation(
        self,
        state: DialogState,
        raw_text: str,
    ) -> DialogResponse:
        answer = self._classify_yes_no(raw_text)

        if answer == "no":
            state.step = STEP_BOOKED
            return DialogResponse(
                reply=(
                    "Хорошо, оставляю запись в силе. "
                    "Всего доброго!"
                ),
                step=state.step,
            )

        if answer != "yes":
            return DialogResponse(
                reply=(
                    "Я не понял ответ. Вы действительно хотите "
                    "отменить запись? Скажите «да» или «нет»."
                ),
                step=state.step,
            )

        if state.visit_id is None:
            logger.error(
                "Невозможно отменить запись: visit_id отсутствует"
            )
            state.step = STEP_ESCALATED
            return DialogResponse(
                reply=(
                    "Не удалось определить запись для отмены. "
                    "Соединяю вас с регистратурой."
                ),
                step=state.step,
                escalated=True,
            )

        try:
            await self._mis.update_visit(
                visit_id=state.visit_id,
                data=VisitUpdate(status=VisitStatus.CANCELLED),
            )
        except Exception:
            logger.exception(
                "Не удалось отменить запись %s",
                state.visit_id,
            )
            state.step = STEP_ESCALATED
            return DialogResponse(
                reply=(
                    "Не удалось подтвердить отмену записи в медицинской "
                    "системе. Чтобы проверить её статус, соединяю вас "
                    "с сотрудником регистратуры."
                ),
                step=state.step,
                escalated=True,
            )

        cancelled_visit_id = state.visit_id
        state.step = STEP_BOOKED

        logger.info(
            "Запись %s отменена для пациента %s",
            cancelled_visit_id,
            state.client_id,
        )

        return DialogResponse(
            reply=(
                "Готово, запрос на отмену записи выполнен. "
                "Спасибо, что предупредили. Всего доброго!"
            ),
            step=state.step,
            visit_id=cancelled_visit_id,
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
            return DialogResponse(
                reply=(
                    "Не удалось подтвердить данные после нескольких "
                    "попыток. Соединяю вас с регистратурой."
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
        """Возвращает yes, no или None для неоднозначного ответа."""
        text = text.lower().replace("ё", "е").strip()
        text = re.sub(r"[^\w\s-]", " ", text)
        text = " ".join(text.split())

        no_phrases = (
            "нет",
            "не надо",
            "не нужно",
            "не подтверждаю",
            "не отменяйте",
            "не отменять",
            "оставьте",
            "оставить как есть",
            "не хочу",
            "не являюсь",
            "я не мама",
            "я не папа",
            "я не опекун",
            "я не родитель",
        )

        yes_phrases = (
            "да",
            "подтверждаю",
            "подтверждаю отмену",
            "отменяйте",
            "отменяй",
            "согласен",
            "согласна",
            "хорошо",
            "верно",
            "ага",
            "являюсь",
            "я родитель",
            "я мама",
            "я папа",
            "я опекун",
            "я законный представитель",
        )

        # Сначала проверяем отрицания.
        if any(
            text == phrase or text.startswith(phrase + " ")
            for phrase in no_phrases
        ):
            return "no"

        if any(
            text == phrase or text.startswith(phrase + " ")
            for phrase in yes_phrases
        ):
            return "yes"

        return None

    @staticmethod
    def _classify_cancel_or_reschedule(
        text: str,
    ) -> str | None:
        text = text.lower().replace("ё", "е").strip()
        text = re.sub(r"[^\w\s-]", " ", text)
        text = " ".join(text.split())

        # Явные отрицания важнее отдельных совпавших слов.
        cancel_negations = (
            "не отменять",
            "не отменяйте",
            "не отменяй",
            "не хочу отменять",
            "не надо отменять",
        )
        reschedule_negations = (
            "не переносить",
            "не переносите",
            "не хочу переносить",
            "не надо переносить",
        )

        if any(phrase in text for phrase in cancel_negations):
            return "reschedule"

        if any(phrase in text for phrase in reschedule_negations):
            return "cancel"

        cancel_words = (
            "отменить",
            "отмена",
            "отменяй",
            "отменяйте",
            "просто отмен",
        )
        reschedule_words = (
            "перенести",
            "перенос",
            "перезапис",
            "другое время",
            "другой день",
        )

        wants_cancel = any(word in text for word in cancel_words)
        wants_reschedule = any(word in text for word in reschedule_words)

        if wants_cancel and wants_reschedule:
            return None

        if wants_reschedule:
            return "reschedule"

        if wants_cancel:
            return "cancel"

        return None

    @staticmethod
    def _extract_choice(
        text: str,
        max_idx: int,
    ) -> int | None:
        for token in text.split():
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
            "четвертый": 4,
            "четвертая": 4,
            "четвертое": 4,
            "четыре": 4,
            "пятый": 5,
            "пятая": 5,
            "пятое": 5,
            "пять": 5,
        }

        words = re.findall(
            r"[а-яa-z0-9]+",
            text.lower().replace("ё", "е"),
        )

        for word in words:
            number = word_to_num.get(word)
            if number is not None and 1 <= number <= max_idx:
                return number - 1

        return None
