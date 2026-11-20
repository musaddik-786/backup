# # """
# # STEP 3 of 4: Ingest the 4 raw sources, tag them, render SOP text, run
# # the SME Curation Gate, and INSERT every one of them into their tables as
# # curation_status='PendingReview', embedding=NULL. Nothing is auto-approved
# # here -- see curation.py. A record only becomes SMEApproved (and only
# # then eligible for step4's embedding pass) once a human runs
# # `python sme_review.py` and approves it.

# # Run:
# #     python step3_populate_data.py

# #     # Selective ingestion -- only process specific source(s). Omitting
# #     # --sources (the default) processes all 4, same as always.
# #     python step3_populate_data.py --sources release_notes
# #     python step3_populate_data.py --sources tickets,chat_rca

# #     # Duplicate-run prevention is on by default: a record whose raw
# #     # content hasn't changed since the last run that fully processed it is
# #     # skipped entirely (no LLM tagging call). --force bypasses that and
# #     # re-tags every selected record regardless.
# #     python step3_populate_data.py --force
# # """

# # import argparse

# # from curation import run_curation_gate
# # from ingestion import SOURCE_KEY_TO_TYPE, ingest_all
# # from knowledge_store import (
# #     fetch_ingestion_hashes,
# #     get_connection,
# #     insert_records_no_embedding,
# #     log_curation_audit,
# #     log_refresh_run,
# #     new_kb_version,
# # )
# # from structuring import render_sops
# # from taxonomy import SourceType


# # def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
# #     parser = argparse.ArgumentParser(
# #         description="Step 3: ingest + tag + curate + insert (no embeddings yet)."
# #     )
# #     parser.add_argument(
# #         "--sources",
# #         type=str,
# #         default=None,
# #         help=(
# #             f"Comma-separated subset of {{{','.join(SOURCE_KEY_TO_TYPE)}}} to ingest. "
# #             "Omit to ingest all 4 (default, unchanged behavior)."
# #         ),
# #     )
# #     parser.add_argument(
# #         "--force",
# #         action="store_true",
# #         help="Re-tag every selected record even if unchanged since the last run.",
# #     )
# #     return parser.parse_args(argv)


# # if __name__ == "__main__":
# #     args = _parse_args()
# #     selected_sources: list[SourceType] | None = None
# #     if args.sources:
# #         selected_sources = [SOURCE_KEY_TO_TYPE[s.strip()] for s in args.sources.split(",")]

# #     kb_version = new_kb_version()

# #     conn = get_connection()
# #     try:
# #         existing_hashes = {} if args.force else fetch_ingestion_hashes(conn)

# #         print("Ingesting + tagging selected source(s)..." if selected_sources else "Ingesting + tagging all 4 sources...")
# #         records = ingest_all(sources=selected_sources, existing_hashes=existing_hashes)
# #         print(f"  -> {len(records)} record(s) need (re)processing this run")

# #         if not records:
# #             print("Nothing new or changed -- nothing to render, curate, or insert.")
# #         else:
# #             print("Rendering structured SOP text (no embeddings yet)...")
# #             records = render_sops(records)

# #             print("Running SME Curation Gate...")
# #             # simulate_sme defaults to False: every record is held as
# #             # PENDING_REVIEW, `approved` is always empty. Nothing is
# #             # auto-approved -- a human must run sme_review.py.
# #             approved, held_back = run_curation_gate(records)
# #             print(f"  -> {len(held_back)} record(s) flagged for SME review (0 auto-approved)")

# #             written = insert_records_no_embedding(conn, held_back, kb_version)
# #             log_curation_audit(conn, held_back)
# #             log_refresh_run(conn, kb_version, len(records), len(approved), len(held_back))

# #             for table, count in written.items():
# #                 if count:
# #                     print(f"  -> {count} rows inserted into {table} as PendingReview (embedding = NULL)")
# #     finally:
# #         conn.close()

# #     print("\nStep 3 complete. All records are PendingReview; embeddings are NOT set.")
# #     print("Next: python sme_review.py  (approve/reject before step4_store_embeddings.py)")


# """
# STEP 3 of 4: Ingest the 4 raw sources, tag them, render SOP text, run
# the SME Curation Gate, and INSERT every one of them into their tables as
# curation_status='PendingReview', embedding=NULL. Nothing is auto-approved
# here -- see curation.py. A record only becomes SMEApproved (and only
# then eligible for step4's embedding pass) once a human runs
# `python sme_review.py` and approves it.

# Run:
#     python step3_populate_data.py

#     # Selective ingestion -- only process specific source(s). Omitting
#     # --sources (the default) processes all 4, same as always.
#     python step3_populate_data.py --sources release_notes
#     python step3_populate_data.py --sources tickets,chat_rca

#     # Duplicate-run prevention is on by default: a record whose raw
#     # content hasn't changed since the last run that fully processed it is
#     # skipped entirely (no LLM tagging call). --force bypasses that and
#     # re-tags every selected record regardless.
#     python step3_populate_data.py --force
# """

# import argparse

# from curation import run_curation_gate
# from ingestion import SOURCE_KEY_TO_TYPE, ingest_all
# from knowledge_store import (
#     fetch_ingestion_hashes,
#     get_connection,
#     insert_records_no_embedding,
#     log_curation_audit,
#     log_refresh_run,
#     new_kb_version,
#     upsert_ingestion_hashes,
# )
# from structuring import render_sops
# from taxonomy import SourceType


# def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
#     parser = argparse.ArgumentParser(
#         description="Step 3: ingest + tag + curate + insert (no embeddings yet)."
#     )
#     parser.add_argument(
#         "--sources",
#         type=str,
#         default=None,
#         help=(
#             f"Comma-separated subset of {{{','.join(SOURCE_KEY_TO_TYPE)}}} to ingest. "
#             "Omit to ingest all 4 (default, unchanged behavior)."
#         ),
#     )
#     parser.add_argument(
#         "--force",
#         action="store_true",
#         help="Re-tag every selected record even if unchanged since the last run.",
#     )
#     return parser.parse_args(argv)


# if __name__ == "__main__":
#     args = _parse_args()
#     selected_sources: list[SourceType] | None = None
#     if args.sources:
#         selected_sources = [SOURCE_KEY_TO_TYPE[s.strip()] for s in args.sources.split(",")]

#     kb_version = new_kb_version()

#     conn = get_connection()
#     try:
#         existing_hashes = {} if args.force else fetch_ingestion_hashes(conn)

#         print("Ingesting + tagging selected source(s)..." if selected_sources else "Ingesting + tagging all 4 sources...")
#         records = ingest_all(sources=selected_sources, existing_hashes=existing_hashes)
#         print(f"  -> {len(records)} record(s) need (re)processing this run")

#         if not records:
#             print("Nothing new or changed -- nothing to render, curate, or insert.")
#         else:
#             print("Rendering structured SOP text (no embeddings yet)...")
#             records = render_sops(records)

#             print("Running SME Curation Gate...")
#             # simulate_sme defaults to False: every record is held as
#             # PENDING_REVIEW, `approved` is always empty. Nothing is
#             # auto-approved -- a human must run sme_review.py.
#             approved, held_back = run_curation_gate(records)
#             print(f"  -> {len(held_back)} record(s) flagged for SME review (0 auto-approved)")

#             written = insert_records_no_embedding(conn, held_back, kb_version)
#             log_curation_audit(conn, held_back)
#             log_refresh_run(conn, kb_version, len(records), len(approved), len(held_back))
#             # Register every record's content hash now, at ingestion time
#             # -- not only on SME approval -- so a still-PendingReview
#             # record isn't re-tagged on every future run just because no
#             # one has reviewed it yet.
#             upsert_ingestion_hashes(conn, held_back, kb_version)

#             for table, count in written.items():
#                 if count:
#                     print(f"  -> {count} rows inserted into {table} as PendingReview (embedding = NULL)")
#     finally:
#         conn.close()

#     print("\nStep 3 complete. All records are PendingReview; embeddings are NOT set.")
#     print("Next: python sme_review.py  (approve/reject before step4_store_embeddings.py)")


"""
STEP 3 of 4: Ingest the 4 raw sources, tag them, render SOP text, run
the SME Curation Gate, and INSERT every one of them into their tables
with embedding=NULL. Ticket records are auto-approved here (closed
tickets never need SME review -- see curation.py) and inserted as
AutoApproved; the other 3 sources are inserted as PendingReview and only
become SMEApproved once a human runs `python sme_review.py`. Either way,
embedding generation is step4's job -- it picks up any AutoApproved/
SMEApproved row that still has embedding=NULL, tickets included.

Run:
    python step3_populate_data.py

    # Selective ingestion -- only process specific source(s). Omitting
    # --sources (the default) processes all 4, same as always.
    python step3_populate_data.py --sources release_notes
    python step3_populate_data.py --sources tickets,chat_rca

    # Duplicate-run prevention is on by default: a record whose raw
    # content hasn't changed since the last run that fully processed it is
    # skipped entirely (no LLM tagging call). --force bypasses that and
    # re-tags every selected record regardless.
    python step3_populate_data.py --force
"""

import argparse

from curation import run_curation_gate
from ingestion import SOURCE_KEY_TO_TYPE, ingest_all
from knowledge_store import (
    fetch_ingestion_hashes,
    get_connection,
    insert_records_no_embedding,
    log_curation_audit,
    log_refresh_run,
    new_kb_version,
    upsert_ingestion_hashes,
)
from structuring import render_sops
from taxonomy import SourceType


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Step 3: ingest + tag + curate + insert (no embeddings yet)."
    )
    parser.add_argument(
        "--sources",
        type=str,
        default=None,
        help=(
            f"Comma-separated subset of {{{','.join(SOURCE_KEY_TO_TYPE)}}} to ingest. "
            "Omit to ingest all 4 (default, unchanged behavior)."
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-tag every selected record even if unchanged since the last run.",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = _parse_args()
    selected_sources: list[SourceType] | None = None
    if args.sources:
        selected_sources = [SOURCE_KEY_TO_TYPE[s.strip()] for s in args.sources.split(",")]

    kb_version = new_kb_version()

    conn = get_connection()
    try:
        existing_hashes = {} if args.force else fetch_ingestion_hashes(conn)

        print("Ingesting + tagging selected source(s)..." if selected_sources else "Ingesting + tagging all 4 sources...")
        records = ingest_all(sources=selected_sources, existing_hashes=existing_hashes)
        print(f"  -> {len(records)} record(s) need (re)processing this run")

        if not records:
            print("Nothing new or changed -- nothing to render, curate, or insert.")
        else:
            print("Rendering structured SOP text (no embeddings yet)...")
            records = render_sops(records)

            print("Running SME Curation Gate...")
            # simulate_sme defaults to False: ticket records are
            # auto-approved (closed tickets, no SME needed); every other
            # record is held as PENDING_REVIEW for a human.
            approved, held_back = run_curation_gate(records)
            if approved:
                print(f"  -> {len(approved)} ticket record(s) auto-approved")
            print(f"  -> {len(held_back)} record(s) flagged for SME review")

            # Both groups are inserted with embedding=NULL -- step4 backfills
            # embeddings for anything AutoApproved/SMEApproved, tickets included.
            written = insert_records_no_embedding(conn, records, kb_version)
            log_curation_audit(conn, records)
            log_refresh_run(conn, kb_version, len(records), len(approved), len(held_back))
            # Register every record's content hash now, at ingestion time
            # -- not only on SME approval -- so a still-PendingReview
            # record isn't re-tagged on every future run just because no
            # one has reviewed it yet.
            upsert_ingestion_hashes(conn, records, kb_version)

            for table, count in written.items():
                if count:
                    print(f"  -> {count} rows inserted into {table} (embedding = NULL)")
    finally:
        conn.close()

    print("\nStep 3 complete. Embeddings are NOT set yet.")
    print("Next: python sme_review.py for pending non-ticket records, then")
    print("      python step4_store_embeddings.py to backfill embeddings for everything approved.")