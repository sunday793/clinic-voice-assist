
"""Фабрика MIS-клиента: выбор реализации по настройке MIS_MODE.

Режимы:
    - mock — in-memory мок без токена, для разработки и тестов.
    - real — HTTP-клиент для реального стенда «Регистратура».

Использование:
    from app.services.mis_factory import create_mis_client

    client = create_mis_client()
    doctors = await client.list_doctors(specialty="терапевт")
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from app.config import settings
from app.schemas.mis import Doctor
from app.services.mis_client import MISClient, MISClientProtocol
from app.services.mis_client_mock import MISClientMock

logger = logging.getLogger(__name__)

_VALID_MODES = {"mock", "real"}
_DOCTOR_ID_PATTERN = re.compile(r"^[Dd](\d+)$|^(\d+)$")


def create_mis_client(mode: str | None = None) -> MISClientProtocol:
    """Создать MIS-клиент в соответствии с заданным режимом.

    Args:
        mode: "mock" или "real". Если None, используется settings.MIS_MODE.

    Returns:
        Экземпляр MISClientMock или MISClient.

    Raises:
        ValueError: неизвестный режим или отсутствуют обязательные настройки.
    """
    configured_mode = settings.MIS_MODE if mode is None else mode
    resolved = str(configured_mode or "").strip().lower()

    if resolved not in _VALID_MODES:
        raise ValueError(
            f"Неизвестный MIS_MODE: {resolved!r}. "
            "Ожидается 'mock' или 'real'."
        )

    if resolved == "mock":
        client = MISClientMock()
        _seed_doctors_from_schedule(client)
        return client

    token = str(settings.MIS_TOKEN or "").strip()
    base_url = str(settings.MIS_BASE_URL or "").strip().rstrip("/")

    if not token:
        raise ValueError(
            "MIS_MODE=real, но MIS_TOKEN пуст. "
            "Укажи токен в .env или переключись на MIS_MODE=mock."
        )

    if not base_url:
        raise ValueError(
            "MIS_MODE=real, но MIS_BASE_URL пуст. "
            "Укажи адрес MIS API в .env."
        )

    return MISClient(
        base_url=base_url,
        token=token,
    )


def _seed_doctors_from_schedule(mock: MISClientMock) -> None:
    """Заполнить мок справочником врачей из Excel-расписания.

    Из листа «Слоты» извлекаются уникальные врачи.
    ID вида D001 преобразуются в числовые ID 1.

    Если файл расписания отсутствует, мок остаётся без врачей.
    Если файл существует, но не удаётся загрузить расписание,
    возбуждается RuntimeError, чтобы ошибка не осталась незамеченной.
    """
    configured_path = settings.SCHEDULE_XLSX_PATH

    if not configured_path:
        logger.warning(
            "SCHEDULE_XLSX_PATH не задан. "
            "MISClientMock будет создан без врачей."
        )
        return

    path = Path(configured_path).expanduser()

    if not path.is_file():
        logger.warning(
            "Файл расписания не найден: %s. "
            "MISClientMock будет создан без врачей.",
            path,
        )
        return

    # Импортируем только при необходимости работы с расписанием.
    from app.services.schedule import ScheduleService

    schedule = ScheduleService(str(path))

    try:
        schedule.load()
    except Exception as exc:
        logger.exception("Не удалось загрузить расписание из %s", path)
        raise RuntimeError(
            f"Не удалось загрузить расписание из файла {path}"
        ) from exc

    # В текущем интерфейсе ScheduleService используется приватный
    # атрибут _slots. При наличии публичного метода получения всех
    # слотов лучше заменить это обращение на него.
    slots = getattr(schedule, "_slots", None)

    if slots is None:
        raise RuntimeError(
            "ScheduleService не предоставляет атрибут _slots. "
            "Проверь его интерфейс и способ получения загруженных слотов."
        )

    # Excel ID -> (имя врача, специальность).
    unique_doctors: dict[str, tuple[str, str]] = {}

    for slot in slots:
        excel_id = str(getattr(slot, "doctor_excel_id", "") or "").strip()
        full_name = str(getattr(slot, "doctor_name", "") or "").strip()
        specialty = str(getattr(slot, "specialty", "") or "").strip()

        if not excel_id or not full_name:
            logger.warning(
                "Пропущена запись врача с некорректными данными: "
                "doctor_excel_id=%r, doctor_name=%r",
                excel_id,
                full_name,
            )
            continue

        if excel_id not in unique_doctors:
            unique_doctors[excel_id] = (full_name, specialty)

    mis_doctors: list[Doctor] = []
    used_numeric_ids: dict[int, str] = {}

    for excel_id, (full_name, specialty) in unique_doctors.items():
        match = _DOCTOR_ID_PATTERN.fullmatch(excel_id)

        if not match:
            logger.warning(
                "Пропущен врач %r: ID %r не соответствует "
                "поддерживаемым форматам D001 или 001.",
                full_name,
                excel_id,
            )
            continue

        numeric_id = int(match.group(1) or match.group(2))

        if numeric_id <= 0:
            logger.warning(
                "Пропущен врач %r: ID должен быть положительным, получено %r.",
                full_name,
                excel_id,
            )
            continue

        existing_excel_id = used_numeric_ids.get(numeric_id)

        if existing_excel_id is not None:
            logger.error(
                "Коллизия ID врачей: %r и %r преобразуются в %d. "
                "Врач %r пропущен.",
                existing_excel_id,
                excel_id,
                numeric_id,
                full_name,
            )
            continue

        used_numeric_ids[numeric_id] = excel_id

        mis_doctors.append(
            Doctor(
                id=numeric_id,
                full_name=full_name,
                specialty=specialty,
                is_active=True,
            )
        )

    mock.seed_doctors(mis_doctors)

    logger.info(
        "MISClientMock: загружено врачей из %s: %d",
        path,
        len(mis_doctors),
    )
