# Архитектура

Проект разделён на слои:
- **Presentation (API):** `app/api/` и `app/voice/` — эндпоинты FastAPI, WebSocket/AudioSocket. Не содержит бизнес-логики.
- **Application (Services):** `app/services/` — диалоговый роутер (`engine.py`), сценарии (`scenarios/`), NLU, интеграция с МИС.
- **Infrastructure:** `app/voice/` (локальные Whisper и Silero), `mis_client.py` (httpx), `schedule.py` (Excel).

**Голосовой пайплайн:** Asterisk (AudioSocket) → VAD (webrtcvad) → Whisper (STT) → DialogEngine → Silero (TTS) → Asterisk.