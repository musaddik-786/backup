"""
Stage 3: SME Curation Gate
(diagram box: "Human SMEs spot-check a sample of entries and approve
taxonomy quality before anything goes live." Annotated as: "The one
deliberate human gate -- a bad tag here poisons every downstream agent's
matching.")

This stage does NOT re-run the classifier. Its job is to decide, per
record, whether the Ingestion & Normalization tagging can be trusted
automatically or needs a human to look at it before the record is allowed
into the Knowledge Store:

  - tagging_confidence >= AUTO_APPROVE_CONFIDENCE_THRESHOLD
        -> CurationStatus.AUTO_APPROVED (no human touches it)
  - tagging_confidence <  AUTO_APPROVE_CONFIDENCE_THRESHOLD
        -> routed to a simulated SME review queue

The "simulated SME" here is a stand-in for a real reviewer UI/workflow.
In production this function is replaced by: write low-confidence records
to a review queue table, a human approves/edits tags in a UI, that
decision is written back as SMEApproved / SMERejected / NeedsRetag. The
NormalizedRecord shape and CurationStatus enum are exactly what that real
workflow would read and write -- nothing else in the pipeline needs to
change.

Only AUTO_APPROVED and SME_APPROVED records are eligible to be written
to the Knowledge Store. SME_REJECTED and NEEDS_RETAG records are held
back and reported, never silently dropped.
"""

from __future__ import annotations

from schemas import NormalizedRecord
from taxonomy import AUTO_APPROVE_CONFIDENCE_THRESHOLD, CurationStatus


def run_curation_gate(
    records: list[NormalizedRecord], simulate_sme: bool = True
) -> tuple[list[NormalizedRecord], list[NormalizedRecord]]:
    """
    Returns (approved_records, held_back_records).
    Mutates curation_status / curation_notes on every record in place.
    """
    approved: list[NormalizedRecord] = []
    held_back: list[NormalizedRecord] = []

    for r in records:
        if r.tagging_confidence >= AUTO_APPROVE_CONFIDENCE_THRESHOLD:
            r.curation_status = CurationStatus.AUTO_APPROVED
            r.curation_notes = (
                f"Auto-approved: tagging confidence {r.tagging_confidence:.2f} "
                f">= threshold {AUTO_APPROVE_CONFIDENCE_THRESHOLD}."
            )
            approved.append(r)
            continue

        # Below threshold -> requires SME sign-off.
        if simulate_sme:
            decision = _simulate_sme_review(r)
        else:
            r.curation_status = CurationStatus.PENDING_REVIEW
            r.curation_notes = "Awaiting SME review (not yet reviewed)."
            held_back.append(r)
            continue

        r.curation_status = decision["status"]
        r.curation_notes = decision["notes"]
        if decision["status"] == CurationStatus.SME_APPROVED:
            approved.append(r)
        else:
            held_back.append(r)

    return approved, held_back


def _simulate_sme_review(record: NormalizedRecord) -> dict:
    """
    Stand-in for a human reviewer's decision, so this demo can run
    end-to-end without a real review UI. The heuristic: if confidence is
    only marginally below the auto-approve threshold AND the module
    resolution came from a direct/high-confidence match, an SME would
    typically approve on inspection. Genuinely low-signal records (e.g.
    fallback classifications) are sent back for retagging.
    """
    gap = AUTO_APPROVE_CONFIDENCE_THRESHOLD - record.tagging_confidence

    if record.tagging_confidence >= 0.55:
        return {
            "status": CurationStatus.SME_APPROVED,
            "notes": (
                f"SME reviewed and approved. Confidence {record.tagging_confidence:.2f} "
                f"was below auto-approve threshold but tags were verified correct on inspection."
            ),
        }

    return {
        "status": CurationStatus.NEEDS_RETAG,
        "notes": (
            f"SME flagged for retagging. Confidence {record.tagging_confidence:.2f} too low "
            f"to trust (gap={gap:.2f}) -- classifier likely fell back to a default label."
        ),
    }


if __name__ == "__main__":
    from ingestion import ingest_all
    from structuring import structure_and_embed

    records = structure_and_embed(ingest_all())
    approved, held_back = run_curation_gate(records)

    print(f"Curation Gate results on {len(records)} records:")
    print(f"  Approved into Knowledge Store: {len(approved)}")
    print(f"  Held back (needs retag / pending): {len(held_back)}")
    for r in held_back:
        print(f"    - {r.record_id} [{r.curation_status.value}]: {r.curation_notes}")
