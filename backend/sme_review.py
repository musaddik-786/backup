# # """
# # Human-in-the-loop SME review queue.

# # Every record coming out of pipeline.py / step3_populate_data.py lands in
# # its table as curation_status='PendingReview', embedding=NULL (see
# # curation.py -- nothing is auto-approved, ever). This script is the one
# # place a record can actually leave that state:

# #   - approve  -> curation_status='SMEApproved', embedding generated and
# #                 written in the SAME step (so a record is never marked
# #                 approved without actually being embedded right after).
# #                 Its content_hash is also registered in ingestion_state,
# #                 so the next refresh run doesn't re-tag it.
# #   - reject   -> curation_status='SMERejected'. No embedding, ever.
# #   - retag    -> curation_status='NeedsRetag'. No embedding. Deliberately
# #                 NOT registered in ingestion_state, so the next refresh
# #                 run retries tagging it instead of skipping it forever.
# #   - skip     -> leave it PendingReview, come back to it later.

# # Run:
# #     python sme_review.py
# #         Interactive: shows each pending record's tags/confidence/SOP,
# #         prompts [a]pprove / [r]eject / [n]eeds-retag / [s]kip / [q]uit.

# #     python sme_review.py --source tickets
# #         Only review pending records from one source (same keys as
# #         pipeline.py --sources: tickets, chat_rca, release_notes, runbooks).

# #     python sme_review.py --auto-approve-above 0.7
# #         Non-interactive: approves every pending record whose
# #         tagging_confidence is >= 0.7 and leaves the rest PendingReview,
# #         untouched, for a later interactive pass. This is still a real,
# #         explicit approval decision -- just rule-driven instead of typed
# #         in by hand -- and only ever runs when a human deliberately passes
# #         this flag; it is not a silent default the pipeline falls back to.
# # """

# # from __future__ import annotations

# # import argparse

# # from knowledge_store import (
# #     fetch_pending_review,
# #     get_connection,
# #     new_kb_version,
# #     register_approved_hash,
# #     set_curation_decision,
# #     update_embedding,
# # )
# # from structuring import fit_embeddings
# # from taxonomy import CurationStatus

# # SOURCE_KEY_TO_TABLE: dict[str, str] = {
# #     "tickets": "ticket_records",
# #     "chat_rca": "chat_rca_records",
# #     "release_notes": "release_note_records",
# #     "runbooks": "runbook_records",
# # }


# # def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
# #     parser = argparse.ArgumentParser(description="Human-in-the-loop SME review queue.")
# #     parser.add_argument(
# #         "--source",
# #         type=str,
# #         default=None,
# #         help=f"Only review one source: {{{','.join(SOURCE_KEY_TO_TABLE)}}}. Omit to review all 4.",
# #     )
# #     parser.add_argument(
# #         "--auto-approve-above",
# #         type=float,
# #         default=None,
# #         metavar="CONFIDENCE",
# #         help=(
# #             "Non-interactive: approve every pending record with "
# #             "tagging_confidence >= CONFIDENCE (0-1); leave the rest PendingReview."
# #         ),
# #     )
# #     return parser.parse_args(argv)


# # def _approve(conn, record: dict, notes: str, kb_version: str) -> None:
# #     table = record["table"]
# #     record_id = record["record_id"]
# #     embedding = fit_embeddings([f"{record['title']} {record['raw_text']}"])[0]
# #     update_embedding(conn, table, record_id, embedding)
# #     set_curation_decision(conn, table, record_id, CurationStatus.SME_APPROVED, notes)
# #     register_approved_hash(conn, table, record_id, kb_version)
# #     conn.commit()


# # def _reject(conn, record: dict, notes: str) -> None:
# #     set_curation_decision(conn, record["table"], record["record_id"], CurationStatus.SME_REJECTED, notes)


# # def _retag(conn, record: dict, notes: str) -> None:
# #     set_curation_decision(conn, record["table"], record["record_id"], CurationStatus.NEEDS_RETAG, notes)


# # def _print_record(record: dict) -> None:
# #     print("\n" + "=" * 70)
# #     print(f"[{record['table']}] {record['record_id']}  (source_ref_id={record['source_ref_id']})")
# #     print(f"Title: {record['title']}")
# #     print(
# #         f"Module={record['module']}  Category={record['category']}  "
# #         f"Severity={record['severity']}  Resolution={record['resolution_type']}"
# #     )
# #     print(f"tagging_confidence = {record['tagging_confidence']:.2f}")
# #     if record.get("tagging_reasoning"):
# #         print(f"LLM reasoning: {record['tagging_reasoning']}")
# #     print("-" * 70)
# #     print(record["structured_sop"])


# # def run_auto_approve(conn, pending: list[dict], threshold: float, kb_version: str) -> None:
# #     approved = 0
# #     for record in pending:
# #         if record["tagging_confidence"] >= threshold:
# #             notes = (
# #                 f"SME auto-approved via --auto-approve-above {threshold}: "
# #                 f"confidence {record['tagging_confidence']:.2f} >= {threshold}."
# #             )
# #             _approve(conn, record, notes, kb_version)
# #             print(f"  APPROVED  {record['table']:<22} {record['record_id']}  (conf={record['tagging_confidence']:.2f})")
# #             approved += 1
# #         else:
# #             print(f"  left pending {record['table']:<22} {record['record_id']}  (conf={record['tagging_confidence']:.2f})")
# #     print(f"\n{approved}/{len(pending)} record(s) approved + embedded. The rest remain PendingReview.")


# # def run_interactive(conn, pending: list[dict], kb_version: str) -> None:
# #     if not pending:
# #         print("Nothing pending review.")
# #         return

# #     print(f"{len(pending)} record(s) awaiting SME review.\n")
# #     for i, record in enumerate(pending, start=1):
# #         _print_record(record)
# #         while True:
# #             choice = input(
# #                 f"\n[{i}/{len(pending)}] Decision -- [a]pprove / [r]eject / [n]eeds-retag / [s]kip / [q]uit: "
# #             ).strip().lower()
# #             if choice in ("a", "approve"):
# #                 notes = input("  Approval notes (optional): ").strip() or "SME reviewed and approved."
# #                 _approve(conn, record, notes, kb_version)
# #                 print("  -> SMEApproved, embedding generated.")
# #                 break
# #             if choice in ("r", "reject"):
# #                 notes = input("  Rejection reason: ").strip() or "SME rejected."
# #                 _reject(conn, record, notes)
# #                 print("  -> SMERejected.")
# #                 break
# #             if choice in ("n", "retag", "needs-retag"):
# #                 notes = input("  Retag reason: ").strip() or "SME flagged for retagging."
# #                 _retag(conn, record, notes)
# #                 print("  -> NeedsRetag.")
# #                 break
# #             if choice in ("s", "skip"):
# #                 print("  -> left PendingReview.")
# #                 break
# #             if choice in ("q", "quit"):
# #                 print("\nStopping review early. Remaining records are untouched.")
# #                 return
# #             print("  Not a valid choice -- enter a/r/n/s/q.")


# # if __name__ == "__main__":
# #     args = _parse_args()
# #     kb_version = new_kb_version()

# #     conn = get_connection()
# #     try:
# #         pending = fetch_pending_review(conn)
# #         if args.source:
# #             table = SOURCE_KEY_TO_TABLE[args.source]
# #             pending = [r for r in pending if r["table"] == table]

# #         if args.auto_approve_above is not None:
# #             run_auto_approve(conn, pending, args.auto_approve_above, kb_version)
# #         else:
# #             run_interactive(conn, pending, kb_version)
# #     finally:
# #         conn.close()



# """
# Human-in-the-loop SME review queue.

# Every record coming out of pipeline.py / step3_populate_data.py lands in
# its table as curation_status='PendingReview', embedding=NULL (see
# curation.py -- nothing is auto-approved, ever). This script is the one
# place a record can actually leave that state:

#   - approve  -> curation_status='SMEApproved', embedding generated and
#                 written in the SAME step (so a record is never marked
#                 approved without actually being embedded right after).
#                 Its content_hash is also registered in ingestion_state,
#                 so the next refresh run doesn't re-tag it.
#   - reject   -> curation_status='SMERejected'. No embedding, ever.
#   - retag    -> curation_status='NeedsRetag'. No embedding. Deliberately
#                 NOT registered in ingestion_state, so the next refresh
#                 run retries tagging it instead of skipping it forever.
#   - skip     -> leave it PendingReview, come back to it later.

# Run:
#     python sme_review.py
#         Interactive: shows each pending record's tags/confidence/SOP,
#         prompts [a]pprove / [r]eject / [n]eeds-retag / [s]kip / [q]uit.

#     python sme_review.py --source tickets
#         Only review pending records from one source (same keys as
#         pipeline.py --sources: tickets, chat_rca, release_notes, runbooks).

#     python sme_review.py --auto-approve-above 0.7
#         Non-interactive: approves every pending record whose
#         tagging_confidence is >= 0.7 and leaves the rest PendingReview,
#         untouched, for a later interactive pass. This is still a real,
#         explicit approval decision -- just rule-driven instead of typed
#         in by hand -- and only ever runs when a human deliberately passes
#         this flag; it is not a silent default the pipeline falls back to.
# """

# from __future__ import annotations

# import argparse

# from knowledge_store import (
#     clear_ingestion_hash,
#     fetch_pending_review,
#     get_connection,
#     new_kb_version,
#     register_approved_hash,
#     set_curation_decision,
#     update_embedding,
# )
# from structuring import fit_embeddings
# from taxonomy import CurationStatus

# SOURCE_KEY_TO_TABLE: dict[str, str] = {
#     "tickets": "ticket_records",
#     "chat_rca": "chat_rca_records",
#     "release_notes": "release_note_records",
#     "runbooks": "runbook_records",
# }


# def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
#     parser = argparse.ArgumentParser(description="Human-in-the-loop SME review queue.")
#     parser.add_argument(
#         "--source",
#         type=str,
#         default=None,
#         help=f"Only review one source: {{{','.join(SOURCE_KEY_TO_TABLE)}}}. Omit to review all 4.",
#     )
#     parser.add_argument(
#         "--auto-approve-above",
#         type=float,
#         default=None,
#         metavar="CONFIDENCE",
#         help=(
#             "Non-interactive: approve every pending record with "
#             "tagging_confidence >= CONFIDENCE (0-1); leave the rest PendingReview."
#         ),
#     )
#     return parser.parse_args(argv)


# def _approve(conn, record: dict, notes: str, kb_version: str) -> None:
#     table = record["table"]
#     record_id = record["record_id"]
#     embedding = fit_embeddings([f"{record['title']} {record['raw_text']}"])[0]
#     update_embedding(conn, table, record_id, embedding)
#     set_curation_decision(conn, table, record_id, CurationStatus.SME_APPROVED, notes)
#     register_approved_hash(conn, table, record_id, kb_version)
#     conn.commit()


# def _reject(conn, record: dict, notes: str) -> None:
#     set_curation_decision(conn, record["table"], record["record_id"], CurationStatus.SME_REJECTED, notes)
#     clear_ingestion_hash(conn, record["record_id"])


# def _retag(conn, record: dict, notes: str) -> None:
#     set_curation_decision(conn, record["table"], record["record_id"], CurationStatus.NEEDS_RETAG, notes)
#     clear_ingestion_hash(conn, record["record_id"])


# def _print_record(record: dict) -> None:
#     print("\n" + "=" * 70)
#     print(f"[{record['table']}] {record['record_id']}  (source_ref_id={record['source_ref_id']})")
#     print(f"Title: {record['title']}")
#     print(
#         f"Module={record['module']}  Category={record['category']}  "
#         f"Severity={record['severity']}  Resolution={record['resolution_type']}"
#     )
#     print(f"tagging_confidence = {record['tagging_confidence']:.2f}")
#     if record.get("tagging_reasoning"):
#         print(f"LLM reasoning: {record['tagging_reasoning']}")
#     print("-" * 70)
#     print(record["structured_sop"])


# def run_auto_approve(conn, pending: list[dict], threshold: float, kb_version: str) -> None:
#     approved = 0
#     for record in pending:
#         if record["tagging_confidence"] >= threshold:
#             notes = (
#                 f"SME auto-approved via --auto-approve-above {threshold}: "
#                 f"confidence {record['tagging_confidence']:.2f} >= {threshold}."
#             )
#             _approve(conn, record, notes, kb_version)
#             print(f"  APPROVED  {record['table']:<22} {record['record_id']}  (conf={record['tagging_confidence']:.2f})")
#             approved += 1
#         else:
#             print(f"  left pending {record['table']:<22} {record['record_id']}  (conf={record['tagging_confidence']:.2f})")
#     print(f"\n{approved}/{len(pending)} record(s) approved + embedded. The rest remain PendingReview.")


# def run_interactive(conn, pending: list[dict], kb_version: str) -> None:
#     if not pending:
#         print("Nothing pending review.")
#         return

#     print(f"{len(pending)} record(s) awaiting SME review.\n")
#     for i, record in enumerate(pending, start=1):
#         _print_record(record)
#         while True:
#             choice = input(
#                 f"\n[{i}/{len(pending)}] Decision -- [a]pprove / [r]eject / [n]eeds-retag / [s]kip / [q]uit: "
#             ).strip().lower()
#             if choice in ("a", "approve"):
#                 notes = input("  Approval notes (optional): ").strip() or "SME reviewed and approved."
#                 _approve(conn, record, notes, kb_version)
#                 print("  -> SMEApproved, embedding generated.")
#                 break
#             if choice in ("r", "reject"):
#                 notes = input("  Rejection reason: ").strip() or "SME rejected."
#                 _reject(conn, record, notes)
#                 print("  -> SMERejected.")
#                 break
#             if choice in ("n", "retag", "needs-retag"):
#                 notes = input("  Retag reason: ").strip() or "SME flagged for retagging."
#                 _retag(conn, record, notes)
#                 print("  -> NeedsRetag.")
#                 break
#             if choice in ("s", "skip"):
#                 print("  -> left PendingReview.")
#                 break
#             if choice in ("q", "quit"):
#                 print("\nStopping review early. Remaining records are untouched.")
#                 return
#             print("  Not a valid choice -- enter a/r/n/s/q.")


# if __name__ == "__main__":
#     args = _parse_args()
#     kb_version = new_kb_version()

#     conn = get_connection()
#     try:
#         pending = fetch_pending_review(conn)
#         if args.source:
#             table = SOURCE_KEY_TO_TABLE[args.source]
#             pending = [r for r in pending if r["table"] == table]

#         if args.auto_approve_above is not None:
#             run_auto_approve(conn, pending, args.auto_approve_above, kb_version)
#         else:
#             run_interactive(conn, pending, kb_version)
#     finally:
#         conn.close()


"""
Human-in-the-loop SME review queue.

Every record coming out of pipeline.py / step3_populate_data.py lands in
its table as curation_status='PendingReview', embedding=NULL (see
curation.py -- nothing is auto-approved, ever). This script is the one
place a record can actually leave that state:

  - approve  -> curation_status='SMEApproved', embedding generated and
                written in the SAME step (so a record is never marked
                approved without actually being embedded right after).
                Its content_hash is also registered in ingestion_state,
                so the next refresh run doesn't re-tag it.
  - reject   -> curation_status='SMERejected'. No embedding, ever.
  - retag    -> curation_status='NeedsRetag'. No embedding. Deliberately
                NOT registered in ingestion_state, so the next refresh
                run retries tagging it instead of skipping it forever.
  - skip     -> leave it PendingReview, come back to it later.

Run:
    python sme_review.py
        Interactive: shows each pending record's tags/confidence/SOP,
        prompts [a]pprove / [r]eject / [n]eeds-retag / [s]kip / [q]uit.

    python sme_review.py --source tickets
        Only review pending records from one source (same keys as
        pipeline.py --sources: tickets, chat_rca, release_notes, runbooks).

    python sme_review.py --auto-approve-above 0.7
        Non-interactive: approves every pending record whose
        tagging_confidence is >= 0.7 and leaves the rest PendingReview,
        untouched, for a later interactive pass. This is still a real,
        explicit approval decision -- just rule-driven instead of typed
        in by hand -- and only ever runs when a human deliberately passes
        this flag; it is not a silent default the pipeline falls back to.
"""

from __future__ import annotations

import argparse

from knowledge_store import (
    clear_ingestion_hash,
    fetch_pending_review,
    get_connection,
    new_kb_version,
    register_approved_hash,
    set_curation_decision,
    update_embedding,
)
from structuring import fit_embeddings
from taxonomy import CurationStatus

SOURCE_KEY_TO_TABLE: dict[str, str] = {
    "tickets": "ticket_records",
    "chat_rca": "chat_rca_records",
    "release_notes": "release_note_records",
    "runbooks": "runbook_records",
}


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Human-in-the-loop SME review queue.")
    parser.add_argument(
        "--source",
        type=str,
        default=None,
        help=f"Only review one source: {{{','.join(SOURCE_KEY_TO_TABLE)}}}. Omit to review all 4.",
    )
    parser.add_argument(
        "--auto-approve-above",
        type=float,
        default=None,
        metavar="CONFIDENCE",
        help=(
            "Non-interactive: approve every pending record with "
            "tagging_confidence >= CONFIDENCE (0-1); leave the rest PendingReview."
        ),
    )
    return parser.parse_args(argv)


def _approve(conn, record: dict, notes: str, kb_version: str) -> None:
    table = record["table"]
    record_id = record["record_id"]
    embedding = fit_embeddings([record['raw_text']])[0]
    update_embedding(conn, table, record_id, embedding)
    # ticket_records only stores AutoApproved/PendingReview, never
    # SMEApproved -- so an approval here writes back AutoApproved instead.
    approved_status = CurationStatus.AUTO_APPROVED if table == "ticket_records" else CurationStatus.SME_APPROVED
    set_curation_decision(conn, table, record_id, approved_status, notes)
    register_approved_hash(conn, table, record_id, kb_version)
    conn.commit()


def _reject(conn, record: dict, notes: str) -> None:
    set_curation_decision(conn, record["table"], record["record_id"], CurationStatus.SME_REJECTED, notes)
    clear_ingestion_hash(conn, record["record_id"])


def _retag(conn, record: dict, notes: str) -> None:
    set_curation_decision(conn, record["table"], record["record_id"], CurationStatus.NEEDS_RETAG, notes)
    clear_ingestion_hash(conn, record["record_id"])


def _print_record(record: dict) -> None:
    print("\n" + "=" * 70)
    print(f"[{record['table']}] {record['record_id']}  (source_ref_id={record['source_ref_id']})")
    print(f"Title: {record['title']}")
    print(
        f"Module={record['module']}  Category={record['category']}  "
        f"Severity={record['severity']}  Resolution={record['resolution_type']}"
    )
    print(f"tagging_confidence = {record['tagging_confidence']:.2f}")
    if record.get("tagging_reasoning"):
        print(f"LLM reasoning: {record['tagging_reasoning']}")
    print("-" * 70)
    print(record["structured_sop"])


def run_auto_approve(conn, pending: list[dict], threshold: float, kb_version: str) -> None:
    approved = 0
    for record in pending:
        if record["tagging_confidence"] >= threshold:
            notes = (
                f"SME auto-approved via --auto-approve-above {threshold}: "
                f"confidence {record['tagging_confidence']:.2f} >= {threshold}."
            )
            _approve(conn, record, notes, kb_version)
            print(f"  APPROVED  {record['table']:<22} {record['record_id']}  (conf={record['tagging_confidence']:.2f})")
            approved += 1
        else:
            print(f"  left pending {record['table']:<22} {record['record_id']}  (conf={record['tagging_confidence']:.2f})")
    print(f"\n{approved}/{len(pending)} record(s) approved + embedded. The rest remain PendingReview.")


def run_interactive(conn, pending: list[dict], kb_version: str) -> None:
    if not pending:
        print("Nothing pending review.")
        return

    print(f"{len(pending)} record(s) awaiting SME review.\n")
    for i, record in enumerate(pending, start=1):
        _print_record(record)
        while True:
            choice = input(
                f"\n[{i}/{len(pending)}] Decision -- [a]pprove / [r]eject / [n]eeds-retag / [s]kip / [q]uit: "
            ).strip().lower()
            if choice in ("a", "approve"):
                notes = input("  Approval notes (optional): ").strip() or "SME reviewed and approved."
                _approve(conn, record, notes, kb_version)
                print("  -> SMEApproved, embedding generated.")
                break
            if choice in ("r", "reject"):
                notes = input("  Rejection reason: ").strip() or "SME rejected."
                _reject(conn, record, notes)
                print("  -> SMERejected.")
                break
            if choice in ("n", "retag", "needs-retag"):
                notes = input("  Retag reason: ").strip() or "SME flagged for retagging."
                _retag(conn, record, notes)
                print("  -> NeedsRetag.")
                break
            if choice in ("s", "skip"):
                print("  -> left PendingReview.")
                break
            if choice in ("q", "quit"):
                print("\nStopping review early. Remaining records are untouched.")
                return
            print("  Not a valid choice -- enter a/r/n/s/q.")


if __name__ == "__main__":
    args = _parse_args()
    kb_version = new_kb_version()

    conn = get_connection()
    try:
        pending = fetch_pending_review(conn)
        if args.source:
            table = SOURCE_KEY_TO_TABLE[args.source]
            pending = [r for r in pending if r["table"] == table]

        if args.auto_approve_above is not None:
            run_auto_approve(conn, pending, args.auto_approve_above, kb_version)
        else:
            run_interactive(conn, pending, kb_version)
    finally:
        conn.close()