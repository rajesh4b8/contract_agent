PY := backend/.venv/bin/python

.DEFAULT_GOAL := help
.PHONY: help install test test-integration test-all run stop logs smoke

help:  ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

install:  ## Install backend deps including the dev/test group
	cd backend && uv sync --group dev

test:  ## Offline unit tests — no Neo4j, no API keys, no server
	$(PY) -m pytest

test-integration:  ## Tests needing a live Neo4j (start it with `make run` first)
	$(PY) -m pytest -m integration

test-all:  ## Everything, offline and integration
	$(PY) -m pytest -m ""

run:  ## Start backend, frontend and Neo4j via docker compose
	docker compose up --build

stop:  ## Stop the stack
	docker compose down

logs:  ## Tail backend logs
	docker compose logs -f backend

smoke:  ## List the manual end-to-end checks (need a running stack)
	@echo "These hit real services — start them with 'make run' first."
	@echo "Run one at a time; each is standalone:"
	@for f in scripts/smoke/*.py; do echo "  $(PY) $$f"; done
