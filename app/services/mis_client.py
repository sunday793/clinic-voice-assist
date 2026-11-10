
"""REST-клиент тестового стенда «Регистратура».

MISClientProtocol — общий интерфейс для реального клиента и мока.
MISClient — асинхронная HTTP-реализация.

Особенности:
- обработка HTTP-ошибок и ограничения частоты запросов;
- поддержка Retry-After в секундах и в формате HTTP-date;
- повторная регистрация клиента: HTTP 200 означает created=False;
- сериализация Pydantic-моделей в JSON;
- единая обработка HTTP-запросов и валидация пагинации.

Форматы API должны соответствовать фактической документации стенда.
"""
from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Protocol, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from app.core.exceptions import (
    MISConflict,
    MISError,
    MISNotFound,
    MISRateLimited,
    MISServerError,
    MISUnauthorized,
    MISValidationError,
)
from app.schemas.mis import (
    Client,
    ClientCreate,
    ClientUpdate,
    Doctor,
    Escalation,
    EscalationCreate,
    MISErrorBody,
    Visit,
    VisitCreate,
    VisitStatus,
    VisitUpdate,
)

ModelT = TypeVar("ModelT", bound=BaseModel)


class MISClientProtocol(Protocol):
    """Интерфейс клиента MIS."""

    async def list_doctors(
        self,
        specialty: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Doctor]: ...

    async def register_client(
        self,
        data: ClientCreate,
    ) -> tuple[Client, bool]: ...

    async def find_clients(
        self,
        last_name: str | None = None,
        first_name: str | None = None,
        birth_date: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Client]: ...

    async def get_client(self, client_id: int) -> Client: ...

    async def update_client(
        self,
        client_id: int,
        data: ClientUpdate,
    ) -> Client: ...

    async def create_visit(self, data: VisitCreate) -> Visit: ...

    async def list_visits(
        self,
        client_id: int | None = None,
        doctor_id: int | None = None,
        status: VisitStatus | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Visit]: ...

    async def get_visit(self, visit_id: int) -> Visit: ...

    async def update_visit(
        self,
        visit_id: int,
        data: VisitUpdate,
    ) -> Visit: ...

    async def create_escalation(
        self,
        data: EscalationCreate,
    ) -> Escalation: ...

    async def list_escalations(
        self,
        client_id: int | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Escalation]: ...

    async def get_escalation(self, escalation_id: int) -> Escalation: ...


class MISClient:
    """Асинхронный HTTP-клиент стенда «Регистратура»."""

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        timeout: float = 10.0,
    ) -> None:
        if not base_url or not base_url.strip():
            raise ValueError("base_url не должен быть пустым")

        if not token or not token.strip():
            raise ValueError("token не должен быть пустым")

        if timeout <= 0:
            raise ValueError("timeout должен быть больше нуля")

        self._base_url = base_url.rstrip("/")
        self._token = token.strip()
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Accept": "application/json",
            },
            timeout=timeout,
        )

    async def aclose(self) -> None:
        """Закрывает HTTP-сессию."""
        await self._client.aclose()

    async def __aenter__(self) -> MISClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    @staticmethod
    def _validate_pagination(limit: int, offset: int) -> None:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("limit должен быть целым числом больше нуля")

        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValueError("offset должен быть целым неотрицательным числом")

    @staticmethod
    def _validate_id(value: int, name: str) -> None:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"{name} должен быть положительным целым числом")

    @staticmethod
    def _retry_after_seconds(value: str | None) -> int | None:
        """Преобразует Retry-After в количество секунд ожидания."""
        if not value:
            return None

        value = value.strip()

        try:
            # Стандартный вариант: количество секунд.
            return max(0, int(value))
        except ValueError:
            pass

        try:
            # Альтернативный вариант: HTTP-date.
            retry_at = parsedate_to_datetime(value)
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=timezone.utc)

            now = datetime.now(timezone.utc)
            seconds = (retry_at.astimezone(timezone.utc) - now).total_seconds()
            return max(0, int(seconds + 0.999))
        except (TypeError, ValueError, OverflowError):
            return None

    @staticmethod
    def _error_details(response: httpx.Response) -> tuple[str | None, str]:
        """Извлекает код и сообщение из тела ошибки, если оно распознаётся."""
        fallback = f"Ошибка MIS: HTTP {response.status_code}"

        try:
            payload = response.json()
        except ValueError:
            message = response.text.strip()
            return None, message or fallback

        try:
            body = MISErrorBody.model_validate(payload)
            raw_code = body.code
            code = getattr(raw_code, "value", raw_code)
            message = body.message
            return str(code) if code is not None else None, message or fallback
        except (ValidationError, AttributeError, TypeError, ValueError):
            pass

        # Запасной вариант для распространённых форматов JSON-ошибки.
        if isinstance(payload, dict):
            raw_code = payload.get("code")
            raw_message = payload.get("message") or payload.get("detail")
            code = getattr(raw_code, "value", raw_code)

            if isinstance(raw_message, str) and raw_message.strip():
                return (
                    str(code) if code is not None else None,
                    raw_message.strip(),
                )

        return None, fallback

    def _raise_from_response(self, response: httpx.Response) -> None:
        """Преобразует ошибочный HTTP-ответ в исключение MIS."""
        status = response.status_code
        code, message = self._error_details(response)

        if status == 429:
            retry_after = self._retry_after_seconds(
                response.headers.get("Retry-After")
            )
            raise MISRateLimited(
                message,
                retry_after=retry_after,
                code=code,
                status_code=status,
            )

        if status == 401:
            raise MISUnauthorized(
                message,
                code=code,
                status_code=status,
            )

        if status == 404:
            raise MISNotFound(
                message,
                code=code,
                status_code=status,
            )

        if status == 409:
            raise MISConflict(
                message,
                code=code,
                status_code=status,
            )

        if status in (400, 422):
            raise MISValidationError(
                message,
                code=code,
                status_code=status,
            )

        if status >= 500:
            raise MISServerError(
                message,
                code=code,
                status_code=status,
            )

        if status >= 400:
            raise MISError(
                message,
                code=code,
                status_code=status,
            )

        raise MISError(
            f"Неожиданный HTTP-ответ: {status}",
            code=code,
            status_code=status,
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        expected_statuses: tuple[int, ...] = (200,),
        **kwargs: object,
    ) -> httpx.Response:
        """Выполняет запрос и проверяет допустимый HTTP-статус."""
        response = await self._client.request(
            method,
            path,
            **kwargs,
        )

        if response.status_code not in expected_statuses:
            self._raise_from_response(response)

        return response

    @staticmethod
    def _parse_model(
        model: type[ModelT],
        response: httpx.Response,
    ) -> ModelT:
        """Валидирует JSON-ответ как Pydantic-модель."""
        try:
            payload = response.json()
            return model.model_validate(payload)
        except (ValueError, ValidationError, TypeError) as exc:
            raise MISError(
                f"Некорректный ответ MIS для модели {model.__name__}: {exc}",
                code="INVALID_RESPONSE",
                status_code=response.status_code,
            ) from exc

    @staticmethod
    def _parse_list(
        model: type[ModelT],
        response: httpx.Response,
    ) -> list[ModelT]:
        """Валидирует список объектов из JSON-ответа."""
        try:
            payload = response.json()
            if not isinstance(payload, list):
                raise ValueError("Ожидался JSON-массив")

            return [model.model_validate(item) for item in payload]
        except (ValueError, ValidationError, TypeError) as exc:
            raise MISError(
                f"Некорректный список MIS для модели {model.__name__}: {exc}",
                code="INVALID_RESPONSE",
                status_code=response.status_code,
            ) from exc

    @staticmethod
    def _dump_create(data: BaseModel) -> dict[str, object]:
        """Сериализует модель создания, исключая отсутствующие значения."""
        return data.model_dump(mode="json", exclude_none=True)

    @staticmethod
    def _dump_update(data: BaseModel) -> dict[str, object]:
        """Сериализует только явно заданные поля модели обновления."""
        return data.model_dump(
            mode="json",
            exclude_unset=True,
            exclude_none=True,
        )

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

        params: dict[str, object] = {
            "limit": limit,
            "offset": offset,
        }
        if specialty and specialty.strip():
            params["specialty"] = specialty.strip()

        response = await self._request(
            "GET",
            "/doctors",
            params=params,
        )
        return self._parse_list(Doctor, response)

    # ------------------------------------------------------------------
    # Клиенты
    # ------------------------------------------------------------------

    async def register_client(
        self,
        data: ClientCreate,
    ) -> tuple[Client, bool]:
        response = await self._request(
            "POST",
            "/clients",
            expected_statuses=(200, 201),
            json=self._dump_create(data),
        )
        client = self._parse_model(Client, response)
        created = response.status_code == 201
        return client, created

    async def find_clients(
        self,
        last_name: str | None = None,
        first_name: str | None = None,
        birth_date: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Client]:
        self._validate_pagination(limit, offset)

        params: dict[str, object] = {
            "limit": limit,
            "offset": offset,
        }

        if last_name and last_name.strip():
            params["last_name"] = last_name.strip()
        if first_name and first_name.strip():
            params["first_name"] = first_name.strip()
        if birth_date and birth_date.strip():
            params["birth_date"] = birth_date.strip()

        response = await self._request(
            "GET",
            "/clients",
            params=params,
        )
        return self._parse_list(Client, response)

    async def get_client(self, client_id: int) -> Client:
        self._validate_id(client_id, "client_id")
        response = await self._request("GET", f"/clients/{client_id}")
        return self._parse_model(Client, response)

    async def update_client(
        self,
        client_id: int,
        data: ClientUpdate,
    ) -> Client:
        self._validate_id(client_id, "client_id")
        response = await self._request(
            "PATCH",
            f"/clients/{client_id}",
            json=self._dump_update(data),
        )
        return self._parse_model(Client, response)

    # ------------------------------------------------------------------
    # Записи на приём
    # ------------------------------------------------------------------

    async def create_visit(self, data: VisitCreate) -> Visit:
        response = await self._request(
            "POST",
            "/visits",
            expected_statuses=(200, 201),
            json=self._dump_create(data),
        )
        return self._parse_model(Visit, response)

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

        params: dict[str, object] = {
            "limit": limit,
            "offset": offset,
        }

        if client_id is not None:
            self._validate_id(client_id, "client_id")
            params["client_id"] = client_id

        if doctor_id is not None:
            self._validate_id(doctor_id, "doctor_id")
            params["doctor_id"] = doctor_id

        if status is not None:
            params["status"] = status.value

        if date_from and date_from.strip():
            params["date_from"] = date_from.strip()

        if date_to and date_to.strip():
            params["date_to"] = date_to.strip()

        response = await self._request(
            "GET",
            "/visits",
            params=params,
        )
        return self._parse_list(Visit, response)

    async def get_visit(self, visit_id: int) -> Visit:
        self._validate_id(visit_id, "visit_id")
        response = await self._request("GET", f"/visits/{visit_id}")
        return self._parse_model(Visit, response)

    async def update_visit(
        self,
        visit_id: int,
        data: VisitUpdate,
    ) -> Visit:
        self._validate_id(visit_id, "visit_id")
        response = await self._request(
            "PATCH",
            f"/visits/{visit_id}",
            json=self._dump_update(data),
        )
        return self._parse_model(Visit, response)

    # ------------------------------------------------------------------
    # Эскалации
    # ------------------------------------------------------------------

    async def create_escalation(
        self,
        data: EscalationCreate,
    ) -> Escalation:
        response = await self._request(
            "POST",
            "/escalations",
            expected_statuses=(200, 201),
            json=self._dump_create(data),
        )
        return self._parse_model(Escalation, response)

    async def list_escalations(
        self,
        client_id: int | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Escalation]:
        self._validate_pagination(limit, offset)

        params: dict[str, object] = {
            "limit": limit,
            "offset": offset,
        }

        if client_id is not None:
            self._validate_id(client_id, "client_id")
            params["client_id"] = client_id

        response = await self._request(
            "GET",
            "/escalations",
            params=params,
        )
        return self._parse_list(Escalation, response)

    async def get_escalation(self, escalation_id: int) -> Escalation:
        self._validate_id(escalation_id, "escalation_id")
        response = await self._request(
            "GET",
            f"/escalations/{escalation_id}",
        )
        return self._parse_model(Escalation, response)
