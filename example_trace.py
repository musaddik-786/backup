"""
Traces ONE record through all 4 pipeline stages and prints what it looks
like at each step. This is purely for demonstration/understanding -- not
part of the production pipeline (that's pipeline.py).

Run: python example_trace.py
"""

from __future__ import annotations

import json

from curation import run_curation_gate
from ingestion import load_raw, normalize_ticket
from schemas import RawTicket
from structuring import structure_and_embed

TRACE_TICKET_ID = "INC-100234"


def main():
    print("=" * 78)
    print(f"TRACING ONE RECORD THROUGH THE PIPELINE: {TRACE_TICKET_ID}")
    print("=" * 78)

    # ---- STAGE 0: raw data, exactly as it sits in data/raw/tickets.json ----
    tickets = load_raw("tickets.json", RawTicket)
    raw = next(t for t in tickets if t.ticket_id == TRACE_TICKET_ID)

    print("\n### STAGE 0 -- Raw source record (data/raw/tickets.json) ###\n")
    print(json.dumps(json.loads(raw.model_dump_json()), indent=2))

    # ---- STAGE 1: Ingestion & Normalization ----
    normalized = normalize_ticket(raw)

    print("\n### STAGE 1 -- After Ingestion & Normalization (ingestion.py) ###\n")
    print(f"  record_id           : {normalized.record_id}")
    print(f"  module              : {normalized.module.value}")
    print(f"  category            : {normalized.category.value}")
    print(f"  severity            : {normalized.severity.value}")
    print(f"  resolution_type     : {normalized.resolution_type.value}")
    print(f"  tagging_confidence  : {normalized.tagging_confidence}")
    print(f"  tagging_reasoning   : {normalized.tagging_reasoning}")
    print("  (^ module/severity resolved from raw fields; category/resolution_type")
    print("     were picked by an LLM call constrained to the taxonomy.py enums --")
    print("     see _llm_classify() in ingestion.py)")

    # ---- STAGE 2: Structuring Agent ----
    # structure_and_embed expects the whole corpus (it fits one shared
    # embedding space across all records) -- we run it on everything so
    # the vector space matches what the real pipeline produces, then pull
    # our one record back out.
    from ingestion import ingest_all

    all_records = ingest_all()
    all_records = structure_and_embed(all_records)
    traced = next(r for r in all_records if r.source_ref_id == TRACE_TICKET_ID)

    print("\n### STAGE 2 -- After Structuring Agent (structuring.py) ###\n")
    print("--- structured_sop (rendered SOP/runbook format) ---")
    print(traced.structured_sop)
    print(f"--- embedding: {len(traced.embedding)}-dimensional vector ---")
    print(f"  first 8 values: {[round(v, 4) for v in traced.embedding[:8]]}")
    print("  (the rest are mostly 0.0 -- TF-IDF vectors are sparse: only the")
    print("   ~handful of distinctive words in this record have non-zero weight)")

    # ---- STAGE 3: SME Curation Gate ----
    approved, held_back = run_curation_gate(all_records)
    traced_after_gate = next(r for r in approved + held_back if r.source_ref_id == TRACE_TICKET_ID)

    print("\n### STAGE 3 -- After SME Curation Gate (curation.py) ###\n")
    print(f"  curation_status : {traced_after_gate.curation_status.value}")
    print(f"  curation_notes  : {traced_after_gate.curation_notes}")
    eligible = traced_after_gate in approved
    print(f"  -> {'ELIGIBLE for Knowledge Store' if eligible else 'HELD BACK, not written to Knowledge Store'}")

    # ---- STAGE 4: Knowledge Store (what the row would look like) ----
    print("\n### STAGE 4 -- Row as written to Postgres knowledge_records table ###\n")
    print(f"  record_id           = '{traced_after_gate.record_id}'")
    print(f"  source_type         = '{traced_after_gate.source_type.value}'")
    print(f"  module              = '{traced_after_gate.module.value}'")
    print(f"  category            = '{traced_after_gate.category.value}'")
    print(f"  severity            = '{traced_after_gate.severity.value}'")
    print(f"  resolution_type     = '{traced_after_gate.resolution_type.value}'")
    print(f"  embedding           = vector(3072)  -- the array printed above")
    print(f"  curation_status     = '{traced_after_gate.curation_status.value}'")
    print(f"  kb_version          = (stamped at upsert time, e.g. kb-20261112T...)")

    print("\n### HOW AN AGENT WOULD FIND THIS LATER ###\n")
    print('  L1 agent sees a NEW ticket: "submissions stuck in processing after upgrade"')
    print("  -> embeds that query text into the same 3072-dim space")
    print("  -> runs: SELECT ... WHERE module='PolicyCenter' AND source_type IN")
    print("           ('HistoricalTicket','RunbookOrSOP') ORDER BY embedding <=> query LIMIT 3")
    print(f"  -> this record ({traced_after_gate.record_id}) comes back as the top/near-top match,")
    print("     because its embedding is close in vector space to the new query's embedding")
    print("     (both are about: PolicyCenter + stuck submissions + post-upgrade)")


if __name__ == "__main__":
    main()
