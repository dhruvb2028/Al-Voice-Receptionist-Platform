# Development workflow. Requires: uv, node 20+, docker (image builds only).

.PHONY: install lint format typecheck test check dev-api dev-voice dev-worker dev-dashboard \
        build-dashboard build-images migrate migration

install: ## Install all Python and Node dependencies
	uv sync --all-packages
	cd apps/dashboard && npm install

lint: ## Lint Python and TypeScript
	uv run ruff check .
	uv run ruff format --check .
	cd apps/dashboard && npm run lint

format: ## Auto-format all code
	uv run ruff check --fix .
	uv run ruff format .

typecheck: ## Strict type checks for both stacks
	uv run mypy packages services
	cd apps/dashboard && npx tsc --noEmit

test: ## Run all Python tests
	uv run pytest -q

check: lint typecheck test ## Full quality gate

dev-api: ## Run the API service locally
	uv run uvicorn api.main:app --reload --port 8000

dev-voice: ## Run the voice service locally
	uv run uvicorn voice.main:app --reload --port 8001

dev-worker: ## Run the worker service locally
	uv run uvicorn worker.main:app --reload --port 8002

dev-dashboard: ## Run the dashboard locally
	cd apps/dashboard && npm run dev

build-dashboard: ## Production build of the dashboard
	cd apps/dashboard && npm run build

build-images: ## Build all service container images
	docker build -f services/api/Dockerfile -t ai-receptionist-api .
	docker build -f services/voice/Dockerfile -t ai-receptionist-voice .
	docker build -f services/worker/Dockerfile -t ai-receptionist-worker .

migrate: ## Apply database migrations (needs DATABASE_DIRECT_URL)
	uv run alembic upgrade head

migration: ## Autogenerate a migration: make migration m="add calls table"
	uv run alembic revision --autogenerate -m "$(m)"

seed: ## Seed the demo plumbing tenant (needs DATABASE_DIRECT_URL)
	uv run python -m ai_database.seed

test-db: ## Start the local test database container
	docker run -d --name receptionist-test-pg -e POSTGRES_PASSWORD=test \
		-e POSTGRES_DB=receptionist_test -p 55432:5432 postgres:16-alpine
