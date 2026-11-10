"""AudioSocket-мост Asterisk ⇄ VAD ⇄ ASR ⇄ DialogEngine ⇄ TTS.

Протокол AudioSocket (кадр = 1 байт тип + 2 байта длина BE + данные):
    0x00 hangup · 0x01 UUID · 0x03 DTMF · 0x10 аудио (slin 8 кГц, 16 бит) · 0xFF ошибка

Исходящий звук: один «отправитель» (_pacer) шлёт кадры по 20 мс непрерывно с момента
соединения: речь ассистента, а когда говорить нечего, тишину. Так RTP к телефону не
прерывается (NAT и Linphone не считают звонок «мёртвым»), а две корутины не пишут
в сокет одновременно.

Barge-in: пока ассистент говорит, параллельно работает _listen в strict-режиме.
Если VAD поймал осмысленную речь пациента, буфер _Out мгновенно очищается —
ассистент замолкает и переключается на распознавание новой реплики.

Первые BARGE_IN_GRACE_SEC секунд каждой фразы barge-in не слушается: эхо
собственной речи на линии иначе обрывает приветствие на полуслове.

Защита от фоновых шумов:
    - webrtcvad в максимально строгом режиме;
    - _looks_like_speech требует несколько пауз (переходов voiced→unvoiced) —
      монотонный шум (стиральная машинка, вентилятор) даёт 0–1 переход
      и отбрасывается;
    - ZCR-фильтр отсекает широкополосный шум (вода, улица);
    - пост-фильтр по ASR: пустой/мусорный текст не считается репликой.

Перевод на оператора: если движок вернул escalated=True (красный флаг, жалоба и т.д.)
или что-то сломалось, call_id кладётся в TRANSFERS. Диалплан после выхода из
AudioSocket спрашивает GET /voice/transfer/{uuid} и при «1» звонит регистратору.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import struct
import uuid as uuidlib
from collections import deque

import numpy as np
import webrtcvad

from app.voice.stt import transcribe_pcm8k, warmup as stt_warmup
from app.voice.tts import get_tts

logger = logging.getLogger(__name__)

KIND_HANGUP, KIND_UUID, KIND_DTMF, KIND_AUDIO, KIND_ERROR = 0x00, 0x01, 0x03, 0x10, 0xFF

FRAME = 320                     # 20 мс при 8 кГц, 16 бит
FRAME_SEC = 0.02
BYTES_PER_SEC = 16000           # 8 кГц * 16 бит
AUDIO_HEADER = bytes([KIND_AUDIO]) + struct.pack(">H", FRAME)
SILENCE = AUDIO_HEADER + b"\x00" * FRAME

# ── VAD и пороги ──
VAD_MODE = int(os.getenv("VAD_MODE", "3"))                        # 0..3, выше = строже
END_SILENCE_FRAMES = int(os.getenv("END_SILENCE_FRAMES", "25"))   # 0.5 с тишины = конец
MIN_VOICED_FRAMES = int(os.getenv("MIN_VOICED_FRAMES", "50"))     # 1 с речи = реплика
MAX_UTTERANCE_FRAMES = 600      # не дольше 12 с
NO_SPEECH_FRAMES = 500          # 10 с молчания = «пусто»
MAX_CALL_SEC = 600              # страховка от зависшего звонка

START_VOICED = int(os.getenv("START_VOICED", "8"))                # 8 из 10 = 160 мс
START_VOICED_STRICT = int(os.getenv("START_VOICED_STRICT", "10"))
STRICT_MIN_VOICED = int(os.getenv("STRICT_MIN_VOICED", "60"))     # barge-in: 1.2 с

# Barge-in не слушается первые N секунд фразы — защита от эха собственной речи
BARGE_IN_GRACE_SEC = float(os.getenv("BARGE_IN_GRACE_SEC", "1.0"))

# Структурный фильтр речи
MIN_GAPS_SHORT = int(os.getenv("MIN_GAPS_SHORT", "1"))            # <1 с: достаточно 1 паузы
MIN_GAPS_LONG = int(os.getenv("MIN_GAPS_LONG", "2"))              # ≥1 с: минимум 2 паузы
SHORT_UTTERANCE_FRAMES = 50     # 1 с

# ZCR-фильтр: широкополосный шум даёт высокий ZCR
ZCR_MAX = float(os.getenv("ZCR_MAX", "0.25"))

TRANSFERS: set[str] = set()     # звонки, которые нужно перевести на оператора


def _is_transfer(resp) -> bool:
    return bool(getattr(resp, "escalated", False) or getattr(resp, "transfer", False))


class _Out:
    """Исходящий буфер речи звонка."""

    def __init__(self) -> None:
        self.buf = bytearray()
        self.is_speaking = False

    def clear(self) -> None:
        """Мгновенно замолчать (barge-in)."""
        self.buf.clear()
        self.is_speaking = False


class AudioServer:
    def __init__(self, host: str = "0.0.0.0", port: int = 9092, engine=None):
        self.host, self.port = host, port
        if engine is None:
            from app.api.demo import _engine  # общий движок по умолчанию

            engine = _engine
        self.engine = engine  # можно подставить свой (тесты, другой сценарий)

    def _forget(self, call_id: str) -> None:
        """Забыть состояние звонка в движке."""
        end_call = getattr(self.engine, "end_call", None)
        if end_call:
            end_call(call_id)
        else:
            self.engine._states.pop(call_id, None)  # noqa: SLF001

    # ── протокол ──

    @staticmethod
    async def _read_msg(reader):
        header = await reader.readexactly(3)
        length = struct.unpack(">H", header[1:3])[0]
        payload = await reader.readexactly(length) if length else b""
        return header[0], payload

    async def _pump(self, reader, q: asyncio.Queue, closed: asyncio.Event):
        """Всё время читает сокет (даже пока мы говорим) и режет звук на кадры по 20 мс."""
        buf = bytearray()
        try:
            while True:
                kind, payload = await self._read_msg(reader)
                if kind == KIND_AUDIO:
                    buf += payload
                    while len(buf) >= FRAME:
                        q.put_nowait(bytes(buf[:FRAME]))
                        del buf[:FRAME]
                elif kind in (KIND_HANGUP, KIND_ERROR):
                    break
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            closed.set()

    async def _pacer(self, writer, out: _Out, closed: asyncio.Event):
        """Единственный писатель в сокет. Тишина, если буфер пуст."""
        loop = asyncio.get_running_loop()
        t0, n = loop.time(), 0
        try:
            while not closed.is_set():
                if out.buf:
                    out.is_speaking = True
                    chunk = bytes(out.buf[:FRAME]).ljust(FRAME, b"\x00")
                    del out.buf[:FRAME]
                    writer.write(AUDIO_HEADER + chunk)
                else:
                    out.is_speaking = False
                    writer.write(SILENCE)
                await writer.drain()
                n += 1
                delay = t0 + n * FRAME_SEC - loop.time()
                if delay > 0:
                    await asyncio.sleep(delay)
                elif delay < -1.0:
                    # Если сильно отстали — сбрасываем буфер, чтобы не копить задержку
                    out.buf.clear()
                    t0, n = loop.time(), 0
        except (ConnectionError, asyncio.CancelledError):
            pass

    async def _say(self, out: _Out, text: str, closed: asyncio.Event) -> float:
        """Синтезировать, положить в буфер. Вернуть длительность PCM в секундах."""
        if not text:
            return 0.0
        pcm = await asyncio.to_thread(get_tts().synthesize_pcm, text)
        if closed.is_set() or not pcm:
            return 0.0
        out.buf += pcm
        out.is_speaking = True
        return len(pcm) / BYTES_PER_SEC

    # ── фильтры шума ──

    @staticmethod
    def _zcr(pcm: bytes) -> float:
        """Zero-crossing rate: доля переходов сигнала через ноль.

        У широкополосного шума (вода, улица) стабильно высокий; у речи — ниже
        и варьируется. Используется как дополнительный фильтр.
        """
        if not pcm:
            return 0.0
        a = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
        if a.size < 2:
            return 0.0
        s = np.signbit(a)
        return float(np.mean(s[1:] != s[:-1]))

    @classmethod
    def _looks_like_speech(cls, speech: bytes, flags: list[bool], min_voiced: int) -> bool:
        """Речь = достаточно voiced-кадров И несколько пауз И нормальный ZCR.

        Монотонный шум (машинка, вентилятор) даёт 0–1 переход voiced→unvoiced —
        отбрасывается. Широкополосный шум (вода) отсекается по ZCR.
        """
        if sum(flags) < min_voiced:
            return False

        # Адаптивный порог пауз: короткие реплики («да», «нет») не должны страдать
        min_gaps = MIN_GAPS_SHORT if len(flags) < SHORT_UTTERANCE_FRAMES else MIN_GAPS_LONG
        gaps = sum(1 for i in range(1, len(flags)) if flags[i - 1] and not flags[i])
        if gaps < min_gaps:
            return False

        if cls._zcr(speech) > ZCR_MAX:
            return False

        return True

    @staticmethod
    def _looks_like_real_text(text: str) -> bool:
        """Пост-фильтр по ASR: пустой или мусорный текст не считаем репликой."""
        t = text.strip().lower()
        if len(t) < 2:
            return False
        return any(ch.isalnum() for ch in t)

    # ── VAD ──

    async def _listen(
        self, q: asyncio.Queue, closed: asyncio.Event, *, strict: bool = False
    ) -> bytes | None:
        """Ждёт реплику пациента (VAD).

        Возвращает:
            bytes — захваченная реплика (возможно b'' при долгом молчании),
            None  — звонок завершён.

        strict=True используется для barge-in, пока ассистент говорит:
        VAD строже, порог старта выше — чтобы эхо/шум линии не считались речью.
        В strict-режиме q очищается полностью: пока ассистент говорит, всё в q —
        эхо собственной речи, а не пациент.
        """
        # Пока ассистент говорит (strict) — всё в q это эхо линии. Чистим полностью.
        # В обычном режиме тоже чистим: накопленное за время нашей речи — не реплика.
        while not q.empty():
            q.get_nowait()

        mode = min(VAD_MODE + 1, 3) if strict else VAD_MODE
        start_voiced = START_VOICED_STRICT if strict else START_VOICED
        min_voiced = STRICT_MIN_VOICED if strict else MIN_VOICED_FRAMES

        vad = webrtcvad.Vad(mode)
        ring: deque = deque(maxlen=10)  # предзапись 200 мс, чтобы не резать начало слова
        speech, flags = bytearray(), []
        started, silence, voiced, waited = False, 0, 0, 0

        while True:
            try:
                frame = await asyncio.wait_for(q.get(), 0.1)
            except asyncio.TimeoutError:
                if closed.is_set():
                    return None
                continue

            is_sp = vad.is_speech(frame, 8000)

            if not started:
                ring.append((frame, is_sp))
                waited += 1
                # В strict-режиме ждём полного окна ring, иначе 10 из 10 не наберётся.
                full = len(ring) >= ring.maxlen if strict else True
                if full and sum(s for _, s in ring) >= start_voiced:
                    started, silence = True, 0
                    speech = bytearray(b"".join(f for f, _ in ring))
                    flags = [s for _, s in ring]
                    voiced = sum(flags)
                elif waited > NO_SPEECH_FRAMES:
                    return b""
                continue

            speech += frame
            flags.append(is_sp)
            voiced += is_sp
            silence = 0 if is_sp else silence + 1
            too_long = len(speech) > MAX_UTTERANCE_FRAMES * FRAME
            if silence >= END_SILENCE_FRAMES or too_long:
                if self._looks_like_speech(bytes(speech), flags, min_voiced):
                    logger.info(
                        "[listen] accepted len=%.1fs voiced=%d gaps=%d zcr=%.2f strict=%s",
                        len(speech) / BYTES_PER_SEC,
                        sum(flags),
                        sum(1 for i in range(1, len(flags)) if flags[i - 1] and not flags[i]),
                        self._zcr(bytes(speech)),
                        strict,
                    )
                    return bytes(speech)

                logger.info(
                    "[listen] rejected len=%.1fs voiced=%d gaps=%d zcr=%.2f strict=%s",
                    len(speech) / BYTES_PER_SEC,
                    sum(flags),
                    sum(1 for i in range(1, len(flags)) if flags[i - 1] and not flags[i]),
                    self._zcr(bytes(speech)),
                    strict,
                )
                # Шум — слушаем дальше
                ring.clear()
                speech, flags = bytearray(), []
                started, silence, voiced = False, 0, 0

    # ── сценарий звонка ──

    async def _say_and_listen(
        self, out: _Out, q: asyncio.Queue, text: str, closed: asyncio.Event
    ) -> bytes | None:
        """Сказать фразу и одновременно слушать.

        Barge-in: если пациент заговорил раньше, чем ассистент закончил, буфер
        очищается. Первые BARGE_IN_GRACE_SEC секунд не слушаем — чтобы эхо
        собственной речи не обрывало фразу (особенно приветствие).

        Возвращает:
            bytes — реплика пациента (b'' если он молчал до конца фразы),
            None  — звонок завершён.
        """
        duration = await self._say(out, text, closed)
        if closed.is_set():
            return None
        if duration <= 0:
            return await self._listen(q, closed)

        # 1. Grace-период: только проигрываем, не слушаем.
        grace = min(BARGE_IN_GRACE_SEC, duration)
        await asyncio.sleep(grace)
        if closed.is_set():
            return None

        remaining = duration - grace
        if remaining <= 0:
            # Короткая фраза — barge-in не имеет смысла, просто слушаем ответ.
            return await self._listen(q, closed, strict=False)

        # 2. Основная часть фразы: играем + слушаем strict.
        player = asyncio.create_task(asyncio.sleep(remaining))
        listener = asyncio.create_task(self._listen(q, closed, strict=True))

        try:
            done, _ = await asyncio.wait(
                {player, listener}, return_when=asyncio.FIRST_COMPLETED
            )
        except asyncio.CancelledError:
            player.cancel()
            listener.cancel()
            raise

        if listener in done:
            # Barge-in.
            out.clear()
            player.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await player
            return listener.result()

        # Ассистент договорил — ждём ответа в обычном режиме.
        player.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await player
        return await self._listen(q, closed, strict=False)

    async def _session(self, call_id: str, q, closed, out: _Out):
        r = await self.engine.handle(call_id, "")   # затравка: движок сам поздоровается       # приветствие движка

        # Приветствие — через _say_and_listen: barge-in работает с первого слова,
        # но первые BARGE_IN_GRACE_SEC защищены от эха.
        pending_pcm = await self._say_and_listen(out, q, r.reply, closed)
        if pending_pcm is None:
            return

        empty = 0
        while not closed.is_set():
            if pending_pcm:
                pcm = pending_pcm
                pending_pcm = None
            else:
                pcm = await self._listen(q, closed)
                if pcm is None:
                    return

            if pcm:
                logger.info("[%s] речь %.1f с, распознаю...", call_id, len(pcm) / BYTES_PER_SEC)
            text = await asyncio.to_thread(transcribe_pcm8k, pcm) if pcm else ""
            logger.info("[%s] пациент: %r", call_id, text)

            if not text or not self._looks_like_real_text(text):
                empty += 1
                if empty >= 3:
                    await self._say(out, "Я вас не слышу. Соединяю с регистратором.", closed)
                    TRANSFERS.add(call_id)               # человек всегда доступен
                    return
                pending_pcm = await self._say_and_listen(
                    out, q, "Извините, не расслышала. Повторите, пожалуйста.", closed
                )
                if pending_pcm is None:
                    return
                continue
            empty = 0

            r = await self.engine.handle(call_id, text)
            logger.info("[%s] ассистент: %r (step=%s)", call_id, r.reply, r.step)

            next_pcm = await self._say_and_listen(out, q, r.reply, closed)
            if next_pcm is None:
                return

            while next_pcm:
                pcm = next_pcm
                next_pcm = None

                if _is_transfer(r):
                    TRANSFERS.add(call_id)
                    return
                if getattr(r, "booked", False):
                    return

                text = await asyncio.to_thread(transcribe_pcm8k, pcm) if pcm else ""
                logger.info("[%s] пациент (barge-in): %r", call_id, text)

                if not text or not self._looks_like_real_text(text):
                    empty += 1
                    if empty >= 3:
                        await self._say(
                            out, "Я вас не слышу. Соединяю с регистратором.", closed
                        )
                        TRANSFERS.add(call_id)
                        return
                    next_pcm = await self._say_and_listen(
                        out, q, "Извините, не расслышала. Повторите, пожалуйста.", closed
                    )
                    if next_pcm is None:
                        return
                    continue

                empty = 0
                r = await self.engine.handle(call_id, text)
                logger.info("[%s] ассистент: %r (step=%s)", call_id, r.reply, r.step)

                if _is_transfer(r):
                    TRANSFERS.add(call_id)
                    return
                if getattr(r, "booked", False):
                    return

                next_pcm = await self._say_and_listen(out, q, r.reply, closed)
                if next_pcm is None:
                    return

            if _is_transfer(r):
                TRANSFERS.add(call_id)
                return
            if getattr(r, "booked", False):
                return

    async def handle_client(self, reader, writer):
        peer = writer.get_extra_info("peername")
        closed, q, out = asyncio.Event(), asyncio.Queue(), _Out()
        pump = pacer = None
        call_id = "unknown"
        try:
            kind, payload = await asyncio.wait_for(self._read_msg(reader), 5)
            if kind == KIND_UUID and len(payload) == 16:
                call_id = str(uuidlib.UUID(bytes=payload))
            logger.info("Звонок %s от %s", call_id, peer)

            pacer = asyncio.create_task(self._pacer(writer, out, closed))
            pump = asyncio.create_task(self._pump(reader, q, closed))
            await asyncio.wait_for(self._session(call_id, q, closed, out), MAX_CALL_SEC)
            await asyncio.sleep(0.5)                     # дать доиграть хвост фразы
        except (asyncio.IncompleteReadError, ConnectionError):
            logger.info("Звонок %s: соединение закрыто", call_id)
        except asyncio.TimeoutError:
            logger.warning("Звонок %s: превышен MAX_CALL_SEC, завершаем", call_id)
        except Exception:
            logger.exception("Звонок %s: ошибка. Переводим на регистратора", call_id)
            TRANSFERS.add(call_id)
        finally:
            closed.set()
            for task in (pump, pacer):
                if task:
                    task.cancel()
            self._forget(call_id)
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:  # noqa: BLE001
                pass

    def _warmup(self):
        """Прогреть модели, иначе первый звонок ждёт десятки секунд."""
        try:
            stt_warmup()
            get_tts().synthesize_pcm("Проверка")
            logger.info("Модели ASR и TTS прогреты")
        except Exception:
            logger.exception("Прогрев моделей не удался")

    async def start(self) -> None:
        server = await asyncio.start_server(self.handle_client, self.host, self.port)
        logger.info("AudioSocket сервер запущен на %s:%s", self.host, self.port)
        self._warm_task = asyncio.create_task(asyncio.to_thread(self._warmup))
        async with server:
            await server.serve_forever()