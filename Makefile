.PHONY: install run test lint format docker-up docker-down demo migrate migrate-down soak

install:
	python -m pip install -r requirements.txt

run:
	uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

test:
	pytest -q

lint:
	ruff check app tests

format:
	ruff format app tests

docker-up:
	docker compose up --build -d

docker-down:
	docker compose down -v

demo:
	python scripts/demo_text_turn.py

migrate:
	alembic upgrade head

migrate-down:
	alembic downgrade -1

migrate-stamp:
	alembic stamp head

soak:
	python scripts/load_soak.py --sessions 20 --turns 5 --chaos
