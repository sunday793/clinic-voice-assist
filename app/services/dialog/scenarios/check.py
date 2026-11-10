
"""Сценарий C-05 «Проверка своей записи»."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from app.schemas.mis import VisitStatus
from app.services.dialog.base import ScenarioProtocol
from app.services.dialog.state import (
    DialogResponse,
    DialogState,
    STEP_ASK_NAME,
    STEP_BOOKED,
    STEP_ESCALATED,
    STEP_GREETING,
)
from app.services.mis_client import MISClientProtocol
from app.services.nlu import ParsedUtterance
from app.services.red_flags import RedFlag

logger = logging.getLogger(__name__)

STEP_CHECK_RESULT = "check_result"


class CheckScenario(ScenarioProtocol):
    """Проверяет запланированные записи пациента в MIS."""

    name = "check"

    def __init__(self, mis: MISClientProtocol) -> None:
        self._mis = mis

    async def handle(
        self,
        state: DialogState,
        parsed: ParsedUtterance | None = None,
        red_flag: RedFlag | None = None,
        raw_text: str = "",
    ) -> DialogResponse:
        # Красные флаги должны обрабатываться общим DialogEngine.
        # Здесь не продолжаем обычный сценарий, если он уже эскалирован.
        if state.step == STEP_ESCALATED:
            return DialogResponse(
                reply="Оставайтесь, пожалуйста, на линии. Вам поможет сотрудник клиники.",
                step=state.step,
                escalated=True,
            )

        if state.step in (STEP_GREETING, "greeting"):
            state.failed_attempts = 0
            state.step = STEP_ASK_NAME
            return DialogResponse(
                reply=(
                    "Чтобы проверить запись, назовите, пожалуйста, "
                    "фамилию, имя и дату рождения."
                ),
                step=state.step,
            )

        if state.step == STEP_ASK_NAME:
            return await self._handle_name(state, parsed)

        if state.step == STEP_CHECK_RESULT:
            state.step = STEP_BOOKED
            return DialogResponse(
                reply="Рад был помочь. Могу ли я ещё чем-нибудь помочь?",
                step=state.step,
            )

        # Неизвестное состояние: возвращаем диалог к началу сценария.
        state.step = STEP_GREETING
        return DialogResponse(
            reply="Извините, давайте начнём сначала. Я помогу проверить вашу запись.",
            step=state.step,
        )

    async def _handle_name(
        self,
        state: DialogState,
        parsed: ParsedUtterance | None,
    ) -> DialogResponse:
        if (
            parsed is None
            or not parsed.last_name
            or not parsed.first_name
            or not parsed.birth_date
        ):
            state.failed_attempts += 1
            state.step = STEP_ASK_NAME

            if state.failed_attempts >= 3:
                state.step = STEP_ESCALATED
                logger.warning(
                    "Не удалось получить данные пациента для проверки записи"
                )
                return DialogResponse(
                    reply=(
                        "Не удалось разобрать данные. "
                        "Передаю запрос сотруднику клиники."
                    ),
                    step=state.step,
                    escalated=True,
                )

            return DialogResponse(
                reply=(
                    "Не удалось разобрать данные. Назовите, пожалуйста, "
                    "фамилию, имя и дату рождения ещё раз."
                ),
                step=state.step,
            )

        state.last_name = parsed.last_name.strip()
        state.first_name = parsed.first_name.strip()
        state.middle_name = (
            parsed.middle_name.strip() if parsed.middle_name else None
        )
        state.birth_date = parsed.birth_date.strip()
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

        try:
            clients = await self._mis.find_clients(
                last_name=state.last_name,
                first_name=state.first_name,
                birth_date=state.birth_date,
            )
        except Exception:
            logger.exception(
                "Ошибка MIS при поиске пациента для проверки записи"
            )
            return self._mis_error(state)

        if not clients:
            state.step = STEP_BOOKED
            return DialogResponse(
                reply=(
                    "Не удалось найти пациента с такими данными. "
                    "Проверьте фамилию, имя и дату рождения "
                    "или обратитесь к сотруднику клиники."
                ),
                step=state.step,
            )

        # Не выбираем случайный профиль, если MIS вернул несколько совпадений.
        if len(clients) > 1:
            logger.warning(
                "MIS вернул несколько профилей для проверки записи"
            )
            state.step = STEP_ESCALATED
            return DialogResponse(
                reply=(
                    "В базе найдено несколько совпадений. "
                    "Чтобы не сообщить вам неверные сведения, "
                    "передаю запрос сотруднику клиники."
                ),
                step=state.step,
                escalated=True,
            )

        client = clients[0]

        try:
            visits = await self._mis.list_visits(
                client_id=client.id,
                status=VisitStatus.PLANNED,
            )
        except Exception:
            logger.exception(
                "Ошибка MIS при получении записей пациента"
            )
            return self._mis_error(state)

        state.step = STEP_CHECK_RESULT

        if not visits:
            return DialogResponse(
                reply=(
                    "По данным системы, у вас нет запланированных записей. "
                    "Могу помочь с другим вопросом?"
                ),
                step=state.step,
            )

        # Сортируем только даты, которые удалось однозначно разобрать.
        # Если формат неизвестен, сохраняем исходный порядок MIS.
        visits = self._sort_visits(visits)

        lines: list[str] = []
        for index, visit in enumerate(visits, start=1):
            scheduled_at = getattr(visit, "scheduled_at", None)
            doctor = getattr(visit, "doctor", None)
            doctor_name = getattr(doctor, "full_name", None)

            date_text = str(scheduled_at) if scheduled_at else "дата не указана"
            doctor_text = str(doctor_name) if doctor_name else "врач не указан"

            if len(visits) == 1:
                lines.append(f"Вы записаны на {date_text} к {doctor_text}.")
            else:
                lines.append(f"{index}. {date_text}, врач: {doctor_text}.")

        if len(visits) == 1:
            reply = (
                f"{lines[0]} Возьмите с собой необходимые документы. "
                "Могу помочь с другим вопросом?"
            )
        else:
            reply = (
                "В системе найдены следующие запланированные записи:\n"
                + "\n".join(lines)
                + "\nМогу помочь с другим вопросом?"
            )

        return DialogResponse(reply=reply, step=state.step)

    @staticmethod
    def _sort_visits(visits: list[Any]) -> list[Any]:
        """Сортирует записи по дате, если формат даты распознаваем."""

        parsed_dates: list[tuple[float, int, Any]] = []

        for index, visit in enumerate(visits):
            value = getattr(visit, "scheduled_at", None)
            parsed_date = CheckScenario._parse_datetime(value)

            if parsed_date is None:
                # Не меняем порядок, если хотя бы одну дату нельзя разобрать.
                return visits

            parsed_dates.append((parsed_date.timestamp(), index, visit))

        parsed_dates.sort(key=lambda item: (item[0], item[1]))
        return [item[2] for item in parsed_dates]

    @staticmethod
    def _parse_datetime(value: Any) -> datetime | None:
        if isinstance(value, datetime):
            return value

        if not isinstance(value, str) or not value.strip():
            return None

        value = value.strip()

        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            pass

        for date_format in (
            "%d.%m.%Y %H:%M:%S",
            "%d.%m.%Y %H:%M",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%d.%m.%Y",
            "%Y-%m-%d",
        ):
            try:
                return datetime.strptime(value, date_format)
            except ValueError:
                continue

        return None

    @staticmethod
    def _mis_error(state: DialogState) -> DialogResponse:
        state.step = STEP_ESCALATED
        return DialogResponse(
            reply=(
                "Сейчас не удаётся получить сведения из медицинской системы. "
                "Передаю запрос сотруднику клиники."
            ),
            step=state.step,
            escalated=True,
        )
