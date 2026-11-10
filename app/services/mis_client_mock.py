
"""In-memory мок MIS-клиента для локальной разработки и тестов.

Реализует интерфейс MISClientProtocol без HTTP-запросов и токена.

Поддерживает:
- регистрацию и поиск клиентов;
- просмотр и обновление записей;
- проверку существования клиента и врача;
- ограничения на изменение терминальных записей;
- заявки на эскалацию;
- пагинацию и фильтрацию по датам;
- фиксированную временную зону GMT+4.

Все данные хранятся в памяти процесса и удаляются при reset().
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from itertools import count

from app.core.exceptions import (
    MISConflict,
    MISNotFound,
    MISValidationError,
)
from app.schemas.mis import (
    Client,
    ClientCreate,
    ClientUpdate,
    Doctor,
    Escalation,
    EscalationCreate,
    Visit,
    VisitCreate,
    VisitStatus,
    VisitUpdate,
)

GMT4 = timezone(timedelta(hours=4))
STAMP_FORMAT = "%d.%m.%Y %H:%M"
DATE_FORMAT = "%d.%m.%Y"


def _now() -> str:
    """Текущее время в формате стенда GMT+4."""
    return datetime.now(GMT4).strftime(STAMP_FORMAT)


def _normalize(value: str | None) -> str:
    """Нормализует строку для сравнения."""
    return " ".join((value or "").strip().casefold().split())


def _parse_datetime(value: str, *, field_name: str) -> datetime:
    """Разбирает дату в формате стенда."""
    if not isinstance(value, str) or not value.strip():
        raise MISValidationError(
            f"Поле «{field_name}» должно содержать дату",
            code="НЕВЕРНЫЙ_ЗАПРОС",
            status_code=400,
        )

    value = value.strip()

    for fmt in (STAMP_FORMAT, DATE_FORMAT):
        try:
            parsed = datetime.strptime(value, fmt)
            return parsed.replace(tzinfo=GMT4)
        except ValueError:
            continue

    raise MISValidationError(
        f"Некорректный формат поля «{field_name}»: {value!r}. "
        "Ожидается ДД.ММ.ГГГГ или ДД.ММ.ГГГГ ЧЧ:ММ.",
        code="НЕВЕРНЫЙ_ЗАПРОС",
        status_code=400,
    )


class MISClientMock:
    """In-memory реализация MIS-клиента для сценариев и тестов."""

    def __init__(self) -> None:
        self._doctors: dict[int, Doctor] = {}
        self._clients: dict[int, Client] = {}
        self._visits: dict[int, Visit] = {}
        self._escalations: dict[int, Escalation] = {}

        self._client_ids = count(1)
        self._visit_ids = count(1)
        self._escalation_ids = count(1)

    # ------------------------------------------------------------------
    # Вспомогательные методы
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_pagination(limit: int, offset: int) -> None:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("limit должен быть целым числом больше нуля")

        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValueError("offset должен быть целым неотрицательным числом")

    @staticmethod
    def _validate_id(value: int, field_name: str) -> None:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(
                f"{field_name} должен быть положительным целым числом"
            )

    @staticmethod
    def _not_found(object_name: str, object_id: int) -> MISNotFound:
        return MISNotFound(
            f"{object_name} {object_id} не найден",
            code="НЕ_НАЙДЕНО",
            status_code=404,
        )

    @staticmethod
    def _validation_error(message: str) -> MISValidationError:
        return MISValidationError(
            message,
            code="НЕВЕРНЫЙ_ЗАПРОС",
            status_code=400,
        )

    @staticmethod
    def _conflict(message: str) -> MISConflict:
        return MISConflict(
            message,
            code="КОНФЛИКТ_СТАТУСА",
            status_code=409,
        )

    @staticmethod
    def _copy_model(model):
        """Возвращает копию, не раскрывая внутренний объект хранилища."""
        return model.model_copy(deep=True)

    def seed_doctors(self, doctors: list[Doctor]) -> None:
        """Добавляет или заменяет записи в справочнике врачей."""
        for doctor in doctors:
            self._doctors[doctor.id] = self._copy_model(doctor)

    def reset(self) -> None:
        """Полностью очищает данные и сбрасывает последовательности ID."""
        self._doctors.clear()
        self._clients.clear()
        self._visits.clear()
        self._escalations.clear()

        # Повторное использование ID после reset допустимо:
        # хранилище полностью очищено.
        self._client_ids = count(1)
        self._visit_ids = count(1)
        self._escalation_ids = count(1)

    # ------------------------------------------------------------------
    # Врачи
    # ------------------------------------------------------------------

    async def list_doctors(
        self,
        specialty: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Doctor]:
        self._validate_pagination(limit, offset)

        items = sorted(self._doctors.values(), key=lambda doctor: doctor.id)

        if specialty and specialty.strip():
            needle = _normalize(specialty)
            items = [
                doctor
                for doctor in items
                if _normalize(doctor.specialty) == needle
            ]

        return [
            self._copy_model(doctor)
            for doctor in items[offset:offset + limit]
        ]

    # ------------------------------------------------------------------
    # Клиенты
    # ------------------------------------------------------------------

    async def register_client(
        self,
        data: ClientCreate,
    ) -> tuple[Client, bool]:
        key = (
            _normalize(data.last_name),
            _normalize(data.first_name),
            _normalize(data.birth_date),
        )

        for existing in self._clients.values():
            existing_key = (
                _normalize(existing.last_name),
                _normalize(existing.first_name),
                _normalize(existing.birth_date),
            )
            if existing_key == key:
                return self._copy_model(existing), False

        client_id = next(self._client_ids)

        client = Client(
            id=client_id,
            last_name=data.last_name.strip(),
            first_name=data.first_name.strip(),
            middle_name=data.middle_name,
            birth_date=data.birth_date.strip(),
            comment=data.comment,
            created_at=_now(),
        )

        self._clients[client_id] = client
        return self._copy_model(client), True

    async def find_clients(
        self,
        last_name: str | None = None,
        first_name: str | None = None,
        birth_date: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Client]:
        self._validate_pagination(limit, offset)

        items = sorted(self._clients.values(), key=lambda client: client.id)

        if last_name and last_name.strip():
            needle = _normalize(last_name)
            items = [
                client for client in items
                if _normalize(client.last_name) == needle
            ]

        if first_name and first_name.strip():
            needle = _normalize(first_name)
            items = [
                client for client in items
                if _normalize(client.first_name) == needle
            ]

        if birth_date and birth_date.strip():
            needle = _normalize(birth_date)
            items = [
                client for client in items
                if _normalize(client.birth_date) == needle
            ]

        return [
            self._copy_model(client)
            for client in items[offset:offset + limit]
        ]

    async def get_client(self, client_id: int) -> Client:
        self._validate_id(client_id, "client_id")

        client = self._clients.get(client_id)
        if client is None:
            raise self._not_found("Клиент", client_id)

        return self._copy_model(client)

    async def update_client(
        self,
        client_id: int,
        data: ClientUpdate,
    ) -> Client:
        client = await self.get_client(client_id)

        payload = data.model_dump(
            exclude_unset=True,
            exclude_none=True,
        )
        updated = client.model_copy(update=payload)

        self._clients[client_id] = updated
        return self._copy_model(updated)

    # ------------------------------------------------------------------
    # Записи на приём
    # ------------------------------------------------------------------

    async def create_visit(self, data: VisitCreate) -> Visit:
        if data.client_id not in self._clients:
            raise self._validation_error(
                "Поле «client_id» указывает на несуществующего клиента"
            )

        doctor = self._doctors.get(data.doctor_id)
        if doctor is None:
            raise self._validation_error(
                "Поле «doctor_id» указывает на врача вне справочника"
            )

        if not doctor.is_active:
            raise self._validation_error(
                "Запись к деактивированному врачу невозможна"
            )

        # Проверяем формат даты, не меняя исходную строку модели.
        _parse_datetime(data.scheduled_at, field_name="scheduled_at")

        visit_id = next(self._visit_ids)
        now = _now()

        visit = Visit(
            id=visit_id,
            client_id=data.client_id,
            doctor=self._copy_model(doctor),
            scheduled_at=data.scheduled_at,
            status=VisitStatus.PLANNED,
            created_at=now,
            updated_at=now,
        )

        self._visits[visit_id] = visit
        return self._copy_model(visit)

    async def list_visits(
        self,
        client_id: int | None = None,
        doctor_id: int | None = None,
        status: VisitStatus | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Visit]:
        self._validate_pagination(limit, offset)

        start = (
            _parse_datetime(date_from, field_name="date_from")
            if date_from
            else None
        )
        end = (
            _parse_datetime(date_to, field_name="date_to")
            if date_to
            else None
        )

        if start is not None and end is not None and start > end:
            raise self._validation_error(
                "date_from не может быть позже date_to"
            )

        items = list(self._visits.values())

        if client_id is not None:
            items = [visit for visit in items if visit.client_id == client_id]

        if doctor_id is not None:
            items = [
                visit for visit in items
                if visit.doctor.id == doctor_id
            ]

        if status is not None:
            items = [visit for visit in items if visit.status == status]

        if start is not None:
            items = [
                visit for visit in items
                if _parse_datetime(
                    visit.scheduled_at,
                    field_name="scheduled_at",
                ) >= start
            ]

        if end is not None:
            # Если верхняя граница задана только датой,
            # включаем весь этот день.
            if date_to and len(date_to.strip()) == 10:
                end = end + timedelta(days=1)

            items = [
                visit for visit in items
                if _parse_datetime(
                    visit.scheduled_at,
                    field_name="scheduled_at",
                ) < end
            ]

        items.sort(
            key=lambda visit: (
                _parse_datetime(
                    visit.scheduled_at,
                    field_name="scheduled_at",
                ),
                visit.id,
            )
        )

        return [
            self._copy_model(visit)
            for visit in items[offset:offset + limit]
        ]

    async def get_visit(self, visit_id: int) -> Visit:
        self._validate_id(visit_id, "visit_id")

        visit = self._visits.get(visit_id)
        if visit is None:
            raise self._not_found("Запись", visit_id)

        return self._copy_model(visit)

    async def update_visit(
        self,
        visit_id: int,
        data: VisitUpdate,
    ) -> Visit:
        visit = await self.get_visit(visit_id)

        payload = data.model_dump(
            exclude_unset=True,
            exclude_none=True,
        )

        # Отменённые и завершённые записи не редактируются.
        if visit.status in (
            VisitStatus.CANCELLED,
            VisitStatus.COMPLETED,
        ):
            raise self._conflict(
                f"Запись в статусе «{visit.status.value}» "
                "изменению не подлежит"
            )

        requested_status = payload.get("status")

        # У деактивированного врача можно только отменить запись.
        if not visit.doctor.is_active:
            is_cancellation = requested_status == VisitStatus.CANCELLED
            if not is_cancellation or set(payload) != {"status"}:
                raise self._conflict(
                    "Врач деактивирован: разрешена только отмена записи"
                )

        # В схеме Visit хранится объект doctor, а запрос может содержать
        # doctor_id. Проверяем врача и переводим ID в объект модели.
        if "doctor_id" in payload:
            doctor_id = payload.pop("doctor_id")
            if doctor_id is not None:
                doctor = self._doctors.get(doctor_id)

                if doctor is None:
                    raise self._validation_error(
                        "Поле «doctor_id» указывает на врача вне справочника"
                    )

                if not doctor.is_active:
                    raise self._validation_error(
                        "Нельзя перенести запись к деактивированному врачу"
                    )

                payload["doctor"] = self._copy_model(doctor)

        if "scheduled_at" in payload:
            _parse_datetime(
                payload["scheduled_at"],
                field_name="scheduled_at",
            )

        updated = visit.model_copy(
            update={
                **payload,
                "updated_at": _now(),
            }
        )

        self._visits[visit_id] = updated
        return self._copy_model(updated)

    # ------------------------------------------------------------------
    # Эскалации
    # ------------------------------------------------------------------

    async def create_escalation(
        self,
        data: EscalationCreate,
    ) -> Escalation:
        if data.client_id is not None and data.client_id not in self._clients:
            raise self._validation_error(
                "Поле «client_id» указывает на несуществующего клиента"
            )

        escalation_id = next(self._escalation_ids)

        escalation = Escalation(
            id=escalation_id,
            client_id=data.client_id,
            description=data.description,
            created_at=_now(),
        )

        self._escalations[escalation_id] = escalation
        return self._copy_model(escalation)

    async def list_escalations(
        self,
        client_id: int | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Escalation]:
        self._validate_pagination(limit, offset)

        items = list(self._escalations.values())

        if client_id is not None:
            items = [
                escalation
                for escalation in items
                if escalation.client_id == client_id
            ]

        items.sort(
            key=lambda escalation: (
                _parse_datetime(
                    escalation.created_at,
                    field_name="created_at",
                ),
                escalation.id,
            ),
            reverse=True,
        )

        return [
            self._copy_model(escalation)
            for escalation in items[offset:offset + limit]
        ]

    async def get_escalation(self, escalation_id: int) -> Escalation:
        self._validate_id(escalation_id, "escalation_id")

        escalation = self._escalations.get(escalation_id)
        if escalation is None:
            raise self._not_found("Заявка", escalation_id)

        return self._copy_model(escalation)
