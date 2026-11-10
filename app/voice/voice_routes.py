"""Голосовые эндпоинты.

    POST /synthesize             {"text": "..."} → WAV 8 кГц mono (проверка TTS)
    POST /transcribe             тело = WAV mono 16 бит → {"text": "..."} (проверка ASR)
    GET  /voice/transfer/{id}    "1", если звонок нужно перевести на оператора
"""

from __future__ import annotations

import io
import logging
import os
import tempfile
import wave

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, PlainTextResponse
from starlette.background import BackgroundTask

from app.voice.audio_server import TRANSFERS
from app.voice.stt import transcribe_pcm
from app.voice.tts import get_tts

logger = logging.getLogger(__name__)

router = APIRouter(tags=["voice"])


@router.post("/synthesize")
async def synthesize_speech(data: dict):
    """Синтез речи: принимает текст, возвращает WAV-файл."""
    text = (data.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Missing 'text'")

    fd, path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    
    await run_in_threadpool(get_tts().synthesize, text, path)
    return FileResponse(
        path, 
        media_type="audio/wav", 
        background=BackgroundTask(os.unlink, path)
    )


@router.post("/transcribe")
async def transcribe(request: Request):
    """Проверить ASR без телефона: отправьте запись голоса в WAV (mono, 16 бит)."""
    raw = await request.body()
    try:
        with wave.open(io.BytesIO(raw), "rb") as wf:
            if wf.getnchannels() != 1 or wf.getsampwidth() != 2:
                raise HTTPException(
                    status_code=400, 
                    detail="Нужен WAV: mono, 16 бит"
                )
            rate = wf.getframerate()
            pcm = wf.readframes(wf.getnframes())
    except (wave.Error, EOFError):
        raise HTTPException(
            status_code=400, 
            detail="Не удалось прочитать WAV. Убедитесь, что это WAV-файл (mono, 16 бит)."
        )
    
    # Запускаем распознавание в отдельном потоке, чтобы не блокировать сервер
    text = await run_in_threadpool(transcribe_pcm, pcm, rate)
    return {"text": text}


@router.get("/voice/transfer/{call_id}")
async def transfer_flag(call_id: str):
    """Вызывается диалпланом после завершения AudioSocket."""
    hit = call_id in TRANSFERS
    TRANSFERS.discard(call_id)
    return PlainTextResponse("1" if hit else "0")