.PHONY: up down logs ps psql shell test ingest transcript lint typecheck check migrate revision downgrade health clean

up:            ## Bring up db and api, wait for health
	docker compose up -d --build
	@echo "waiting for api..." && sleep 3 && $(MAKE) health

down:          ## Stop everything, keep the data
	docker compose down

clean:         ## Stop everything and DESTROY the database volume
	docker compose down -v

logs:
	docker compose logs -f

ps:
	docker compose ps

psql:          ## Open a shell on the database
	docker compose exec db psql -U agentlab -d agentlab

shell:         ## Open a shell in the api container
	docker compose exec api bash

health:
	@curl -s -w '\nHTTP %{http_code}\n' http://localhost:8000/health

test:
	uv run pytest

ingest:        ## make ingest f=path/to/calendar.ics
	uv run python -m agent_lab.ingest.cli calendar "$(f)"

transcript:    ## make transcript f=path/to/transcript.txt
	uv run python -m agent_lab.ingest.cli transcript "$(f)"

lint:
	uv run ruff check .
	uv run ruff format --check .

typecheck:
	uv run mypy

check: lint typecheck test

migrate:       ## Apply all migrations
	docker compose exec api alembic upgrade head

downgrade:     ## Roll all the way back. Run this at least once, on purpose.
	docker compose exec api alembic downgrade base

revision:      ## make revision m="add meetings table"
	docker compose exec api alembic revision --autogenerate -m "$(m)"
