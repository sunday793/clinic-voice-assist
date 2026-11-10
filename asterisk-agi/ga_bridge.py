#!/usr/bin/env python3
"""AGI-мост Asterisk ↔ FastAPI-ассистент клиники.

Использует только стандартную библиотеку Python (urllib),
чтобы не зависеть от pip в контейнере Asterisk.

Цикл:
    1. AGI GET DATA — получить цифру от пациента (заглушка вместо STT)
    2. HTTP /demo/dialog — получить ответ движка
    3. HTTP /synthesize — получить WAV
    4. AGI STREAM FILE — проиграть в трубку
"""

import json
import os
import sys
import urllib.request

API = os.environ.get("GA_API", "http://app:8000")
SOUNDS_DIR = "/var/lib/asterisk/sounds/ga"


def agi(cmd: str) -> str:
    sys.stdout.write(cmd + "\n")
    sys.stdout.flush()
    return sys.stdin.readline().strip()


def vlog(text: str) -> None:
    safe = text.replace('"', "'").replace("\n", " ")
    agi(f'VERBOSE "{safe}" 1')


def http_post_json(url: str, payload: dict, timeout: int = 30) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def http_post_bytes(url: str, payload: dict, timeout: int = 60) -> bytes:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


env = {}
while True:
    line = sys.stdin.readline().strip()
    if not line:
        break
    k, _, v = line.partition(": ")
    env[k] = v

call_id = env.get("agi_uniqueid", "call")
vlog(f"[GA] Старт call_id={call_id}")
vlog(f"[GA] API={API}")

os.makedirs(SOUNDS_DIR, exist_ok=True)


def say(text: str, name: str) -> None:
    try:
        wav_bytes = http_post_bytes(f"{API}/synthesize", {"text": text}, timeout=60)
    except Exception as e:
        vlog(f"[GA] TTS error: {e}")
        return

    # Папка и права — до записи
    os.makedirs(SOUNDS_DIR, exist_ok=True)
    try:
        os.chmod(SOUNDS_DIR, 0o777)
    except Exception as e:
        vlog(f"[GA] chmod error: {e}")

    wav_path = os.path.join(SOUNDS_DIR, f"{name}.wav")
    try:
        with open(wav_path, "wb") as f:
            f.write(wav_bytes)
    except Exception as e:
        vlog(f"[GA] write error: {e}")
        return

    vlog(f"[GA] say: {text!r}")
    agi(f'STREAM FILE ga/{name} ""')


def dialog(text: str) -> dict:
    try:
        return http_post_json(
            f"{API}/demo/dialog",
            {"call_id": call_id, "text": text},
            timeout=30,
        )
    except Exception as e:
        vlog(f"[GA] dialog error: {e}")
        return {
            "reply": "Извините, произошла ошибка.",
            "step": "error",
            "booked": False,
        }


vlog("[GA] === Ассистент начинает ===")

r = dialog("алло")
say(r["reply"], f"{call_id}_hello")

for i in range(15):
    resp = agi("GET DATA /tmp/ga_digit 10000 1")
    vlog(f"[GA] GET DATA: {resp}")
    digit = None
    if "result=" in resp:
        val = resp.split("result=")[1].strip().split()[0]
        if val and val not in ("-1", "0"):
            digit = val

    if digit is None:
        say("Я вас не слышу. Завершаю звонок.", f"{call_id}_bye")
        break

    vlog(f"[GA] Цифра {digit}")
    r = dialog(f"нажата цифра {digit}")
    say(r["reply"], f"{call_id}_out{i}")
    vlog(f"[GA] step={r.get('step')} booked={r.get('booked')}")

    if r.get("booked"):
        break

vlog("[GA] === Конец ===")
agi("HANGUP")