
"""Голосовые эндпоинты: синтез речи, распознавание и перевод звонка."""

from __future__ import annotations

import io
import logging
import os
import tempfile
import wave
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.background import BackgroundTask

from app.voice.audio_server import TRANSFERS
from app.voice.stt import transcribe_pcm
from app.voice.tts import get_tts

logger = logging.getLogger(__name__)

router = APIRouter(tags=["voice"])

# Ограничения запросов.
MAX_TEXT_LENGTH = 5_000
MAX_AUDIO_BYTES = 20 * 1024 * 1024  # 20 MiB


# ---------------------------------------------------------------------
# Схемы API
# ---------------------------------------------------------------------


class SynthesizeRequest(BaseModel):
    """Текст для преобразования в речь."""

    model_config = ConfigDict(extra="ignore")

    text: str = Field(
        min_length=1,
        max_length=MAX_TEXT_LENGTH,
        description="Текст, который нужно озвучить",
    )

    @field_validator("text")
    @classmethod
    def validate_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Текст не должен быть пустым")
        return value


class TranscribeResponse(BaseModel):
    """Результат распознавания речи."""

    text: str


# ---------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------


def _safe_unlink(path: str) -> None:
    """Удалить временный файл, не вызывая ошибку при его отсутствии."""
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
    except OSError:
        logger.exception("Не удалось удалить временный файл: %s", path)


async def _read_limited_body(
    request: Request,
    max_bytes: int,
) -> bytes:
    """Прочитать тело запроса, не допуская превышения лимита размера."""
    content_length = request.headers.get("content-length")

    if content_length is not None:
        try:
            declared_size = int(content_length)
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail="Некорректный заголовок Content-Length",
            ) from exc

        if declared_size < 0:
            raise HTTPException(
                status_code=400,
                detail="Некорректный размер запроса",
            )

        if declared_size > max_bytes:
            raise HTTPException(
                status_code=413,
                detail="Аудиофайл превышает допустимый размер 20 MiB",
            )

    chunks: list[bytes] = []
    total_size = 0

    async for chunk in request.stream():
        total_size += len(chunk)

        if total_size > max_bytes:
            raise HTTPException(
                status_code=413,
                detail="Аудиофайл превышает допустимый размер 20 MiB",
            )

        chunks.append(chunk)

    return b"".join(chunks)


def _decode_wav(raw: bytes) -> tuple[bytes, int]:
    """Извлечь PCM из WAV-файла и проверить основные параметры."""
    try:
        with wave.open(io.BytesIO(raw), "rb") as wav_file:
            channels = wav_file.getnchannels()
            sample_width = wav_file.getsampwidth()
            sample_rate = wav_file.getframerate()
            frame_count = wav_file.getnframes()
            compression = wav_file.getcomptype()

            if compression != "NONE":
                raise HTTPException(
                    status_code=400,
                    detail="Поддерживаются только WAV-файлы без сжатия",
                )

            if channels != 1 or sample_width != 2:
                raise HTTPException(
                    status_code=400,
                    detail="Нужен WAV-файл: mono, 16 бит",
                )

            if sample_rate <= 0 or frame_count <= 0:
                raise HTTPException(
                    status_code=400,
                    detail="WAV-файл не содержит корректных аудиоданных",
                )

            pcm = wav_file.readframes(frame_count)

            if not pcm:
                raise HTTPException(
                    status_code=400,
                    detail="WAV-файл пуст",
                )

            expected_size = frame_count * channels * sample_width
            if len(pcm) != expected_size:
                raise HTTPException(
                    status_code=400,
                    detail="WAV-файл повреждён или содержит неполные аудиоданные",
                )

            return pcm, sample_rate

    except HTTPException:
        raise
    except (wave.Error, EOFError, OSError) as exc:
        raise HTTPException(
            status_code=400,
            detail="Не удалось прочитать WAV. Проверьте целостность файла.",
        ) from exc


# ---------------------------------------------------------------------
# Синтез речи
# ---------------------------------------------------------------------


@router.post(
    "/synthesize",
    summary="Преобразовать текст в речь",
    responses={
        200: {
            "description": "Синтезированный WAV-файл",
            "content": {"audio/wav": {}},
        },
        422: {"description": "Некорректный текст"},
        500: {"description": "Ошибка синтеза речи"},
    },
)
async def synthesize_speech(data: SynthesizeRequest) -> FileResponse:
    """Синтезировать WAV-файл и вернуть его клиенту."""
    fd, path = tempfile.mkstemp(suffix=".wav", prefix="voice_tts_")
    os.close(fd)

    try:
        tts = get_tts()
        await run_in_threadpool(tts.synthesize, data.text, path)

        if not os.path.isfile(path) or os.path.getsize(path) == 0:
            raise RuntimeError("TTS не создал WAV-файл или создал пустой файл")

        return FileResponse(
            path=path,
            media_type="audio/wav",
            filename="speech.wav",
            background=BackgroundTask(_safe_unlink, path),
        )

    except Exception as exc:
        _safe_unlink(path)
        logger.exception("Ошибка синтеза речи")
        raise HTTPException(
            status_code=500,
            detail="Не удалось синтезировать речь",
        ) from exc


# ---------------------------------------------------------------------
# Распознавание речи
# ---------------------------------------------------------------------


@router.post(
    "/transcribe",
    response_model=TranscribeResponse,
    summary="Распознать речь из WAV-файла",
)
async def transcribe(request: Request) -> TranscribeResponse:
    """Распознать аудио из WAV-файла (mono, PCM, 16 бит)."""
    raw = await _read_limited_body(request, MAX_AUDIO_BYTES)

    if not raw:
        raise HTTPException(
            status_code=400,
            detail="Тело запроса пусто",
        )

    pcm, sample_rate = _decode_wav(raw)

    try:
        text = await run_in_threadpool(
            transcribe_pcm,
            pcm,
            sample_rate,
        )
    except Exception as exc:
        logger.exception("Ошибка распознавания речи")
        raise HTTPException(
            status_code=500,
            detail="Не удалось распознать речь",
        ) from exc

    return TranscribeResponse(text=text)


# ---------------------------------------------------------------------
# Проверка флага перевода звонка
# ---------------------------------------------------------------------


@router.get(
    "/voice/transfer/{call_id}",
    response_class=PlainTextResponse,
    summary="Проверить флаг перевода звонка",
)
async def transfer_flag(
    call_id: Annotated[
        str,
        Path(min_length=1, max_length=200),
    ],
) -> PlainTextResponse:
    """Вернуть '1', если перевод запрошен, иначе '0'.

    Флаг потребляется при чтении: после запроса он удаляется из TRANSFERS.
    """
    call_id = call_id.strip()

    if not call_id:
        raise HTTPException(
            status_code=422,
            detail="call_id не должен быть пустым",
        )

    hit = call_id in TRANSFERS
    TRANSFERS.discard(call_id)

    return PlainTextResponse("1" if hit else "0")
