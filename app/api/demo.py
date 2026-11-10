
"""HTTP API для демонстрации диалогового движка и обзвона (C-10).

Маршруты:
- POST /demo/dialog           — реплика пациента и ответ ассистента.
- POST /demo/reset            — сброс состояния диалога.
- GET  /demo/state/{call_id}  — состояние диалога (отладка).
- POST /demo/nlu              — результат разбора NLU (отладка).
- POST /demo/outbound/test    — тестовый исходящий звонок.
- POST /demo/outbound/run-now — запуск обзвона записей на завтра.

Важно: глобальные объекты рассчитаны на текущую MVP-архитектуру.
При нескольких worker-процессах состояние в памяти не будет общим.
"""

from __future__ import annotations

import logging
import re
import uuid as uuidlib
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.config import settings
from app.services.dialog import DialogEngine
from app.services.dialog.state import DialogState
from app.services.mis_factory import create_mis_client
from app.services.outbound import OutboundCaller
from app.services.schedule import get_schedule

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/demo", tags=["demo"])


# ---------------------------------------------------------------------
# Сервисы
# ---------------------------------------------------------------------

_mis = create_mis_client()
_schedule = get_schedule()

_engine = DialogEngine(
    mis=_mis,
    schedule=_schedule,
    today=settings.SCHEDULE_TODAY,
)

_outbound = OutboundCaller(
    mis=_mis,
    engine=_engine,
)


# ---------------------------------------------------------------------
# Общая базовая схема
# ---------------------------------------------------------------------


class APIModel(BaseModel):
    """Общие настройки схем запросов и ответов."""

    model_config = ConfigDict(extra="ignore")


# ---------------------------------------------------------------------
# Схемы — диалог
# ---------------------------------------------------------------------


class DialogRequest(APIModel):
    """Реплика пациента."""

    call_id: str = Field(
        min_length=1,
        max_length=200,
        description="Внутренний ID звонка",
    )
    text: str = Field(
        min_length=1,
        max_length=5000,
        description="Реплика пациента",
    )

    @field_validator("call_id", "text")
    @classmethod
    def validate_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Значение не должно быть пустым")
        return value


class DialogResponseSchema(APIModel):
    """Ответ диалогового движка."""

    reply: str
    step: str
    visit_id: int | None = None
    booked: bool = False
    escalated: bool = False
    red_flag_category: str | None = None


class ResetRequest(APIModel):
    """Запрос на сброс состояния диалога."""

    call_id: str = Field(
        min_length=1,
        max_length=200,
        description="ID звонка, состояние которого нужно сбросить",
    )

    @field_validator("call_id")
    @classmethod
    def validate_call_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("call_id не должен быть пустым")
        return value


class ResetResponse(APIModel):
    """Результат сброса диалога."""

    ok: bool = True


class StateResponse(APIModel):
    """Состояние диалога для отладки."""

    call_id: str
    step: str
    last_name: str | None = None
    first_name: str | None = None
    birth_date: str | None = None
    specialty: str | None = None
    visit_id: int | None = None


class NLUResponse(APIModel):
    """Результат извлечения данных из реплики."""

    intent: str
    specialty: str | None = None
    last_name: str | None = None
    first_name: str | None = None
    birth_date: str | None = None


# ---------------------------------------------------------------------
# Схемы — исходящий обзвон (C-10)
# ---------------------------------------------------------------------


class OutboundTestRequest(APIModel):
    """Запрос на тестовый исходящий звонок."""

    phone: str = Field(
        min_length=1,
        max_length=32,
        description="Российский номер, например +79001234567",
        examples=["+79001234567"],
    )
    scenario: Literal["reminder", "waitlist"] = Field(
        default="reminder",
        description="Сценарий звонка: reminder или waitlist",
    )
    visit_id: int | None = Field(
        default=None,
        ge=1,
        description=(
            "ID записи для сценария reminder. "
            "Если не указан, используется тестовый звонок без записи."
        ),
    )

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, value: str) -> str:
        """Проверить российский номер и привести его к формату +7XXXXXXXXXX."""
        value = value.strip()

        # Разрешаем распространённые разделители при вводе номера.
        if not re.fullmatch(r"[+0-9()\-\s]+", value):
            raise ValueError(
                "Некорректный номер телефона. "
                "Используйте российский номер, например +79001234567"
            )

        digits = re.sub(r"\D", "", value)

        if len(digits) == 11 and digits.startswith("8"):
            digits = "7" + digits[1:]
        elif len(digits) == 10:
            digits = "7" + digits

        if len(digits) != 11 or not digits.startswith("7"):
            raise ValueError(
                "Некорректный номер телефона. "
                "Ожидается российский номер, например +79001234567"
            )

        return f"+{digits}"


class OutboundTestResponse(APIModel):
    """Результат попытки инициировать звонок."""

    status: Literal["initiated", "failed"]
    uniqueid: str | None = Field(
        default=None,
        description="AMI Uniqueid звонка",
    )
    call_id: str = Field(description="Внутренний ID звонка")
    message: str = Field(description="Описание результата")


class OutboundRunNowRequest(APIModel):
    """Запрос на запуск обзвона."""

    force: bool = Field(
        default=True,
        description=(
            "Игнорировать временное окно обзвона. "
            "Для реальной эксплуатации следует использовать false."
        ),
    )
    limit: int = Field(
        default=5,
        ge=1,
        le=50,
        description="Максимальное количество звонков за запуск",
    )


class OutboundRunNowResponse(APIModel):
    """Результат запуска обзвона."""

    status: Literal["ok", "no_visits", "error"]
    visit_ids: list[int] = Field(
        default_factory=list,
        description="ID записей, для которых инициирован звонок",
    )
    message: str


# ---------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------


def _get_dialog_state(call_id: str) -> DialogState | None:
    """Получить состояние из текущего хранилища DialogEngine.

    Сейчас используется внутреннее хранилище движка для совместимости
    с существующей реализацией. В дальнейшем предпочтительнее добавить
    публичные методы получения и удаления состояния в DialogEngine.
    """
    return _engine._states.get(call_id)  # noqa: SLF001


def _remove_dialog_state(call_id: str) -> None:
    """Удалить состояние из текущего хранилища DialogEngine."""
    _engine._states.pop(call_id, None)  # noqa: SLF001


def _create_reminder_state(
    call_id: str,
    visit_id: int | None,
) -> None:
    """Подготовить состояние для сценария напоминания."""
    _engine._states[call_id] = DialogState(  # noqa: SLF001
        call_id=call_id,
        scenario="reminder",
        step="reminder_greeting",
        visit_id=visit_id,
    )


# ---------------------------------------------------------------------
# Эндпоинты — диалог
# ---------------------------------------------------------------------


@router.post(
    "/dialog",
    response_model=DialogResponseSchema,
    summary="Обработать реплику пациента",
)
async def dialog(request: DialogRequest) -> DialogResponseSchema:
    """Обработать одну реплику пациента."""
    try:
        response = await _engine.handle(
            request.call_id,
            request.text,
        )
    except Exception:
        logger.exception(
            "Ошибка обработки диалога call_id=%s",
            request.call_id,
        )

        # Возвращаем предусмотренный для демо ответ с эскалацией.
        # Фактическое соединение с оператором зависит от реализации движка
        # и не должно считаться выполненным только по этому ответу.
        return DialogResponseSchema(
            reply=(
                "Извините, произошла техническая ошибка. "
                "Соединяю с регистратором."
            ),
            step="escalated",
            escalated=True,
        )

    return DialogResponseSchema(
        reply=response.reply,
        step=response.step,
        visit_id=response.visit_id,
        booked=response.booked,
        escalated=response.escalated,
        red_flag_category=response.red_flag_category,
    )


@router.post(
    "/reset",
    response_model=ResetResponse,
    summary="Сбросить состояние диалога",
)
async def reset(
    call_id: str = Query(
        ...,
        min_length=1,
        max_length=200,
        description="ID звонка",
    ),
) -> ResetResponse:
    """Сбросить состояние диалога.

    Для совместимости call_id передаётся query-параметром:
    POST /demo/reset?call_id=...
    """
    if not call_id.strip():
        raise HTTPException(
            status_code=422,
            detail="call_id не должен быть пустым",
        )

    _remove_dialog_state(call_id)
    return ResetResponse(ok=True)


@router.get(
    "/state/{call_id}",
    response_model=StateResponse,
    summary="Получить состояние диалога",
)
async def state(call_id: str) -> StateResponse:
    """Посмотреть состояние диалога для отладки."""
    st = _get_dialog_state(call_id)

    if st is None:
        raise HTTPException(
            status_code=404,
            detail="Диалог не найден",
        )

    return StateResponse(
        call_id=st.call_id,
        step=st.step,
        last_name=st.last_name,
        first_name=st.first_name,
        birth_date=st.birth_date,
        specialty=st.specialty,
        visit_id=st.visit_id,
    )


@router.post(
    "/nlu",
    response_model=NLUResponse,
    summary="Проверить разбор NLU",
)
async def nlu_debug(request: DialogRequest) -> NLUResponse:
    """Показать, какие данные NLU извлёк из текста."""
    from app.services.nlu import parse

    try:
        parsed = await parse(
            request.text,
            settings.SCHEDULE_TODAY,
        )
    except Exception as exc:
        logger.exception("Ошибка NLU")
        raise HTTPException(
            status_code=500,
            detail="Не удалось обработать текст с помощью NLU",
        ) from exc

    return NLUResponse(
        intent=parsed.intent,
        specialty=parsed.specialty,
        last_name=parsed.last_name,
        first_name=parsed.first_name,
        birth_date=parsed.birth_date,
    )


# ---------------------------------------------------------------------
# Эндпоинты — исходящий обзвон (C-10)
# ---------------------------------------------------------------------


@router.post(
    "/outbound/test",
    response_model=OutboundTestResponse,
    summary="Тестовый исходящий звонок через AMI",
    description=(
        "Инициирует исходящий звонок через Asterisk AMI.\n\n"
        "Для проверки демо используйте российский номер телефона. "
        "Результат initiated означает, что запрос на звонок принят "
        "сервисом, но не гарантирует, что абонент ответил."
    ),
)
async def outbound_test(
    req: OutboundTestRequest,
) -> OutboundTestResponse:
    """Инициировать тестовый исходящий звонок."""
    call_id = f"outbound_test_{uuidlib.uuid4().hex[:8]}"

    if req.scenario == "reminder":
        _create_reminder_state(
            call_id=call_id,
            visit_id=req.visit_id,
        )

    try:
        uniqueid = await _outbound.ami_originate(
            phone=req.phone,
            call_id=call_id,
            visit_id=req.visit_id or 0,
        )
    except Exception as exc:
        logger.exception(
            "Ошибка инициации исходящего звонка call_id=%s",
            call_id,
        )
        _remove_dialog_state(call_id)

        raise HTTPException(
            status_code=502,
            detail="Не удалось инициировать звонок через AMI",
        ) from exc

    if uniqueid is None:
        _remove_dialog_state(call_id)

        return OutboundTestResponse(
            status="failed",
            uniqueid=None,
            call_id=call_id,
            message=(
                "AMI не подтвердил инициацию звонка. "
                "Проверьте состояние Asterisk и журналы приложения."
            ),
        )

    return OutboundTestResponse(
        status="initiated",
        uniqueid=str(uniqueid),
        call_id=call_id,
        message=(
            f"Звонок на {req.phone} передан в AMI. "
            f"Uniqueid: {uniqueid}. "
            "Фактический результат вызова проверьте в журналах Asterisk."
        ),
    )


@router.post(
    "/outbound/run-now",
    response_model=OutboundRunNowResponse,
    summary="Запустить обзвон записей на завтра",
    description=(
        "Находит записи на завтра и инициирует звонки. "
        "При force=true временное окно обзвона игнорируется. "
        "В рабочей эксплуатации необходимо ограничить доступ к этому "
        "эндпоинту и использовать force=false."
    ),
)
async def outbound_run_now(
    req: OutboundRunNowRequest,
) -> OutboundRunNowResponse:
    """Запустить обзвон записей на завтра."""
    if not settings.OUTBOUND_ENABLED:
        return OutboundRunNowResponse(
            status="error",
            message="Обзвон отключён настройкой OUTBOUND_ENABLED",
        )

    try:
        visit_ids = await _outbound.run_once(
            force=req.force,
            limit=req.limit,
        )
    except Exception:
        logger.exception("Ошибка запуска исходящего обзвона")

        return OutboundRunNowResponse(
            status="error",
            message=(
                "Не удалось выполнить обзвон. "
                "Подробности доступны в журнале приложения."
            ),
        )

    if not visit_ids:
        return OutboundRunNowResponse(
            status="no_visits",
            message=(
                "Записей на завтра для обзвона не найдено "
                "либо подходящие записи уже обработаны."
            ),
        )

    return OutboundRunNowResponse(
        status="ok",
        visit_ids=visit_ids,
        message=f"Задача обзвона выполнена. Обработано записей: {len(visit_ids)}.",
    )
