# POC Progress Tracker

**Resume point: Increment 7 — `awaiting your test`.**
Increments 0–6 are accepted. Increment 7 is built; test it before 8 starts.

Still awaiting your test: **[Fix — model failures now say what happened](#fix--model-failures-now-say-what-happened)**
(out-of-increment bug fix, from your report of an unexplained "processing error") and
**[Debug — a live timeline of what the pipeline is doing](#debug--a-live-timeline-of-what-the-pipeline-is-doing)**
(out-of-increment, from your report that uploads take a long time with nothing on screen to say why).

**Increment 7 is built and waiting on you.** 8 and 9 remain specified: analysing the whole contract
rather than its first 12,000 characters (8), and the cross-version change report (9).

Two sections are worth knowing about before starting anything:
[Product shape](#product-shape--what-this-system-is-the-source-of-truth-for) (settled — what this
system is the record of) and
[Roadmap — is Neo4j the right store?](#roadmap--is-neo4j-the-right-store-open-not-decided)
(**open**, full evidence recorded, deliberately not decided yet).

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
| 5 | One measurable outcome | accepted |
| 6 | Multiple contracts, each resumable | accepted |
| 7 | Content-addressed chunks | **awaiting your test** |
| 8 | Analyse the whole contract | specified — not started |
| 9 | Incremental re-analysis and the change report | specified — not started |

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

_Passed testing 2026-09-13. Accepted._

---

## Fix — model failures now say what happened

**Status: `awaiting your test`.** Not an increment — reported while testing: *"I am getting a
processing error without a clear error message in case the AI agent quota expired or the agent is
giving a forbidden or something else."*

### What was actually wrong

Three separate failures, all with the same symptom:

1. **The message was the exception.** An upload rendered `Processing error: 429 You exceeded your
   current quota ... [violations { quota_metric: ... }]`, or just `Processing failed: 'gemini-flash'`
   — a bare `KeyError` on `llm_mgr.agents[model]` when the picked model had no key configured.
2. **The analysis did not report a failure at all.** This was the worse one. With planning on (the
   default), a refused call failed one step, and execution carried on to the end: the API answered
   **200** with no clauses, no violations, risk `UNKNOWN`. On screen that is indistinguishable from
   a contract with nothing wrong in it. The non-planning path did the same at three layers, and
   `analyze_contract_by_id` turned any exception into `None`, which the API reports as **404
   contract not found**.
3. **Refusals were retried.** A spent quota answered the same way three times, ~30s apart, and the
   planning path then fell back and ran the whole analysis again against the same dead model.

### What changed

**`backend/shared/errors/provider_errors.py`** — one place that reads a provider exception and says
what happened and what to do. It classifies quota exhaustion, per-minute throttling, a rejected
key, a forbidden model, a retired model, context-length, safety blocks, timeouts and provider
outages, by class name, HTTP status and message text — no provider SDK imports, so an
uninstalled provider costs nothing. It returns `None` for anything that is not plainly a model
failure, which is what keeps a Neo4j outage from being reported as "the AI provider is down".

The messages name the model and the way out: *"Google Gemini (model 'gemini-flash') has no quota
left on this API key. The Gemini free tier allows only a handful of requests per day and resets at
midnight Pacific. Pick one of the 'Free ·' models in the dropdown to keep working, or enable
billing on the key in GOOGLE_API_KEY."*

- **The status code is the failure's own**, not a blanket 500: 429 for quota and throttling (with
  `Retry-After`), 504 for a timeout, 502 for a provider outage, 422 for a document too long.
  A *server-side* key problem answers **503, never 401/403** — those two are how this API says
  "you are not allowed", and the reviewer is.
- **A refused analysis raises instead of returning an empty one.** Clause extraction is the step
  nothing can proceed without, so a refusal there stops the run in both the planned and the
  traditional path. Later steps still degrade — a partial analysis is worth having — but the API
  now returns `warnings` naming each stage that did not run and why, and the UI lists them in the
  "Partial Analysis Results" panel instead of "Some analysis components may have failed".
- **Refusals are not retried**, in either the step executor or the planning fallback.
- **The chat stream** reports the reason as an `error` event rather than closing the connection and
  leaving "Error: Failed to generate the response".
- Search classifies too — semantic search embeds the query, so it hits the same quota.

**One unrelated bug this uncovered:** the chat was broken outright. The prompt-injection validator
matched its patterns against an undefined name (`prompt` instead of `input_text`), so *every* chat
turn raised `NameError` inside the guard and killed the stream before a single token — which is
exactly why it presented as "Error: Failed to generate the response". Fixed, with tests.

### How to test

```bash
make test            # 282 passed, 3 skipped
```

To see it in the app, force a failure without waiting for a real quota to run out — put a bad key
in `.env`, restart the backend, and upload a contract:

```bash
GOOGLE_API_KEY=not-a-real-key      # → "rejected the API key as invalid or expired. Check GOOGLE_API_KEY…"
```

Or pick a model whose provider has no key set at all (the dropdown marks these "API key not set")
and analyse a contract: the panel should name the missing variable rather than showing an empty
analysis. The real quota case is the Gemini free tier — a few analyses in a day will reach it.

### Your feedback

_(write here)_

---

## Debug — a live timeline of what the pipeline is doing

**Status: `awaiting your test`.** Not an increment — from your report: *"it is taking a lot of time
to upload and then process… I want to know exactly where it is in terms of technical steps… logs
should have worked but it's very noisy and I can't find the things I need."*

### What you get

A **Pipeline Debug** panel below Document Upload on the Intelligence page (and on Search and Chat),
rendered only when `DEBUG_EVENTS=true` is set in `.env`. It shows every technical step as it happens
— the one currently running ticks a live timer — with the duration of each and the few facts that
explain it. Plus **Copy JSON**, so a whole trace can be pasted into a session instead of hunting
through `make logs`.

This is a different thing from the logs and from Phoenix. The logs have the detail and none of the
shape; Phoenix has the LLM calls but not the PDF extraction, chunking, embedding or Neo4j writes, and
neither tells you where a request is *right now*.

### What it measures — the answer to your question

A real 25-second upload of `TinyContract-Fast.pdf` on `gemini-flash-lite`:

```
 read_file / validate / duplicate_check / save_temp      ~4ms total
 pdf_extract                              58ms    chars=1058
 chunking                               2820ms    chunks=8 strategy=section quality=0.9
   chunking.embed                       1687ms    8 progress events, one per chunk
 process_pdf                           22039ms
   pdf_agent.extract_text                  2ms    ← the same PDF, extracted a second time
   pdf_agent.analyze_contract          20940ms
     llm call                          20938ms    in 907 / out 306 tokens
   pdf_agent.store_contract             1070ms
     embed_summary / neo4j.create / link_parties
```

**One model call is 84% of the upload.** Chunking is 11%, and everything else — validation, file I/O,
three Neo4j round trips — is under 1% put together. And an analysis of the same contract:

```
 extract_clauses    2289ms  (llm 2285ms)
 check_policies     3678ms  (llm 3674ms)
 assess_risk           0.1ms
 cuad_mitigation       7ms
 validate_results      0.0ms
 generate_redlines  2444ms  (llm 2442ms)
 store_results       394ms  → 5 clauses, 6 violations
```

8.4 seconds, of which 8.4 is three sequential model calls. That is now a measurement rather than an
assumption.

### How it works

- **`backend/shared/debug/`** — a bounded in-memory ring buffer (1000 events), `trace_step()` for
  timing a block, and a LangChain callback attached in `build_llm()`. Because `build_llm` is the only
  place a chat model is constructed, that one line instruments *every* model call in the app,
  including the provider SDK's own retry backoff — the 1s→17s walk that otherwise reads as a single
  unexplained stall.
- **`/api/debug/status | events | events/stream`** — SSE, polled off the ring buffer every 200ms so
  events emitted from worker threads are safe. Reconnects carry `since=<seq>`, so a dropped
  connection during a two-minute analysis resumes instead of losing what it missed. Mounted under
  `/api` because Vite proxies only that; the pre-existing `/debug` router is unreachable from a
  browser. No `VIEW_AUDIT` dependency — the dev default role does not hold it, and copying that
  pattern would give a permanently empty panel.
- **Grouping** reuses the correlation id the tracing middleware already sets. The frontend now sends
  its own `X-Correlation-ID` per action, so a run is one block rather than a flat list.

### Three bugs this work fixed or found

1. **The correlation id was being dropped for the entire analysis.** `run_coroutine` and
   `analyze_contract` hand work to a `ThreadPoolExecutor`, and `submit()` does not carry
   `contextvars`. Everything the analysis logged was therefore unattributed. Fixed with
   `contextvars.copy_context()`; the existing JSON logs benefit too.
2. **Telemetry broke the thing it measured — twice, in live testing.** A step reporting a fact it
   called `status` collided with `emit`'s own parameter and failed an upload; then a `note` keyword
   relabelled an event instead of recording the fact. Fields are now passed as a dict and the
   reserved parameters are positional-only, so no field name can shadow the envelope. Pinned by
   tests over every envelope key.
3. **The chat is rejecting ordinary questions.** "Summarise the indemnification clauses in our
   contracts" is refused by the prompt guard as `OUT_OF_SCOPE` in 33ms. Not touched here — but it is
   the first thing the panel showed, and it explains a chat that looks broken.

Also visible, not fixed: **every upload extracts the PDF twice**, once in the API and once in the
agent's `extract_text` node.

### How to test

```bash
make test    # 359 passed, 3 skipped
```

I have set `DEBUG_EVENTS=true` in your `.env` and restarted the backend, so it is on now. In the app:

1. Upload `sample-contracts/TinyContract-Fast.pdf` with `gemini-flash-lite`. The panel fills in live.
2. Click **Analyze** and watch the six planned steps.
3. Refresh mid-run — the panel refills from the buffer rather than starting blank.
4. Approve a redline, run a search, send a chat message; each appears under its own phase.
5. Set `DEBUG_EVENTS=false` and restart the backend: the panel must not render and
   `/api/debug/events` must 404.

```bash
curl -N http://localhost:8000/api/debug/events/stream   # the raw feed, no UI
```

### Addressed in review (Copilot, PR #7)

Four findings, all valid:

- **"Development only" was documentation, not code.** The router was mounted unconditionally, so
  `DEBUG_EVENTS=true` on a production deployment would have exposed filenames, tenant ids, contract
  ids and provider errors to an unauthenticated caller. The gate now lives in
  `debug_events_enabled()` itself, so a misconfigured production process does not even *buffer* the
  events — there is nothing to leak however the endpoints are reached — and the router is mounted
  only in development on top of that.
- **Which uncovered a real deployment hole.** `ENVIRONMENT` was never passed into the backend
  container, and the root `.env` is not copied into the image — so the container has always run as
  `development`. That is why the production gate did nothing until compose was fixed, and it means
  **RBAC's fail-closed-to-VIEWER path has never been reachable in this stack either.**
- **Time to first token was measured from the wrong frame.** The stream carries `updates` frames and
  tool-call chunks, and the flag was set on the first item of any kind — so the number reported was
  earlier than the first token. It now waits for a non-empty `AIMessageChunk`.
- **The test pinning the correlation-id fix did not exercise the branch it claimed to.**
  `asyncio.to_thread` propagates the context itself and leaves the worker with no running loop, so
  `run_coroutine` took the plain `asyncio.run` path and the test passed with or without the fix.
  Called directly from the loop thread now — verified to fail when `copy_context` is reverted. The
  pre-existing test above it had the same flaw and the same fix.
- **Concurrent runs cancelled each other's "running" indicator.** `inFlightSteps` keyed on
  `phase/step` across every run the panel holds, so one run finishing `upload/process_pdf` cleared it
  for another still inside it. Keyed by correlation id as well.

### Addressed from your manual testing

**"Analysis events don't appear until it's finished."** The cause was not the panel — the analysis
was running *on the event loop thread*. `analyze_contract_by_id` is a coroutine, but it called the
synchronous `analyze_contract_intelligence` in line, so for the whole analysis the server answered
nothing at all: not the debug stream, not the 500ms `/api/workflow/status` poll the page itself was
making, not another user's request. Measured before the fix, a trivial `/api/debug/status` call took
**6.4 seconds** to answer because it waited for the analysis to finish. It now runs via
`asyncio.to_thread` (which copies the context, so the correlation id still reaches it). Measured
after: probe latency stays at **2ms** throughout, and the first events land ~1s in.

That is a product bug in its own right — the upload path already awaited properly, which is why its
events always streamed.

**"Show the latest first."** Runs were already newest-first; the steps inside a run now are too.
`buildRows` still folds start/end pairs chronologically — only the display is reversed. While there:
`chunking.embed.progress` events now fold into their parent row as a live `done=n total=m` instead of
becoming one row per chunk, which on a real contract was a hundred lines of scrolling between you and
everything else.

### Limits

- **In-memory, single process.** A backend restart loses the buffer.
- **Not a profiler.** Durations are wall-clock around a step, so nested steps overlap their parent —
  `process_pdf` includes the LLM call inside it.
- **Not for production.** The endpoints are unauthenticated by design; the flag defaults off and they
  do not exist when it is off.
- **Additive.** The `/api/workflow/status` poll and the "🤖 PDF Processing Agent" banner are unchanged.

### Your feedback

_(write here)_

---

## Increment 6 — Multiple contracts, each resumable

**Status: `accepted`** (2026-09-13, after your manual testing — two rounds of Copilot review and
two reports from you folded in). Follows from the
[Product shape](#product-shape--what-this-system-is-the-source-of-truth-for) decision: if this
system is the record of the *review*, a review has to be a durable object you can leave and come
back to.

**Goal:** today a review lives in one browser tab. `selectedContractId` and the current page are
`useState` with no URL, so a refresh empties the screen; the contract list is in `localStorage`, so
a colleague or a second machine sees nothing. Worse, the backend could not answer even if the UI
asked — `_store_intelligence_results` persists **counts only** (`clauses_count`,
`violations_count`, `risk_score`) plus the redlines. The clause findings, evidence spans and
violations are never written to the graph at all, so "reopen this review" would mean re-running a
two-minute analysis. There is also no list-contracts endpoint the app can call: the only one is
`/api/documents/debug/contracts`, gated on `VIEW_AUDIT`, which the default `LEGAL_REVIEWER` role
does not hold.

### Failure cases, settled first

Increment 4's approach, for the same reason: this is the increment where getting it wrong loses a
reviewer's place, or files their work against the wrong contract.

| case | behaviour |
|---|---|
| upload a byte-identical document to an existing version | **no new version.** Open that matter with "Exactly matches version N of MSA-2026-0042" |
| user chooses "New contract" for a document much like an existing one | **honoured without argument.** A new SOW for a different vendor off the same template is exactly this case, and only the user knows |
| extraction fails on a new contract | no matter created, **no reference number burned** — no gaps in the sequence |
| upload a new version to a `CLOSED` matter | 409 naming the matter; reopening is an explicit action |
| two uploads race for the same reference | counter increments in one atomic statement; refs are unique |
| analysis fails after the version is stored | the version exists, findings empty, the failure shown as a warning — never rendered as "no findings" |
| matter opened while its analysis is still running | shows the in-progress state, not an empty review |
| a matter with no versions | cannot exist. A matter is created only after one successful extraction |
| reference number for an unknown or other-tenant matter | 404, not 403 — same rule as redlines |

### The model

```
(:Matter {matter_ref, tenant_id, title, counterparty, contract_type,
          status, created_at, updated_at})
  -[:HAS_VERSION {n}]->(:ContractVersion {version_id, source_sha256, uploaded_at,
                                          analysis_status})
      -[:HAS_FINDING]->(:ClauseFinding)      <- new; the gap this increment closes
      -[:HAS_REDLINE]->(:Redline)            <- exists already, re-pointed at the version
```

Chunks are attached to the version in Increment 7; this increment leaves chunking exactly as it is.

**Status** is derived wherever it can be. `IN_REVIEW` vs `REVIEWED` is `pending == 0`, which
`redline_review_summary()` already computes. Only the transitions a human makes are stored:

```
DRAFT -> IN_REVIEW -> REVIEWED -> AWAITING_COUNTERPARTY -> CLOSED
```

### Settled: one document per matter

Decided 2026-09-13. **One document per matter**, full stop — no `Matter -> Document -> Version`
nesting. If an SOW and its MSA need to be related later, that is a *link between two matters* by
reference number — "SOW-2026-0051 is issued under MSA-2026-0042" — not a container. A
`(:Matter)-[:ISSUED_UNDER]->(:Matter)` edge costs nothing to add later and keeps this model flat.

### Reference numbers

`{TYPE}-{YYYY}-{NNNN}` — `MSA-2026-0042` — sequential per tenant per year, allocated from a counter
node in a single statement so concurrent uploads cannot collide:

```cypher
MERGE (c:Counter {tenant_id: $t, year: $y, kind: $k})
  ON CREATE SET c.n = 0
SET c.n = c.n + 1
RETURN c.n
```

Sequential rather than a UUID because the reference is the thing people say out loud and put in
emails. Allocated **after** extraction succeeds.

### The two upload paths

**Into a matter (the common one).** The reviewer opens `MSA-2026-0042` and clicks *Upload new
round*. The matter is in the URL; nothing is inferred, nothing is asked.

**New contract.** Button at the top of the list. File picker first, then extraction, then a
confirmation card **pre-filled** with the counterparty, contract type and dates the upload pipeline
already extracts — so the user corrects rather than types. Confirming allocates the reference and
creates the matter.

No similarity matching in this increment: the user always chooses. Advisory match suggestions
arrive in Increment 7, once chunks have identity.

### The one automatic case

`source_sha256` — a hash of the canonical full text — is stored on every version. If an upload
matches an existing version exactly, that is not a judgement call: no version is created, and the
user lands on that matter with the note. Prevents double-clicks producing v2 = v1.

### Fixes folded in

Small, and all in the path this increment rewrites:

- **The model dropdown does nothing on upload.** `DocumentUpload.tsx:68` sends `model` in the
  `FormData` body, but the endpoint declares `model: str = Query(...)`. Every upload has therefore
  used `DEFAULT_MODEL_ID` — currently `free-large`, at 50–130s a call. This is very likely the
  "uploads take a long time" report.
- **Upload ignores the tenant header.** `apiFetch` sends `X-Tenant-ID`, but upload reads
  `tenant_id` from a query parameter the frontend never sets, so contracts always land in
  `default-tenant` while redlines are looked up under the header's tenant.
- **Tenant resolution is a three-way split** — query parameter on upload/analyze, `X-Tenant-ID`
  header on redlines, hardcoded `"default-tenant"` on status and dashboard. Collapsed to
  `Depends(get_current_tenant)` everywhere. `default-tenant` stays the only tenant in use and is
  never surfaced in the UI; this is so there is one seam to change when auth lands.
- **The duplicate check never fires.** `MATCH (c:Contract) WHERE c.file_id CONTAINS $filename`
  compares against `UPLOADED_{random}_{date}`, which never contains the filename. Replaced by
  `source_sha256`.

### What changed

**The findings are on the graph now.** `_store_intelligence_results` wrote three numbers —
`clauses_count`, `violations_count`, `risk_score` — and the redlines. The clause findings, the
evidence spans, the rule citations and the risk narrative were never persisted at all, so
"reopen this review" meant re-running a two-minute analysis and hoping the model agreed with
itself. They are now `(:ContractVersion)-[:HAS_FINDING]->(:ClauseFinding)` and
`-[:HAS_VIOLATION]->(:PolicyViolation)`, with `critical_issues` and `recommendations` stored
alongside the score. `GET /api/intelligence/contracts/{id}/analysis` reads the lot back in the
same shape `POST /analyze` returns, so the page renders identically whether it analysed or
reopened. Measured against the live stack: an analysis that took **71s** reopens in **136ms**.

**A version is the existing `Contract` node, given a second label.** The one structural decision
worth knowing about:

```
(:Matter {matter_ref, tenant_id, title, counterparty, contract_type, status, version_count, …})
  -[:HAS_VERSION {n}]->(:Contract:ContractVersion {version_id, source_sha256, uploaded_at,
                                                   analysis_status, analysis_error})
      -[:HAS_FINDING]->(:ClauseFinding)
      -[:HAS_VIOLATION]->(:PolicyViolation)
      -[:HAS_REDLINE]->(:Redline)          <- already there, already pointing at the version
```

Not a separate node beside the contract. Every redline decision from Increment 4 hangs off
`(:Contract)-[:HAS_REDLINE]->(:Redline)` and is looked up by `file_id`, and every URL in the app
carries that same id. A distinct node would have meant re-pointing all of it and migrating the
decisions across — the one thing this increment must not risk. A label re-points nothing, and
`version_id == file_id` keeps the analyse/decide flow working untouched.

**Reference numbers** are allocated from a `(:Counter {tenant_id, year, kind})` node in one
statement, so two uploads racing for `MSA-2026-0042` cannot both get it — the second waits on the
lock the first holds. Version numbers are allocated the same way, inside the statement that
increments the matter's counter. Allocation happens at the *confirmation*, after extraction has
already succeeded and after the version is known to exist, so a PDF that fails to parse and a card
the reviewer cancels both burn nothing.

**Two upload paths**, distinguished by one optional field:

| path | what happens |
|---|---|
| `matter_ref` given | the reviewer opened the matter and clicked *Upload new round*. Nothing inferred, nothing asked. The matter is checked for existence and closure **before** extraction, so a closed matter costs a second rather than two minutes. |
| no `matter_ref` | extracted and stored as an *unfiled* version. The response carries a proposal — title, counterparty, type, dates — pre-filled from what the pipeline already found. `POST /api/matters` confirms it. |

**The one automatic case.** `source_sha256` — SHA-256 over the whitespace-folded full text — is
stored on every version. An exact match returns "Exactly matches version N of MSA-2026-0042" and
creates nothing. Folding whitespace is deliberate: PDF extraction is not byte-stable across runs,
so hashing the raw string would report the same file as different. A matching version that is
*unfiled* re-offers its confirmation card instead, so a cancelled card never leaves two copies.

**Status is derived wherever it can be.** `IN_REVIEW` vs `REVIEWED` is `pending == 0` on the latest
version, computed from the redlines rather than stored beside them where it could drift.
`AWAITING_COUNTERPARTY` and `CLOSED` are human statements and are never overridden by a count.
`PATCH /api/matters/{ref}/status` is guarded by `APPROVE_REDLINE`, not `ANALYZE` — `VIEWER` holds
`ANALYZE`, and closing a matter is a judgement about the negotiation, not a query against it.

**An analysis that fails is said out loud.** `analysis_status` moves `NOT_STARTED → RUNNING →
COMPLETE | FAILED` on the version, written around the run rather than after it. A matter opened
mid-analysis shows the in-progress state; one whose analysis died shows the reason as a warning.
Neither is ever rendered as "no findings", which reads as a clean contract. `clauses_extracted` was
added to `ContractIntelligence` alongside the existing `redlines_generated` so a failed run cannot
replace a good stored review with nothing.

**The frontend navigates by URL.** `/` is the matters list, `/matters/MSA-2026-0042` is one matter.
`useRouter` uses `pushState` and `popstate`, so the back button works, a matter opens in a new tab,
and a hard refresh keeps your place. `localStorage` is demoted to what its name now says — a
`matters_cache_v1` that paints the last known list while the request is in flight and is replaced
wholesale by whatever the server says. `IntelligencePage` and `DocumentUpload` are gone; their work
happens inside a matter, and `/intelligence` redirects to the list rather than 404ing.

**The four bugs, fixed:**

| bug | what it was doing |
|---|---|
| model dropdown | `DocumentUpload.tsx` sent `model` in the FormData body; the endpoint declared `model: str = Query(...)`. Every upload has run on `DEFAULT_MODEL_ID`. The endpoint now reads both, body first — `scripts/evaluate_pipeline.py` passes it as a query parameter and still works. Confirmed live: `"model_used":"gemini-flash-lite"`. |
| upload ignored the tenant header | it read `tenant_id` from a query parameter the frontend never set, so contracts landed in `default-tenant` while redlines were looked up under the header's tenant. |
| three-way tenant split | query parameter on upload/analyze, header on redlines, hardcoded `"default-tenant"` on status and dashboard. All ten tenant-scoped endpoints now use `Depends(get_current_tenant)`, and a test walks the route table to keep it that way. |
| duplicate check never fired | `WHERE c.file_id CONTAINS $filename` compared against `UPLOADED_{random}_{date}`, which never contains the filename — and it had no tenant filter, so a collision would have returned another tenant's contract id (bug #1 in the architecture review). Replaced by the tenant-scoped `source_sha256` lookup. |

**Migration.** `scripts/migrate_matters.py` (`make check-matters` / `make migrate-matters`) gives
every contract that predates matters a single-version matter, with references allocated in upload
order. It is purely additive — it adds a label and a `Matter` and writes to no `(:Redline)` — and
idempotent, so it is safe to re-run. A test asserts that no statement it issues contains `DELETE`,
`REMOVE` or `Redline`. Dry-run against your database: **51 contracts** would be migrated.

**Tests: 359 → 550**, all offline. The failure-case table above is walked row by row in
`backend/tests/test_matters.py::TestTheFailureCases`.

| file | covers |
|---|---|
| `test_matters.py` (87) | the failure-case table, reference numbers, derived status and transitions, source hashing, the migration |
| `test_matters_api.py` (25) | the wire — 404 for an unknown *and* another tenant's reference, 409 on a double-clicked confirm, 422 on a derived status, 403 for a VIEWER |
| `test_review_is_resumable.py` (24) | findings reach the graph, a failed analysis never erases a good review, the read-back shape |
| `test_upload_paths.py` (23) | the four bugs, pinned by inspecting what the routes actually declare |
| `test_frontend_navigation.py` (29) | static checks that a review has a URL, the list comes from the server, and a background analysis lands without a refresh |

**Also verified against the live stack** (a throwaway tenant, since removed): every statement this
increment introduces, the full new-contract → confirm → analyse → decide → re-analyse flow, the
duplicate short circuit, the 409 on a closed matter, and that version 1's approved redline survived
a re-analysis. That check found one real bug — `list_matters` counted a matter with *no* redlines as
having one pending, because `coalesce(rl.status, 'PENDING')` over a null `OPTIONAL MATCH` row reads
as `PENDING`. Fixed, and pinned by a test.

### Addressed in review (Copilot, PR #8)

Nine findings, all nine fixed. Four were about the same thing, and it is the thing this increment
is *for*: a check performed before a two-minute extraction is not a guarantee.

- **The duplicate check was check-then-write.** Two uploads of the same PDF both pass the
  pre-flight hash lookup, then spend two minutes extracting, then both write — the exact
  double-click the increment claims to prevent. The lookup is now the fast path only. The guarantee
  is a **uniqueness constraint** on `source_key` (`tenant_id|source_sha256`), so the loser is
  rejected by the database and told which version won. Verified with two real threads: exactly one
  wins.
- **`MERGE` does not make a counter singleton.** Two first allocations for the same
  `(tenant, year, kind)` could create two `Counter` nodes that each return `1`, and two matters
  claiming one reference. Added uniqueness constraints for the counter, the matter reference and
  the source key, created at startup and by the migration. Verified: eight concurrent allocations
  produce eight distinct references and one counter node.
- **A failed analysis still overwrote the stored review.** `_store_clause_findings` was guarded but
  `_store_intelligence_results` was not, so the failure path's risk 0.0 / `UNKNOWN` / every-count-zero
  result replaced a good previous review — and on the matters list that reads as a contract with
  nothing wrong with it. Nothing is written now unless the analysis ran.
- **`COMPLETE` was claimed without checking that anything was saved.** A Neo4j write failure was
  swallowed and the version marked complete anyway. Persistence now reports success, and the status
  follows it: an analysis that ran and then failed to save is, to whoever reopens the matter,
  indistinguishable from one that never ran.
- **A filing failure was reported as success.** `MatterPage` treats every non-error response as
  filed, so a failed `attach_version` closed the uploader on a round that is not there — and a
  failed `record_version` produced a confirmation card whose confirmation could only 404.
  `needs_filing` is now set only once the version is known to exist, and a filing failure returns
  an explicit recoverable error.
- **The landing page silently truncated.** For a system whose whole claim is that the review is
  durable, "the work is there and there is no way back to it" is the worst failure available. The
  list is paged and says so — `total`, `offset`, `has_more` — with a *Load more* button.
- **The migration would have filed cancelled uploads.** A version uploaded since this increment and
  left unfiled is a confirmation card the reviewer has not answered; the scan would have picked it
  up and burned a reference on a decision they never made. It now skips anything already labelled
  `:ContractVersion`, while still recovering its own interrupted work via `pending_migration`.
- **The drop zone was mouse-only.** A click-only `div` in front of a `display: none` input, so
  keyboard users could not upload at all. It is a `<button>` now.
- **The duplicate case stayed on the wrong page.** Uploading bytes that belong to a different matter
  showed a note and left the reviewer looking at the wrong contract. It opens the matter that owns
  them.
- **A failed restore looked like an unanalysed contract**, whose obvious next move is to spend two
  minutes re-deriving a review that is already on the graph. Network and server failures are shown
  with a retry; only a genuine 404 stays quiet.

**One thing the review did not ask for, found while fixing it.** The loser of a duplicate race
leaves a `(:Contract)` that never became a version — unreachable, but the migration would later
file it as a matter of its own and put the duplicate back. It is marked `superseded_by` rather than
deleted: deletion is irreversible and the node is evidence that two uploads raced.

**And one the legacy data forced.** Migrating the development database showed what the broken
duplicate check actually did: **52 contracts are 8 distinct documents**, one of them uploaded 18
times. Filing those as 52 matters would have put the old bug on the landing page and buried the
eight real contracts. Repeat uploads are grouped into rounds of one matter instead, oldest first.
Nothing is discarded — every copy keeps its own redline decisions under its own version number.

Tests: **504 → 526.**

### Second review round (Copilot, PR #8)

The re-review found nine more, and they were right about all of them. Four were about the same
thing again — **a guarantee that is not enforced where the write happens is not a guarantee**:

- **The review was persisted across three separate transactions.** Every `graph.query` call
  auto-commits, so a failure between the summary write, the findings and the violations left the new
  score beside the new clauses and the *previous* run's violations — marked FAILED, and not a review
  of anything. All three are now one statement. Verified by killing the write mid-flight against a
  live database: the previous review comes back whole, score and clauses and violations together.
- **`COMPLETE` was claimed when redline storage failed.** `_store_redlines` caught every write
  exception and returned normally, so the response promised drafted language that reopening the
  matter would not show. It reports failure now, and the version is marked FAILED.
- **Two confirmations of one document could each create a matter.** The unfiled check and the
  `CREATE` were both reads, and nothing in the schema limits a version to one incoming
  `HAS_VERSION`. The version is now write-locked before the ownership check, so the loser resumes
  after the winner commits and sees the relationship. Verified: four concurrent confirmations, one
  winner, one `HAS_VERSION` edge.
- **A refused attach burned a version number.** The matter's counter was incremented before the
  ownership check, so a rejected round left a gap — "version 1, version 3". The counter now moves
  only on rows that pass. Verified: four concurrent rounds produce 2, 3, 4, 5.

The migration took three more:

- **A run that died part-way split one document across two references.** Everything before the
  failure had committed, so a retry grouped the unattached remainder and made it a *second* matter.
  It now recovers the matter a previous run created and attaches only what is missing.
- **Every textless legacy contract hashed the empty string**, so a tenant's documents with no
  `full_text` and no `summary` would have arrived as versions of one matter — grouping on the
  absence of evidence. They get an identity of their own.
- The scan comment now says *why* `pending_migration` exists, since that is the only thing making
  the two exclusions correct rather than contradictory.

And the upload path had one more destination bug:

- **An unfiled duplicate ignored `matter_ref`.** Uploading a round whose bytes already exist as an
  unfiled version returned the new-contract confirmation flow, so the matter page closed the
  uploader and no version ever appeared. The destination the reviewer asked for is honoured.

Four on the frontend:

- **A 200 was taken as proof of persistence.** The server answers with the in-memory results and a
  warning while marking the version FAILED when saving fails, so the panel claimed a review the
  server knew it did not have. It re-reads the stored status instead of assuming.
- **A rejected status transition ejected the reviewer from the matter**, because it wrote into the
  same state as "could not load this matter" and the render guard replaced the whole page. Action
  errors are separate and render inline.
- **Refreshing after *Load more* truncated back to the first page**, so one row starting an analysis
  made the extra pages vanish ten seconds later. A refresh re-requests the loaded range.
- **The cache cleanup could stop the app mounting.** If `getItem` threw because storage is disabled,
  the `catch` called `removeItem`, which throws for the same reason, and the exception escaped the
  provider — taking the whole app down for the sake of a cache.

Copilot's own agent pushed a fix for two of these to the branch while this was being written —
the redline return value and the duplicate destination, both of which had been fixed here
independently. The two were merged rather than one discarded: its `_duplicate_upload_response`
is kept, because it also handles the case where the winning version cannot be identified at all,
and the duplicate helper written here was removed.

Tests: **526 → 544**, plus 21 checks against a live Neo4j covering the concurrency and atomicity
claims with real threads and a deliberately failed write.

### From your manual testing

- **"The word Matters is confusing, can we replace it with Contracts?"** Left as *matter* for now —
  see the [Decisions log](#decisions-log) for why, and the list page now says what the word means
  instead of assuming it.
- **"The app is stuck here"** — the matter page on `Loading SER-2026-0001…` indefinitely. The dev
  backend had stopped answering (see below); the bug this exposed is that the page had no deadline,
  no error and no way out. Fixed on both counts.
- **"After the analysis is complete, the page didn't load the findings without hitting refresh."**
  Fixed. The analysis runs on a worker thread and outlives the request that started it, which is why
  leaving the page does not stop it — but the page that came back found the version `RUNNING` and
  then never asked again. The review panel now polls quietly while a version is running, and the
  matter page and the matters list do the same for the summary beside each round, so a row stops
  saying "Analysing" by itself. All three poll only while there is something to watch; an idle page
  makes no requests. It gives up after ten minutes rather than waiting for ever on a version left
  `RUNNING` by a server that restarted, and says so.

### A dev-stack trap worth knowing about

Hit twice during your testing, and it looks exactly like an application bug.

**`fastapi dev`'s reloader sometimes kills its worker and never starts a
replacement.** Seen after a *bulk* file change — a git checkout or rebase
rewriting several files at once; single edits reload fine. The reloader process
survives, so the container still reports `Up`, the port is still open, and every
request simply hangs. The app itself is fine: importing it inside the container
succeeds, and the lifespan starts and shuts down cleanly in 1.1s.

```bash
docker compose restart backend      # the fix
```

Two things were added so it costs a minute instead of an hour:

- a **healthcheck** on the backend service, so `docker ps` says `unhealthy`
  rather than `Up`;
- a **30-second deadline on every frontend request** (`apiFetch`), because
  `fetch` has none of its own. That is what turned *"the app is stuck here"*
  — `Loading SER-2026-0001…` for ever, no error, no retry, no way back — into a
  message and a *Try again* button. The calls that genuinely take minutes, the
  upload and the analysis, opt out with `timeoutMs: 0`.

And the trigger turned out to be plainer than "bulk changes": **editing a single
test file restarted the server**, and one of those restarts wedged it. Tests are
never imported by the server, so `docker-compose.yml` now runs
`uvicorn --reload --reload-exclude 'backend/tests/*'` instead of `fastapi dev`,
which has no such flag. Verified: touching a test file no longer reloads;
touching `backend/api/matters.py` still does.

### One thing this increment could not finish

`frontend/src/services/enhancedSearchApi.ts:1` is fixed (bug #2), but **that was not the only thing
breaking `npm run build`**. The build fails on **31 TypeScript errors across 15 files** — unused
imports, implicit `any`, index-signature errors — none of them in code this increment wrote. Fixing
bug #2 alone does not produce a working production build, and the note in *Known issues* implying
it would is wrong.

What I did: fixed bug #2, then fixed the ten errors that were in files this increment touches
(`ContractIntelligence.tsx`, `ClausesDetail.tsx`, `useModal.ts`, and by deleting `DocumentUpload.tsx`).
That took the count from **41 to 31**, and lint in those files from 4 errors to 2. The rest sit in `ViolationsDetail`, `ErrorBoundary`, the
documentation tabs, `theme-provider`, `tabs.tsx`, the chat input and the search results — files
this increment has no reason to touch and no test coverage for. I left them rather than change
behaviour in areas I cannot verify offline.

`npx vite build` succeeds (esbuild does not type-check), which is why the dev server has always
worked. Everything new type-checks clean and lints clean.

### How to test

```bash
make test
```

550 pass, 3 skipped — offline, no Docker, no Neo4j, no API keys.

Then, with the stack up (`make run`):

```bash
make check-matters      # writes nothing; lists what it would do
make migrate-matters    # creates the matters, and the uniqueness constraints
```

On your database that is **51 contracts becoming 8 matters** — the repeat uploads the old duplicate
check never caught become rounds of one matter rather than 51 separate ones. Nothing is discarded:
every copy keeps its own redline decisions under its own version number. The migration is additive
and idempotent, and your Increment 4 decisions come through untouched. Then, in the browser:

1. **The landing page is the matters list.** `/` shows every contract from the server, with its
   reference number, counterparty, version count and how many redlines are still pending.
2. **The existing flow, in its new home.** Open a migrated matter, press *Analyze*, decide a
   redline. This is the Increment 1–4 path; it should behave exactly as before.
3. **Hard-refresh.** The list is intact, the matter reopens on the same clause findings — no
   re-analysis, no two-minute wait — and the decision is still recorded.
4. **`/matters/MSA-2026-0001` in a new tab** loads that matter directly. The back button works.
5. **Upload two different contracts as new matters.** Each shows a confirmation card pre-filled
   with the counterparty and type from extraction. Correct anything, confirm, and the reference is
   allocated. Cancelling one burns no number.
6. **Re-upload a byte-identical file.** No new version — it opens the matter with
   *"Exactly matches version 1 of …"*, and returns in about a second rather than two minutes.
7. **Upload a new round from a matter's own page.** It becomes version 2, and version 1's
   decisions are still there under version 1.
8. **Close a matter, then try to upload into it.** Refused by name, immediately. *Reopen* puts it
   back in review.
9. **Pick `gemini-flash-lite` and upload.** The debug panel should show the call using it — this
   is the `model` fix. It is noticeably faster than the old silent default.

Worth knowing before you start: `npm run build` still fails (see above), but the dev server the
stack runs is unaffected.

### Your feedback

_(write here)_

---

## Increment 7 — Content-addressed chunks

**Status: `awaiting your test`.** Built 2026-09-13.

**Goal:** give every chunk an identity derived from its content, so unchanged text is never
re-embedded and two versions of a contract can be compared at all. Chunk embedding is one network
call per chunk — measured at ~210ms each, so ~42s for a 200-chunk contract — and today every upload
pays it in full even when one paragraph changed.

### Why the hash definition must be settled in this increment

Everything that determines *what the hash is* has to land together. If a later increment changes
the hash from "whole chunk" to "body only, no overlap", **every stored hash is invalidated**: the
version membership, the match results and the embedding reuse all become meaningless, and
recovering needs a migration that re-hashes and re-embeds everything. So heading-stripping and
overlap-removal belong here, not later.

### What changes

- **`canonical()`** — NFKC, soft hyphens removed, de-hyphenation across line breaks, smart quotes
  folded, whitespace collapsed, casefolded. The existing `_redline_id` normalisation
  (`contract_intelligence_service.py:294`) is the germ of this but is too weak for PDF text.
- **Heading stripped before hashing.** The section number is kept as a property on the membership
  relationship, not in the hashed body — otherwise inserting a section renumbers every heading
  after it and invalidates the whole document.
- **Overlap off.** `_add_overlap` (`section_strategy.py:193`) prepends 20% of chunk *i* onto chunk
  *i+1*, so a chunk's identity depends on its neighbour. It also mutates `next_chunk['content']` in
  place and then uses the grown chunk as the source for the next overlap, so overlap compounds down
  a chain of sub-chunks. Deleting it fixes both. Retrieval context comes from joining neighbours by
  `INCLUDES` order at query time instead.
- **Section strategy pinned, profile recorded.** `ChunkingProfile` on each version: extractor,
  normaliser version, chunker version, strategy, min/max size, overlap. Strategy selection runs
  **once, for version 1**; later versions reuse the recorded profile rather than re-running
  threshold-based scoring that can flip on a one-word edit (`strategy_selector.py:76-90`).
- **`MERGE (c:Chunk {tenant_id, hash})` — never on `hash` alone.** A global key would collapse
  identical boilerplate ("governed by the laws of the State of Delaware") from two customers into
  one shared node: a tenancy violation, and a GDPR deletion that cannot be performed without
  destroying someone else's version.
- **`(:ContractVersion)-[:INCLUDES {order, heading}]->(:Chunk)`** as the version's membership list.
  Unchanged chunks are referenced, not copied.
- **Embedding reuse.** Falls out of the MERGE: if the chunk node exists, so does its embedding.
  Guarded by `embedding_model` and `embedding_dimensions` stored on the chunk — the pattern already
  used for `Section` and `Clause` in `embedding_service.py:96,112` — so a model change forces a
  re-embed rather than silently serving stale vectors.
- **Advisory match on the New-contract path.** Not a scan: look up only the incoming document's
  hashes and let the graph walk back to matters.

```cypher
UNWIND $hashes AS h
MATCH (c:Chunk {tenant_id: $tenant, hash: h})
        <-[:INCLUDES]-(v:ContractVersion)<-[:HAS_VERSION]-(m:Matter)
WHERE m.status <> 'CLOSED'
RETURN m.matter_ref, m.title, v.n, count(DISTINCT h) AS shared
ORDER BY shared DESC LIMIT 5
```

O(chunks in the new document), not O(chunks in the tenant). Two numbers per candidate, because one
is not enough: `forward = shared/|new|` and `backward = shared/|matter|`. A genuine new round scores
high on both; an SOW that merely quotes an MSA's boilerplate scores low on `backward`. Weight shared
chunks by `1/log(1+df)`, where `df` is the chunk node's degree, so boilerplate contributes almost
nothing. Anything at or above **80%** is shown as a link with its percentage. **It never decides
anything** — it sits next to the choice the user was going to make anyway.

### Retention: nothing is deleted

A superseded chunk is still referenced by the version that used it, so it is not garbage — it is the
history this system exists to keep. Never delete a chunk because a newer version replaced it;
deletion is only ever justified by no version referencing it:

```cypher
MATCH (c:Chunk) WHERE NOT (:ContractVersion)-[:INCLUDES]->(c) DETACH DELETE c
```

True orphans arise only from failed uploads or an explicitly withdrawn version.

### Stability tests — the part that makes it trustworthy

All offline, no stack, no keys:

- **Edit is local.** Change one paragraph; assert at most two chunk hashes move. This is the
  regression guard — if anyone later swaps in a greedy token packer, it fails immediately instead
  of quietly doubling the embedding bill.
- **Insertion is local.** Insert a whole new section; assert every chunk after it is unchanged.
- **Golden hashes.** A fixture's hash list is pinned; changing it requires bumping
  `chunker_version`.
- **Reconstruction.** Concatenating chunks in `order` returns the canonical text. Catches
  overlap contamination and dropped text outright.

### Measured expectations, so nobody is surprised

Simulated on `SampleContract-Shuttle.pdf` (24 sections) by inserting a new Section 4 and
renumbering everything after it:

| hashing scheme | chunks surviving | stable |
|---|---|---|
| naive, whole chunk | 18 / 37 | 49% |
| heading stripped | 30 / 37 | **81%** |
| heading stripped + refs masked | 31 / 37 | 84% |

Reference masking is **not** in this increment: it buys 3 points here and would hide a lawyer
deliberately repointing a cross-reference, which is a real edit. Revisit only if a
reference-dense contract shows it hurting.

The residual failures degrade gracefully — their similarity to the correct match is 0.998–0.999, so
the Increment 9 diff still classifies them correctly and only the embedding skip is lost. Worst case
overall is one full re-embed: ~42s and a fraction of a cent. **Content addressing is an
optimisation layered on a diff that works without it.**

### Known limits of chunk identity, and what to do about them

Discussed and measured on 2026-09-13. None of these block the increment; they are written down so
nobody rediscovers them.

- **PDF re-export is the dominant real-world threat.** The counterparty edits in Word and
  re-exports; ligatures, hyphenation and reading order shift, and every hash changes even though the
  text did not. `canonical()` absorbs most of it, chunking on semantic boundaries absorbs more, and
  the embedding-similarity fallback catches the rest. **Prefer DOCX wherever the workflow allows** —
  extraction is far more stable than from PDF. Note also that
  `TextExtractionService.extract_with_fallback` (`text_extractors.py:51`) tries extractors in order
  and returns the first yielding >50 characters, **without recording which one won**; different
  libraries produce different whitespace for the same file. That is why `extractor` is part of the
  profile.

- **Documents with no detectable headings lose boundary locality entirely.** If none of
  `SectionStrategy`'s five patterns match — an unnumbered NDA, a scan with broken line breaks —
  selection falls through to paragraph or sentence chunking, which packs greedily, so a single
  insertion shifts every boundary after it and invalidates every hash downstream. Silently. The
  principled fix is **content-defined chunking**: a rolling (Rabin) fingerprint placing boundaries
  where the hash matches a mask, which is how rsync/restic/borg get locality without structural
  anchors. Not in this increment — but it is the right answer when an unstructured document shows
  up, and a cheap detector ("no section pattern matched") should at least *flag* the document as
  having unstable chunk identity rather than pretending otherwise.

- **Mid-chunk section numbers survive a leading-anchored strip.** Measured: after heading-stripping,
  one residual chunk still differed only as `v1='13.' -> v2='14.'` — a *second* section number
  inside the body, because that heading did not trigger a split. Fixing it means distinguishing
  section numbers from dollar amounts and statute citations ("10115", "Title 21", "$2,500"), which
  is real work for a small payoff. Left alone deliberately.

- **Sub-chunk boundaries are the only failure that misleads.** In the same simulation one chunk fell
  to **0.584** similarity — v1 1,607 chars, v2 663 — because `_split_large_section` re-divided an
  oversized section differently, compounded by overlap. Everything else scored 0.998–0.999 and
  classifies correctly. Removing overlap (above) is what fixes this, and it is the reason removal is
  in scope rather than deferred.

- **GDPR deletion is family-scoped, not version-scoped.** Because chunks are shared between
  versions, "delete version 1" will **not** remove text that version 2 still references. That is
  normally the desired behaviour, but if a document is uploaded by mistake and must genuinely be
  gone, the honest answer is purging the whole matter. Worth knowing before a compliance
  conversation assumes per-version deletion works.

### What changed, and what it measured

**`backend/domain/chunking.py` is the whole hash definition**, in one place and with no I/O, because
changing it later invalidates every stored hash at once. `canonical()` does NFKC, strips invisible
characters, rejoins words hyphenated across line breaks, folds smart quotes and dashes, collapses
whitespace and casefolds. `split_heading()` takes the section number *out* of the hashed body and
keeps it on the membership relationship.

**Measured against `SampleContract-Shuttle.pdf`** (37 chunks), by inserting a new Section 4 and
renumbering everything after it, exactly as the specification described:

| hashing scheme | chunks surviving | stable |
|---|---|---|
| naive, whole chunk | 18 / 37 | 49% |
| heading stripped | **32 / 37** | **86%** |

The naive figure reproduces the specification's exactly. Heading-stripping came out **86%** against
the 81% predicted — the heading pattern here recognises a few more real forms (`ARTICLE IV -`,
`Section 7:`, `12.3 Title`) while still refusing street numbers, dollar amounts and `1.5 million`.

**Overlap is gone.** `_add_overlap` prepended 20% of chunk *i* onto chunk *i+1*, so a chunk's
identity depended on its neighbour — and because it mutated `next_chunk['content']` in place and
then used the grown chunk as the source for the next overlap, the contamination compounded down a
chain of sub-chunks. That is what dropped one chunk to 0.584 similarity against its own unedited
self while everything else scored 0.998+.

**`(:ContractVersion)-[:INCLUDES {order, heading}]->(:Chunk {tenant_id, hash})`**, MERGEd on the
tenant *and* the hash — never the hash alone, which would collapse two customers' identical
boilerplate onto one node and make a GDPR deletion destroy someone else's version. Embedding reuse
falls out of the MERGE, guarded on `embedding_model` and `embedding_dimensions` so a model change
forces a re-embed rather than silently mixing vectors from two models in one similarity search.

**Measured end to end through the HTTP API**, two rounds of a real contract with one clause edited:

```
round 1:  14 chunks, 14 embedded,  0 reused
round 2:  14 chunks,  1 embedded, 13 reused   ->  92% of the embedding work skipped
```

**The profile is recorded on version 1 and reused.** Strategy selection is threshold-based scoring
that can flip on a one-word edit; a document chunked by `section` in v1 and `paragraph` in v2 has no
chunk in common, for no reason a reader could ever see. `TextExtractionService` gained
`extract_with_source`, because which extractor won is part of the profile and the old method threw
that answer away.

**The advisory match** looks up only the incoming document's own hashes — O(chunks in the new
document), not O(chunks in the tenant) — and scores two directions. `forward` is how much of the new
document a candidate explains; `backward` is how much of the candidate it covers. Shared chunks are
weighted `1/log(1+df)` so boilerplate contributes almost nothing. Live:

- a lightly edited copy of a filed contract → **suggests it at 84%**, with a *File as a new round*
  button (`POST /api/matters/{ref}/versions`, which files a document already on the server rather
  than making you upload the same bytes twice);
- an unrelated contract → **nothing**;
- an SOW that shares only the governing-law boilerplate → **nothing**.

It decides nothing. The pre-filled new-matter form is still right there underneath it.

**A document with no headings says so.** `_identify_sections` returns a trailing block for any
non-empty text, so a document where no pattern matched came back looking exactly like a
well-structured one — the detector asks the text directly instead. Without headings the chunker
packs greedily, one insertion shifts every boundary after it, and chunk identity is worth nothing;
that is now logged and flagged on the version rather than pretended away.

### One thing found while building it

**Retention would have deleted the old search corpus.** The specification's orphan query is
`NOT (:ContractVersion)-[:INCLUDES]->(c)`, on the reasoning that true orphans come only from failed
uploads. On your database that describes **3,508 chunks** — everything written before this
increment, which hangs off a `(:Document)` MERGEd on filename and is referenced by no version at
all. A routine tidy-up would have swept the lot. `delete_orphan_chunks` is now scoped to
content-addressed chunks (`hash IS NOT NULL`), so it cannot reach them; retiring them is a
deliberate act, and one worth scheduling — 6 `Document` nodes hold 3,508 chunks for 49 contracts,
which is the filename-MERGE bug in plain numbers.

### How to test

```bash
make test    # the stability tests are the point
```

648 pass, 3 skipped — offline, no Docker, no Neo4j, no API keys. The ones that matter:

| file | covers |
|---|---|
| `test_chunk_identity.py` (64) | canonical form, heading-stripping, **edit is local**, **insertion is local**, golden hashes, reconstruction, the unstructured-document flag |
| `test_chunk_storage.py` (28) | embedding reuse, model-guarded vectors, tenancy, membership replacement, retention |

With the stack up:

1. **Upload a contract, file it as a new matter.** The response's `chunks` reports
   `embedded == chunks` and `reused: 0` — everything is new the first time.
2. **Edit one clause and upload it into that matter** (*Upload new round*). `reused` should be
   everything but the changed chunk, and the upload should be visibly faster. The debug panel shows
   `chunking.reuse_check` and `chunking.embed_new` with the counts.
3. **Upload a similar-but-not-identical contract on the New contract path.** The filing card should
   offer the existing matter with a percentage and a *File as a new round* button — and still let
   you create a new matter instead, because a new SOW off the same template looks the same from here.
4. **Upload something unrelated.** No suggestion at all.

### Your feedback

_(write here)_

---

## Increment 8 — Analyse the whole contract

**Status: `specified — not started`.**

**Goal:** `ClauseDetectorTool` truncates at 12,000 characters (`intelligence_tools.py:95`). On the
real contracts in `data/`, that means the system silently ignores most of the document and reports
the result as a completed review:

| contract | chars | analysed | skipped |
|---|---|---|---|
| `Shell_Pacific_Corp_MESA.pdf` | 313,620 | 4% | **96%** |
| `Salesforce_MSA.pdf` | 70,757 | 17% | **83%** |
| `SampleContract-Shuttle.pdf` | 32,885 | 36% | **64%** |
| every `evaluation/` fixture | ≤1,317 | 100% | 0% |

Every clause finding, policy check, risk score and redline on a long contract comes from the first
few pages. And the last row is why `make eval` reports 1.00 across the board: **all three fixtures
fit inside the window**, so the metric is structurally incapable of seeing this. Increment 5 noted
that "1.00 says the fixtures are not yet hard enough" — this is what it was saying.

### What changes

- **Chunk-aligned windows instead of truncation.** Not one call per chunk (200 calls on the Shell
  MESA is unaffordable): pack consecutive whole chunks into windows up to the token budget, never
  splitting a chunk. 313k characters becomes ~26 calls. Run them concurrently within the provider's
  rate limit.
- **Chunk-aligned is load-bearing.** Because every window is a set of whole chunks, findings map
  back to specific chunks — which is exactly what makes Increment 9's incremental re-analysis
  possible. It reuses Increment 7's `INCLUDES` membership rather than inventing a parallel
  structure.
- **Merge and dedupe across windows.** The same clause can surface twice at a boundary; merge on
  clause type plus evidence-span hash. Grounding still validates against the window the finding
  came from.
- **Policy checking batched too.** It currently sends every clause in one prompt; a long contract
  yields far more clauses, so it needs the same treatment.
- **Longer eval fixtures, with breaches placed late.** Without these the evaluation still cannot
  fail on this, and an increment whose metric cannot fail is not measured.

### Expect the scores to drop

Analysing previously invisible text means more findings and more chances to be wrong. That is the
metric becoming honest, not a regression. Run `make eval` before and after so the two are
distinguishable — the same discipline as Increment 5.

### Your feedback

_(write here)_

---

## Increment 9 — Incremental re-analysis and the change report

**Status: `specified — not started`.** The payoff of the
[Product shape](#product-shape--what-this-system-is-the-source-of-truth-for) decision.

**Goal:** answer the question a legal team actually asks — *what changed since last round, and did
the counterparty accept our redline?* Nothing else on the market does this well.

### What changes

- **Profile enforcement.** Version N is chunked with version 1's recorded `ChunkingProfile`. If
  profiles differ, refuse to diff: re-chunk and re-embed wholesale, and say so.
- **The diff engine**, all stdlib. `difflib.SequenceMatcher(None, prev_hashes, new_hashes,
  autojunk=False)` over hash lists gives equal/replace/delete/insert directly. `autojunk=False`
  matters: the default heuristic discards elements appearing in >1% of sequences longer than 200,
  which on a long contract with repeated boilerplate would silently misalign.
- **Classify `replace` blocks** by similarity — cosine over the stored embeddings, which
  Increment 7 already has. Above ~0.80 it is a MODIFIED chunk (show the word-level diff using the
  existing `wordDiff.ts` LCS); below, it is a delete plus an insert.
- **Moves are free.** A hash appearing in both the delete and insert sets is a relocation, not a
  change — and a relocated indemnity clause is a real negotiation signal.
- **Incremental re-analysis.** Re-analyse only the windows containing changed chunks. Carry findings
  and redline decisions forward for unchanged ones, joined on `(rule_id, clause_hash)`.
  `_redline_id` already keys on a hash of normalised clause text, so re-keying it on `matter_ref`
  instead of `contract_id` is most of the work.
- **`GET /api/matters/{ref}/changes?from=2&to=3`** and the UI that renders it.

### Your feedback

_(write here)_

---

## Out-of-increment — export the review

Not scheduled, roughly a day, and worth doing whenever convenient. A reviewer can approve six
redlines today and has **no way to get them out of the browser**: `final_text` is written, read
back, rendered, and consumed by nothing. An export of the approved edits with their rationale is
what makes the human-in-the-loop work into a work product. Under the Product-shape decision this is
a *review* artifact — findings, decisions and suggested edits — **not** an amended contract.


## Decisions log

Settled — do not re-litigate without saying so explicitly.

| Decision | Rationale |
|---|---|
| **Hosted models only** for this phase | The Qwen teacher/student, LoRA, distillation and vLLM track stays deferred. `training/` is real code but a detached silo — the backend loads no local model and declares no ML dependencies. Getting the loop working and measurable comes first. |
| **Quarantine, don't delete** unused subsystems | Supervisor consensus/quality gates/circuit breakers, the pattern orchestrator, the planning agent and the six chunking strategies move off the live path but stay in the repo. |
| **One contract end-to-end, asserted** is the first milestone | Matches the design doc's own "POC Scope — Start Narrow" guidance. |
| **Work on `main`** | A parallel session is also committing here; small increments reduce collision risk. |
| **Source of truth for the _review_, not the contract** | Settled 2026-09-13. The contract stays in the customer's Word/CLM; this system owns the review history. See [Product shape](#product-shape--what-this-system-is-the-source-of-truth-for). |
| **A version *is* the `Contract` node, relabelled** | Settled while building Increment 6. `(:Contract:ContractVersion)` with `version_id == file_id`, rather than a new node beside it. Every Increment 4 redline decision hangs off that node by `file_id`, and every URL carries the same id — a separate node would have meant migrating the decisions across, which is exactly the risk the increment exists to remove. |
| **The word stays "matter"** | Raised during your testing on 2026-09-13: *"The word Matters is confusing, can we replace it with Contracts?"* They are genuinely different — a matter is the negotiation, `(:Contract)` is one uploaded round — but since we settled one-document-per-matter, "contract" would arguably be the better word. It is not renamed because `Contract` is already taken in the code for a single version, so a UI-only rename creates a permanent vocabulary gap and a full rename is a data migration over live contracts and redlines. **Decided: keep it, and say what it means on the page** rather than assume the reader knows. Revisit if it confuses anyone else. |
| **Status is derived, not stored, wherever it can be** | `IN_REVIEW` / `REVIEWED` is `pending == 0` over the latest version's redlines. Storing it would be a second copy of a fact the redlines already hold, free to drift. Only `DRAFT`, `AWAITING_COUNTERPARTY` and `CLOSED` — the transitions a human makes — are written down. |

## Product shape — what this system is the source of truth for

**Decided 2026-09-13.** The question was whether this system holds the contract and emits a
final document each round, or whether it reviews a document that lives elsewhere. Three shapes
were on the table:

| | Shape | Verdict |
|---|---|---|
| A | Pure advisory — review one upload, output findings | Too thin. The reviewer's decisions have nowhere to go. |
| B | System of record for the **contract** — apply redlines, emit v_next as DOCX | A product, not an increment. Needs document reconstruction from clause spans, formatting fidelity, compare/merge, eventually e-sign. Clause extraction does not even store character offsets into the source, so text cannot be spliced back into position today. |
| **C** | **System of record for the _review_** | **Chosen.** |

**C in one sentence:** the contract lives in the customer's Word or CLM and we never claim to own
it; we own the review — every version, every finding, every human decision and the rationale behind
it, across the whole negotiation.

**Why.** It matches the design guide, which calls the system a "contract review **copilot**" whose
Final Output is "contract summary, risk dashboard, clause-level findings, and suggested edits" —
a review artifact, not a contract. It is also the only one of the three that produces something a
legal team cannot already get: *"we rejected this indemnity wording twice in the last round"* is a
question no drafting tool answers.

**What follows from it, and is therefore in scope:**

- A **contract family / version model**. A stable family id survives re-upload; each `Contract`
  becomes a version under it. Decisions carry forward across versions by `(rule_id, clause_hash)`,
  so a reviewer is never asked to re-approve language they already approved.
- A **change report between versions** — what moved since last round, and whether the counterparty
  accepted our redline. This is the feature, not a detail of it.
- An **export** of the review. Today a reviewer can approve six redlines and has no way to get them
  out of the browser; `final_text` is written, read back, rendered, and consumed by nothing.

**What follows from it, and is therefore out of scope:** generating the amended contract, DOCX with
track changes, and anything that would make this system the place the contract text is edited.

**Three things the current code does that contradict C**, all to be fixed by the version model:

1. `Contract.full_text` is written once at `CREATE` (`contract_repository.py:89`) and never updated,
   so the stored copy never reflects an approved redline. Under C that is correct — but it should be
   explicit, not accidental.
2. ~~The duplicate check matches the filename against `file_id`~~ — **fixed in Increment 6.** It is
   now a tenant-scoped `source_sha256` match, and a revised contract uploaded into its matter becomes
   version 2 rather than a second unrelated `Contract`.
3. Chunk storage `MERGE`s a `Document` on the **filename** (`document_upload.py:253`) and then
   `CREATE`s fresh chunks, so v1 and v2 chunks accumulate under one node and semantic search returns
   a blend of both with no way to tell which version a hit came from.

~~`ContractVersion` / `HAS_VERSION` exist today only inside `enterprise_schema_migration.py`~~ —
**closed by Increment 6.** `MatterRepository` reads and writes them, and every contract that
predates them gets one from `scripts/migrate_matters.py`. Point 3 above — chunks accumulating under
a `Document` MERGEd on the filename — is untouched and belongs to Increment 7.


## Deferred — in the design doc, deliberately not now

Vector index and hybrid search (there is currently **no** vector index — similarity is a full
cosine scan); Qwen teacher/student, LoRA, distillation, vLLM serving, risk-based model routing;
Ragas / DeepEval; A/B shadow evaluation; data masking, anonymization and retention policies.

## Roadmap — is Neo4j the right store? (open, **not decided**)

Investigated at length on 2026-09-13. **Deliberately left open** — this records the evidence and
both cases so the decision can be made later without re-deriving any of it. Nothing here is settled;
do not treat it as a Decisions-log entry.

### What the code actually does today

- **Every relationship is a 1:N parent→child "owns" edge.** The live ones are `HAS_RULE`,
  `HAS_CHUNK`, `HAS_SECTION`, `CONTAINS_CLAUSE`, `HAS_REDLINE`, `PARTY_TO`, `HAS_GOVERNING_LAW`.
- **Zero multi-hop traversals.** Grepping for two-relationship patterns
  (`)-[:X]->()-[:Y]->(`), variable-length paths (`*..`) and `shortestPath` returns nothing. The
  deepest query in the repo is `MATCH (c:Contract)-[:HAS_SECTION]->(s:Section)` filtered by
  `tenant_id` (`enhanced_contract_search_tool.py:153`) — i.e. `SELECT … FROM sections JOIN
  contracts`.
- **No graph algorithms.** Four sites call `gds.similarity.cosine`
  (`advanced_rag_agent.py:75`, `policy_repository.py:200`, `chunk_embedding_service.py:198,215`)
  but `docker-compose.yml:65` installs only APOC, so those would throw `Unknown function` if
  reached. The live search path uses the built-in `vector.similarity.cosine` instead.
- **No vector index exists.** Nothing issues `CREATE VECTOR INDEX`. The things named like vector
  indexes in `multi_level_embeddings.py:41,53,73,78` are plain range indexes on a 1536-float list
  property and do nothing for similarity — every semantic search is a full cosine scan.
- **`neo4j:5-community`** (`docker-compose.yml:62`): no row-level security, no PITR backups, no
  clustering, no multi-database.

### What the project's own specs ask for

- The design guide (`AI-Powered-Smart-Contract-Review-Guide.pdf`, p.~16): *"we utilize a robust
  **vector database (such as Qdrant or pgvector)** to store and index our legal knowledge base…
  hybrid search combining vector similarity with metadata filters"*, and Graph RAG is
  *"**recommended for Phase 2** of the Proof of Concept. While it adds a layer of complexity…"*
- `docs/Phase1_Core_Features_Design.md` specifies the **entire** core model — chunks, lineage,
  embeddings, analysis results, tenancy with RLS, versioning — in **PostgreSQL DDL**, with
  `ivfflat` vector indexes.
- `docs/Enterprise_Database_Design.md` is polyglot and gives Neo4j one job: **data lineage and
  amendment chains**.

The implementation inverted this: the Phase-1 relational model became node labels
(`ProcessingLineage`, `DocumentEmbedding`, `ContractVersion`, `ContractAnalysis` are literal
translations of those tables), and the Phase-2 graph was never built.

### The case for keeping Neo4j

- **Cross-clause risk detection is genuinely graph-shaped.** The guide's own example — *"a liability
  cap in one section subtly undermines an indemnification clause in another"* — is a query where the
  path between two clauses is the answer. Recursive CTEs handle two hops; four gets unpleasant, and
  you hand-roll cycle detection.
- **Multi-hop reasoning**: termination → notice period → cure period.
- **Obligation and deadline mapping**, queried in both directions.
- **Amendment lineage** — "which version of clause 9 is in force as of date X, after three
  amendments?" `ContractVersion`, `HAS_VERSION` and `HAS_LINEAGE` are defined and unused; this is
  the shape they were for.
- **Provenance DAGs** — document → chunk → embedding → extraction → human approval, walked
  backwards to answer "what evidence produced this finding?"
- **Schema flexibility while the clause relationships are still being discovered.**

### The case against

- **Vector search is its weakest axis and retrieval is the hot path.** HNSW only, no quantization,
  no native reranking, manual hybrid fusion. Qdrant and pgvector both beat it here.
- **No RLS, and tenancy is hand-rolled.** 75 `MATCH (c:Contract` sites; isolation depends on each
  remembering `tenant_id`, with no database-level backstop. Several live ones don't (see Known
  issues).
- **Weak declarative integrity.** Community gives uniqueness constraints and nothing else — no FKs,
  no CHECK. "MODIFIED requires edited_text" lives in Python because the DB cannot express it.
- **Community edition vs the stated production requirements** (99.9% uptime, RTO <15min, RPO <5min,
  multi-region) — that is Enterprise or Aura Professional+, materially pricier than managed
  Postgres.
- **Compliance tooling is thin**: GDPR right-to-be-forgotten, retention policies, PITR, column-level
  encryption and temporal tables are all mature in Postgres and DIY here.
- **Reporting is second-class** — no materialized views, window functions or partitioning.
- **Polyglot cost.** Following the design doc and adding a vector DB makes Neo4j a *third* store
  holding only relationships.

### The crux

Neo4j is a good fit for perhaps 20% of the data model and mediocre for the rest — but that 20% is
the part that differentiates the product. The real problem is not graph vs. relational: it is that a
**containment** graph was built (Contract *owns* Section *owns* Clause — hierarchy, which relational
does better) where a **dependency** graph is needed (Clause *limits / conditions / supersedes*
Clause — which only a graph does well).

### The two paths, for whenever this is picked up

**A — keep Neo4j and make it load-bearing.** Build the clause-dependency graph: `LIMITS`,
`CONDITIONS`, `SUPERSEDES`, `CROSS_REFERENCES` between `Clause` nodes, and ship cross-clause risk
detection. Roughly a day for a first version. This is the only thing that justifies the dependency,
it is in the spec, and it would make the "GraphRAG" claim in `README.md` and
`docs/RESUME_WRITEUP.md` true. It would also make the section-renumbering problem solve itself,
since cross-references would resolve to stable section identities rather than numbers.

**B — migrate to Postgres + pgvector.** A single-document extract → check → redline → approve
pipeline with RLS, real constraints and real ANN search is better served there, and it drops a
dependency. Cost: ~40 Cypher queries across ~15 files, and persistence is not abstracted — services
call `repository.graph.query(...)` with raw Cypher directly.

**Decision rule:** *does the product need to answer questions where the path between clauses is the
answer?* The design guide says yes. The code says not yet. The expensive outcome is neither — paying
Neo4j's costs without collecting its benefits.

### Interim position (agreed 2026-09-13)

Do not migrate, and do not defend the current schema. Neo4j is **not** the bottleneck — the debug
traces put one LLM call at 84% of an upload and all three at ~100% of an analysis, with Neo4j round
trips under 1% combined. Two cheap fixes are worth doing regardless of which path is eventually
chosen: **a real vector index**, and **a single tenant-scoped query helper** so isolation is not
re-implemented 75 times.

**Revisit when:** Increment 9 lands, or a real customer contract makes full-scan similarity too slow
— whichever comes first.

### A framing note

`README.md` and `docs/RESUME_WRITEUP.md` both lead with "Agentic GraphRAG" and "graph-based
knowledge system". Anyone who opens the repo sees single-hop joins. Either describe it accurately
("Neo4j-backed contract store with multi-level vector search") or take path A and earn the label.


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

### Found 2026-09-13 during the architecture review — verify after the increments land

Recorded rather than fixed, at your request. Several sit in code Increments 6–8 rewrite, so re-check
each once those land rather than fixing them twice.

| # | Bug | Where | Why it matters |
|---|---|---|---|
| 1 | ~~**Duplicate check has no tenant filter.**~~ **Fixed in Increment 6.** Replaced by `MatterRepository.version_by_source_hash`, which matches `(:ContractVersion {tenant_id, source_sha256})` — tenant-scoped by construction, and pinned by `test_upload_paths.py` | `api/document_upload.py` | was cross-tenant disclosure |
| 2 | ~~**Frontend build is broken.**~~ The stale import is fixed in Increment 6 — but it was **not the only cause**. `npm run build` still fails on 31 `tsc` errors across 15 files (unused imports, implicit `any`, index-signature errors), down from 41. Increment 6 fixed the ten in files it touched; the rest sit in `ViolationsDetail`, `ErrorBoundary`, the documentation tabs, `theme-provider`, `tabs.tsx`, the chat input and the search results. `npx vite build` succeeds, which is why dev has always worked | `frontend/src/` (15 files) | still no production build; **needs its own scheduled pass** |
| 3 | **Precedent matching silently returns nothing.** Query matches `[:CONTAINS]`, but the write path creates `[:CONTAINS_CLAUSE]` | `agents/enhanced_cuad_tools.py:354` | the missing `tenant_id` recorded in Increment 1 is not the whole story; this is the other half |
| 4 | **Chat rejects ordinary contract questions.** `TopicValidator` matches `\bcontract\b`, which does **not** match "contracts", and its allowlist has "indemnity" but not "indemnification". "Summarise the indemnification clauses in our contracts" is refused as `OUT_OF_SCOPE` | `governance/validators/topic.py:8-13` | the symptom is noted in the debug-panel section; this is the root cause |
| 5 | **Dead import.** `create_production_router` is imported and never mounted | `backend/main.py:15` | misleading — suggests a production route set that does not exist |
| 6 | **Unused duplicate modules.** `backend/domain/contracts/` and `backend/domain/search/` duplicate `domain/entities.py` and `domain/search_entities.py`. Only `domain/policies/` and `domain/documentation/` are imported | — | two sources of truth for the same entities |

All four bugs that were "covered by Increment 6 rather than listed above" are now fixed: the model
dropdown having no effect on upload, upload ignoring `X-Tenant-ID`, the three-way tenant split, and
the duplicate check never firing. See [Increment 6 — What changed](#increment-6--multiple-contracts-each-resumable).
