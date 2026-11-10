"""Синтез речи (Silero TTS, работает локально — 152-ФЗ не нарушается).

Два формата вывода:
    - synthesize_pcm(text) → bytes: сырой PCM 16 бит, mono, 8000 Гц (для AudioSocket);
    - synthesize(text, path) → WAV 8 кГц mono PCM16 (для /synthesize).
"""

from __future__ import annotations

import logging
import re
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

_SAMPLE_RATE = 8000
_SPEAKER = "xenia"
_LANGUAGE = "ru"
_MODEL_ID = "v4_ru"


class SileroTTSWrapper:
    """Ленивая инициализация модели; потокобезопасный синтез."""

    def __init__(
        self,
        language: str = _LANGUAGE,
        speaker: str = _SPEAKER,
        sample_rate: int = _SAMPLE_RATE,
        model_id: str = _MODEL_ID,
    ) -> None:
        self.language = language
        self.speaker = speaker
        self.sample_rate = sample_rate
        self.model_id = model_id
        self._model = None
        self._lock = threading.Lock()

    def _lazy_init(self) -> None:
        with self._lock:
            if self._model is not None:
                return
            import torch
            from silero import silero_tts

            logger.info("Инициализация Silero TTS: %s, speaker=%s", self.model_id, self.speaker)
            model, _ = silero_tts(language=self.language, speaker=self.model_id)
            model.to(torch.device("cpu"))
            self._model = model
            logger.info("Silero TTS готов")

    def _synth_float(self, text: str):
        """Текст → numpy float32 в диапазоне [-1, 1]."""
        import numpy as np
        import torch

        self._lazy_init()
        clean = speakable(text) or "."
        with self._lock:
            audio = self._model.apply_tts(
                text=clean,
                speaker=self.speaker,
                sample_rate=self.sample_rate,
                put_accent=True,
                put_yo=True,
            )
        if isinstance(audio, torch.Tensor):
            audio = audio.squeeze().cpu().numpy()
        return np.asarray(audio, dtype=np.float32)

    def synthesize_pcm(self, text: str) -> bytes:
        """Текст → сырой PCM16 mono 8 кГц (то, что ждёт AudioSocket)."""
        import numpy as np

        a = self._synth_float(text)
        return (np.clip(a, -1.0, 1.0) * 32767).astype("<i2").tobytes()

    def synthesize(self, text: str, output_path: str | Path) -> Path:
        """Текст → WAV 8 кГц mono PCM16."""
        import soundfile as sf

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        sf.write(str(output_path), self._synth_float(text), self.sample_rate, subtype="PCM_16")
        logger.info("Аудио сохранено: %s", output_path)
        return output_path


_tts: SileroTTSWrapper | None = None


def get_tts() -> SileroTTSWrapper:
    """Один общий экземпляр на процесс (модель тяжёлая)."""
    global _tts
    if _tts is None:
        _tts = SileroTTSWrapper()
    return _tts


# ─────────────────────────────────────────────────────────────
# Нормализация текста: числа, даты, время → слова
# ─────────────────────────────────────────────────────────────

_MONTHS = [
    "", "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]
_DAYS = [
    "", "первое", "второе", "третье", "четвёртое", "пятое", "шестое", "седьмое",
    "восьмое", "девятое", "десятое", "одиннадцатое", "двенадцатое",
    "тринадцатое", "четырнадцатое", "пятнадцатое", "шестнадцатое",
    "семнадцатое", "восемнадцатое", "девятнадцатое", "двадцатое",
    "двадцать первое", "двадцать второе", "двадцать третье",
    "двадцать четвёртое", "двадцать пятое", "двадцать шестое",
    "двадцать седьмое", "двадцать восьмое", "двадцать девятое",
    "тридцатое", "тридцать первое",
]


def _num(n: int) -> str:
    try:
        from num2words import num2words

        return num2words(n, lang="ru")
    except Exception:  # noqa: BLE001 — без num2words читаем цифры как есть
        return str(n)


def _date_repl(m: re.Match) -> str:
    d, mo = int(m.group(1)), int(m.group(2))
    if 1 <= d <= 31 and 1 <= mo <= 12:
        return f"{_DAYS[d]} {_MONTHS[mo]}"
    return m.group(0)


def _time_repl(m: re.Match) -> str:
    h, mi = int(m.group(1)), int(m.group(2))
    if mi == 0:
        return f"{_num(h)} ноль ноль"
    if mi < 10:
        return f"{_num(h)} ноль {_num(mi)}"
    return f"{_num(h)} {_num(mi)}"


def speakable(text: str) -> str:
    """Убрать то, что Silero читает плохо: нумерацию, даты, время, цифры."""
    text = re.sub(r"(?m)^\s*(\d+)\.\s*", lambda m: f"Вариант {_num(int(m.group(1)))}. ", text)
    text = re.sub(r"\b(\d{1,2})\.(\d{2})\.\d{4}\b", _date_repl, text)
    text = re.sub(r"\b(\d{1,2}):(\d{2})\b", _time_repl, text)
    text = re.sub(r"\d+", lambda m: _num(int(m.group(0))), text)
    text = text.replace("«", "").replace("»", "").replace("\n", " ")
    return re.sub(r"\s+", " ", text).strip()


# обратная совместимость со старым именем
_speakable = speakable