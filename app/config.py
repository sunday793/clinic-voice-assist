
from __future__ import annotations

from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Настройки голосового ассистента клиники.

    Значения читаются из переменных окружения и файла .env.
    Переменные окружения имеют приоритет над значениями по умолчанию.
    """

    APP_NAME: str = "voice-assistant-clinic"
    DEBUG: bool = False

    # ─────────────────────────────────────────────────
    # База данных и Redis
    # ─────────────────────────────────────────────────
    DATABASE_URL: str = (
        "postgresql+asyncpg://user:pass@db:5432/clinic"
    )
    REDIS_URL: str = "redis://redis:6379/0"

    # ─────────────────────────────────────────────────
    # МИС
    # ─────────────────────────────────────────────────
    MIS_MODE: Literal["mock", "real"] = "mock"
    MIS_BASE_URL: str = (
        "https://crm-med-mock.dbi-testing.ru/api/v1"
    )
    MIS_TOKEN: str = ""

    # ─────────────────────────────────────────────────
    # Расписание врачей
    # ─────────────────────────────────────────────────
    SCHEDULE_XLSX_PATH: str = "data/schedule.xlsx"

    # Пустая строка означает, что используется текущая дата.
    # Формат заданной даты: ДД.ММ.ГГГГ.
    SCHEDULE_TODAY: str = ""

    # ─────────────────────────────────────────────────
    # Asterisk Manager Interface
    # ─────────────────────────────────────────────────
    AMI_HOST: str = "clinic-asterisk"
    AMI_PORT: int = 5038
    AMI_USER: str = "clinic-ami"
    AMI_PASSWORD: str = ""
    AMI_TIMEOUT: float = 10.0

    # ─────────────────────────────────────────────────
    # Исходящий обзвон
    # ─────────────────────────────────────────────────
    OUTBOUND_ENABLED: bool = True
    OUTBOUND_TRUNK: str = "PJSIP/phone-039"
    OUTBOUND_CONTEXT: str = "from-outbound"
    OUTBOUND_CALLER_ID: str = "+70007000707"
    OUTBOUND_MAX_ATTEMPTS: int = 2
    OUTBOUND_RETRY_INTERVAL_H: int = 2

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        validate_default=True,
    )

    # ─────────────────────────────────────────────────
    # Валидация строк и URL
    # ─────────────────────────────────────────────────

    @field_validator("APP_NAME", "AMI_HOST", "AMI_USER")
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Значение не должно быть пустым")
        return value

    @field_validator("MIS_MODE", mode="before")
    @classmethod
    def normalize_mis_mode(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @field_validator("MIS_BASE_URL")
    @classmethod
    def validate_mis_base_url(cls, value: str) -> str:
        value = value.strip().rstrip("/")

        parsed = urlsplit(value)

        if parsed.scheme not in {"http", "https"}:
            raise ValueError(
                "MIS_BASE_URL должен начинаться с http:// или https://"
            )

        if not parsed.hostname:
            raise ValueError(
                "MIS_BASE_URL должен содержать имя хоста"
            )

        if parsed.username or parsed.password:
            raise ValueError(
                "Не указывайте логин или пароль внутри MIS_BASE_URL; "
                "используйте отдельные настройки"
            )

        if parsed.query or parsed.fragment:
            raise ValueError(
                "MIS_BASE_URL не должен содержать query-параметры "
                "или fragment"
            )

        return value

    @field_validator("DATABASE_URL")
    @classmethod
    def validate_database_url(cls, value: str) -> str:
        value = value.strip()

        if not value:
            raise ValueError("DATABASE_URL не должен быть пустым")

        parsed = urlsplit(value)

        if parsed.scheme not in {
            "postgresql",
            "postgresql+asyncpg",
        }:
            raise ValueError(
                "DATABASE_URL должен использовать PostgreSQL "
                "и драйвер asyncpg"
            )

        if not parsed.hostname:
            raise ValueError(
                "DATABASE_URL должен содержать имя хоста"
            )

        if not parsed.path or parsed.path == "/":
            raise ValueError(
                "В DATABASE_URL необходимо указать имя базы данных"
            )

        return value

    @field_validator("REDIS_URL")
    @classmethod
    def validate_redis_url(cls, value: str) -> str:
        value = value.strip()
        parsed = urlsplit(value)

        if parsed.scheme not in {"redis", "rediss"}:
            raise ValueError(
                "REDIS_URL должен начинаться с redis:// или rediss://"
            )

        if not parsed.hostname:
            raise ValueError(
                "REDIS_URL должен содержать имя хоста"
            )

        return value

    @field_validator("SCHEDULE_XLSX_PATH")
    @classmethod
    def validate_schedule_path(cls, value: str) -> str:
        value = value.strip()

        if not value:
            raise ValueError(
                "SCHEDULE_XLSX_PATH не должен быть пустым"
            )

        return value

    @field_validator("SCHEDULE_TODAY")
    @classmethod
    def validate_schedule_today(cls, value: str) -> str:
        value = value.strip()

        if not value:
            return ""

        try:
            datetime.strptime(value, "%d.%m.%Y")
        except ValueError as exc:
            raise ValueError(
                "SCHEDULE_TODAY должен иметь формат ДД.ММ.ГГГГ "
                "или быть пустым"
            ) from exc

        return value

    @field_validator("AMI_PASSWORD", "MIS_TOKEN")
    @classmethod
    def normalize_secret_whitespace(cls, value: str) -> str:
        # Не меняем сам секрет, только проверяем, что он задан.
        # Ведущие и завершающие пробелы теоретически могут быть частью
        # секрета, поэтому значение намеренно не обрезается.
        if not isinstance(value, str):
            raise ValueError("Секрет должен быть строкой")
        return value

    # ─────────────────────────────────────────────────
    # Валидация числовых параметров
    # ─────────────────────────────────────────────────

    @field_validator("AMI_PORT")
    @classmethod
    def validate_ami_port(cls, value: int) -> int:
        if not 1 <= value <= 65535:
            raise ValueError("AMI_PORT должен быть в диапазоне 1–65535")
        return value

    @field_validator("AMI_TIMEOUT")
    @classmethod
    def validate_ami_timeout(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("AMI_TIMEOUT должен быть больше нуля")
        return value

    @field_validator("OUTBOUND_MAX_ATTEMPTS")
    @classmethod
    def validate_outbound_attempts(cls, value: int) -> int:
        if value < 1:
            raise ValueError(
                "OUTBOUND_MAX_ATTEMPTS должен быть не меньше 1"
            )
        return value

    @field_validator("OUTBOUND_RETRY_INTERVAL_H")
    @classmethod
    def validate_retry_interval(cls, value: int) -> int:
        if value < 1:
            raise ValueError(
                "OUTBOUND_RETRY_INTERVAL_H должен быть не меньше 1"
            )
        return value

    @model_validator(mode="after")
    def validate_configuration(self) -> Settings:
        # Реальная МИС должна иметь токен.
        if self.MIS_MODE == "real":
            if not self.MIS_TOKEN.strip():
                raise ValueError(
                    "При MIS_MODE=real необходимо задать MIS_TOKEN в .env"
                )

        # Если включён исходящий обзвон, требуем пароль AMI.
        if self.OUTBOUND_ENABLED:
            if not self.AMI_PASSWORD.strip():
                raise ValueError(
                    "При OUTBOUND_ENABLED=true необходимо задать "
                    "AMI_PASSWORD в .env"
                )

            if not self.OUTBOUND_TRUNK.strip():
                raise ValueError(
                    "OUTBOUND_TRUNK не должен быть пустым "
                    "при включённом обзвоне"
                )

            if not self.OUTBOUND_CONTEXT.strip():
                raise ValueError(
                    "OUTBOUND_CONTEXT не должен быть пустым "
                    "при включённом обзвоне"
                )

            if not self.OUTBOUND_CALLER_ID.strip():
                raise ValueError(
                    "OUTBOUND_CALLER_ID не должен быть пустым "
                    "при включённом обзвоне"
                )

        return self


settings = Settings()



# from pydantic_settings import BaseSettings, SettingsConfigDict
#
#
# class Settings(BaseSettings):
#     APP_NAME: str = "voice-assistant-clinic"
#     DEBUG: bool = False
#
#     DATABASE_URL: str = "postgresql+asyncpg://user:pass@db:5432/clinic"
#     REDIS_URL: str = "redis://redis:6379/0"
#
#     MIS_MODE: str = "mock"  # mock | real
#     MIS_BASE_URL: str = "https://crm-med-mock.dbi-testing.ru/api/v1"
#     MIS_TOKEN: str = ""
#
#     # Расписание врачей
#     SCHEDULE_XLSX_PATH: str = "data/schedule.xlsx"
#     SCHEDULE_TODAY: str = ""  # пусто → используем текущую системную дату
#
#     # ─────────────────────────────────────────────────
#     # AMI (Asterisk Manager Interface) — для обзвона
#     # ─────────────────────────────────────────────────
#     AMI_HOST: str = "clinic-asterisk"
#     AMI_PORT: int = 5038
#     AMI_USER: str = "clinic-ami"
#     AMI_PASSWORD: str = ""            # обязательно из .env
#     AMI_TIMEOUT: float = 10.0
#
#     # ─────────────────────────────────────────────────
#     # Исходящий обзвон (С-10)
#     # ─────────────────────────────────────────────────
#     OUTBOUND_ENABLED: bool = True
#     OUTBOUND_TRUNK: str = "PJSIP/phone-039"       # транк/учётка для звонка
#     OUTBOUND_CONTEXT: str = "from-outbound"
#     OUTBOUND_CALLER_ID: str = "+70007000707"
#     OUTBOUND_MAX_ATTEMPTS: int = 2
#     OUTBOUND_RETRY_INTERVAL_H: int = 2
#
#     model_config = SettingsConfigDict(env_file=".env", extra="ignore")
#
#
# settings = Settings()

