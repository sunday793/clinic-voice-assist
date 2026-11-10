
"""Исходящий обзвон через Asterisk AMI (сценарий C-10).

Правила:
- Будни: 10:00–19:00, суббота: 10:00–15:00.
- Воскресенье: обзвон не выполняется.
- Обзвон за один рабочий день до приёма.
- Не звонить, если запись создана менее 24 часов назад.
- Не более двух попыток с интервалом не менее двух часов.
- После исчерпания попыток без ответа — SMS.
- Часовой пояс клиники: GMT+4.

Внимание:
- on_call_finished() необходимо вызывать из обработчика событий Asterisk.
- _send_sms() требует подключения реального SMS-провайдера.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta

from app.config import settings
from app.core.greetings import GMT4
from app.redis_client import get_redis
from app.schemas.mis import Visit, VisitStatus
from app.services.dialog.state import DialogState
from app.services.mis_client import MISClientProtocol

logger = logging.getLogger(__name__)

RESULT_CONFIRMED = "CONFIRMED"
RESULT_RESCHEDULED = "RESCHEDULED"
RESULT_CANCELLED = "CANCELLED"
RESULT_WRONG_PERSON = "WRONG_PERSON"
RESULT_NO_ANSWER = "NO_ANSWER"
RESULT_OPT_OUT = "OPT_OUT"
RESULT_ESCALATED = "ESCALATED"

REDIS_ATTEMPTS = "outbound:attempts"
REDIS_LAST_ATTEMPT = "outbound:last_attempt"
REDIS_ACTIVE_PREFIX = "outbound:active:"
REDIS_DONE_PREFIX = "outbound:done:"

# Автоматическое снятие блокировки, если процесс аварийно завершился.
ACTIVE_CALL_TTL_SECONDS = 4 * 60 * 60

TERMINAL_RESULTS = {
    RESULT_CONFIRMED,
    RESULT_RESCHEDULED,
    RESULT_CANCELLED,
    RESULT_WRONG_PERSON,
    RESULT_OPT_OUT,
    RESULT_ESCALATED,
}


RELEASE_LOCK_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
"""


class OutboundCaller:
    """Управление исходящими звонками-напоминаниями."""

    def __init__(self, mis: MISClientProtocol, engine) -> None:
        self._mis = mis
        self._engine = engine

        # Локальная защита от повторного запуска задачи в одном процессе.
        self._active_calls: set[int] = set()

    async def _redis(self):
        return await get_redis()

    @staticmethod
    def _active_key(visit_id: int) -> str:
        return f"{REDIS_ACTIVE_PREFIX}{visit_id}"

    @staticmethod
    def _done_key(visit_id: int) -> str:
        return f"{REDIS_DONE_PREFIX}{visit_id}"

    async def check_and_call(self, force: bool = False) -> list[int]:
        """Плановый запуск обзвона."""
        return await self.run_once(force=force, limit=50)

    async def run_once(
        self,
        force: bool = False,
        limit: int = 5,
    ) -> list[int]:
        """Найти записи на целевую дату и запланировать звонки."""
        if limit <= 0:
            return []

        now = datetime.now(GMT4)

        if not force and not self._is_valid_time(now):
            logger.debug("Вне разрешённого окна обзвона: %s", now)
            return []

        target_date = self._target_visit_date(now)
        if target_date is None:
            logger.debug("Нет целевой даты обзвона: %s", now)
            return []

        try:
            visits = await self._mis.list_visits(
                status=VisitStatus.PLANNED,
                date_from=target_date,
                date_to=target_date,
            )
        except Exception:
            logger.exception("Не удалось получить записи из МИС")
            return []

        triggered: list[int] = []

        for visit in visits:
            if len(triggered) >= limit:
                break

            if visit.id in self._active_calls:
                continue

            try:
                eligible = await self._should_call(visit, now)
            except Exception:
                logger.exception(
                    "Не удалось проверить возможность обзвона визита %s",
                    visit.id,
                )
                continue

            if not eligible:
                continue

            # Ставим локальную блокировку до создания задачи:
            # это предотвращает дубли в одном проходе планировщика.
            self._active_calls.add(visit.id)

            asyncio.create_task(self._make_call(visit))
            triggered.append(visit.id)

        return triggered

    async def _should_call(
        self,
        visit: Visit,
        now: datetime,
    ) -> bool:
        """Проверить ограничения перед исходящим звонком."""
        redis = await self._redis()

        if await redis.exists(self._done_key(visit.id)):
            return False

        if await redis.exists(self._active_key(visit.id)):
            return False

        attempts = int(
            await redis.hget(REDIS_ATTEMPTS, str(visit.id)) or 0
        )

        max_attempts = settings.OUTBOUND_MAX_ATTEMPTS
        if attempts >= max_attempts:
            return False

        last_raw = await redis.hget(
            REDIS_LAST_ATTEMPT,
            str(visit.id),
        )

        if last_raw:
            last_timestamp = float(last_raw)
            last = datetime.fromtimestamp(last_timestamp, tz=GMT4)

            retry_hours = settings.OUTBOUND_RETRY_INTERVAL_H
            if now - last < timedelta(hours=retry_hours):
                return False

        # Если created_at отсутствует или имеет неизвестный формат,
        # не звоним: соблюдение ограничения 24 часов не подтверждено.
        created_recently = self._created_less_than_24h(visit, now)

        if created_recently is not False:
            logger.warning(
                "Визит %s пропущен: нельзя подтвердить ограничение 24 ч",
                visit.id,
            )
            return False

        return True

    @staticmethod
    def _parse_created_at(raw) -> datetime | None:
        """Разобрать дату создания записи в поддерживаемых форматах."""
        if raw is None:
            return None

        if isinstance(raw, datetime):
            created = raw
        elif isinstance(raw, date):
            created = datetime.combine(raw, datetime.min.time())
        else:
            value = str(raw).strip()
            created = None

            try:
                created = datetime.fromisoformat(
                    value.replace("Z", "+00:00")
                )
            except ValueError:
                pass

            if created is None:
                for fmt in (
                    "%d.%m.%Y %H:%M",
                    "%d.%m.%Y %H:%M:%S",
                    "%d.%m.%Y",
                ):
                    try:
                        created = datetime.strptime(value, fmt)
                        break
                    except ValueError:
                        continue

            if created is None:
                return None

        if created.tzinfo is None:
            created = created.replace(tzinfo=GMT4)
        else:
            created = created.astimezone(GMT4)

        return created

    def _created_less_than_24h(
        self,
        visit: Visit,
        now: datetime,
    ) -> bool | None:
        """True — моложе 24 ч; False — старше; None — проверить нельзя."""
        raw = getattr(visit, "created_at", None)
        created = self._parse_created_at(raw)

        if created is None:
            logger.warning(
                "У визита %s отсутствует или некорректна дата создания",
                visit.id,
            )
            return None

        age = now - created

        # Будущая дата также считается некорректной для обзвона.
        if age.total_seconds() < 0:
            logger.warning(
                "У визита %s дата создания находится в будущем",
                visit.id,
            )
            return None

        return age < timedelta(hours=24)

    @staticmethod
    def _is_valid_time(now: datetime) -> bool:
        """Проверить разрешённое время звонков по GMT+4."""
        weekday = now.weekday()

        if weekday < 5:
            return 10 <= now.hour < 19

        if weekday == 5:
            return 10 <= now.hour < 15

        return False

    @staticmethod
    def _target_visit_date(now: datetime) -> str | None:
        """Выбрать дату приёма за один рабочий день до него."""
        weekday = now.weekday()

        if weekday <= 3:  # Понедельник–четверг: следующий день.
            target = now + timedelta(days=1)
        elif weekday == 4:  # Пятница: понедельник.
            target = now + timedelta(days=3)
        elif weekday == 5:  # Суббота: понедельник.
            target = now + timedelta(days=2)
        else:  # Воскресенье.
            return None

        return target.strftime("%d.%m.%Y")

    async def _make_call(self, visit: Visit) -> None:
        """Инициировать звонок и оставить блокировку до его завершения."""
        call_id = f"outbound_{visit.id}"
        redis = None
        lock_acquired = False
        originate_accepted = False

        try:
            redis = await self._redis()

            # Межпроцессная блокировка: только один экземпляр приложения
            # сможет инициировать звонок для данного визита.
            lock_acquired = bool(
                await redis.set(
                    self._active_key(visit.id),
                    call_id,
                    nx=True,
                    ex=ACTIVE_CALL_TTL_SECONDS,
                )
            )

            if not lock_acquired:
                return

            # Повторная проверка после получения блокировки.
            if await redis.exists(self._done_key(visit.id)):
                return

            phone = await self._resolve_patient_phone(visit.client_id)
            if not phone:
                logger.warning(
                    "Визит %s: у клиента %s нет телефона; звонок пропущен",
                    visit.id,
                    visit.client_id,
                )
                return

            self._engine._states[call_id] = DialogState(
                call_id=call_id,
                scenario="reminder",
                step="reminder_greeting",
                visit_id=visit.id,
                client_id=visit.client_id,
            )

            uniqueid = await self.ami_originate(
                phone=phone,
                call_id=call_id,
                visit_id=visit.id,
            )

            if not uniqueid:
                logger.error(
                    "Не удалось инициировать звонок для визита %s",
                    visit.id,
                )
                self._engine._states.pop(call_id, None)
                return

            # Попытка фиксируется после успешного принятия Originate.
            # Это ещё не означает, что пациент ответил.
            now = datetime.now(GMT4).timestamp()

            await redis.hincrby(
                REDIS_ATTEMPTS,
                str(visit.id),
                1,
            )
            await redis.hset(
                REDIS_LAST_ATTEMPT,
                str(visit.id),
                str(now),
            )

            originate_accepted = True

            logger.info(
                "Originate принят: visit=%s, call_id=%s, uniqueid=%s",
                visit.id,
                call_id,
                uniqueid,
            )

            # Не снимаем блокировку здесь: звонок ещё может продолжаться.
            # Её снимает on_call_finished() при обработке события Asterisk.

        except Exception:
            logger.exception(
                "Ошибка при исходящем звонке для визита %s",
                visit.id,
            )

        finally:
            if not originate_accepted:
                self._active_calls.discard(visit.id)

                if lock_acquired and redis is not None:
                    try:
                        await redis.eval(
                            RELEASE_LOCK_SCRIPT,
                            1,
                            self._active_key(visit.id),
                            call_id,
                        )
                    except Exception:
                        logger.exception(
                            "Не удалось снять блокировку визита %s",
                            visit.id,
                        )

    async def on_call_finished(
        self,
        visit_id: int,
        result: str,
    ) -> None:
        """Обработать завершение звонка из обработчика событий Asterisk.

        result должен соответствовать одному из RESULT_*.
        Вызывать только после подтверждённого завершения вызова.
        """
        call_id = f"outbound_{visit_id}"

        try:
            redis = await self._redis()

            attempts = int(
                await redis.hget(REDIS_ATTEMPTS, str(visit_id)) or 0
            )

            if result in TERMINAL_RESULTS:
                await redis.set(
                    self._done_key(visit_id),
                    result,
                )

            elif (
                result == RESULT_NO_ANSWER
                and attempts >= settings.OUTBOUND_MAX_ATTEMPTS
            ):
                # После исчерпания попыток больше не обзваниваем.
                await redis.set(
                    self._done_key(visit_id),
                    RESULT_NO_ANSWER,
                )

                phone = None
                try:
                    visit = await self._mis.get_visit(visit_id)
                    phone = await self._resolve_patient_phone(
                        visit.client_id
                    )
                except Exception:
                    logger.exception(
                        "Не удалось получить данные визита %s для SMS",
                        visit_id,
                    )

                if phone:
                    await self._send_sms(
                        phone,
                        "Напоминаем о вашей записи в клинику. "
                        "Пожалуйста, свяжитесь с регистратурой "
                        "для подтверждения.",
                    )

            await redis.eval(
                RELEASE_LOCK_SCRIPT,
                1,
                self._active_key(visit_id),
                call_id,
            )

            self._engine._states.pop(call_id, None)

        except Exception:
            logger.exception(
                "Ошибка обработки завершения звонка visit=%s",
                visit_id,
            )

        finally:
            self._active_calls.discard(visit_id)

    async def ami_originate(
        self,
        phone: str,
        call_id: str,
        visit_id: int,
    ) -> str | None:
        """Отправить Originate через AMI и вернуть Uniqueid."""
        try:
            import panoramisk
        except ImportError:
            logger.error("Не установлен panoramisk")
            return None

        manager = panoramisk.Manager(
            host=settings.AMI_HOST,
            port=settings.AMI_PORT,
            username=settings.AMI_USER,
            secret=settings.AMI_PASSWORD,
        )

        try:
            await manager.connect()

            action = manager.send_action(
                {
                    "Action": "Originate",
                    "Channel": f"{settings.OUTBOUND_TRUNK}/{phone}",
                    "Context": settings.OUTBOUND_CONTEXT,
                    "Exten": "s",
                    "Priority": "1",
                    "CallerID": (
                        f"Поликлиника <{settings.OUTBOUND_CALLER_ID}>"
                    ),
                    "Timeout": str(
                        int(settings.AMI_TIMEOUT * 1000)
                    ),
                    "Async": "true",
                    "Variable": [
                        f"CALL_ID={call_id}",
                        f"VISIT_ID={visit_id}",
                        "SCENARIO=reminder",
                    ],
                }
            )

            response = await asyncio.wait_for(
                action,
                timeout=settings.AMI_TIMEOUT,
            )

            if response.get("Response") != "Success":
                logger.error("AMI Originate отклонён: %s", response)
                return None

            uniqueid = response.get("Uniqueid")
            if not uniqueid:
                logger.error(
                    "AMI принял Originate, но Uniqueid отсутствует: %s",
                    response,
                )
                return None

            return str(uniqueid)

        except asyncio.TimeoutError:
            logger.error(
                "Таймаут AMI Originate: %.1f с",
                settings.AMI_TIMEOUT,
            )
            return None

        except Exception:
            logger.exception("Ошибка AMI Originate")
            return None

        finally:
            try:
                await manager.close()
            except Exception:
                logger.exception("Ошибка закрытия AMI-соединения")

    async def _resolve_patient_phone(
        self,
        client_id: int,
    ) -> str | None:
        """Получить настоящий номер телефона пациента."""
        try:
            client = await self._mis.get_client(client_id)
            phone = getattr(client, "phone", None)

            if not phone:
                return None

            phone = str(phone).strip()
            return phone or None

        except Exception:
            logger.exception(
                "Не удалось получить телефон клиента %s",
                client_id,
            )
            return None

    async def _send_sms(self, phone: str, text: str) -> None:
        """Заглушка SMS. Заменить интеграцией SMS-провайдера."""
        logger.warning(
            "SMS НЕ ОТПРАВЛЕНО: провайдер не подключён; телефон=%s",
            phone,
        )


_load_manager: OutboundCaller | None = None


def get_outbound_caller(
    mis: MISClientProtocol,
    engine,
) -> OutboundCaller:
    """Получить экземпляр менеджера исходящих звонков."""
    global _load_manager

    if _load_manager is None:
        _load_manager = OutboundCaller(mis, engine)

    return _load_manager
