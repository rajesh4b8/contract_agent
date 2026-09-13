PY := backend/.venv/bin/python
# .env points NEO4J_URI at the compose hostname, which only resolves inside a
# container. Host-run scripts reach the published port instead. Override to
# point at a remote instance: `make seed-playbook HOST_NEO4J_URI=neo4j+s://...`
HOST_NEO4J_URI ?= bolt://localhost:7687
# Evaluation fixtures are uploaded here rather than a real tenant: there is no
# delete endpoint, so isolation is what keeps repeated runs from silting up
# a tenant's reports and search with synthetic contracts.
EVAL_TENANT ?= evaluation-tenant

.DEFAULT_GOAL := help
.PHONY: help install test test-integration test-all run stop logs smoke seed-playbook seed-eval-playbook check-playbook check-matters migrate-matters eval eval-stability

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

check-matters:  ## List the contracts that would get a matter, without writing
	NEO4J_URI=$(HOST_NEO4J_URI) $(PY) scripts/migrate_matters.py --check

migrate-matters:  ## Give contracts that predate matters a single-version matter each
	NEO4J_URI=$(HOST_NEO4J_URI) $(PY) scripts/migrate_matters.py $(ARGS)

seed-eval-playbook:  ## Seed the playbook into the evaluation tenant (idempotent)
	NEO4J_URI=$(HOST_NEO4J_URI) $(PY) scripts/seed_playbook.py --tenant $(EVAL_TENANT)

eval: seed-eval-playbook  ## Score the pipeline against evaluation/ (needs the stack up)
	$(PY) scripts/evaluate_pipeline.py $(ARGS)

eval-stability: seed-eval-playbook  ## Same, three passes per contract, to see how much the answer moves
	$(PY) scripts/evaluate_pipeline.py --runs 3 $(ARGS)

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
