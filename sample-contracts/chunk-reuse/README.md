# Test contracts for Increment 7 — content-addressed chunks

One fictional deal, seven documents, each written to exercise one thing. They
are `.txt` so you can read and edit them, with a `.pdf` beside each because the
UI only accepts PDFs. Regenerate a PDF after editing:

```bash
backend/.venv/bin/python scripts/make_sample_pdf.py sample-contracts/chunk-reuse/<file>.txt
```

## The deal

**Vertex Systems Inc.** (Provider) and **Meridian Freight Holdings LLC**
(Customer) — a logistics MSA, `VTX-MSA-2026-0114`. Sections 3, 8 and 9 are
drafted to breach the default playbook (payment at 90 days, uncapped indemnity,
unlimited liability), so the analysis has real findings and real redlines to
decide.

## The seven, and what each is for

| # | file | what it is | what should happen |
|---|---|---|---|
| 1 | `01-round1-vertex-meridian-msa` | the contract as first received | file it as a new matter. **Everything is embedded** — `reused: 0` |
| 2 | `02-round2-one-clause-edited` | payment moved 90 → 45 days | upload into that matter. **~85% of chunks reused**, 2 embedded |
| 3 | `03-round3-section-inserted` | a new Section 9 (Insurance) inserted, everything after it **renumbered**, payment moved to 30 days | **~86% still reused** despite every later heading changing number. This is the case heading-stripping exists for |
| 4 | `04-lookalike-different-counterparty` | a *different deal* on the same template — Apex Cold Chain, different fee | upload as a **New contract**. It **suggests** `VTX-MSA-…` at ~85% — and you should **ignore it and create a new matter anyway.** That is the point |
| 5 | `05-sow-quotes-the-boilerplate` | an SOW under the MSA, quoting its confidentiality and governing-law clauses verbatim | **no suggestion.** Forward 23%, backward 13% — one number would have called this a new round |
| 6 | `06-unrelated-harbour-point-lease` | a UK commercial property lease | **no suggestion**, nothing reused |
| 7 | `07-unstructured-kestrel-nda` | an NDA written as prose, no numbered headings | chunks, but the version is flagged as having **unstable chunk identity** — without headings the chunker packs greedily and one insertion invalidates everything after it |

Every figure above was measured by running these files through the live API, not
estimated. `forward` is how much of the uploaded document a matter explains,
`backward` how much of the matter it covers.

**These fixtures found a real bug.** On the first run, file 4 got no suggestion:
document frequency counted *versions*, so the three rounds of the matter made its
own clauses look like boilerplate and the score fell to 79%. A clause carried
through four rounds of one negotiation is the strongest evidence a match could
have — `df` now counts distinct **matters**, and file 4 suggests at 86%.

## Suggested run

1. Upload **1** as a new contract, confirm the card, file it. Analyse it and
   decide a redline, so there is human work to preserve.
2. Upload **2** into that matter with *Upload new round*. Compare the wall
   clock against step 1, and check `chunking.reuse_check` in the debug panel.
3. Upload **1 again**, unchanged → *"Exactly matches version 1 of …"*, no new
   version, and it returns in about a second. (Increment 6's rule, still true.)
4. Upload **3** into the matter. The renumbering should cost almost nothing.
5. Upload **4** as a **New contract**. Read the suggestion, then ignore it and
   create a separate matter. Confirm both matters now exist.
6. Upload **5**, then **6**. Neither should suggest anything.
7. Upload **7**. It should still work — the flag is a warning about future
   rounds, not a refusal.

## A note on round 2

It reuses 12 of 14 chunks rather than 13: the agreement number in the preamble
also carries a "(Round 2)" marker, as a real second round usually would, so that
chunk changes too. Two changed paragraphs, two embeddings.
