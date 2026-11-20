# Test Scenarios for Knowledge Base Pipeline

## Overview
Test data designed to exercise all curation paths:
- **SMEApproved path**: confidence ≥ 0.55
- **NeedsRetag path**: confidence < 0.55
- NO AutoApproved path (removed per latest change)

---

## SOURCE 1: TICKETS (tickets.json)

### Scenario 1.1: HIGH-CONFIDENCE TICKET (P1 = Emergency)
- **Deterministic Path**: P1 → Emergency (0.95 confidence)
- **Module Mention**: "PolicyCenter policy issuance"
- **Expected**: tagging_confidence ≈ 0.8+, **SMEApproved**
- **Reason**: P1 always gives high severity confidence; direct module mention

### Scenario 1.2: AMBIGUOUS MODULE, P3 PRIORITY
- **Deterministic Path**: P3 → Normal (0.7 confidence)
- **Module Mention**: "system error when clicking buttons" (vague, no module keyword)
- **Module Fuzzy Match**: will fallback to PolicyCenter at low confidence (0.3)
- **LLM Override**: module should be corrected (e.g., to ClaimCenter if text mentions "claims")
- **Expected**: tagging_confidence ≈ 0.45–0.55, **borderline NeedsRetag or SMEApproved** depending on final scores
- **Reason**: Tests hybrid fallback; low deterministic module conf triggers LLM

### Scenario 1.3: LOW PRIORITY, CLEAR RESOLUTION
- **Deterministic Path**: P4 → Low (0.6 confidence)
- **Module Mention**: "BillingCenter invoice processing workflow"
- **Expected**: tagging_confidence ≈ 0.65–0.7, **SMEApproved**
- **Reason**: Clear module, normal severity confidence, straightforward

---

## SOURCE 2: CHAT/RCA (chat_rca.json)

### Scenario 2.1: RCA LINKED TO HIGH-SEV TICKET
- **Record Type**: rca_report
- **Incident ID**: Links to ticket T-Emergency-0001 (a P1 ticket ingested in step 1)
- **Module Mention**: "PolicyCenter renewal engine failed"
- **Deterministic Path**: ticket link → Emergency (0.95 inherited confidence)
- **Expected**: tagging_confidence ≈ 0.85+, **SMEApproved**
- **Reason**: RCA linked to high-severity ticket guarantees high severity confidence

### Scenario 2.2: CHAT TRANSCRIPT, NO LINK, VAGUE LANGUAGE
- **Record Type**: chat_transcript
- **Incident ID**: null (no ticket link)
- **Module Mention**: "can't log in to system"
- **Deterministic Path**: No keyword match → PolicyCenter (0.3 fallback)
- **Severity**: No "outage"/"unresponsive" keywords, no P-code → Normal (0.4 fallback)
- **LLM Override**: Should correct both if text suggests ContactManager or other module
- **Expected**: tagging_confidence ≈ 0.35–0.45, **NeedsRetag**
- **Reason**: Tests lowest-confidence path; very weak deterministic signals

### Scenario 2.3: FORMAL RCA WITH CLEAR ROOT CAUSE
- **Record Type**: rca_report
- **Incident ID**: null
- **Module Mention**: "ClaimCenter database connection pooling"
- **Body**: "connection timeout due to max_connections limit. corrective action: increased pool size"
- **Deterministic Path**: "ClaimCenter" exact match, no severity keywords → Normal (0.5)
- **LLM Override**: Unlikely; text is clear and technical
- **Expected**: tagging_confidence ≈ 0.6–0.65, **SMEApproved**
- **Reason**: Good module confidence, reasonable severity, clear resolution language

---

## SOURCE 3: RELEASE NOTES (release_notes.json)

### Scenario 3.1: RESOLVED ISSUE, SINGLE MODULE
- **Note Type**: resolved_issue
- **Product**: ClaimCenter
- **Title**: "Fixed duplicate claim creation in batch processing"
- **Affected Modules**: ["ClaimCenter"]
- **Deterministic Path**: "ClaimCenter" in affected_modules → very high module conf (0.9+)
- **Expected**: tagging_confidence ≈ 0.8+, **SMEApproved**
- **Reason**: Explicit module field, clear resolution_type (Patch)

### Scenario 3.2: KNOWN ISSUE, VAGUE DETAILS
- **Note Type**: known_issue
- **Product**: GuidewireProduct (generic, no ClaimCenter/PolicyCenter)
- **Title**: "Performance may vary in certain scenarios"
- **Affected Modules**: [] (empty)
- **Deterministic Path**: No module in list, title has no keywords → PolicyCenter (0.3 fallback)
- **LLM Override**: Likely, but text is so vague LLM may also struggle
- **Expected**: tagging_confidence ≈ 0.35–0.45, **NeedsRetag**
- **Reason**: Virtually no signal; text is generic filler

### Scenario 3.3: FEATURE, MULTI-MODULE
- **Note Type**: feature
- **Product**: IntegrationGateway
- **Title**: "New webhook support for PolicyCenter and ClaimCenter events"
- **Affected Modules**: ["PolicyCenter", "ClaimCenter", "IntegrationGateway"]
- **Deterministic Path**: Multiple modules, no single clear pick → fuzzy match on "IntegrationGateway" (0.7)
- **Expected**: tagging_confidence ≈ 0.65–0.75, **SMEApproved**
- **Reason**: IntegrationGateway is the product; multi-module context is okay

---

## SOURCE 4: RUNBOOKS (runbooks.json)

### Scenario 4.1: WELL-STRUCTURED RUNBOOK
- **Title**: "ClaimCenter: Recover from database deadlock"
- **Trigger Condition**: "SELECT max_connections from pg_settings; if not 200, update to 200"
- **Owner Team**: "ClaimCenter-DBA"
- **Module Mention**: "ClaimCenter" in title + owner_team
- **Deterministic Path**: "ClaimCenter" exact match, no severity keywords → Normal (0.5), "ClaimCenter" module (0.85)
- **Steps**: Clear numbered steps (good SOP content)
- **Expected**: tagging_confidence ≈ 0.7–0.75, **SMEApproved**
- **Reason**: Clear module, structured trigger, technical owner

### Scenario 4.2: VAGUE RUNBOOK, AMBIGUOUS TRIGGER
- **Title**: "Troubleshooting: System slowness"
- **Trigger Condition**: "If users report slow performance"
- **Owner Team**: "General Support"
- **Module Mention**: None (generic)
- **Deterministic Path**: No keywords → PolicyCenter (0.3 fallback), no severity keyword → Normal (0.4 fallback)
- **Expected**: tagging_confidence ≈ 0.35, **NeedsRetag**
- **Reason**: Tests lowest-confidence runbook path; too generic

### Scenario 4.3: CRITICAL COMPONENT RUNBOOK
- **Title**: "IntegrationGateway: Connection pool exhaustion response"
- **Trigger Condition**: "When connection_wait_time_ms > 5000 for >2 minutes"
- **Owner Team**: "IntegrationGateway-Oncall"
- **Module Mention**: "IntegrationGateway" (exact match)
- **Steps**: Quantified thresholds, actionable steps
- **Expected**: tagging_confidence ≈ 0.75–0.8, **SMEApproved**
- **Reason**: Clear module, numeric/measurable trigger, specialist team

---

## EXPECTED DISTRIBUTION

| Curation Status | Count | Trigger |
|---|---|---|
| **SMEApproved** | 8–9 | confidence ≥ 0.55 |
| **NeedsRetag** | 3–4 | confidence < 0.55 |
| **AutoApproved** | 0 | (removed; never assigned) |

---

## EMBEDDING BEHAVIOR

- **SMEApproved records** (8–9): Will have embeddings generated (post-curation)
- **NeedsRetag records** (3–4): No embeddings; held back for human review
- **Knowledge Store**: Only SMEApproved records written (rest held back in curation logs)

---

## TESTING CHECKLIST

- [ ] All 12 records ingest without errors
- [ ] tagging_confidence values match expected ranges above
- [ ] No `AutoApproved` records present (all go through SME review)
- [ ] Hybrid LLM fallback triggers correctly for low-confidence module/severity
- [ ] Embeddings generated only for approved records
- [ ] Curation audit log contains all NeedsRetag records with reasoning
- [ ] Selective ingestion: can run `--sources tickets` alone, rest skipped
- [ ] Duplicate-run prevention: unchanged records skipped on re-run

