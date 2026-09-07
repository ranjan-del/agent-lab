.PHONY: help up down logs ps psql shell test ingest task transcript lint typecheck check migrate revision downgrade health clean

.DEFAULT_GOAL := help

help:          ## Show this help
	@grep -hE '^[a-z-]+:.*?##' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

up:            ## Bring up db and api, wait for health
	docker compose up -d --build
	@echo "waiting for api..." && sleep 3 && $(MAKE) health

down:          ## Stop everything, keep the data
	docker compose down

clean:         ## Stop everything and DESTROY the database volume
	docker compose down -v

logs:          ## Follow the logs from every service
	docker compose logs -f

ps:            ## Show container status
	docker compose ps

psql:          ## Open a shell on the database
	docker compose exec db psql -U agentlab -d agentlab

shell:         ## Open a shell in the api container
	docker compose exec api bash

health:        ## Curl /health and show the status code
	@curl -s -w '\nHTTP %{http_code}\n' http://localhost:8000/health

test:          ## Run the test suite
	uv run pytest

ingest:        ## make ingest f=path/to/calendar.ics
	uv run python -m agent_lab.ingest.cli calendar "$(f)"

task:          ## make task t="Plan my week" [script=tests/fixtures/script_plan_week.json]
	uv run python -m agent_lab.agent.cli --script "$(or $(script),tests/fixtures/script_plan_week.json)" "$(t)"
transcript:    ## make transcript f=path/to/transcript.txt
	uv run python -m agent_lab.ingest.cli transcript "$(f)"

lint:          ## Run ruff check and format --check
	uv run ruff check .
	uv run ruff format --check .

typecheck:     ## Run mypy --strict
	uv run mypy

check: lint typecheck test   ## Run lint, types and tests together

migrate:       ## Apply all migrations
	docker compose exec api alembic upgrade head

downgrade:     ## Roll all the way back. Run this at least once, on purpose.
	docker compose exec api alembic downgrade base

revision:      ## make revision m="add meetings table"
	docker compose exec api alembic revision --autogenerate -m "$(m)"
