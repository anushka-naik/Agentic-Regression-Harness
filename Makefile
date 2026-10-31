.PHONY: setup db migrate data validate test lint typecheck check mutations demo live-smoke serve clean db-reset

setup:            ## Install dependencies from the lockfile
	uv sync --dev --frozen
	test -f .env || cp .env.example .env

db:               ## Start PostgreSQL (localhost only)
	docker compose up -d --wait db

migrate:          ## Apply database migrations
	uv run alembic upgrade head

data:             ## Regenerate the dataset (byte-identical for the same seed)
	uv run python scripts/generate_dataset.py --seed 42

validate:         ## Validate dataset schema, splits, leakage and oracle consistency
	uv run agentcheck dataset validate data/retail_v1

test:             ## All offline tests (no model download)
	uv run pytest -m "not live"

lint:
	uv run ruff check src tests scripts

typecheck:
	uv run mypy

check: lint typecheck validate test mutations   ## Everything CI runs

mutations:        ## Prove the 8 controlled mutations are detected
	uv run agentcheck mutation-check --all --output artifacts/mutations

demo:             ## Offline regression demo (fixture mode, no LLM)
	uv run agentcheck demo --mode fixture --output artifacts/demo

live-smoke:       ## Genuine Qwen3 smoke comparison (needs Ollama + model)
	uv run agentcheck doctor --model qwen3:4b-q4_K_M
	uv run agentcheck experiment --baseline configs/agents/baseline.yaml --candidate configs/agents/candidate.yaml --suite smoke --repeats 1 --output artifacts/live-smoke

serve:            ## Dashboard on http://127.0.0.1:8000
	uv run agentcheck serve

db-reset:         ## DELETES all stored runs
	docker compose down -v

clean:
	rm -rf artifacts/* .pytest_cache .mypy_cache .ruff_cache
