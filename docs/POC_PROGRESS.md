# POC Progress Tracker

**Resume point: Increment 5 — `awaiting your test`.**
Run `make test`, then `make eval` with the stack up and the playbook seeded.

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
| 1 | Real clause extraction against a schema | accepted |
| 2 | Ground policy checks in one real playbook | accepted |
| 3 | Redlines that are real and persisted | accepted |
| 4 | Human-in-the-loop approve / edit / reject | accepted |
| 5 | One measurable outcome | **awaiting your test** |

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
  Moving them broke `backend.*` imports (run directly, `sys.path[0]` is the script's own
  directory), and two had been importing `backend.tools` / `backend.services` — packages that
  stopped existing at some reorg, so they had been dead well before this. All nine now run from
  any working directory, guarded statically by `backend/tests/test_smoke_scripts_are_runnable.py`.
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

### Making the loop fast enough to iterate on

Analysis is slow because it is three sequential model calls, and nothing else. Measured on the
32,885-character Shuttle contract with `free-large`:

| step | time |
|---|---|
| extract clauses | 41.6s |
| check policies | 60.1s |
| generate redlines | 16.4s |
| risk, CUAD mitigation, validation | 0–8ms |

Two levers, and they compound:

**A small fixture.** `sample-contracts/TinyContract-Fast.txt` (and `.pdf`) is a 1,058-character
contract that deliberately breaches **all seven playbook rules** — Net 90 payment, a fixed $50k
liability cap, indemnity for the other party's own negligence, immediate termination with no
payment for work in progress, and assignment of pre-existing IP. It is both faster *and* better
coverage than the real samples, which trip one rule between them.

**A faster model.** On that fixture, with identical output (5 clauses, 6 violations):

| model | time |
|---|---|
| `gemini-flash-lite` | **11s** |
| `free-large` (OpenRouter) | 64s |
| `free-large` on the 32.9k contract | 118s |

So `TinyContract-Fast.pdf` + `?model=gemini-flash-lite` is roughly **11× faster** than the
combination used up to now. Mind the 20-requests/day Gemini cap: `gemini-flash` was exhausted again
during this measurement and returned zero clauses after its retries.

Regenerate the PDF after editing the text with:

```bash
python scripts/make_sample_pdf.py sample-contracts/TinyContract-Fast.txt
```

### Model options, and the quota trap

`gemini-flash` is capped at **20 requests/day** on the free tier. A handful of analysis runs
exhausts it, and it then presents as a failed upload — the upload path calls the model too, to
extract parties and dates.

Free **OpenRouter** models are wired in for development so this does not block iteration. Set
`OPENROUTER_API_KEY` in `.env` (https://openrouter.ai/keys) and pick one in the UI, or pass
`?model=free-large`. Setting `DEFAULT_MODEL_ID=free-large` makes everything use it by default.

| id | model | notes |
|---|---|---|
| `free-large` | Nemotron 3 Super 120B | **use this one.** Reliable; 50–130s per analysis |
| `free-small` | Gemma 4 26B | faster and cleaner JSON, but its free endpoint is often rate-limited |
| `free-long` | Nemotron 3.5 Lightning | 1M context; too slow to be practical (>200s) |

Not every OpenRouter `:free` model is callable from a plain API — `thinkingmachines/inkling`
returns 403 "only available on agentic harnesses" and `dots-3-note-preview` returns null content.
The three above were each verified end to end on 2026-09-10. The free line-up changes, so all three
are env-overridable (`OPENROUTER_SMALL_MODEL` etc.).

**Free models are flaky and weaker.** Expect intermittent upstream 429/502s — a retry usually
succeeds — and lower extraction quality: the Shuttle contract yields 3 clauses on Gemini and 2 on
`free-large`. Use them to exercise the pipeline, not to judge accuracy.

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

_Passed testing 2026-09-10. Accepted._

---

## Increment 2 — Ground policy checks in one real playbook

**Goal:** policy lived in a Python dict and a chain of `if` statements inside
`intelligence_tools.py`. A finding could say *"payment terms exceed company policy"* but could not
name the rule, policy could only be changed by editing code, and no lawyer could review it.

### What changed

**Policy is now data.** `data/playbooks/default_playbook.yaml` holds the seven rules that were
previously hardcoded — payment, two liability rules, indemnification, termination, IP and
confidentiality — each with a stable id, severity, section reference and preferred redline. A test
asserts every topic the old checker enforced still has a rule, so nothing was lost in the move.

**Seeding.** `make seed-playbook` loads it into `(:PolicyDocument)-[:HAS_RULE]->(:PolicyRule)`.
Idempotent — rules MERGE on `(tenant_id, id)`, so editing the file and re-running updates in place.
`make check-playbook` validates without touching the database. Validation is strict: a missing
field, unknown severity or duplicate id fails loudly, because a silently dropped rule means
contracts stop being checked against it with nothing in the output to say so.

**Checking is grounded in the rules.** `PolicyCheckerTool` now receives the tenant's rules and asks
the model which clauses breach which, returning a rule id per finding. Three things are taken from
the playbook rather than the model:

- **the rule id** — a violation citing an id that was not supplied is discarded, as is one pointing
  at a clause index that does not exist;
- **severity** — it drives the risk score, so it must not drift between runs;
- **the suggested fix** — the rule's own redline text.

**Findings carry their provenance.** `violated_policy` on a clause names the rule(s) it breaches,
and violations expose `rule_id` and `section_reference` through the API.

**An empty playbook is an error.** Previously zero rules would have produced zero violations, which
reads as "this contract is compliant" — a completely different claim. It now raises.

### One thing this increment had to undo

CUAD keyword deviations were being merged into `policy_violations`. They are heuristics with no
playbook rule behind them, so with them in the list a consumer could not tell a cited breach from a
guess — which would have made the citation guarantee worthless. They stay in
`cuad_analysis.deviations`, where the API already surfaced them separately.

### Also fixed

`backend/run_migration.py upgrade` only ran the embeddings migration. It now runs the section,
clause, audit and phase-2/3 migrations too. `clause_schema_migration` seeds the `(:ClauseType)`
nodes that `CLASSIFIED_AS` matches against — without it every CUAD classification was being
silently dropped at write time.

### How to test

```bash
make test            # 166 passed, 3 skipped
make check-playbook  # validates the file, no database needed
```

With the stack up:

```bash
make seed-playbook   # 7 rules
```

Then analyse a contract and confirm each violation names a rule.

**Verified on 2026-09-10** against the Acme MSA:

```
6 clauses, 1 violation, risk 55.0 (MEDIUM)
  IND-001  [CRITICAL] Indemnification
           The clause requires Customer to indemnify Provider for third-party claims...

clause citations:
  Indemnification    violated_policy=IND-001
  (others)           violated_policy=None

every violation cites a rule: True
cuad deviations (kept separate): 1
```

Note `make seed-playbook` runs on the host, where `.env`'s `NEO4J_URI` points at the compose
hostname. The target overrides it to `bolt://localhost:7687`; use
`make seed-playbook HOST_NEO4J_URI=...` for a remote instance.

### Addressed in review (Copilot, PR #2)

Ten findings, all valid. The consequential ones:

- **The default API path was ungrounded.** `use_planning` defaults to `true`, and that route built
  `PolicyCheckerTool()` with no model and no rules. Every live check I ran used
  `use_planning=false`, so I had tested the path I built rather than the one the product uses.
  Rules are now loaded per run in the planning engine, which also stamps clauses.
- **The fail-closed guard was being swallowed.** `_check_policies` caught every exception and
  returned `policy_violations: []`, so an unseeded tenant still read as compliant. Configuration
  faults now propagate.
- **Citations could leak between clauses.** Attachment matched on clause text, so two clauses
  sharing an evidence span both got cited. It attaches by index now.
- **Deleted rules kept firing.** Seeding only MERGEd, so removing a rule from the YAML left it
  active. Seeding now retires rules absent from the file.
- **The clause migration injected sample data.** `run_migration()` called `_create_sample_clauses`,
  which uses `CREATE` — every `upgrade` would have added fabricated clauses to real contracts.
  Sample data is opt-in; `migrate_schema_only()` is what the runner calls.
- **Partial migration failure exited 0.** It now exits non-zero and names what failed.
- Also: deviations were still merged into violations on two fallback paths; the empty-clause short
  circuit ran before the no-playbook check; rules were selected by a copied `tenant_id` rather than
  the `HAS_RULE` relationship; and one test's name claimed to check a default its fixture overrode.

### Your feedback

_Merged in PR #2 on 2026-09-10. Accepted._

---

## Increment 3 — Redlines that are real and persisted

**Goal:** redlines came from a five-branch `if/elif` on clause type that returned a constant, so
every payment violation in every contract produced the same sentence. And they were never stored —
only `redlines_count` was persisted, so the drafted language existed solely in the HTTP response
and was gone on refresh.

### What changed

**Redlines are drafted for the clause in hand.** `RedlineGeneratorTool` now receives the violated
clause, the rule's requirement and the breach, and rewrites *that* clause. The prompt asks it to
keep the contract's own defined terms, party names and numbering, to change only what the rule
requires, and to preserve protections the rule does not object to.

The difference is the point of the increment. A real result from the Acme MSA, `IND-001`:

> **before** — Provider shall defend and indemnify Customer against third-party claims alleging that
> the Services infringe a United States patent, copyright, or trade secret. Customer shall defend and
> indemnify Provider against third-party claims arising from **Customer Data or Customer's unlawful
> use of the Services**. The indemnified Party must give prompt written notice and reasonable
> cooperation.
>
> **after** — …Customer shall defend and indemnify Provider against third-party claims arising from
> **Customer's gross negligence, willful misconduct, or infringement of intellectual property under
> this Agreement**. …

Both compliant sentences are untouched; only the offending scope is rewritten, in the contract's own
terms. The old constant would have replaced the whole clause with generic wording a lawyer had to
redraft.

**Grounding, as with the earlier increments.** Only violations that cite a rule can be redlined —
the rule is what defines "fixed". `original_text` is the clause as extracted, not the model's
paraphrase. `priority` follows the rule's severity, so it cannot drift. Redlines citing an unknown
rule are discarded.

**They are persisted.** `(:Contract)-[:HAS_REDLINE]->(:Redline)` with the rule id, both texts,
justification and priority. Re-analysing replaces the set rather than accumulating duplicates.
`GET /api/intelligence/contracts/{id}/redlines` reads them back — which is also what Increment 4's
approve/reject flow will act on.

### How to test

```bash
make test    # 180 passed, 3 skipped
```

With the stack up and the playbook seeded, analyse a contract, then:

```bash
curl "http://localhost:8000/api/intelligence/contracts/<id>/redlines"
```

**Verified on 2026-09-10** against the **default** API route (no `use_planning` param — the path
the product actually uses, which the last review showed I had not been exercising):

- 6 clauses, 1 violation, 1 redline citing `IND-001` at CRITICAL
- backend restarted, redline read back intact from `(:Redline)`
- re-analysed: still 1 redline, not 2

### Addressed in review (Copilot, PR #3)

Four findings, all valid:

- **A failed draft destroyed good redlines.** `_generate_redlines` catches errors and returns `[]`,
  which persistence could not distinguish from "none needed" — so one rate-limited call wiped the
  stored set. The state now records the failure and persistence refuses to replace on it.
- **Two clauses breaching the same rule were conflated.** The redline join keyed on `rule_id` alone,
  so both collapsed to the last one and a redline drafted for one clause was stored against
  another's text. Reproduced it, then keyed the join by `(rule_id, clause_index)` with the index in
  the model contract. `clause_index` now flows through to the graph and the API — Increment 4 needs
  it to anchor approvals.
- **The redlines endpoint let any reader pick a tenant.** It accepted `tenant_id` from the query
  string while RBAC only checked the role. Added `get_current_tenant`, which takes the tenant from
  the caller (header in production, `DEV_DEFAULT_TENANT` in development) and ignores the query
  parameter entirely.

### Your feedback

_Merged in PR #3 on 2026-09-10. Accepted._

---

## Increment 4 — Human-in-the-loop approve / edit / reject

**Goal:** the design doc makes human approval a POC deliverable. Until now a decision could be
POSTed as a free-form string to a detached `(:LegalDecision)` node that gated nothing — the redline
itself carried no state, and the UI had no way to act on one.

### Failure cases were designed first

This is the first increment where a wrong failure mode loses *human* work rather than machine
output, so these were settled before the happy path:

| case | behaviour |
|---|---|
| decide on a redline that does not exist | 404, never a silent no-op |
| decide on another tenant's redline | 404 — a 403 would confirm it exists |
| `MODIFIED` with no replacement text | 422; there is nothing to apply |
| `APPROVED`/`REJECTED` *with* replacement text | 422; guessing either way discards what they typed |
| decide twice | allowed, last wins, and the response names the prior status |
| re-analysis after a decision | the decision is preserved |
| a viewer attempting to approve | 403 |

### What changed

**Decisions live on the redline.** `status` is `PENDING` → `APPROVED` / `MODIFIED` / `REJECTED`,
with `final_text`, `decision_note`, `decided_by` and `decided_at`. `final_text` is resolved once at
decision time — the suggestion for APPROVED, the reviewer's wording for MODIFIED, the original
clause for REJECTED — so nothing downstream has to reconstruct "what did they actually agree to"
from a status plus three text fields.

**A dedicated permission.** `APPROVE_REDLINE`, held by ADMIN and LEGAL_REVIEWER. Deliberately not
`ANALYZE`: VIEWER holds that, and being able to run an analysis is not the same as being able to
accept its output.

**Re-analysis no longer discards judgement.** This was flagged as a risk when Increment 3 landed.
Reviewed redlines are left exactly as the reviewer left them; only undecided drafts are refreshed,
and drafts for breaches no longer reported are dropped so the queue does not accumulate stale items.

**Redline ids are identity-based.** Live testing showed positional ids (`_redline_000`) collide
across runs — two redlines ended up sharing one. An id is now
`{contract_id}_{rule_id}_c{clause_index}`: one breach of one rule on one clause has one redline,
and persistence MERGEs on it rather than deleting and recreating.

**UI.** A Redlines card on the Intelligence page opens a review panel showing current vs suggested
text, the rule, priority and status, with Approve / Edit / Reject and an optional reason. Rejected
API messages are shown verbatim, since they usually tell the reviewer what to do differently.

### How to test

```bash
make test    # 204 passed, 3 skipped
```

With the stack up: analyse a contract, open the **Redlines** card, and try each action. Then
re-analyse and confirm your decision is still there.

**Verified on 2026-09-10** (default route throughout):

- VIEWER approving → 403; LEGAL_REVIEWER → 200, `PENDING -> APPROVED`
- `MODIFIED` with no text, `APPROVED` with text, and `PENDING` → 422 with a specific reason
- unknown redline → 404; another tenant's redline → 404
- a `MODIFIED` redline survived **two** re-analyses with its text and note intact, while a newly
  found breach was added alongside it as `PENDING`

### Addressed in review (Copilot, PR #4)

Nine findings. Eight fixed, one honestly downgraded:

- **Redline identity was positional.** `clause_index` is an offset into whichever list the last
  extraction produced, so a reordering would orphan a reviewer's decision — breaking the guarantee
  this increment exists to give. Ids are now keyed on a hash of the normalised clause text.
- **The decision was a read-then-write race**, and an empty result crashed with `IndexError` → 500.
  It is now one statement that reports the status it replaced, and a vanished row raises a 404.
- **No uniqueness constraint** backed the MERGE. Added `redline_schema_migration` with a
  `redline_id` uniqueness constraint and a de-duplication pass, wired into `run_migration upgrade`.
- **Decisions are now audited.** The field description promised an audit trail that did not exist.
- **The note box was shared across every row** — typing a reason for one populated all of them, and
  deciding on another row submitted the wrong reason. Keyed per redline.
- **A failed load rendered as "No redlines"**, telling the reviewer to re-run analysis when the real
  answer was a 401 or a 500. The error is shown first, with a retry.
- **The panel sent no identity headers**, so it could not work in production at all. Added
  `frontend/src/lib/apiClient.ts` as the single place the frontend states who it is.
- **The new card was not keyboard-operable.** `role`, `tabIndex`, Enter/Space and a focus ring.

**Not fixed — the tenant header is not trusted.** `get_current_tenant` reads `X-Tenant-ID` and
nothing validates it, so any caller can name any tenant. Moving it off the query string removed the
casual form of the problem but not the problem. There is no honest fix without authentication, so
the claim has been downgraded everywhere rather than dressed up: this is **not** tenant isolation
and the system should not see real client data until auth exists.

**A residual limitation worth knowing.** Content-hashed ids are stable under reordering, but the
extractor is non-deterministic — a re-run can produce a slightly different span for the same clause
and therefore a new redline alongside the decided one. Stale *pending* drafts are cleaned up on the
next run; decided ones are kept deliberately. Quantifying that variance is Increment 5's job.

### Your feedback

_Merged in PR #4 on 2026-09-10. Accepted._

---

## Increment 5 — One measurable outcome

**Goal:** every claim about quality so far has been anecdotal — "the redline looks good", "it found
the right rule". The design doc asks for a measurable outcome, and without one there is no way to
tell a prompt change that helped from one that did not.

### What makes this measurable

The playbook supplies an objectively correct answer. For a labelled contract there is a specific
set of rule ids a correct review should raise, so precision and recall over those ids mean
something concrete — unlike a generic "clause accuracy" figure.

`evaluation/` holds three contracts, written so the right answer is a matter of construction rather
than opinion:

| fixture | expects | why it is there |
|---|---|---|
| `breaching` | 6 rules | every clause drafted to breach |
| `partial` | `PAY-001`, `TRM-001` | the discriminating case — most clauses are compliant |
| `clean` | nothing | **the one that matters**: only a compliant contract can show whether the system invents violations |

They are synthetic deliberately. Labelling a real contract needs a lawyer, and a number produced
from an engineer's guess at the right answer would look objective without being it.

### Metrics

Set-based precision / recall / F1 over rule ids, **micro-averaged** so a one-rule contract does not
weigh as much as a six-rule one, and so the compliant fixture cannot inflate the score by doing
nothing. Plus groundedness (evidence spans genuinely present in the contract), redline coverage
(violations that produced replacement language), and stability across repeated runs.

The scorer is pure functions in `backend/evaluation/scorer.py` with 22 unit tests, so the
arithmetic is checkable without a stack running. The harness that drives the live API is thin.

### Results — `gemini-flash-lite`, 3 runs per contract

```
  breaching.pdf    exact  P 1.00  R 1.00  F1 1.00   71s
                   grounded 5/5   redlined 6/6      across 3 runs: identical
  partial.pdf      exact  P 1.00  R 1.00  F1 1.00   33s
                   grounded 6/6   redlined 1/2      across 3 runs: identical
  clean.pdf        exact  P 1.00  R 1.00  F1 1.00   11s
                   grounded 6/6   redlined 0/0      across 3 runs: identical

  Overall   precision 1.00   recall 1.00   F1 1.00
            8 correct, 0 invented, 0 missed
            3/3 contracts exactly right
```

**Rule detection is stable.** Identical across three runs on all three contracts, including raising
nothing on the compliant one. That contradicts what I had assumed from watching `free-large` on
long contracts, and is worth knowing before optimising anything.

**Redline drafting is not.** `partial.pdf` scored 2/2 coverage on one pass and 1/2 on another: the
same two violations, but one run failed to draft language for one of them. Detection and drafting
have different reliability, which nothing before this increment could have told us.

### Two bugs the evaluation found immediately

- **Violations carried `clause_index: None`** through the API while redlines carried the real
  index, so nothing could join a violation to its redline — redline coverage read 0/6 when it was
  really 6/6. A real product bug that four increments of inspection had not surfaced.
- **The harness scored grounding against the source `.txt`** rather than the PDF-extracted text the
  system actually saw. Punctuation differs between the two, so genuine clauses were reported as
  hallucinated (2/5 instead of 5/5). A measurement bug that looked exactly like a product bug —
  which is its own lesson about trusting a new metric before checking it.

### Review round on PR #5

Copilot raised five findings on the harness, all of them real, all now fixed:

- **A clause with no evidence scored as grounded.** The empty string is a substring of every
  contract, so `"" in source` passed — the one metric meant to catch a hole in the extractor's
  grounding guard was blind to the worst case of it. Blank spans now count as ungrounded and are
  reported separately.
- **Eval fixtures were uploaded into `default-tenant` and never removed.** There is no delete
  endpoint to clean up with, so isolation is the fix: fixtures go to `evaluation-tenant`, which
  `make eval` seeds the playbook into first. `default-tenant` had accumulated 40 contracts from
  earlier runs before this.
- **Failed runs were dropped from the stability check.** Three requested passes of which two
  errored would summarise as one run and print no stability line at all — a flaky provider made to
  look stable. Failures are now counted, and the report says when a check was incomplete.
- **Per-run redline coverage was thrown away.** Only the last run survived, so the harness could
  not reproduce its own headline finding — the same violations detected every time, language
  drafted for them only sometimes. Drafting variance is now tracked and reported apart from
  detection stability.
- **`make eval` exited 0 when nothing was scored.** The exit check looked only at false positives,
  so an evaluation that ran zero contracts read as an evaluation that passed — loudest exactly
  when the provider is down. Any unscored contract now fails the command.

### How to test

```bash
make test            # 237 passed, 3 skipped — scorer arithmetic, offline
```

With the stack up (`make eval` seeds the evaluation tenant itself):

```bash
make eval ARGS="--model gemini-flash-lite"      # one pass, ~2 minutes
make eval-stability ARGS="--model gemini-flash-lite"   # three passes
```

`make eval` exits non-zero if the pipeline invented a breach that is not in the contract — a false
positive on a compliant clause is the failure that costs a reviewer's trust, so it is the one worth
failing a build over.

### Honest limits of this number

- **Three synthetic contracts is a floor, not a benchmark.** It catches gross regressions; it says
  nothing about long real-world contracts with unusual drafting.
- **Scoring the rule set does not score the redline's quality.** Coverage counts whether language
  was drafted, not whether a lawyer would accept it. The human override rate from Increment 4's
  decisions is the natural next metric and needs real reviewer use to accumulate.
- **1.00 across the board says the fixtures are not yet hard enough.** A benchmark everything
  passes has stopped providing information — the next useful move is adversarial fixtures
  (ambiguous drafting, breaches split across clauses, near-miss wording) rather than more of these.

### Your feedback

_(write here)_

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
