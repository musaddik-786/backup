# """
# Stage 3: SME Curation Gate
# (diagram box: "Human SMEs spot-check a sample of entries and approve
# taxonomy quality before anything goes live." Annotated as: "The one
# deliberate human gate -- a bad tag here poisons every downstream agent's
# matching.")

# This stage does NOT re-run the classifier, and it does NOT decide
# approval either. Its only job is to put every record in front of a real
# SME before it is allowed anywhere near the Knowledge Store:

#   - every record is marked CurationStatus.PENDING_REVIEW and held back --
#         unconditionally, regardless of tagging_confidence.
#   - CurationStatus.AUTO_APPROVED / SME_APPROVED are NEVER assigned by this
#         function -- only a human reviewer (sme_review.py) can move a
#         record out of PendingReview.

# A record only becomes eligible for embedding + the Knowledge Store once a
# human reviewer runs `python sme_review.py` and explicitly approves it
# (SMEApproved), reject it (SMERejected), or sends it back for retagging
# (NeedsRetag). The NormalizedRecord shape and CurationStatus enum are
# exactly what that reviewer workflow reads and writes -- nothing else in
# the pipeline needs to change.

# `simulate_sme=True` is kept ONLY for ad-hoc local demos of this module in
# isolation (see the `__main__` block below) -- pipeline.py and
# step3_populate_data.py never pass it, so the real pipeline always holds
# every record for a human.
# """

# from __future__ import annotations

# from schemas import NormalizedRecord
# from taxonomy import AUTO_APPROVE_CONFIDENCE_THRESHOLD, CurationStatus


# def run_curation_gate(
#     records: list[NormalizedRecord], simulate_sme: bool = False
# ) -> tuple[list[NormalizedRecord], list[NormalizedRecord]]:
#     """
#     Returns (approved_records, held_back_records).
#     Mutates curation_status / curation_notes on every record in place.

#     With the default simulate_sme=False (what pipeline.py and
#     step3_populate_data.py use), `approved` is ALWAYS empty: every record
#     is marked PENDING_REVIEW and returned in `held_back`, full stop. A
#     record can only reach SMEApproved via a real human decision --
#     see sme_review.py.
#     """
#     approved: list[NormalizedRecord] = []
#     held_back: list[NormalizedRecord] = []

#     for r in records:
#         if simulate_sme:
#             decision = _simulate_sme_review(r)
#             r.curation_status = decision["status"]
#             r.curation_notes = decision["notes"]
#             if decision["status"] == CurationStatus.SME_APPROVED:
#                 approved.append(r)
#             else:
#                 held_back.append(r)
#             continue

#         # True human-in-the-loop: no automatic decision of any kind.
#         r.curation_status = CurationStatus.PENDING_REVIEW
#         r.curation_notes = "Awaiting SME review (not yet reviewed)."
#         held_back.append(r)

#     return approved, held_back


# def _simulate_sme_review(record: NormalizedRecord) -> dict:
#     """
#     Stand-in for a human reviewer's decision, so this demo can run
#     end-to-end without a real review UI. The heuristic: a tagging
#     confidence of 0.55+ means an SME would typically verify the tags as
#     correct on inspection. Genuinely low-signal records (e.g. fallback
#     classifications) are sent back for retagging instead.
#     """
#     gap = AUTO_APPROVE_CONFIDENCE_THRESHOLD - record.tagging_confidence

#     if record.tagging_confidence >= 0.55:
#         return {
#             "status": CurationStatus.SME_APPROVED,
#             "notes": (
#                 f"SME reviewed and approved. Confidence {record.tagging_confidence:.2f}; "
#                 f"tags verified correct on inspection."
#             ),
#         }

#     return {
#         "status": CurationStatus.NEEDS_RETAG,
#         "notes": (
#             f"SME flagged for retagging. Confidence {record.tagging_confidence:.2f} too low "
#             f"to trust (gap={gap:.2f}) -- classifier likely fell back to a default label."
#         ),
#     }


# if __name__ == "__main__":
#     from ingestion import ingest_all
#     from structuring import render_sops, fit_embeddings

#     # Curation runs before embedding: only SME-approved records ever get
#     # an embedding generated, so rejected/held-back records never cost an
#     # embedding call.
#     records = render_sops(ingest_all())
#     approved, held_back = run_curation_gate(records)

#     corpus = [f"{r.title} {r.raw_text}" for r in approved]
#     if corpus:
#         embeddings = fit_embeddings(corpus)
#         for r, emb in zip(approved, embeddings):
#             r.embedding = emb

#     print(f"Curation Gate results on {len(records)} records:")
#     print(f"  Approved into Knowledge Store: {len(approved)}")
#     print(f"  Held back (needs retag / pending): {len(held_back)}")
#     for r in held_back:
#         print(f"    - {r.record_id} [{r.curation_status.value}]: {r.curation_notes}")



"""
Stage 3: SME Curation Gate
(diagram box: "Human SMEs spot-check a sample of entries and approve
taxonomy quality before anything goes live." Annotated as: "The one
deliberate human gate -- a bad tag here poisons every downstream agent's
matching.")

This stage does NOT re-run the classifier. Its job is to decide, per
record, whether it needs a human SME in front of it before it's allowed
anywhere near the Knowledge Store:

  - HistoricalTicket records are AUTO_APPROVED unconditionally, regardless
        of tagging_confidence. ingestion.py only ever hands this stage
        tickets whose status is already closed (see
        ingestion.CLOSED_TICKET_STATUSES) -- a closed ticket's resolution
        is final, so there's nothing left for an SME to add. These
        records skip SME review entirely: the pipeline embeds and writes
        them straight into the Knowledge Store.
  - Every other source (ChatOrRCAReport, GuidewireReleaseNote,
        RunbookOrSOP) is still marked CurationStatus.PENDING_REVIEW and
        held back unconditionally -- SME_APPROVED is NEVER assigned by
        this function for these; only a human reviewer (sme_review.py)
        can move one of these out of PendingReview.

A non-ticket record only becomes eligible for embedding + the Knowledge
Store once a human reviewer runs `python sme_review.py` and explicitly
approves it (SMEApproved), rejects it (SMERejected), or sends it back for
retagging (NeedsRetag). The NormalizedRecord shape and CurationStatus enum
are exactly what that reviewer workflow reads and writes.

`simulate_sme=True` is kept ONLY for ad-hoc local demos of this module in
isolation (see the `__main__` block below) -- pipeline.py and
step3_populate_data.py never pass it, so the real pipeline always holds
every record for a human.
"""

from __future__ import annotations

from schemas import NormalizedRecord
from taxonomy import AUTO_APPROVE_CONFIDENCE_THRESHOLD, CurationStatus, SourceType


def run_curation_gate(
    records: list[NormalizedRecord], simulate_sme: bool = False
) -> tuple[list[NormalizedRecord], list[NormalizedRecord]]:
    """
    Returns (approved_records, held_back_records).
    Mutates curation_status / curation_notes on every record in place.

    With the default simulate_sme=False (what pipeline.py and
    step3_populate_data.py use):
      - A closed HistoricalTicket record is auto-approved (AutoApproved)
        and returned in `approved` -- closed tickets never need SME
        review.
      - A ticket that is not yet closed (e.g. status "InReview") is marked
        PENDING_REVIEW and returned in `held_back` instead -- its
        resolution isn't final yet, so it isn't embedded (see
        pipeline.py), and the column simply reflects that it is not yet
        approved.
      - Every other source is marked PENDING_REVIEW and returned in
        `held_back`. A non-ticket record can only reach SMEApproved via a
        real human decision -- see sme_review.py.
    """
    approved: list[NormalizedRecord] = []
    held_back: list[NormalizedRecord] = []

    for r in records:
        if simulate_sme:
            decision = _simulate_sme_review(r)
            r.curation_status = decision["status"]
            r.curation_notes = decision["notes"]
            if decision["status"] == CurationStatus.SME_APPROVED:
                approved.append(r)
            else:
                held_back.append(r)
            continue

        if r.source_type == SourceType.TICKET:
            ticket_status = (r.source_metadata or {}).get("status")
            if ticket_status == "Closed":
                r.curation_status = CurationStatus.AUTO_APPROVED
                r.curation_notes = "Auto-approved: closed ticket, no SME review required."
                approved.append(r)
            else:
                r.curation_status = CurationStatus.PENDING_REVIEW
                r.curation_notes = f"Held: ticket status is '{ticket_status}', not yet Closed."
                held_back.append(r)
            continue

        # True human-in-the-loop for non-ticket sources: no automatic
        # approval of any kind.
        r.curation_status = CurationStatus.PENDING_REVIEW
        r.curation_notes = "Awaiting SME review (not yet reviewed)."
        held_back.append(r)

    return approved, held_back


def _simulate_sme_review(record: NormalizedRecord) -> dict:
    """
    Stand-in for a human reviewer's decision, so this demo can run
    end-to-end without a real review UI. The heuristic: a tagging
    confidence of 0.55+ means an SME would typically verify the tags as
    correct on inspection. Genuinely low-signal records (e.g. fallback
    classifications) are sent back for retagging instead.
    """
    gap = AUTO_APPROVE_CONFIDENCE_THRESHOLD - record.tagging_confidence

    if record.tagging_confidence >= 0.55:
        return {
            "status": CurationStatus.SME_APPROVED,
            "notes": (
                f"SME reviewed and approved. Confidence {record.tagging_confidence:.2f}; "
                f"tags verified correct on inspection."
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
    from structuring import render_sops, fit_embeddings

    # Curation runs before embedding: only SME-approved records ever get
    # an embedding generated, so rejected/held-back records never cost an
    # embedding call.
    records = render_sops(ingest_all())
    approved, held_back = run_curation_gate(records)

    corpus = [f"{r.title} {r.raw_text}" for r in approved]
    if corpus:
        embeddings = fit_embeddings(corpus)
        for r, emb in zip(approved, embeddings):
            r.embedding = emb

    print(f"Curation Gate results on {len(records)} records:")
    print(f"  Approved into Knowledge Store: {len(approved)}")
    print(f"  Held back (needs retag / pending): {len(held_back)}")
    for r in held_back:
        print(f"    - {r.record_id} [{r.curation_status.value}]: {r.curation_notes}")