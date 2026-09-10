# POC Progress Tracker

**Resume point: Increment 1 — `awaiting your test`.**
Run `make test`, then try the app (see *Running it locally*) and write under *Your feedback*.

This file is the live state of the POC work. It is committed, so any session on any machine can
pick up by reading it first. The detailed reasoning behind the plan lives in the session that
produced it; this file is what you and I actually work from.

---

## How this works

- **One increment at a time.** I stop after each and wait for you to test. Nothing chains.
- **Statuses:** `not started` → `in progress` → `awaiting your test` → `accepted` / `changes requested`
- **Your feedback** sections are yours. Write anything there — I read this file at the start of
  every session and act on it before moving on.
- To resume in a fresh session, say *"continue the POC work"*. This file is enough context.

## Status board

| # | Increment | Status |
|---|-----------|--------|
| 0 | Make the repo testable | accepted |
| 1 | Real clause extraction against a schema | **awaiting your test** |
| 2 | Ground policy checks in one real playbook | not started |
| 3 | Redlines that are real and persisted | not started |
| 4 | Human-in-the-loop approve / edit / reject | not started |
| 5 | One measurable outcome | not started |

---

## Increment 0 — Make the repo testable

**Goal:** nothing in this repo could be verified — no pytest config, no CI, pytest was not even a
dependency, and importing the app required a live Neo4j and an API key, so tests failed during
collection before a single one ran. This increment makes the truth checkable.

### What changed

**The unlock — service singletons are now lazy.** `backend/shared/utils/lazy.py` adds a small
`LazyProxy`. `graph` and `embedding` were built at import time in
`contract_search_tool.py`, `enhanced_contract_search_tool.py` and `gemini_embedding_service.py`;
they now construct on first attribute access instead. All 101 existing call sites are unchanged.
The whole app, including `backend.main`, now imports with no database and no API key.

**Test infrastructure**
- `pytest` + `pytest-asyncio` added to a `[dependency-groups] dev` group in `backend/pyproject.toml`
- `pytest.ini` at the repo root — offline by default, `-m "not integration"`
- `conftest.py` at the repo root — puts the repo on `sys.path`, points the offline suite at a
  dead Neo4j address so an accidental connection fails fast instead of hanging
- `Makefile` — `make test`, `test-integration`, `test-all`, `run`, `stop`, `logs`, `smoke`

**Test suite cleanup**
- Removed 11 `sys.path` hacks. One of them — `backend/tests/test_ai_patterns.py` inserting
  `backend/` at `sys.path[0]` — made `backend/mcp/` shadow the real `mcp` package and broke
  `fastmcp` for every test collected after it. Four others pointed at a directory that never
  existed (`tests/backend`).
- Moved 9 scripts that need a live server or database out of the test tree into `scripts/smoke/`
  (renamed `test_*` → `check_*`). They were never tests; several had zero assertions.
- Marked 7 genuinely Neo4j-dependent tests `@pytest.mark.integration` rather than deleting them.
- Deleted the import-time mock patching in `test_mcp_capabilities.py` — it existed only to dodge
  the eager singletons.

**Two real bugs fixed**

1. **RBAC defaulted to ADMIN.** `get_current_user_role` returned `UserRole.ADMIN` when the
   `X-User-Role` header was missing — the comment directly above it said it defaulted to VIEWER
   for safety. The frontend never sends that header, so every endpoint was effectively unguarded.
   **The repo's own test already asserted the correct behaviour** — it had simply never been run.

   Failing closed then broke the UI upload with a 403, because the frontend sends no header. The
   resolution keeps the security property and unbreaks local dev: **production fails closed to
   `VIEWER`; development falls back to `DEV_DEFAULT_ROLE`** (default `LEGAL_REVIEWER`, which can
   upload and analyse but not read the audit trail or manage policies). An unrecognised value
   falls back to `VIEWER`. All three paths are covered by tests. Replace this branch with real
   token validation when auth lands.
2. **Every upload chunked the document twice.** `document_upload.py` logs
   `quality_assessment['overall_quality']` on the chunking *success* path, but
   `QualityValidator.validate_chunks` never returned that key. The `KeyError` was swallowed by the
   surrounding handler, which re-chunked the whole document with the fallback strategy and wrote a
   second, differently-keyed set of chunk nodes — doubling Gemini spend and logging it as "async
   chunking failed". The validator now returns the aggregate it always had the parts for.
   `backend/tests/test_chunk_quality_contract.py` pins the contract (verified: 4 tests fail if the
   fix is reverted).

### How to test

```bash
make install          # first time only — adds pytest to the venv
make test
```

**Expected:** `69 passed, 7 deselected`. It must work with Docker down, no `NEO4J_*` reachable and
no API keys — that is the point of the increment.

Worth trying, to confirm the unlock is real:

```bash
docker compose down                 # make sure nothing is running
make test                           # still green
```

### Running it locally

```bash
make run     # docker compose up --build; first run pulls neo4j + phoenix
```

| URL | What |
|---|---|
| http://localhost:3000 | The app. Opens on the Intelligence page |
| http://localhost:8000/docs | FastAPI Swagger |
| http://localhost:7474 | Neo4j browser — `neo4j` / `contractdev` |
| http://localhost:6006 | Phoenix — LLM traces |

`.env` sets `LOG_LEVEL=ERROR`, so the pipeline runs silently. Use `LOG_LEVEL=INFO make run` or
`make logs` to watch it.

**Verified working on 2026-09-09:** all four containers healthy, upload with no role header
returns 200, and the contract lands in Neo4j with its full text.

**What you will see, and it is the point of Increment 1:** analysing two very different
contracts — a 5,756-character MSA and a 32,885-character services agreement — returns *byte-identical*
clauses, violations and a risk score of 45.0 for both, in ~0.1s with no LLM call:

```
clauses identical    : True
violations identical : True
risk score           : 45.0 vs 45.0
```

That is `ClauseDetectorTool` returning its two hardcoded clauses. Increment 1 replaces it.

### Files touched

```
new:      backend/shared/utils/lazy.py
new:      backend/tests/test_chunk_quality_contract.py
new:      conftest.py  pytest.ini  Makefile  docs/POC_PROGRESS.md
changed:  backend/shared/utils/contract_search_tool.py
          backend/shared/utils/enhanced_contract_search_tool.py
          backend/shared/utils/gemini_embedding_service.py
          backend/governance/rbac.py                       (ADMIN -> VIEWER)
          backend/infrastructure/chunking/quality_validator.py  (overall_quality)
          backend/pyproject.toml
          11 test files (sys.path removal, integration marks)
moved:    9 files -> scripts/smoke/check_*.py
```

### Your feedback

_(accepted — no changes requested)_

---

## Increment 1 — Real clause extraction against a schema

**Goal:** the intelligence output was fabricated. `ClauseDetectorTool._run` built a proper
extraction prompt, discarded it — `# This would use the LLM - simplified for prototype` — and
returned two hardcoded clauses for every contract. No LLM was called anywhere in the intelligence
path, so every document produced an identical report.

### What changed

**The schema.** `backend/shared/models/clause_finding.py` defines `ClauseFinding` with the design
doc's seven fields: `clause_type`, `risk_level`, `violated_policy`, `evidence_span`,
`suggested_redline`, `confidence`, `human_review_required`. `violated_policy` and
`suggested_redline` are filled by Increments 2 and 3; the shape is settled now so it only changes
once. Confidence is normalised (models often answer `85` for 85%).

**Real extraction.** The tool now calls the model with a `PydanticOutputParser`, reusing the
pattern already working in `contract_analyzer.py`. Two properties make it trustworthy:

- **Grounding.** Every finding must quote the contract verbatim in `evidence_span`. Findings whose
  span is not actually present are dropped — an ungrounded clause is a hallucination, and policy
  checks and risk scores would inherit it. This fired in practice: one run invented a Termination
  clause and it was discarded.
- **Honest failure.** Extraction raises instead of returning `[]`. "The model was unavailable" and
  "this contract has no notable clauses" produce very different reports, and conflating them is how
  the original stub went unnoticed for so long.

**The model was being thrown away.** `IntelligenceOrchestrator` received an `llm` and stored it,
but every tool was constructed with no arguments. Worse, `_get_llm_for_model` returned a *compiled
LangGraph agent* rather than a chat model — its `._llm` lookup never matched — and a compiled graph
has no `.invoke(prompt)` for a string. It now builds a real chat model via `build_llm()` from the
central catalogue, and the model is threaded to all three tool construction sites.

### Three pre-existing bugs this surfaced

Raising instead of returning `[]` exposed each of these immediately.

1. **A third construction site.** `ReACTAgent` also built the tool with no model. It had been
   silently receiving the two fake clauses.
2. **`asyncio.run()` inside a running event loop.** `_pattern_analysis` crashed whenever a contract
   was complex enough for a pattern to be selected — which killed the entire analysis, extracted
   clauses included. It presented as "this document has no clauses". Fixed with `run_coroutine()`.
3. **Markdown-fenced JSON.** The model wrapped its answer in ```` ```json ```` for the larger
   contract but not the smaller one, so the same prompt succeeded on one document and failed on the
   next with "Invalid json output". `strip_code_fence()` handles it.

### A correction worth recording

I first added a retry wrapper around the model call. The logs showed why that was wrong: the
google-genai SDK already retries 429/503 with its own backoff (1s → 17s), so a second layer turned
a 34-second failure into 100+ seconds and caused request timeouts. The wrapper and its tests were
removed. Provider SDKs own transient retries; do not stack another layer on top.

### How to test

```bash
make test          # 100 passed, 7 deselected
```

Then, with the stack up (`make run`), upload both files in `sample-contracts/` and analyse each.
**They must produce different clauses.** That is the whole point of the increment.

Note: analysis is now genuinely slower (6–60s depending on contract size) because it makes a real
model call. Previously it took ~0.1s because it made none.

**Verified on 2026-09-10:**

```
ACME / NORTHWIND MSA (5,756 chars): 6 clauses, risk 55.0 (MEDIUM)
  LOW      Payment Terms      Section 3
  LOW      Confidentiality    Section 4
  MEDIUM   IP Ownership       Section 5
  MEDIUM   Indemnification    Section 8
  HIGH     Liability          Section 9    [needs review]
  LOW      Termination        Section 10

SHUTTLE SERVICES (32,885 chars): 3 clauses, risk 30.0 (LOW)
  LOW      Payment Terms      Section 2
  MEDIUM   Termination        Section 4
  HIGH     Indemnification    Section 5    [needs review]

clauses identical: False        (it was True before this increment)
```

All 9 evidence spans were checked back against the contract text stored in Neo4j: **9/9 verbatim.**

### Watch out for the Gemini free-tier quota

`gemini-flash` is capped at **20 requests/day** on the free tier and was exhausted during this
work — a 429 `RESOURCE_EXHAUSTED` that presents as a slow request followed by an error. If analysis
starts failing, that is the first thing to check: try `?model=gemini-flash-lite` (separate quota),
or add billing. The failure is now surfaced rather than silently returning zero clauses.

### Files touched

```
new:      backend/shared/models/clause_finding.py
new:      backend/tests/test_clause_extraction.py
          backend/tests/test_message_content.py
          backend/tests/test_run_coroutine.py
changed:  backend/agents/intelligence_tools.py           (the stub -> a real call)
          backend/agents/contract_intelligence_agents.py (llm wiring, run_coroutine)
          backend/agents/planning/execution_engine.py    (llm wiring)
          backend/agents/patterns/react_agent.py         (llm wiring)
          backend/agents/agent_workflow_tracker.py       (None start-time crash)
          backend/application/services/contract_intelligence_service.py (build_llm)
          backend/api/contract_intelligence.py           (new fields in the response)
          backend/domain/entities.py                     (ContractClause fields)
          backend/shared/utils/message_content.py        (strip_code_fence)
          backend/tests/test_pattern_integration.py      (stub llm)
```

### Known issues found but not fixed here

- `EnhancedPrecedentMatcherTool._find_real_precedents()` is called without its required `tenant_id`,
  so precedent matching warns and returns nothing on every clause.
- `ChainOfThoughtAgent` raises `NameError: name 'overall_risk' is not defined`. It fails safely
  (the base agent catches it), but the CoT path produces nothing.
- The frontend still reads `content`/`confidence_score`; the response carries both those aliases and
  the new canonical fields. The UI does not yet show `evidence_span` or the review flag.

### Your feedback

_(write here — anything that should change before Increment 2)_

---

## Increment 2 — Ground policy checks in one real playbook

Seed one playbook (`data/Contract_Policy_Playbook.pdf` exists; `POST /api/policies/upload` already
ingests into `(:PolicyRule)`). Replace keyword matching against the in-code `COMPANY_POLICIES`
dict with retrieval of the seeded rules, and set `violated_policy` to the rule id so every finding
traces back to a playbook entry.

Also fix the migration runner: `backend/run_migration.py` only runs `multi_level_embeddings`, but
`clause_schema_migration` seeds the `(:ClauseType)` nodes that `CLASSIFIED_AS` needs — without it
every classification is silently dropped.

### Your feedback

_(not started)_

---

## Increment 3 — Redlines that are real and persisted

Generate redlines with the LLM grounded in the violated rule, replacing the five-branch constant
lookup at `intelligence_tools.py:288-341`. Persist them — today only `redlines_count` is stored
(`contract_intelligence_service.py:187`), so the redline bodies vanish with the HTTP response.

### Your feedback

_(not started)_

---

## Increment 4 — Human-in-the-loop approve / edit / reject

POC scope item #5 in the design doc, entirely absent from the UI. Add a status field, three
endpoints, and controls in `frontend/src/components/features/intelligence/ClausesDetail.tsx`
(read-only today). Replace the free-form `legal_decision: str` (`feedback_api.py:20`) with an enum.

### Your feedback

_(not started)_

---

## Increment 5 — One measurable outcome

Label 20–30 clauses from the checked-in sample contracts as ground truth; score clause-type
accuracy, risk precision/recall, and groundedness. The metric classes in
`training/scripts/evaluate.py:43-155` are sound and can be reused — they are just currently
pointed at local Qwen checkpoints instead of the product.

### Your feedback

_(not started)_

---

## Decisions log

Settled — do not re-litigate without saying so explicitly.

| Decision | Rationale |
|---|---|
| **Hosted models only** for this phase | The Qwen teacher/student, LoRA, distillation and vLLM track stays deferred. `training/` is real code but a detached silo — the backend loads no local model and declares no ML dependencies. Getting the loop working and measurable comes first. |
| **Quarantine, don't delete** unused subsystems | Supervisor consensus/quality gates/circuit breakers, the pattern orchestrator, the planning agent and the six chunking strategies move off the live path but stay in the repo. |
| **One contract end-to-end, asserted** is the first milestone | Matches the design doc's own "POC Scope — Start Narrow" guidance. |
| **Work on `main`** | A parallel session is also committing here; small increments reduce collision risk. |

## Deferred — in the design doc, deliberately not now

Vector index and hybrid search (there is currently **no** vector index — similarity is a full
cosine scan); Qwen teacher/student, LoRA, distillation, vLLM serving, risk-based model routing;
Ragas / DeepEval; A/B shadow evaluation; data masking, anonymization and retention policies.

## Known issues not yet scheduled

- `get_node()` is called on a compiled LangGraph object in
  `backend/agents/enhanced_pdf_processing_agent.py:106-108`; no such method exists in langgraph
  1.2.11, and the builder never sets an entry point. The `enable_enhanced=true` upload path cannot
  succeed. Slated for the Increment 1–2 window.
- The uploaded PDF leaks on the enhanced path — cleanup lives in an `except` block, not a
  `finally`, and that path returns 200 rather than raising.
- `tenant_id` is a client-supplied query parameter, not derived from the caller.
- No PII redaction on ingest; the security validator detects and warns only.
- The LLM branches of section, clause and CUAD extraction are stubs whose response parsers
  `return []`; only the regex paths produce data.
