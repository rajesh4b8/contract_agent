PY := backend/.venv/bin/python
# .env points NEO4J_URI at the compose hostname, which only resolves inside a
# container. Host-run scripts reach the published port instead. Override to
# point at a remote instance: `make seed-playbook HOST_NEO4J_URI=neo4j+s://...`
HOST_NEO4J_URI ?= bolt://localhost:7687

.DEFAULT_GOAL := help
.PHONY: help install test test-integration test-all run stop logs smoke seed-playbook check-playbook

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

seed-playbook:  ## Load data/playbooks/default_playbook.yaml into Neo4j (needs the stack up)
	NEO4J_URI=$(HOST_NEO4J_URI) $(PY) scripts/seed_playbook.py

check-playbook:  ## Validate the playbook file without touching the database
	$(PY) scripts/seed_playbook.py --check

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
