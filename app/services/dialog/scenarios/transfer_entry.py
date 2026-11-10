"""Сценарий C-03 «Перенос записи»."""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

from app.schemas.mis import VisitStatus
from app.services.dialog.base import ScenarioProtocol
from app.services.dialog.state import (
    DialogResponse,
    DialogState,
    STEP_ASK_NAME,
    STEP_ASK_SPECIALTY,
    STEP_BOOKED,
    STEP_ESCALATED,
    STEP_GREETING,
)
from app.services.inspections.legal_age import LegalAge
from app.services.mis_client import MISClientProtocol
from app.services.nlu import ParsedUtterance
from app.services.red_flags import RedFlag

logger = logging.getLogger(__name__)

STEP_RESCHEDULE_CONFIRM = "reschedule_confirm"
STEP_TRANSFER_CONFIRM_REPRESENTATIVE = "transfer_confirm_representative"
STEP_SELECT_OLD_VISIT = "transfer_select_old_visit"

MAX_FAILED_ATTEMPTS = 3


class TransferScenario(ScenarioProtocol):
    """Сценарий поиска и переноса существующей записи."""

    name = "transfer"

    def __init__(self, mis: MISClientProtocol) -> None:
        self._mis = mis

    async def handle(
        self,
        state: DialogState,
        parsed: ParsedUtterance | None = None,
        red_flag: RedFlag | None = None,
        raw_text: str = "",
    ) -> DialogResponse:
        # После эскалации сценарий не должен продолжать перенос.
        if state.step == STEP_ESCALATED:
            return DialogResponse(
                reply="Оставайтесь, пожалуйста, на линии.",
                step=STEP_ESCALATED,
                escalated=True,
            )

        # Начало сценария.
        if state.step == STEP_GREETING:
            state.failed_attempts = 0
            state.step = STEP_ASK_NAME
            return DialogResponse(
                reply=(
                    "Здравствуйте! Я помогу перенести вашу запись. "
                    "Назовите, пожалуйста, фамилию, имя и дату рождения."
                ),
                step=state.step,
            )

        # Подтверждение, что звонящий является представителем ребёнка.
        if state.step == STEP_TRANSFER_CONFIRM_REPRESENTATIVE:
            answer = self._classify_yes_no(raw_text)

            if answer == "yes":
                state.step = STEP_ASK_NAME
                state.failed_attempts = 0
                return DialogResponse(
                    reply=(
                        "Хорошо. Назовите, пожалуйста, фамилию, имя "
                        "и дату рождения ребёнка."
                    ),
                    step=state.step,
                )

            if answer == "no":
                state.step = STEP_BOOKED
                return DialogResponse(
                    reply=(
                        "Извините, перенос записи ребёнка возможен "
                        "только через законного представителя. Всего доброго."
                    ),
                    step=state.step,
                )

            return DialogResponse(
                reply="Пожалуйста, ответьте «да» или «нет». Вы родитель или законный представитель ребёнка?",
                step=state.step,
            )

        # Выбор существующей записи, если их несколько.
        if state.step == STEP_SELECT_OLD_VISIT:
            return self._select_old_visit(state, raw_text)

        # Идентификация пациента.
        if state.step == STEP_ASK_NAME:
            if parsed is None or not (
                parsed.last_name
                and parsed.first_name
                and parsed.birth_date
            ):
                return self._failed_attempt(
                    state,
                    "Не удалось распознать данные. Назовите, пожалуйста, фамилию, имя и дату рождения.",
                )

            state.last_name = parsed.last_name
            state.first_name = parsed.first_name
            state.middle_name = parsed.middle_name
            state.birth_date = parsed.birth_date
            state.full_name = " ".join(
                part
                for part in (
                    parsed.last_name,
                    parsed.first_name,
                    parsed.middle_name,
                )
                if part
            )

            # Проверяем дату рождения и возраст.
            try:
                age_info = LegalAge(state.birth_date)
            except (ValueError, TypeError):
                return self._failed_attempt(
                    state,
                    "Не удалось распознать дату рождения. Повторите её, пожалуйста.",
                )

            if not age_info.is_valid:
                return self._failed_attempt(
                    state,
                    "Дата рождения некорректна. Повторите её, пожалуйста.",
                )

            if age_info.is_child:
                state.step = STEP_TRANSFER_CONFIRM_REPRESENTATIVE
                return DialogResponse(
                    reply=(
                        "Перенос записи ребёнка возможен только через "
                        "родителя или законного представителя. Вы являетесь "
                        "родителем или законным представителем?"
                    ),
                    step=state.step,
                )

            # Ищем клиента в МИС.
            try:
                clients = await self._mis.find_clients(
                    last_name=state.last_name,
                    first_name=state.first_name,
                    birth_date=state.birth_date,
                )
            except Exception:
                logger.exception(
                    "Ошибка поиска клиента в МИС при переносе записи"
                )
                return DialogResponse(
                    reply=(
                        "Сейчас не удаётся проверить данные в медицинской "
                        "системе. Попробуйте, пожалуйста, немного позже."
                    ),
                    step=state.step,
                )

            if not clients:
                return self._failed_attempt(
                    state,
                    "Я не нашёл пациента в базе. Проверьте фамилию, имя и дату рождения.",
                )

            if len(clients) > 1:
                # Не выбираем первого пациента, если совпадений несколько.
                # Здесь требуется дополнительная процедура идентификации.
                logger.warning(
                    "Несколько совпадений пациента при переносе записи"
                )
                return DialogResponse(
                    reply=(
                        "В системе найдено несколько совпадений. "
                        "Я не могу безопасно выбрать запись автоматически. "
                        "Пожалуйста, обратитесь к администратору."
                    ),
                    step=state.step,
                    escalated=True,
                )

            client = clients[0]
            state.client_id = client.id

            # Получаем активные записи пациента.
            try:
                visits = await self._mis.list_visits(
                    client_id=client.id,
                    status=VisitStatus.PLANNED,
                )
            except Exception:
                logger.exception(
                    "Ошибка получения записей пациента из МИС"
                )
                return DialogResponse(
                    reply=(
                        "Сейчас не удаётся проверить ваши записи. "
                        "Попробуйте, пожалуйста, немного позже."
                    ),
                    step=state.step,
                )

            if not visits:
                state.step = STEP_GREETING
                state.scenario = None
                return DialogResponse(
                    reply=(
                        "У вас нет активных записей, которые можно перенести. "
                        "Если хотите оформить новую запись, скажите об этом."
                    ),
                    step=state.step,
                )

            if len(visits) == 1:
                return self._offer_old_visit(state, visits[0])

            # Несколько записей: сохраняем их в extra для выбора.
            state.extra["reschedule_visits"] = visits
            state.step = STEP_SELECT_OLD_VISIT

            lines = []
            for index, visit in enumerate(visits, start=1):
                doctor_name = getattr(
                    getattr(visit, "doctor", None),
                    "full_name",
                    "специалист",
                )
                scheduled_at = self._format_datetime(
                    getattr(visit, "scheduled_at", None)
                )
                lines.append(
                    f"{index}. {doctor_name}, {scheduled_at}"
                )

            return DialogResponse(
                reply=(
                    "У вас несколько активных записей. Какую из них "
                    "вы хотите перенести? "
                    + " ".join(lines)
                    + " Назовите номер записи."
                ),
                step=state.step,
            )

        # Подтверждение выбранной записи.
        if state.step == STEP_RESCHEDULE_CONFIRM:
            answer = self._classify_yes_no(raw_text)

            if answer == "no":
                state.step = STEP_BOOKED
                state.is_reschedule = False
                state.old_visit_id = None
                state.old_scheduled_at = None
                return DialogResponse(
                    reply="Хорошо, оставляю текущую запись без изменений. Всего доброго!",
                    step=state.step,
                )

            if answer == "yes":
                # Сохраняем ID старой записи отдельно.
                # Отменять старую запись здесь нельзя: сначала нужно
                # успешно создать новую в BookingScenario.
                state.is_reschedule = True
                state.scenario = "booking"
                state.step = STEP_ASK_SPECIALTY

                return DialogResponse(
                    reply=(
                        "Хорошо, подберём новое время. Вы хотите записаться "
                        f"к тому же специалисту ({state.specialty}) "
                        "или к другому?"
                    ),
                    step=state.step,
                )

            return DialogResponse(
                reply="Пожалуйста, ответьте «да» или «нет». Вы хотите перенести эту запись?",
                step=state.step,
            )

        return DialogResponse(
            reply="Извините, я не понял, на каком этапе мы остановились. Давайте начнём перенос заново.",
            step=STEP_GREETING,
        )

    async def _select_old_visit(
        self,
        state: DialogState,
        raw_text: str,
    ) -> DialogResponse:
        """Обрабатывает выбор одной из нескольких существующих записей."""
        visits = state.extra.get("reschedule_visits", [])
        choice = self._parse_choice(raw_text)

        if choice is None or not 1 <= choice <= len(visits):
            return DialogResponse(
                reply=f"Пожалуйста, назовите номер записи от 1 до {len(visits)}.",
                step=state.step,
            )

        visit = visits[choice - 1]
        state.extra.pop("reschedule_visits", None)
        return self._offer_old_visit(state, visit)

    def _offer_old_visit(
        self,
        state: DialogState,
        visit: Any,
    ) -> DialogResponse:
        """Сохраняет выбранную запись и запрашивает подтверждение переноса."""
        state.old_visit_id = visit.id
        state.old_scheduled_at = visit.scheduled_at
        state.visit_id = visit.id

        doctor = getattr(visit, "doctor", None)
        state.specialty = getattr(doctor, "specialty", None)
        doctor_name = getattr(doctor, "full_name", "специалиста")
        scheduled_at = self._format_datetime(visit.scheduled_at)

        state.step = STEP_RESCHEDULE_CONFIRM

        return DialogResponse(
            reply=(
                f"Я вижу вашу запись к {doctor_name} на {scheduled_at}. "
                "Вы хотите перенести её?"
            ),
            step=state.step,
            visit_id=visit.id,
        )

    def _failed_attempt(
        self,
        state: DialogState,
        message: str,
    ) -> DialogResponse:
        """Ограничивает число повторных попыток ввода."""
        state.failed_attempts += 1

        if state.failed_attempts >= MAX_FAILED_ATTEMPTS:
            state.step = STEP_ESCALATED
            logger.warning(
                "Превышено число попыток идентификации при переносе записи"
            )
            return DialogResponse(
                reply=(
                    "Не удалось подтвердить данные после нескольких попыток. "
                    "Передаю вопрос администратору."
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
        """
        Возвращает yes, no или None для неоднозначного ответа.

        Намеренно использует ограниченный набор фраз, чтобы не считать
        любое вхождение слова «да» или «нет» подтверждением.
        """
        normalized = " ".join(
            text.lower().replace("ё", "е").strip().split()
        )
        normalized = normalized.strip(".,!?;:")

        yes_phrases = {
            "да",
            "да хочу",
            "хочу",
            "подтверждаю",
            "верно",
            "все верно",
            "всё верно",
            "согласен",
            "согласна",
            "да подтверждаю",
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
            "я не родитель",
            "я не являюсь",
            "не являюсь",
            "отказываюсь",
        }

        if normalized in no_phrases:
            return "no"

        if normalized in yes_phrases:
            return "yes"

        return None

    @staticmethod
    def _parse_choice(text: str) -> int | None:
        """Извлекает номер выбранной записи."""
        normalized = text.strip().lower()

        for token in normalized.split():
            cleaned = token.strip(".,!?;:()")
            if cleaned.isdigit():
                return int(cleaned)

        return None

    @staticmethod
    def _format_datetime(value: Any) -> str:
        """Форматирует дату/время записи без предположений о типе."""
        if isinstance(value, datetime):
            return value.strftime("%d.%m.%Y в %H:%M")

        if isinstance(value, date):
            return value.strftime("%d.%m.%Y")

        if value is None:
            return "время не указано"

        return str(value)
