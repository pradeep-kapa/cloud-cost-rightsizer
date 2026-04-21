# =============================================================================
# cloud-cost-rightsizer — Makefile
#
# Usage:
#   make install       Install production dependencies
#   make dev           Install development dependencies
#   make run           Run the analyzer (set REGION env var first)
#   make test          Run unit tests
#   make coverage      Run tests with coverage report
#   make lint          Run ruff + mypy
#   make fmt           Format code with black
#   make clean         Remove build artifacts
# =============================================================================

SHELL := /usr/bin/env bash
.DEFAULT_GOAL := help

PYTHON   := python3
VENV     := .venv
PIP      := $(VENV)/bin/pip
PYTEST   := $(VENV)/bin/pytest
BLACK    := $(VENV)/bin/black
RUFF     := $(VENV)/bin/ruff
MYPY     := $(VENV)/bin/mypy

REGION   ?= us-east-1
OUT_DIR  ?= ./reports

GREEN := \033[0;32m
CYAN  := \033[0;36m
RESET := \033[0m

.PHONY: help
help:
	@echo ""
	@echo "\033[1mcloud-cost-rightsizer\033[0m"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
	  | awk 'BEGIN {FS = ":.*?## "}; {printf "  $(CYAN)%-18s$(RESET) %s\n", $$1, $$2}'
	@echo ""

$(VENV)/bin/activate:
	$(PYTHON) -m venv $(VENV)

.PHONY: install
install: $(VENV)/bin/activate ## Install production dependencies
	@$(PIP) install --quiet -r requirements.txt
	@echo "$(GREEN)✓ Dependencies installed$(RESET)"

.PHONY: dev
dev: $(VENV)/bin/activate ## Install development dependencies (includes test + lint tools)
	@$(PIP) install --quiet -r requirements-dev.txt
	@echo "$(GREEN)✓ Dev dependencies installed$(RESET)"

.PHONY: run
run: install ## Run the analyzer against REGION (default: us-east-1)
	@echo "$(GREEN)→ Analyzing $(REGION)$(RESET)"
	@$(VENV)/bin/python -m src.main --region $(REGION) --output-dir $(OUT_DIR)

.PHONY: run-dry
run-dry: install ## Dry run — show what would be analyzed without writing reports
	@$(VENV)/bin/python -m src.main --region $(REGION) --dry-run

.PHONY: test
test: dev ## Run unit tests
	@echo "$(GREEN)→ Running tests$(RESET)"
	@$(PYTEST) tests/ -v --tb=short

.PHONY: coverage
coverage: dev ## Run tests with coverage report
	@$(PYTEST) tests/ -v --cov=src --cov-report=term-missing --cov-report=html:htmlcov
	@echo "$(GREEN)✓ Coverage report: htmlcov/index.html$(RESET)"

.PHONY: lint
lint: dev ## Run ruff linter and mypy type checker
	@echo "$(GREEN)→ Running ruff$(RESET)"
	@$(RUFF) check src/ tests/
	@echo "$(GREEN)→ Running mypy$(RESET)"
	@$(MYPY) src/ --ignore-missing-imports || true

.PHONY: fmt
fmt: dev ## Format code with black
	@$(BLACK) src/ tests/
	@echo "$(GREEN)✓ Formatted$(RESET)"

.PHONY: fmt-check
fmt-check: dev ## Check formatting without modifying files (used in CI)
	@$(BLACK) --check src/ tests/

.PHONY: clean
clean: ## Remove virtual environment, build artifacts, and report files
	@rm -rf $(VENV) __pycache__ .pytest_cache htmlcov .coverage .pricing_cache.json
	@find . -type d -name "__pycache__" | xargs rm -rf
	@find . -name "*.pyc" | xargs rm -f
	@echo "$(GREEN)✓ Clean$(RESET)"
