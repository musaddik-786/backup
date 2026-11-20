# Test Scenarios for Knowledge Base Pipeline

This folder contains **test data and expected outcomes** for end-to-end testing of the Knowledge Base pipeline, covering:
- Hybrid LLM-fallback tagging (module/severity)
- Selective ingestion + duplicate-run prevention
- **True human-in-the-loop SME review** (nothing is auto-approved; embeddings only generated after a human approves)
- **Closed-ticket-only ingestion** (open/in-progress tickets are skipped entirely)

## Files

- **test_tickets.json** — 5 ticket test cases: 3 closed (high-conf P1, ambiguous module, clear P4) + **2 that must NOT be ingested at all** (1 `Open`, 1 `In Progress`)
- **test_chat_rca.json** — 3 chat/RCA test cases (linked RCA, vague chat, technical RCA)
- **test_release_notes.json** — 3 release note test cases (resolved, vague, multi-module)
- **test_runbooks.json** — 3 runbook test cases (structured, vague, critical)

- **TEST_SCENARIOS.md** — High-level scenario overview
- **TEST_EXPECTED_OUTCOMES.md** — Detailed per-record confidence/tagging expectations (written before the HITL change — curation_status in that doc should now be read as "would reach this confidence level," not as an automatic outcome; see below for what actually happens now)

## Quick Start

### 1. Back up original data (optional)
```bash
mkdir -p data/raw/backup
cp data/raw/*.json data/raw/backup/
```

### 2. Copy test data to raw folder
```bash
cp data/test_scenarios/test_tickets.json data/raw/tickets.json
cp data/test_scenarios/test_chat_rca.json data/raw/chat_rca.json
cp data/test_scenarios/test_release_notes.json data/raw/release_notes.json
cp data/test_scenarios/test_runbooks.json data/raw/runbooks.json
```

### 3. Run the pipeline (ingest + tag + hold for review)
```bash
python3 pipeline.py --force
```
This ingests, tags, and writes **every** record into its table as `curation_status='PendingReview'`, `embedding=NULL`. Nothing is auto-approved. You should see:
```
-> 3 records written to ticket_records as PendingReview       (NOT 5 -- the 2 open/in-progress tickets are skipped)
-> 3 records written to chat_rca_records as PendingReview
-> 3 records written to release_note_records as PendingReview
-> 3 records written to runbook_records as PendingReview
```

### 4. Run the human SME review
```bash
python3 sme_review.py
```
This is now the ONLY way a record becomes `SMEApproved` and gets an embedding. It walks through each pending record interactively (shows tags, confidence, reasoning, rendered SOP) and asks you to `[a]pprove / [r]eject / [n]eeds-retag / [s]kip / [q]uit`.

For a faster non-interactive pass while testing, you can rule-approve by confidence instead of typing a decision per record:
```bash
python3 sme_review.py --auto-approve-above 0.55
```
This is still an explicit, human-invoked decision (you chose the threshold and ran the command) -- it is not a default the pipeline falls back to on its own.

### 5. Verify
```bash
python3 query_demo.py
```
Only records that were actually approved in step 4 are retrievable (search_similar() only returns `AutoApproved`/`SMEApproved` rows).

## Expected Results

| Check | Expected |
|---|---|
| Tickets ingested | **3** (T-SC-001, T-AMB-002, T-BILL-003) — closed only |
| Tickets skipped entirely | **2** (T-OPEN-004 status=`Open`, T-PROG-005 status=`In Progress`) — never tagged, never reviewed |
| Records `PendingReview` after `pipeline.py` | **12** (3 tickets + 3 chat/RCA + 3 release notes + 3 runbooks) |
| Records `AutoApproved` after `pipeline.py` | **0** (always — curation.py never assigns this) |
| Records with an embedding before `sme_review.py` runs | **0** |
| Records retrievable via `query_demo.py` before review | **0** |

After running `sme_review.py` and approving some subset:
- Only approved records have `embedding` set and are retrievable.
- Rejected/retagged records stay out of search results permanently (rejected) or get retried on the next `pipeline.py` run (retagged — not registered in `ingestion_state`).

## Test Coverage

✓ Hybrid LLM fallback (module/severity override when deterministic confidence < threshold)
✓ Confidence scoring across all source types
✓ **True SME gate**: zero auto-approval; every record requires an explicit human decision via `sme_review.py`
✓ **Embedding generated only at the moment of approval**, never before
✓ **Closed-ticket-only ingestion**: `Open`/`In Progress` tickets are filtered out before tagging even runs
✓ Selective ingestion (can run `--sources tickets` alone)
✓ Duplicate-run prevention (unchanged + already-decided records skipped on re-run)

## Restore Original Data

```bash
cp data/raw/backup/*.json data/raw/
```
