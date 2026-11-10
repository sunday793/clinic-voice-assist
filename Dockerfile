FROM python:3.11-slim

WORKDIR /app

# 1. Системные зависимости
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y \
    build-essential \
    libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

# 2. Сначала копируем только requirements (чтобы кэшировать pip)
COPY requirements.txt requirements-dev.txt ./
RUN pip install --no-cache-dir --root-user-action=ignore --disable-pip-version-check -r requirements.txt -r requirements-dev.txt

# 3. Потом копируем код (меняется чаще, чем requirements)
COPY app ./app
COPY tests ./tests
COPY alembic ./alembic
COPY alembic.ini .
COPY pyproject.toml .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]