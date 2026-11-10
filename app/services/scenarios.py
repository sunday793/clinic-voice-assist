
from __future__ import annotations

import inspect
import logging
from typing import Any

from app.schemas.mis import VisitCreate

logger = logging.getLogger(__name__)


async def _resolve(value: Any) -> Any:
    """Поддержать как синхронные, так и асинхронные методы."""
    if inspect.isawaitable(value):
        return await value
    return value


def _get_value(obj: Any, key: str, default: Any = None) -> Any:
    """Получить поле из словаря или объекта."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _slot_value(slot: Any) -> str:
    """Преобразовать выбранный слот в строку для ответа и МИС."""
    if isinstance(slot, str):
        return slot.strip()

    # Поддерживаем распространённые имена поля времени слота.
    for key in ("scheduled_at", "visit_date", "datetime", "date_time"):
        value = _get_value(slot, key)
        if value:
            return str(value).strip()

    return str(slot).strip()


async def _unlock_slot(
    slot_manager: Any,
    doctor_id: int,
    slot: Any,
) -> None:
    """Освободить слот; ошибку освобождения записать в лог."""
    try:
        await _resolve(
            slot_manager.unlock(
                doctor_id=doctor_id,
                slot=slot,
            )
        )
    except Exception:
        logger.exception(
            "Не удалось освободить слот: doctor_id=%s, slot=%r",
            doctor_id,
            slot,
        )


async def handle_booking(
    session: Any,
    user_text: str,
    slot_manager: Any,
    mis_client: Any,
) -> str:
    """Обработать запись пациента на приём.

    Ожидаемые состояния:
        ASK_SPECIALTY — выбрать врача и заблокировать слот;
        CONFIRM_SLOT — дождаться согласия пациента;
        BOOKING_DONE — запись уже создана.

    Предполагается, что slot_manager предоставляет suggest_and_lock()
    и unlock(), а MIS-клиент — create_visit(VisitCreate).
    """
    data = session.collected_data

    if session.state == "ASK_SPECIALTY":
        specialty = data.get("specialty")
        visit_date = data.get("date")
        client_id = data.get("client_id")

        if not specialty:
            return "К какому специалисту вы хотите записаться?"

        if not visit_date:
            return "На какую дату вы хотите записаться?"

        if client_id is None:
            return "Сначала необходимо уточнить ваши данные для записи."

        try:
            doctor = await _resolve(find_doctor(mis_client, specialty))
        except Exception:
            logger.exception(
                "Не удалось найти врача по специальности %r",
                specialty,
            )
            return "Не удалось подобрать врача. Попробуйте ещё раз или обратитесь к регистратору."

        if not doctor:
            return (
                f"Не удалось найти врача по специальности «{specialty}». "
                "Назовите другую специальность или обратитесь к регистратору."
            )

        doctor_id = _get_value(doctor, "id")
        if doctor_id is None:
            logger.error("У найденного врача отсутствует ID: %r", doctor)
            return "Не удалось определить врача. Обратитесь к регистратору."

        try:
            slot = await _resolve(
                slot_manager.suggest_and_lock(
                    doctor_id=doctor_id,
                    date=visit_date,
                    call_id=session.call_id,
                )
            )
        except Exception:
            logger.exception(
                "Ошибка поиска или блокировки слота: doctor_id=%s",
                doctor_id,
            )
            return "Не удалось проверить свободное время. Попробуйте ещё раз."

        if not slot:
            return "На эту дату свободных слотов нет. Назовите другую дату."

        # Сохраняем данные только после успешной блокировки.
        data["proposed_slot"] = slot
        data["doctor_id"] = doctor_id
        data["doctor_name"] = _get_value(doctor, "full_name", "")
        session.state = "CONFIRM_SLOT"

        return f"Есть свободное время: {_slot_value(slot)}. Подходит?"

    if session.state == "CONFIRM_SLOT":
        slot = data.get("proposed_slot")
        doctor_id = data.get("doctor_id")
        client_id = data.get("client_id")

        if slot is None or doctor_id is None or client_id is None:
            logger.error(
                "Неполное состояние записи: call_id=%r, data=%r",
                getattr(session, "call_id", None),
                data,
            )
            session.state = "ASK_SPECIALTY"
            data.pop("proposed_slot", None)
            data.pop("doctor_id", None)
            return "Потерялись данные выбранного времени. Давайте подберём время заново."

        if is_yes(user_text):
            scheduled_at = _slot_value(slot)

            if not scheduled_at:
                await _unlock_slot(slot_manager, doctor_id, slot)
                data.pop("proposed_slot", None)
                session.state = "ASK_SPECIALTY"
                return "Не удалось определить время приёма. Давайте выберем другое время."

            try:
                # В соответствии с интерфейсом MIS передаём VisitCreate.
                # Проверь, что в app.schemas.mis поле называется scheduled_at
                # и принимает дату в формате ДД.ММ.ГГГГ ЧЧ:ММ.
                visit_data = VisitCreate(
                    client_id=client_id,
                    doctor_id=doctor_id,
                    scheduled_at=scheduled_at,
                )
                visit = await _resolve(
                    mis_client.create_visit(visit_data)
                )

            except Exception:
                logger.exception(
                    "Не удалось создать запись: call_id=%r, "
                    "client_id=%r, doctor_id=%r, slot=%r",
                    getattr(session, "call_id", None),
                    client_id,
                    doctor_id,
                    slot,
                )

                # Если МИС не подтвердила создание записи, освобождаем
                # временную блокировку, чтобы слот не завис навсегда.
                await _unlock_slot(slot_manager, doctor_id, slot)

                data.pop("proposed_slot", None)
                data.pop("doctor_id", None)
                data.pop("doctor_name", None)
                session.state = "ASK_SPECIALTY"

                return (
                    "Не получилось подтвердить запись в медицинской системе. "
                    "Пожалуйста, попробуйте ещё раз или обратитесь к регистратору."
                )

            # Освобождаем временную блокировку после успешного ответа МИС.
            await _unlock_slot(slot_manager, doctor_id, slot)

            visit_id = _get_value(visit, "id")
            if visit_id is not None:
                data["visit_id"] = visit_id

            data["booked_slot"] = scheduled_at
            session.state = "BOOKING_DONE"

            return (
                f"Вы записаны на {scheduled_at}. "
                "Пожалуйста, приходите к назначенному времени."
            )

        if is_no(user_text):
            # Сначала освобождаем старый слот.
            await _unlock_slot(slot_manager, doctor_id, slot)

            data.pop("proposed_slot", None)

            try:
                new_slot = await _resolve(
                    slot_manager.suggest_and_lock(
                        doctor_id=doctor_id,
                        date=data.get("date"),
                        call_id=session.call_id,
                    )
                )
            except Exception:
                logger.exception(
                    "Не удалось подобрать альтернативный слот: "
                    "doctor_id=%s",
                    doctor_id,
                )
                session.state = "ASK_SPECIALTY"
                data.pop("doctor_id", None)
                return "Не удалось подобрать другое время. Назовите другую дату."

            if not new_slot:
                session.state = "ASK_SPECIALTY"
                data.pop("doctor_id", None)
                data.pop("doctor_name", None)
                return "На эту дату больше нет свободного времени. Назовите другую дату."

            # Не предлагаем тот же слот повторно.
            if _slot_value(new_slot) == _slot_value(slot):
                await _unlock_slot(slot_manager, doctor_id, new_slot)
                session.state = "ASK_SPECIALTY"
                data.pop("doctor_id", None)
                data.pop("doctor_name", None)
                return (
                    "Другого времени на эту дату пока нет. "
                    "Назовите другую дату."
                )

            data["proposed_slot"] = new_slot
            return f"Тогда есть время: {_slot_value(new_slot)}. Подходит?"

        return "Пожалуйста, ответьте «да», если время подходит, или «нет», если хотите выбрать другое."

    if session.state == "BOOKING_DONE":
        return "Запись уже оформлена. Хотите узнать что-нибудь ещё?"

    return "Давайте продолжим запись. Назовите специальность врача."


# ВАЖНО:
# find_doctor(), is_yes() и is_no() должны быть определены в проекте
# или импортированы из соответствующих модулей.
