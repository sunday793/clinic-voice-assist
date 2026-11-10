.PHONY: up down logs test lint format shell

up:
	docker compose up -d --build

down:
	docker compose down

restart:
	docker compose restart

no-cache:
    docker compose build --no-cache app

logs:
	docker compose logs -f app

test:
	docker compose exec app pytest tests/ -v

lint:
	docker compose exec app ruff check app/
	docker compose exec app mypy app/

format:
	docker compose exec app ruff format app/

shell:
	docker compose exec app bash

help:
	@echo "Available commands:"
	@echo "  make up       - Build and start project (docker compose up -d --build)"
	@echo "  make down     - Stop and remove containers (docker compose down)"
	@echo "  make restart  - Restart containers (docker compose restart)"
	@echo "  make no-cache - Build and start project no-cache (docker compose build --no-cache app)"
	@echo "  make logs     - Watch app logs in real-time"
	@echo "  make test     - Run all tests (pytest)"
	@echo "  make lint     - Check code with linters (ruff + mypy)"
	@echo "  make format   - Format code with ruff"
	@echo "  make shell    - Open a shell in the app container"