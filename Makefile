.DEFAULT_GOAL := help
.PHONY: help install format lint type-check type-check-core test clean all pre-commit audit-repo check-frontend test-infrastructure build-pages check-pages

audit-repo:  ## Print a read-only repository hygiene inventory as JSON
	@python3 scripts/audit_repo_hygiene.py

check-frontend:  ## Check tracked frontend JavaScript syntax (Node 22+; no execution)
	@node scripts/check_frontend_syntax.mjs

build-pages:  ## Generate static HTML pages from shared templates (Node 22+)
	@node scripts/build_page_shell.mjs

check-pages:  ## Fail if generated HTML differs from its templates (read-only)
	@node scripts/build_page_shell.mjs --check

test-infrastructure:  ## Run infrastructure unit and offline browser checks (npm ci and Chromium required)
	@npm run test:infra

help:  ## Show this help message
	@echo "Available commands:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

install:  ## Install dependencies
	pip install -r requirements.txt

format:  ## Format code with black, isort, and remove unused imports
	@echo "Removing unused imports with autoflake..."
	autoflake --remove-all-unused-imports --recursive --in-place server/ path_planning_core/ tests/
	@echo "Running isort..."
	isort --profile black server/ path_planning_core/ tests/
	@echo "Running black..."
	black --target-version py311 server/ path_planning_core/ tests/
	@echo "✓ Code formatting complete"

format-check:  ## Check code formatting without making changes
	@echo "Checking unused imports with autoflake..."
	autoflake --remove-all-unused-imports --recursive --check server/ path_planning_core/ tests/
	@echo "Checking import order with isort..."
	isort --profile black --check-only server/ path_planning_core/ tests/
	@echo "Checking code format with black..."
	black --target-version py311 --check server/ path_planning_core/ tests/
	@echo "✓ Format check complete"

imports:  ## Remove unused imports and sort imports
	@echo "Removing unused imports with autoflake..."
	autoflake --remove-all-unused-imports --recursive --in-place server/ path_planning_core/ tests/
	@echo "Sorting imports with isort..."
	isort --profile black server/ path_planning_core/ tests/
	@echo "✓ Import cleanup complete"

lint:  ## Run flake8 linter
	@echo "Running flake8..."
	flake8 server/ path_planning_core/ tests/
	@echo "✓ Linting complete"

type-check:  ## Run mypy type checker
	@echo "Running mypy..."
	mypy server/ path_planning_core/ tests/ --exclude '(^|/)archive($|/)'
	@echo "✓ Type checking complete"

type-check-core:  ## Run mypy on typed core modules that are clean today
	@echo "Running mypy on typed core..."
	mypy path_planning_core \
		server/api/models.py \
		server/api/mission_state.py \
		server/api/agent_routes.py \
		server/api/environment_service.py \
		server/api/environment_routes.py \
		server/api/llm_routes.py \
		server/api/matrix_routes.py \
		server/api/mission_routes.py \
		server/api/optimizer_routes.py \
		server/api/run_ledger_service.py \
		server/api/solve_routes.py \
		server/agents/agent_session.py \
		server/agents/crewai_agent.py \
		server/agents/drone_config.py \
		server/agents/llm_factory.py \
		server/agents/reasoning_agent.py \
		server/agents/v4/allocator.py \
		server/agents/v4/context.py \
		server/agents/v4/critic.py \
		server/agents/v4/metrics.py \
		server/agents/v4/optimizer.py \
		server/agents/v4/optimizer_tools.py \
		server/agents/v4/responder.py \
		server/agents/v4/route_optimizer.py \
		server/agents/v4/state.py \
		server/agents/v4/strategist.py \
		server/database/mission_ledger.py \
		server/database/session_storage.py \
		server/database/supabase_client.py \
		server/memory/algorithm_registry.py \
		server/memory/constraint_parser.py \
		server/memory/constraints.py \
		server/memory/session_store.py \
		server/memory/trim.py \
		server/solver/animation_frame.py \
		server/solver/airport_rules.py \
		server/solver/constraints.py \
		server/solver/matrix_cache.py \
		server/solver/mission_parser.py \
		server/solver/optimizers/common.py \
		server/solver/optimizers/crossing_removal.py \
		server/solver/optimizers/insert_missed.py \
		server/solver/optimizers/or_opt.py \
		server/solver/optimizers/pipeline.py \
		server/solver/optimizers/post_insert.py \
		server/solver/optimizers/refuel_legs.py \
		server/solver/optimizers/regenerate.py \
		server/solver/optimizers/trajectory_swap.py \
		server/solver/optimizers/unified.py \
		server/solver/pipeline_runner.py \
		server/solver/projection.py \
		server/solver/refuel_solver.py \
		server/solver/routing_context.py \
		server/solver/solve_config.py \
		server/solver/solve_mission.py \
		server/solver/solve_one_leg.py \
		server/solver/solver_interface.py \
		server/solver/virtual_drones.py \
		--exclude '(^|/)archive($|/)'
	@echo "✓ Typed core check complete"

test:  ## Run tests with pytest
	@echo "Running pytest..."
	pytest tests/ -v
	@echo "✓ Tests complete"

test-cov:  ## Run tests with coverage report
	@echo "Running pytest with coverage..."
	pytest tests/ -v --cov=server --cov=path_planning_core --cov-report=term-missing --cov-report=html
	@echo "✓ Tests complete - see htmlcov/index.html for coverage report"

clean:  ## Clean up generated files
	@echo "Cleaning up..."
	rm -rf .pytest_cache
	rm -rf .mypy_cache
	rm -rf .coverage
	rm -rf coverage.xml
	rm -rf htmlcov
	rm -rf dist
	rm -rf build
	rm -rf *.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	@echo "✓ Cleanup complete"

all: format lint type-check test  ## Run all checks (format, lint, type-check, test)
	@echo "✓ All checks passed!"

check: format-check lint type-check  ## Run required local quality checks without reformatting
	@echo "✓ Quick checks passed!"
