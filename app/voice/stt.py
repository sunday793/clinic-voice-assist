"""Распознавание речи (ASR): faster-whisper, полностью локально (152-ФЗ).

Вход:  PCM 16 бит mono (8 кГц из телефонии или 16 кГц из WAV).
Выход: текст, уже после пост-обработки (даты цифрами, ФИО с заглавных).

Два бэкенда (оба локальные):
    faster — faster-whisper (CTranslate2), по умолчанию: на CPU в 3–4 раза быстрее;
    hf     — transformers pipeline (openai/whisper-small), запасной вариант.
Если faster-whisper не установился, автоматически берётся hf.

Настройки через переменные окружения:
    ASR_BACKEND      auto | faster | hf            (по умолчанию auto)
    WHISPER_MODEL    tiny | base | small | medium  (по умолчанию small)
    WHISPER_DEVICE   auto | cpu | cuda             (по умолчанию auto: GPU, если есть)
    WHISPER_COMPUTE  int8 | float16 | float32      (по умолчанию: cuda→float16, cpu→int8)
    HF_WHISPER_MODEL модель для hf-бэкенда         (по умолчанию openai/whisper-small)
"""

from __future__ import annotations

import logging
import os
import re
import threading

from app.voice.itn import normalize

logger = logging.getLogger(__name__)

_BACKEND = os.getenv("ASR_BACKEND", "auto").lower()
_MODEL_NAME = os.getenv("WHISPER_MODEL", "small")
_HF_MODEL = os.getenv("HF_WHISPER_MODEL", "openai/whisper-small")

# Короткая нейтральная подсказка: помогает с ФИО и датами, но не «протекает» в ответ.
_PROMPT = "Разговор с регистратурой поликлиники. Фамилия, имя, дата рождения. Запись к врачу."

# Типичные галлюцинации Whisper на тишине и шуме.
_HALLUCINATIONS = re.compile(
    r"продолжение следует|субтитр|спасибо за просмотр|подписывайтесь|"
    r"редактор субтитров|корректор|dimatorzok",
    re.IGNORECASE,
)

_backend = None
_backend_lock = threading.Lock()   # инициализация модели
_run_lock = threading.Lock()       # один поток распознавания за раз


def _device() -> str:
    d = os.getenv("WHISPER_DEVICE", "auto").lower()
    if d != "auto":
        return d
    try:
        import ctranslate2

        return "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
    except Exception:  # noqa: BLE001
        return "cpu"


class _FasterBackend:
    name = "faster-whisper"

    def __init__(self) -> None:
        from faster_whisper import WhisperModel

        device = _device()
        compute = os.getenv("WHISPER_COMPUTE") or ("float16" if device == "cuda" else "int8")
        logger.info("Загрузка faster-whisper %s (%s, %s)", _MODEL_NAME, device, compute)
        self._model = WhisperModel(_MODEL_NAME, device=device, compute_type=compute)

    def run(self, a) -> str:
        segments, _ = self._model.transcribe(
            a,
            language="ru",
            beam_size=5,
            temperature=0.0,
            condition_on_previous_text=False,
            initial_prompt=_PROMPT,
            vad_filter=False,
        )
        parts = []
        for s in segments:
            if s.no_speech_prob > 0.6 and s.avg_logprob < -1.0:
                continue            # скорее шум, чем речь
            parts.append(s.text.strip())
        return " ".join(p for p in parts if p).strip()


class _HFBackend:
    name = "transformers"

    def __init__(self) -> None:
        import torch
        from transformers import pipeline

        device = "cuda:0" if torch.cuda.is_available() else "cpu"
        logger.info("Загрузка transformers-whisper %s (%s)", _HF_MODEL, device)
        self._pipe = pipeline("automatic-speech-recognition", model=_HF_MODEL, device=device)

    def run(self, a) -> str:
        res = self._pipe(
            {"array": a, "sampling_rate": 16000},
            generate_kwargs={"language": "russian", "task": "transcribe"},
        )
        return (res.get("text") or "").strip()


def _get_backend():
    global _backend
    with _backend_lock:
        if _backend is not None:
            return _backend
        if _BACKEND in ("auto", "faster"):
            try:
                _backend = _FasterBackend()
            except Exception:  # noqa: BLE001
                if _BACKEND == "faster":
                    raise
                logger.warning("faster-whisper недоступен, переключаюсь на transformers", exc_info=True)
        if _backend is None:
            _backend = _HFBackend()
        logger.info("ASR готов: %s", _backend.name)
        return _backend


def transcribe_pcm(pcm: bytes, sample_rate: int = 8000) -> str:
    """PCM16 mono → текст. Пустая строка, если речи не найдено."""
    import numpy as np

    if len(pcm) < 2:
        return ""
    a = np.frombuffer(pcm[: len(pcm) // 2 * 2], dtype="<i2").astype(np.float32) / 32768.0
    if sample_rate != 16000:  # Whisper работает на 16 кГц
        n = int(len(a) * 16000 / sample_rate)
        a = np.interp(np.linspace(0, len(a) - 1, n), np.arange(len(a)), a).astype(np.float32)

    peak = float(np.abs(a).max()) if len(a) else 0.0
    if peak < 0.005:        # практически тишина
        return ""
    a = a / peak * 0.9      # нормализация громкости

    backend = _get_backend()
    with _run_lock:
        text = backend.run(a)

    if not text or _HALLUCINATIONS.search(text) or _PROMPT.lower()[:25] in text.lower():
        return ""
    return normalize(text)


def transcribe_pcm8k(pcm: bytes) -> str:
    """Телефонный вариант: PCM16 mono 8 кГц."""
    return transcribe_pcm(pcm, 8000)


def warmup() -> None:
    """Загрузить модель и прогнать тишину, чтобы первый звонок не ждал."""
    import numpy as np

    backend = _get_backend()
    with _run_lock:
        backend.run(np.zeros(16000, dtype=np.float32))
    logger.info("ASR прогрет (%s)", backend.name)


class WhisperSTT:
    """Обёртка для совместимости со старым кодом: get_stt().transcribe_pcm8k(...)."""

    def transcribe_pcm8k(self, pcm_bytes: bytes) -> str:
        return transcribe_pcm8k(pcm_bytes)


def get_stt() -> WhisperSTT:
    return WhisperSTT()