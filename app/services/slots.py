
from __future__ import annotations

import logging
import re
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

import pandas as pd
import redis

logger = logging.getLogger(__name__)


class SlotManager:
    """Управляет слотами из Excel и временными блокировками в Redis.

    Ожидаемые поля расписания:
      - doctor_id — ID врача;
      - date — дата приёма;
      - slot — время приёма или дата и время;
      - status — необязательный статус слота.

    Если Excel использует другие названия колонок, они определяются
    по списку алиасов ниже.

    Важно: Redis-блокировка временная и не заменяет проверку записи в МИС.
    """

    LOCK_TTL_SECONDS = 180

    FREE_STATUSES = {
        "свободен",
        "отменён пациентом",
        "отменен пациентом",
    }

    COLUMN_ALIASES = {
        "doctor_id": (
            "doctor_id",
            "doctor id",
            "id врача",
            "врач id",
            "код врача",
        ),
        "date": (
            "date",
            "дата",
            "дата приёма",
            "дата приема",
        ),
        "slot": (
            "slot",
            "время",
            "время приёма",
            "время приема",
            "начало",
            "время начала",
            "start",
            "scheduled_at",
        ),
        "status": (
            "status",
            "статус",
            "статус слота",
        ),
    }

    # Lua гарантирует, что снять блокировку сможет только её владелец.
    _UNLOCK_SCRIPT = """
    local value = redis.call('GET', KEYS[1])
    if value == ARGV[1] then
        return redis.call('DEL', KEYS[1])
    end
    return 0
    """

    # Продление TTL также доступно только владельцу блокировки.
    _EXTEND_SCRIPT = """
    local value = redis.call('GET', KEYS[1])
    if value == ARGV[1] then
        return redis.call('EXPIRE', KEYS[1], ARGV[2])
    end
    return 0
    """

    def __init__(
        self,
        schedule_path: str,
        redis_client: redis.Redis,
        *,
        sheet_name: str | int = "Слоты",
        lock_ttl_seconds: int = LOCK_TTL_SECONDS,
    ) -> None:
        if lock_ttl_seconds <= 0:
            raise ValueError("lock_ttl_seconds должен быть больше нуля")

        self.schedule_path = Path(schedule_path)
        self.redis = redis_client
        self.sheet_name = sheet_name
        self.LOCK_TTL_SECONDS = lock_ttl_seconds
        self._schedule: pd.DataFrame | None = None
        self._resolved_columns: dict[str, Any] | None = None

    @staticmethod
    def _normalize_column_name(value: Any) -> str:
        return re.sub(r"\s+", " ", str(value).strip().casefold())

    @staticmethod
    def _normalize_doctor_id(value: Any) -> str:
        """Сравнивать Excel ID и ID из приложения без ошибок 123 vs 123.0."""
        if value is None or pd.isna(value):
            return ""

        if isinstance(value, bool):
            return str(value).casefold()

        if isinstance(value, int):
            return str(value)

        if isinstance(value, float) and value.is_integer():
            return str(int(value))

        return str(value).strip().casefold()

    @staticmethod
    def _parse_date(value: Any) -> date | None:
        if value is None or pd.isna(value):
            return None

        if isinstance(value, datetime):
            return value.date()

        if isinstance(value, date):
            return value

        parsed = pd.to_datetime(str(value).strip(), dayfirst=True, errors="coerce")
        if pd.isna(parsed):
            return None

        return parsed.date()

    @staticmethod
    def _parse_time(value: Any) -> time | None:
        if value is None or pd.isna(value):
            return None

        if isinstance(value, datetime):
            return value.time().replace(second=0, microsecond=0)

        if isinstance(value, time):
            return value.replace(second=0, microsecond=0)

        # Excel иногда хранит время как долю суток.
        if isinstance(value, (int, float)) and 0 <= value < 1:
            seconds = round(float(value) * 24 * 60 * 60)
            seconds %= 24 * 60 * 60
            hours, remainder = divmod(seconds, 3600)
            minutes = remainder // 60
            return time(hours, minutes)

        parsed = pd.to_datetime(str(value).strip(), errors="coerce")
        if pd.isna(parsed):
            return None

        return parsed.time().replace(second=0, microsecond=0)

    @classmethod
    def _normalize_requested_date(cls, value: Any) -> date:
        parsed = cls._parse_date(value)
        if parsed is None:
            raise ValueError(
                f"Некорректная дата {value!r}; ожидается ДД.ММ.ГГГГ"
            )
        return parsed

    @classmethod
    def _normalize_slot(cls, value: Any, slot_date: date) -> str | None:
        """Вернуть слот в формате ДД.ММ.ГГГГ ЧЧ:ММ."""
        if value is None or pd.isna(value):
            return None

        if isinstance(value, datetime):
            return value.strftime("%d.%m.%Y %H:%M")

        if isinstance(value, time):
            return datetime.combine(slot_date, value).strftime("%d.%m.%Y %H:%M")

        text = str(value).strip()
        if not text:
            return None

        # Если в ячейке уже есть дата и время.
        parsed_datetime = pd.to_datetime(text, dayfirst=True, errors="coerce")
        if not pd.isna(parsed_datetime):
            if re.search(r"\d{1,2}:\d{2}", text):
                return parsed_datetime.strftime("%d.%m.%Y %H:%M")

        parsed_time = cls._parse_time(value)
        if parsed_time is None:
            return None

        return datetime.combine(slot_date, parsed_time).strftime("%d.%m.%Y %H:%M")

    @staticmethod
    def _parse_slot_datetime(slot_datetime: str) -> datetime:
        parsed = pd.to_datetime(
            str(slot_datetime).strip(),
            dayfirst=True,
            errors="coerce",
        )
        if pd.isna(parsed):
            raise ValueError(
                f"Некорректный слот {slot_datetime!r}; "
                "ожидается ДД.ММ.ГГГГ ЧЧ:ММ"
            )
        return parsed.to_pydatetime().replace(second=0, microsecond=0)

    def _load_schedule(self) -> pd.DataFrame:
        """Загрузить Excel один раз; вызвать refresh_schedule() для обновления."""
        if self._schedule is not None:
            return self._schedule

        if not self.schedule_path.is_file():
            raise FileNotFoundError(
                f"Файл расписания не найден: {self.schedule_path}"
            )

        logger.info("Загрузка расписания: %s", self.schedule_path)

        df = pd.read_excel(
            self.schedule_path,
            sheet_name=self.sheet_name,
        )

        if df.empty:
            logger.warning("Расписание пустое: %s", self.schedule_path)

        self._schedule = df
        self._resolved_columns = self._resolve_columns(df)

        return df

    def refresh_schedule(self) -> None:
        """Сбросить кэш, чтобы при следующем запросе перечитать Excel."""
        self._schedule = None
        self._resolved_columns = None

    def _resolve_columns(self, df: pd.DataFrame) -> dict[str, Any]:
        normalized = {
            self._normalize_column_name(column): column
            for column in df.columns
        }

        resolved: dict[str, Any] = {}

        for field, aliases in self.COLUMN_ALIASES.items():
            for alias in aliases:
                normalized_alias = self._normalize_column_name(alias)
                if normalized_alias in normalized:
                    resolved[field] = normalized[normalized_alias]
                    break

        missing = [
            field for field in ("doctor_id", "date", "slot")
            if field not in resolved
        ]

        if missing:
            raise ValueError(
                "В расписании не удалось определить обязательные колонки: "
                f"{', '.join(missing)}. "
                f"Колонки Excel: {list(df.columns)!r}. "
                "Настройте COLUMN_ALIASES под фактический файл."
            )

        return resolved

    def _lock_key(self, doctor_id: int | str, slot_datetime: str) -> str:
        """Создать стабильный Redis-ключ для врача и времени приёма."""
        normalized_slot = self._parse_slot_datetime(slot_datetime)
        return (
            f"slot:{self._normalize_doctor_id(doctor_id)}:"
            f"{normalized_slot.strftime('%Y%m%d%H%M')}"
        )

    def is_locked(self, doctor_id: int | str, slot_datetime: str) -> bool:
        key = self._lock_key(doctor_id, slot_datetime)
        try:
            return bool(self.redis.exists(key))
        except redis.RedisError:
            logger.exception("Ошибка Redis при проверке блокировки %s", key)
            # При недоступном Redis нельзя безопасно считать слот свободным.
            raise

    def lock(
        self,
        doctor_id: int | str,
        slot_datetime: str,
        call_id: str,
    ) -> bool:
        """Атомарно заблокировать слот на время диалога."""
        if not call_id or not str(call_id).strip():
            raise ValueError("call_id не должен быть пустым")

        key = self._lock_key(doctor_id, slot_datetime)

        try:
            acquired = self.redis.set(
                key,
                str(call_id),
                nx=True,
                ex=self.LOCK_TTL_SECONDS,
            )
        except redis.RedisError:
            logger.exception("Ошибка Redis при блокировке слота %s", key)
            raise

        if acquired:
            logger.info(
                "Слот заблокирован: %s (call_id=%s)",
                key,
                call_id,
            )
            return True

        logger.info("Слот уже заблокирован: %s", key)
        return False

    def unlock(
        self,
        doctor_id: int | str,
        slot_datetime: str,
        call_id: str,
    ) -> bool:
        """Снять блокировку, только если она принадлежит этому звонку.

        Возвращает True, если блокировка снята, иначе False.
        """
        if not call_id:
            raise ValueError("Для снятия блокировки обязателен call_id")

        key = self._lock_key(doctor_id, slot_datetime)

        try:
            removed = self.redis.eval(
                self._UNLOCK_SCRIPT,
                1,
                key,
                str(call_id),
            )
        except redis.RedisError:
            logger.exception("Ошибка Redis при снятии блокировки %s", key)
            raise

        if removed:
            logger.info(
                "Блокировка снята: %s (call_id=%s)",
                key,
                call_id,
            )
            return True

        logger.warning(
            "Блокировка не снята: слот не принадлежит call_id=%s (%s)",
            call_id,
            key,
        )
        return False

    def extend_lock(
        self,
        doctor_id: int | str,
        slot_datetime: str,
        call_id: str,
    ) -> bool:
        """Продлить TTL только для владельца блокировки."""
        if not call_id:
            raise ValueError("Для продления блокировки обязателен call_id")

        key = self._lock_key(doctor_id, slot_datetime)

        try:
            extended = self.redis.eval(
                self._EXTEND_SCRIPT,
                1,
                key,
                str(call_id),
                self.LOCK_TTL_SECONDS,
            )
        except redis.RedisError:
            logger.exception("Ошибка Redis при продлении блокировки %s", key)
            raise

        return bool(extended)

    def get_free_slots(
        self,
        doctor_id: int | str,
        date: str,
        exclude_locked: bool = True,
    ) -> list[str]:
        """Вернуть свободные слоты врача на указанную дату."""
        df = self._load_schedule()
        assert self._resolved_columns is not None
        columns = self._resolved_columns

        requested_date = self._normalize_requested_date(date)
        requested_doctor = self._normalize_doctor_id(doctor_id)

        doctor_values = df[columns["doctor_id"]].map(
            self._normalize_doctor_id
        )
        date_values = df[columns["date"]].map(self._parse_date)

        mask = (
            doctor_values.eq(requested_doctor)
            & date_values.eq(requested_date)
        )

        # Если в файле есть колонка статуса, учитываем только свободные слоты.
        if "status" in columns:
            statuses = df[columns["status"]].fillna("").map(
                lambda value: re.sub(
                    r"\s+",
                    " ",
                    str(value).strip().casefold(),
                )
            )
            mask &= statuses.isin(self.FREE_STATUSES)

        slots: set[str] = set()

        for value in df.loc[mask, columns["slot"]].tolist():
            normalized_slot = self._normalize_slot(value, requested_date)
            if normalized_slot is not None:
                slots.add(normalized_slot)

        result = sorted(
            slots,
            key=self._parse_slot_datetime,
        )

        if exclude_locked:
            available: list[str] = []

            # Не игнорируем ошибки Redis: иначе можно предложить
            # слот, который уже зарезервирован другим звонком.
            for slot in result:
                if not self.is_locked(doctor_id, slot):
                    available.append(slot)

            result = available

        logger.info(
            "Свободных слотов: doctor_id=%s, date=%s, count=%d",
            doctor_id,
            requested_date.strftime("%d.%m.%Y"),
            len(result),
        )
        return result

    def suggest_and_lock(
        self,
        doctor_id: int | str,
        date: str,
        call_id: str,
        exclude_slots: set[str] | None = None,
    ) -> str | None:
        """Найти и атомарно заблокировать первый доступный слот.

        exclude_slots позволяет не предлагать повторно время,
        от которого пациент уже отказался.
        """
        excluded = {
            self._parse_slot_datetime(slot).strftime("%d.%m.%Y %H:%M")
            for slot in (exclude_slots or set())
        }

        free_slots = self.get_free_slots(
            doctor_id=doctor_id,
            date=date,
            exclude_locked=True,
        )

        for slot in free_slots:
            if slot in excluded:
                continue

            # Между чтением расписания и SET NX другой звонок мог
            # успеть занять слот. Redis атомарно разрешит гонку.
            if self.lock(doctor_id, slot, call_id):
                return slot

        return None
