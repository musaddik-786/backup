# Expected Outcomes for Test Scenarios

## TEST INSTRUCTIONS

1. **Replace the current mock data** in `/home/JarvisClaims/Agent_Verse_Musaddique/Knowledgeops_gw/data/raw/` with the test JSON files:
   ```bash
   cp test_tickets.json data/raw/tickets.json
   cp test_chat_rca.json data/raw/chat_rca.json
   cp test_release_notes.json data/raw/release_notes.json
   cp test_runbooks.json data/raw/runbooks.json
   ```

2. **Run the pipeline**:
   ```bash
   python3 pipeline.py --force
   # OR step-by-step:
   python3 step3_populate_data.py --force
   python3 step4_store_embeddings.py
   ```

3. **Verify each record against expected outcomes below**

---

## TICKETS.JSON EXPECTATIONS

### T-SC-001: HIGH-CONFIDENCE (P1 = Emergency)
```
Deterministic Logic:
- priority: "P1" → PRIORITY_TO_SEVERITY.get("P1", Normal) = Emergency
- severity_conf: 0.95 (very high, P1 always critical)
- module_reported: "PolicyCenter"
- module_conf: 0.9 (exact match: "PolicyCenter" in MODULE_ALIASES)

LLM Hybrid Fallback:
- Module 0.9 >= 0.5 threshold → keep deterministic PolicyCenter
- Severity 0.95 >= 0.6 threshold → keep deterministic Emergency
- No LLM override needed

Expected tagging_confidence:
- (0.9 + 0.95 + 2*llm_conf) / 4 where llm_conf ≈ 0.85
- ≈ (0.9 + 0.95 + 1.7) / 4 ≈ 0.89

Expected curation_status: SMEApproved (0.89 >= 0.55)
Expected embedding: YES (post-curation)
```

### T-AMB-002: AMBIGUOUS MODULE, P3
```
Deterministic Logic:
- priority: "P3" → Normal
- severity_conf: 0.7
- module_reported: "system error when clicking buttons" (vague, no keyword)
- module_conf: 0.3 (fallback to PolicyCenter, weak signal)
- Text mentions "ClaimCenter claim entry form" → should be ClaimCenter

LLM Hybrid Fallback:
- Module 0.3 < 0.5 threshold → LLM OVERRIDE expected
  - LLM reads text: "claims are not being saved", "ClaimCenter claim entry form"
  - Should correctly classify as ClaimCenter
  - llm_module_conf ≈ 0.85
- Severity 0.7 >= 0.6 threshold → keep deterministic Normal
  - (No urgent keywords, normal P3 severity)

Expected tagging_confidence:
- (0.85 + 0.7 + 2*0.8) / 4 ≈ (0.85 + 0.7 + 1.6) / 4 ≈ 0.79
- But depends on LLM confidence for category/resolution

Expected curation_status: SMEApproved (likely 0.70+ >= 0.55)
Expected embedding: YES
```

### T-BILL-003: CLEAR MODULE, P4
```
Deterministic Logic:
- priority: "P4" → Low
- severity_conf: 0.6
- module_reported: "BillingCenter invoice processing workflow"
- module_conf: 0.85 (exact match: "BillingCenter")

LLM Hybrid Fallback:
- Both module (0.85) and severity (0.6) above thresholds → no override
- Keep deterministic: BillingCenter, Low

Expected tagging_confidence: ≈ 0.7+ (straightforward)
Expected curation_status: SMEApproved
Expected embedding: YES
```

---

## CHAT_RCA.JSON EXPECTATIONS

### RCA-2025-001: LINKED TO T-SC-001 (HIGH-SEV)
```
Deterministic Logic:
- incident_id: "T-SC-001" (exists in tickets)
- Severity inheritance from ticket T-SC-001: Emergency (0.95 confidence)
- module_reported: "PolicyCenter"
- module_conf: 0.9 (exact match)

LLM Hybrid Fallback:
- Module 0.9 >= 0.5 → keep PolicyCenter
- Severity 0.95 >= 0.6 → keep Emergency
- No override

Expected tagging_confidence: ≈ 0.85+
Expected curation_status: SMEApproved
Expected embedding: YES
```

### CHAT-2025-002: NO LINK, VAGUE LANGUAGE
```
Deterministic Logic:
- incident_id: null (no ticket link)
- module_reported: "can't log in to system"
  - No exact module match → PolicyCenter (0.3 fallback)
  - module_conf: 0.3
- No severity keywords (no "outage", "unresponsive", etc.)
  - No priority code (it's a chat, not a ticket)
  - Default to Normal (0.4 fallback)
  - severity_conf: 0.4

LLM Hybrid Fallback:
- Module 0.3 < 0.5 threshold → LLM OVERRIDE
  - Text: "can't log in", "authentication service timeout"
  - Could be ContactManager, IntegrationGateway, or other auth module
  - LLM might guess ContactManager or IntegrationGateway (50% chance either way)
  - llm_module_conf ≈ 0.6 (LLM uncertain too)
- Severity 0.4 < 0.6 threshold → LLM OVERRIDE
  - Text: "intermittent", "works sometimes"
  - Suggests operational issue, not critical outage
  - LLM might pick Normal or Low
  - llm_severity_conf ≈ 0.5

Expected tagging_confidence:
- (0.6 + 0.5 + 2*llm_cat_conf) / 4
- If llm_cat_conf ≈ 0.45: (0.6 + 0.5 + 0.9) / 4 ≈ 0.5
- **Expected: BORDERLINE, likely < 0.55 → NeedsRetag**

Expected curation_status: NeedsRetag (high likelihood)
Expected embedding: NO (held back)
```

### RCA-2025-003: CLEAR MODULE, TECHNICAL RCA
```
Deterministic Logic:
- incident_id: null
- module_reported: "ClaimCenter database connection pooling"
- module_conf: 0.9 (exact match: "ClaimCenter")
- No severity keywords in title, but body discusses operational impact
- severity_conf: 0.5 (no hard keywords, but technical/performance context)

LLM Hybrid Fallback:
- Module 0.9 >= 0.5 → keep ClaimCenter
- Severity 0.5 < 0.6 threshold → LLM OVERRIDE possible
  - Text: "connection pool exhausted", "timeout", "peak hours"
  - Suggests operational stress but not critical (workaround exists)
  - LLM might pick High or Normal
  - llm_severity_conf ≈ 0.7

Expected tagging_confidence:
- (0.9 + 0.7 + 2*0.75) / 4 ≈ (0.9 + 0.7 + 1.5) / 4 ≈ 0.78

Expected curation_status: SMEApproved
Expected embedding: YES
```

---

## RELEASE_NOTES.JSON EXPECTATIONS

### XENG-2840: RESOLVED_ISSUE, SINGLE MODULE
```
Deterministic Logic:
- affected_modules: ["ClaimCenter"] (explicit)
- module_conf: 0.95 (direct field)
- note_type: "resolved_issue" → ResolutionType.Patch (0.9 conf)
- Severity: not explicitly provided in schema
  - No "critical", "urgent", etc.
  - Assume Normal (0.5 default)
  - severity_conf: 0.5

LLM Hybrid Fallback:
- Module 0.95 >= 0.5 → keep ClaimCenter
- Severity 0.5 < 0.6 threshold → LLM OVERRIDE
  - Title: "duplicate claim creation" (data integrity issue)
  - Could be High (data quality impact)
  - llm_severity_conf ≈ 0.75

Expected tagging_confidence:
- (0.95 + 0.75 + 2*0.8) / 4 ≈ (0.95 + 0.75 + 1.6) / 4 ≈ 0.82

Expected curation_status: SMEApproved
Expected embedding: YES
```

### XENG-2841: KNOWN_ISSUE, VAGUE
```
Deterministic Logic:
- affected_modules: [] (empty)
- No module mention in title/details
- module_conf: 0.3 (fallback PolicyCenter)
- note_type: "known_issue"
- Text is generic filler: "performance may vary in certain scenarios"
- No severity signal
- severity_conf: 0.3 (generic fallback)

LLM Hybrid Fallback:
- Module 0.3 < 0.5 → LLM OVERRIDE
  - Text is too generic for LLM to infer module
  - "performance may vary" doesn't mention any product
  - llm_module_conf ≈ 0.25 (LLM also struggles)
- Severity 0.3 < 0.6 → LLM OVERRIDE
  - Text gives no severity signals
  - "may vary" is non-committal
  - llm_severity_conf ≈ 0.35

Expected tagging_confidence:
- (0.25 + 0.35 + 2*0.3) / 4 ≈ (0.25 + 0.35 + 0.6) / 4 ≈ 0.3
- **Expected: NeedsRetag (< 0.55)**

Expected curation_status: NeedsRetag
Expected embedding: NO
```

### XENG-2842: FEATURE, MULTI-MODULE
```
Deterministic Logic:
- affected_modules: ["PolicyCenter", "ClaimCenter", "IntegrationGateway"]
- Product: "IntegrationGateway"
- Fuzzy match on "IntegrationGateway" → 0.7
- module_conf: 0.7 (clear primary module)
- note_type: "feature" → ResolutionType.Documentation or FeatureRequest
- resolution_type_conf: 0.8
- Severity: feature, no urgency
- severity_conf: 0.4 (features are typically not severity-rated)

LLM Hybrid Fallback:
- Module 0.7 >= 0.5 → keep IntegrationGateway
- Severity 0.4 < 0.6 → LLM OVERRIDE
  - Text: "webhook support for ... events"
  - Features typically have low severity
  - llm_severity_conf ≈ 0.5 (still low, but LLM consistent)

Expected tagging_confidence:
- (0.7 + 0.5 + 2*0.75) / 4 ≈ (0.7 + 0.5 + 1.5) / 4 ≈ 0.675

Expected curation_status: SMEApproved (0.675 >= 0.55)
Expected embedding: YES
```

---

## RUNBOOKS.JSON EXPECTATIONS

### RB-CC-201: WELL-STRUCTURED DEADLOCK RUNBOOK
```
Deterministic Logic:
- owner_team: "ClaimCenter-DBA"
- module_reported: "ClaimCenter"
- module_conf: 0.95 (exact match + owner team reinforces)
- Title: "ClaimCenter: Recover from database deadlock"
- Trigger includes specific SQL: "SELECT * FROM pg_stat_statements"
- Severity signals: "deadlock" (performance/operational)
- severity_conf: 0.6 (operational issue, not critical outage)

LLM Hybrid Fallback:
- Module 0.95 >= 0.5 → keep ClaimCenter
- Severity 0.6 >= 0.6 → keep deterministic (boundary case)
  - Text: "deadlock detected", "blocking transaction"
  - LLM would agree: operational/High
  - No override needed

Expected tagging_confidence: ≈ 0.75+
Expected curation_status: SMEApproved
Expected embedding: YES
```

### RB-GEN-101: VAGUE TROUBLESHOOTING RUNBOOK
```
Deterministic Logic:
- owner_team: "General Support"
- module_reported: "system"
- No specific module mention
- module_conf: 0.3 (fallback PolicyCenter)
- Title: "Troubleshooting: System slowness" (generic)
- Steps: 3 vague lines, not actionable
- Trigger: "If users report slow performance" (not quantified)
- severity_conf: 0.3 (generic, no keywords)

LLM Hybrid Fallback:
- Module 0.3 < 0.5 → LLM OVERRIDE
  - Title and steps give no module hints
  - "system slowness" is too generic
  - llm_module_conf ≈ 0.2 (LLM also can't infer)
- Severity 0.3 < 0.6 → LLM OVERRIDE
  - "slowness" suggests operational issue
  - Not critical/urgent
  - llm_severity_conf ≈ 0.4

Expected tagging_confidence:
- (0.2 + 0.4 + 2*0.3) / 4 ≈ (0.2 + 0.4 + 0.6) / 4 ≈ 0.3
- **Expected: NeedsRetag (< 0.55)**

Expected curation_status: NeedsRetag
Expected embedding: NO
```

### RB-IG-301: CRITICAL COMPONENT WITH NUMERIC TRIGGER
```
Deterministic Logic:
- owner_team: "IntegrationGateway-Oncall"
- module_reported: "IntegrationGateway"
- module_conf: 0.95 (exact match + specialist team)
- Title: "IntegrationGateway: Connection pool exhaustion response"
- Trigger: "connection_wait_time_ms > 5000 for >2 consecutive minutes"
  - Quantified, measurable trigger
  - Suggests operational/performance issue
- Steps: 7 detailed, numbered steps with specific commands
- Severity signals: "exhaustion", "auto-scaling", "high utilization"
- severity_conf: 0.7 (operational issue, measurable thresholds)

LLM Hybrid Fallback:
- Module 0.95 >= 0.5 → keep IntegrationGateway
- Severity 0.7 >= 0.6 → keep deterministic Normal/High
  - Text: "exhaustion", ">80% utilization", "auto-scaling"
  - Operational severity confirmed
  - LLM would agree
  - No override needed

Expected tagging_confidence: ≈ 0.80+
Expected curation_status: SMEApproved
Expected embedding: YES
```

---

## SUMMARY TABLE

| Record ID | Module | Severity | tagging_confidence | Curation Status | Embedding? |
|---|---|---|---|---|---|
| **T-SC-001** | PolicyCenter | Emergency | ≈0.89 | **SMEApproved** | YES |
| **T-AMB-002** | ClaimCenter* | Normal | ≈0.75–0.79 | **SMEApproved** | YES |
| **T-BILL-003** | BillingCenter | Low | ≈0.70+ | **SMEApproved** | YES |
| **RCA-2025-001** | PolicyCenter | Emergency | ≈0.85+ | **SMEApproved** | YES |
| **CHAT-2025-002** | ?* | ?* | **< 0.55** | **NeedsRetag** | NO |
| **RCA-2025-003** | ClaimCenter | High/3* | ≈0.78 | **SMEApproved** | YES |
| **XENG-2840** | ClaimCenter | High/3* | ≈0.82 | **SMEApproved** | YES |
| **XENG-2841** | ?* | ?* | **< 0.55** | **NeedsRetag** | NO |
| **XENG-2842** | IntegrationGateway | Normal/4* | ≈0.675 | **SMEApproved** | YES |
| **RB-CC-201** | ClaimCenter | High/3* | ≈0.75+ | **SMEApproved** | YES |
| **RB-GEN-101** | ?* | ?* | **< 0.55** | **NeedsRetag** | NO |
| **RB-IG-301** | IntegrationGateway | High/3* | ≈0.80+ | **SMEApproved** | YES |

**Expected Distribution:**
- **SMEApproved: 9 records** (eligible for embedding + KB store)
- **NeedsRetag: 3 records** (held back; NO embedding)
- **AutoApproved: 0 records** (removed; never assigned)

---

## VERIFICATION CHECKLIST

After running the pipeline, verify:

```
[ ] INGESTION OUTPUT
    [ ] All 12 records ingested without error
    [ ] tagging_confidence values within expected ranges (see table above)
    [ ] Module/severity shows LLM overrides for records marked with *

[ ] STRUCTURING OUTPUT  
    [ ] SOP markdown rendered for all 12 records
    [ ] Embeddings NOT yet generated (NULL in memory)

[ ] CURATION GATE
    [ ] ZERO records have curation_status = "AutoApproved"
    [ ] 9 records: curation_status = "SMEApproved"
    [ ] 3 records: curation_status = "NeedsRetag"
    [ ] curation_notes populated with reasoning for NeedsRetag

[ ] POST-CURATION EMBEDDING
    [ ] Embeddings generated only for 9 approved records
    [ ] 3 NeedsRetag records have embedding = NULL (never embedded)

[ ] KNOWLEDGE STORE (if running against Postgres)
    [ ] 9 records upserted into KB tables
    [ ] 3 records logged to curation_audit_log (not in KB)
    [ ] ingestion_state table has content_hash for all 12

[ ] DUPLICATE-RUN PREVENTION
    [ ] Run pipeline again (without --force)
    [ ] 0 records reprocessed (all hashes match)
    [ ] No LLM calls made (zero cost)

[ ] SELECTIVE INGESTION
    [ ] Run: python3 pipeline.py --sources tickets,runbooks
    [ ] Chat/RCA and release_notes skipped (0 processed)
    [ ] Only 3 tickets + 3 runbooks processed
```

---

## NOTES

- **\* marks** on Module/Severity indicate LLM hybrid fallback override likely occurred
- **Confidence ranges are approximate** — exact values depend on LLM category/resolution_type scores
- **NeedsRetag records** (CHAT-2025-002, XENG-2841, RB-GEN-101) intentionally designed with weak signals to trigger <0.55 path
- **The 0.55 threshold** in `_simulate_sme_review()` is the cutoff between SMEApproved and NeedsRetag in the demo (not an auto-approve threshold anymore)

