# # """
# # Orchestrates the full pipeline exactly as laid out in the architecture
# # diagram:

# #     4 raw sources
# #         -> Ingestion & Normalization   (ingestion.py)
# #         -> Structuring Agent            (structuring.py)
# #         -> SME Curation Gate            (curation.py)
# #         -> Knowledge Store              (knowledge_store.py, Postgres+pgvector)

# # Run with:
# #     Fill in .env (copy .env.example) with PG_HOST / PG_PORT / PG_DATABASE / PG_USER / PG_PASSWORD
# #     python pipeline.py

# #     # Selective ingestion -- only refresh specific source(s), e.g. just
# #     # release notes, or tickets + chat/RCA. Omitting --sources (the
# #     # default) refreshes all 4, same as always.
# #     python pipeline.py --sources release_notes
# #     python pipeline.py --sources tickets,chat_rca

# #     # Duplicate-run prevention is on by default: a record whose raw
# #     # content hasn't changed since the last run that fully processed it is
# #     # skipped entirely (no LLM tagging call, no re-embedding). --force
# #     # bypasses that and re-processes every selected record regardless.
# #     python pipeline.py --force

# # This run NEVER writes an embedding and NEVER auto-approves anything: every
# # ingested record lands in its table as curation_status='PendingReview'
# # (embedding=NULL), invisible to search_similar() until a human reviews it:
# #     python sme_review.py
# # Only there does a record become SMEApproved/SMERejected/NeedsRetag, and
# # only on approval is its embedding generated.

# # This is the "refresh" job referenced in the diagram footer:
# # "refreshed continuously as tickets close and re-synced on every Guidewire
# # release cycle" -- in production this runs on a schedule (or is triggered
# # by a ticket-closed / release-published webhook) rather than manually.
# # """

# # from __future__ import annotations

# # import argparse
# # import sys

# # from curation import run_curation_gate
# # from ingestion import SOURCE_KEY_TO_TYPE, ingest_all
# # from knowledge_store import (
# #     fetch_ingestion_hashes,
# #     get_connection,
# #     init_schema,
# #     insert_records_no_embedding,
# #     log_curation_audit,
# #     log_refresh_run,
# #     new_kb_version,
# # )
# # from structuring import render_sops
# # from taxonomy import SourceType


# # def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
# #     parser = argparse.ArgumentParser(
# #         description="One-shot Knowledge Base refresh: ingest, structure+embed, SME-gate, write to Postgres."
# #     )
# #     parser.add_argument(
# #         "--sources",
# #         type=str,
# #         default=None,
# #         help=(
# #             f"Comma-separated subset of {{{','.join(SOURCE_KEY_TO_TYPE)}}} to refresh. "
# #             "Omit to refresh all 4 (default, unchanged behavior)."
# #         ),
# #     )
# #     parser.add_argument(
# #         "--force",
# #         action="store_true",
# #         help="Re-tag and re-embed every selected record even if unchanged since the last run.",
# #     )
# #     return parser.parse_args(argv)


# # def run_pipeline(sources: list[SourceType] | None = None, force: bool = False) -> None:
# #     print("=" * 70)
# #     print("KNOWLEDGE OPS AGENT -- KNOWLEDGE BASE REFRESH")
# #     print("=" * 70)

# #     kb_version = new_kb_version()

# #     # Connect early (the same connection is reused for Stage 4 below) so
# #     # already-unchanged records can skip re-tagging/re-embedding, not just
# #     # re-insertion. Graceful degradation is unchanged from before: if
# #     # Postgres isn't reachable, stages 1-3 still run and report results,
# #     # and the same "SKIPPED" message prints at the same point (Stage 4).
# #     conn = None
# #     conn_error: RuntimeError | None = None
# #     try:
# #         conn = get_connection()
# #         init_schema(conn)
# #     except RuntimeError as e:
# #         conn_error = e
# #         conn = None

# #     existing_hashes: dict[str, str] = {}
# #     if conn is not None and not force:
# #         existing_hashes = fetch_ingestion_hashes(conn)

# #     print(f"\n[1/4] Ingestion & Normalization  (kb_version={kb_version})")
# #     records = ingest_all(sources=sources, existing_hashes=existing_hashes)
# #     if not records:
# #         print("      -> nothing new or changed for the selected source(s)")
# #         print("\nNothing to refresh -- Knowledge Store is already up to date.")
# #         if conn is not None:
# #             conn.close()
# #         return
# #     print(f"      -> {len(records)} record(s) ingested/changed")

# #     print("\n[2/4] Structuring Agent (SOP rendering)")
# #     records = render_sops(records)
# #     print(f"      -> structured SOPs rendered for {len(records)} record(s)")

# #     print("\n[3/4] SME Curation Gate")
# #     # Every record is unconditionally held for human review here -- see
# #     # curation.py. `approved` is always empty; nothing is embedded in
# #     # this run. A record only becomes SMEApproved (and only then gets an
# #     # embedding) via a human running sme_review.py.
# #     approved, held_back = run_curation_gate(records)
# #     print(f"      -> {len(held_back)} record(s) flagged for SME review (0 auto-approved)")

# #     print("\n[4/4] Knowledge Store (PostgreSQL + pgvector)")
# #     if conn is None:
# #         print(f"      SKIPPED -- {conn_error}")
# #         print("\nPipeline stages 1-3 completed successfully (see above).")
# #         print("Connect a real Postgres+pgvector instance and re-run to complete stage 4.")
# #         sys.exit(0)

# #     try:
# #         # Written with embedding=NULL, curation_status='PendingReview' --
# #         # visible to a human reviewer but never retrievable (search_similar
# #         # only returns AutoApproved/SMEApproved rows).
# #         written = insert_records_no_embedding(conn, held_back, kb_version)
# #         log_curation_audit(conn, held_back)
# #         log_refresh_run(conn, kb_version, len(records), len(approved), len(held_back))
# #         for table, count in written.items():
# #             if count:
# #                 print(f"      -> {count} records written to {table} as PendingReview")
# #         print(f"      -> refresh run logged as {kb_version}")
# #     finally:
# #         conn.close()

# #     print(f"\n{len(held_back)} record(s) await SME review. Run: python sme_review.py")


# # if __name__ == "__main__":
# #     args = _parse_args()
# #     selected_sources = None
# #     if args.sources:
# #         selected_sources = [SOURCE_KEY_TO_TYPE[s.strip()] for s in args.sources.split(",")]
# #     run_pipeline(sources=selected_sources, force=args.force)








# """
# Orchestrates the full pipeline exactly as laid out in the architecture
# diagram:

#     4 raw sources
#         -> Ingestion & Normalization   (ingestion.py)
#         -> Structuring Agent            (structuring.py)
#         -> SME Curation Gate            (curation.py)
#         -> Knowledge Store              (knowledge_store.py, Postgres+pgvector)

# Run with:
#     Fill in .env (copy .env.example) with PG_HOST / PG_PORT / PG_DATABASE / PG_USER / PG_PASSWORD
#     python pipeline.py

#     # Selective ingestion -- only refresh specific source(s), e.g. just
#     # release notes, or tickets + chat/RCA. Omitting --sources (the
#     # default) refreshes all 4, same as always.
#     python pipeline.py --sources release_notes
#     python pipeline.py --sources tickets,chat_rca

#     # Duplicate-run prevention is on by default: a record whose raw
#     # content hasn't changed since the last run that fully processed it is
#     # skipped entirely (no LLM tagging call, no re-embedding). --force
#     # bypasses that and re-processes every selected record regardless.
#     python pipeline.py --force

# This run NEVER writes an embedding and NEVER auto-approves anything: every
# ingested record lands in its table as curation_status='PendingReview'
# (embedding=NULL), invisible to search_similar() until a human reviews it:
#     python sme_review.py
# Only there does a record become SMEApproved/SMERejected/NeedsRetag, and
# only on approval is its embedding generated.

# This is the "refresh" job referenced in the diagram footer:
# "refreshed continuously as tickets close and re-synced on every Guidewire
# release cycle" -- in production this runs on a schedule (or is triggered
# by a ticket-closed / release-published webhook) rather than manually.
# """

# from __future__ import annotations

# import argparse
# import sys

# from curation import run_curation_gate
# from ingestion import SOURCE_KEY_TO_TYPE, ingest_all
# from knowledge_store import (
#     fetch_ingestion_hashes,
#     get_connection,
#     init_schema,
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
#         description="One-shot Knowledge Base refresh: ingest, structure+embed, SME-gate, write to Postgres."
#     )
#     parser.add_argument(
#         "--sources",
#         type=str,
#         default=None,
#         help=(
#             f"Comma-separated subset of {{{','.join(SOURCE_KEY_TO_TYPE)}}} to refresh. "
#             "Omit to refresh all 4 (default, unchanged behavior)."
#         ),
#     )
#     parser.add_argument(
#         "--force",
#         action="store_true",
#         help="Re-tag and re-embed every selected record even if unchanged since the last run.",
#     )
#     return parser.parse_args(argv)


# def run_pipeline(sources: list[SourceType] | None = None, force: bool = False) -> None:
#     print("=" * 70)
#     print("KNOWLEDGE OPS AGENT -- KNOWLEDGE BASE REFRESH")
#     print("=" * 70)

#     kb_version = new_kb_version()

#     # Connect early (the same connection is reused for Stage 4 below) so
#     # already-unchanged records can skip re-tagging/re-embedding, not just
#     # re-insertion. Graceful degradation is unchanged from before: if
#     # Postgres isn't reachable, stages 1-3 still run and report results,
#     # and the same "SKIPPED" message prints at the same point (Stage 4).
#     conn = None
#     conn_error: RuntimeError | None = None
#     try:
#         conn = get_connection()
#         init_schema(conn)
#     except RuntimeError as e:
#         conn_error = e
#         conn = None

#     existing_hashes: dict[str, str] = {}
#     if conn is not None and not force:
#         existing_hashes = fetch_ingestion_hashes(conn)

#     print(f"\n[1/4] Ingestion & Normalization  (kb_version={kb_version})")
#     records = ingest_all(sources=sources, existing_hashes=existing_hashes)
#     if not records:
#         print("      -> nothing new or changed for the selected source(s)")
#         print("\nNothing to refresh -- Knowledge Store is already up to date.")
#         if conn is not None:
#             conn.close()
#         return
#     print(f"      -> {len(records)} record(s) ingested/changed")

#     print("\n[2/4] Structuring Agent (SOP rendering)")
#     records = render_sops(records)
#     print(f"      -> structured SOPs rendered for {len(records)} record(s)")

#     print("\n[3/4] SME Curation Gate")
#     # Every record is unconditionally held for human review here -- see
#     # curation.py. `approved` is always empty; nothing is embedded in
#     # this run. A record only becomes SMEApproved (and only then gets an
#     # embedding) via a human running sme_review.py.
#     approved, held_back = run_curation_gate(records)
#     print(f"      -> {len(held_back)} record(s) flagged for SME review (0 auto-approved)")

#     print("\n[4/4] Knowledge Store (PostgreSQL + pgvector)")
#     if conn is None:
#         print(f"      SKIPPED -- {conn_error}")
#         print("\nPipeline stages 1-3 completed successfully (see above).")
#         print("Connect a real Postgres+pgvector instance and re-run to complete stage 4.")
#         sys.exit(0)

#     try:
#         # Written with embedding=NULL, curation_status='PendingReview' --
#         # visible to a human reviewer but never retrievable (search_similar
#         # only returns AutoApproved/SMEApproved rows).
#         written = insert_records_no_embedding(conn, held_back, kb_version)
#         log_curation_audit(conn, held_back)
#         log_refresh_run(conn, kb_version, len(records), len(approved), len(held_back))
#         # Register every record's content hash now, at ingestion time --
#         # not only on SME approval. "Already processed by the pipeline"
#         # means "ingested + tagged this run", regardless of review status,
#         # so a still-PendingReview record isn't re-tagged on every future
#         # run just because no one has reviewed it yet. sme_review.py's
#         # own register_approved_hash() call on approval is now redundant
#         # for an unchanged record (same hash) but harmless to leave as is.
#         upsert_ingestion_hashes(conn, held_back, kb_version)
#         for table, count in written.items():
#             if count:
#                 print(f"      -> {count} records written to {table} as PendingReview")
#         print(f"      -> refresh run logged as {kb_version}")
#     finally:
#         conn.close()

#     print(f"\n{len(held_back)} record(s) await SME review. Run: python sme_review.py")


# if __name__ == "__main__":
#     args = _parse_args()
#     selected_sources = None
#     if args.sources:
#         selected_sources = [SOURCE_KEY_TO_TYPE[s.strip()] for s in args.sources.split(",")]
#     run_pipeline(sources=selected_sources, force=args.force)


"""
Orchestrates the full pipeline exactly as laid out in the architecture
diagram:

    4 raw sources
        -> Ingestion & Normalization   (ingestion.py)
        -> Structuring Agent            (structuring.py)
        -> SME Curation Gate            (curation.py)
        -> Knowledge Store              (knowledge_store.py, Postgres+pgvector)

Run with:
    Fill in .env (copy .env.example) with PG_HOST / PG_PORT / PG_DATABASE / PG_USER / PG_PASSWORD
    python pipeline.py

    # Selective ingestion -- only refresh specific source(s), e.g. just
    # release notes, or tickets + chat/RCA. Omitting --sources (the
    # default) refreshes all 4, same as always.
    python pipeline.py --sources release_notes
    python pipeline.py --sources tickets,chat_rca

    # Duplicate-run prevention is on by default: a record whose raw
    # content hasn't changed since the last run that fully processed it is
    # skipped entirely (no LLM tagging call, no re-embedding). --force
    # bypasses that and re-processes every selected record regardless.
    python pipeline.py --force

Ticket records (Closed or InReview status) are auto-approved right here in
this run -- no SME step for tickets at all (see curation.py). Only a
Closed ticket is actually embedded, though: its resolution is final, so
it's embedded and written straight in. An InReview ticket is still
auto-approved but isn't final yet, so it's written with embedding=NULL and
stays unretrievable until a later refresh sees it flip to Closed. Every
other source (chat/RCA, release notes, runbooks)
still lands in its table as curation_status='PendingReview'
(embedding=NULL), invisible to search_similar() until a human reviews it:
    python sme_review.py
Only there does one of those become SMEApproved/SMERejected/NeedsRetag,
and only on approval is its embedding generated.

This is the "refresh" job referenced in the diagram footer:
"refreshed continuously as tickets close and re-synced on every Guidewire
release cycle" -- in production this runs on a schedule (or is triggered
by a ticket-closed / release-published webhook) rather than manually.
"""

from __future__ import annotations

import argparse
import sys

from curation import run_curation_gate
from ingestion import SOURCE_KEY_TO_TYPE, ingest_all
from knowledge_store import (
    fetch_ingestion_hashes,
    get_connection,
    init_schema,
    insert_records_no_embedding,
    log_curation_audit,
    log_refresh_run,
    new_kb_version,
    upsert_ingestion_hashes,
    upsert_records,
)
from structuring import fit_embeddings, render_sops
from taxonomy import SourceType


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="One-shot Knowledge Base refresh: ingest, structure+embed, SME-gate, write to Postgres."
    )
    parser.add_argument(
        "--sources",
        type=str,
        default=None,
        help=(
            f"Comma-separated subset of {{{','.join(SOURCE_KEY_TO_TYPE)}}} to refresh. "
            "Omit to refresh all 4 (default, unchanged behavior)."
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-tag and re-embed every selected record even if unchanged since the last run.",
    )
    return parser.parse_args(argv)


def run_pipeline(sources: list[SourceType] | None = None, force: bool = False) -> None:
    print("=" * 70)
    print("KNOWLEDGE OPS AGENT -- KNOWLEDGE BASE REFRESH")
    print("=" * 70)

    kb_version = new_kb_version()

    # Connect early (the same connection is reused for Stage 4 below) so
    # already-unchanged records can skip re-tagging/re-embedding, not just
    # re-insertion. Graceful degradation is unchanged from before: if
    # Postgres isn't reachable, stages 1-3 still run and report results,
    # and the same "SKIPPED" message prints at the same point (Stage 4).
    conn = None
    conn_error: RuntimeError | None = None
    try:
        conn = get_connection()
        init_schema(conn)
    except RuntimeError as e:
        conn_error = e
        conn = None

    existing_hashes: dict[str, str] = {}
    if conn is not None and not force:
        existing_hashes = fetch_ingestion_hashes(conn)

    print(f"\n[1/4] Ingestion & Normalization  (kb_version={kb_version})")
    records = ingest_all(sources=sources, existing_hashes=existing_hashes)
    if not records:
        print("      -> nothing new or changed for the selected source(s)")
        print("\nNothing to refresh -- Knowledge Store is already up to date.")
        if conn is not None:
            conn.close()
        return
    print(f"      -> {len(records)} record(s) ingested/changed")

    print("\n[2/4] Structuring Agent (SOP rendering)")
    records = render_sops(records)
    print(f"      -> structured SOPs rendered for {len(records)} record(s)")

    print("\n[3/4] SME Curation Gate")
    # Tickets are auto-approved here (closed tickets never need SME
    # review); the other 3 sources are unconditionally held for human
    # review, same as before -- see curation.py.
    approved, held_back = run_curation_gate(records)
    if approved:
        print(f"      -> {len(approved)} ticket record(s) auto-approved (no SME review needed)")
    print(f"      -> {len(held_back)} record(s) flagged for SME review")

    print("\n[4/4] Knowledge Store (PostgreSQL + pgvector)")
    if conn is None:
        print(f"      SKIPPED -- {conn_error}")
        print("\nPipeline stages 1-3 completed successfully (see above).")
        print("Connect a real Postgres+pgvector instance and re-run to complete stage 4.")
        sys.exit(0)

    try:
        if approved:
            # Auto-approved ticket records are written straight into the
            # store here -- no PendingReview stop, no separate
            # sme_review.py step for these. But only a Closed ticket is
            # actually embedded: an InReview ticket is still auto-approved
            # (no SME review needed) but isn't final yet, so it's written
            # with embedding=NULL and stays unretrievable until a later
            # refresh sees it flip to Closed.
            embeddable = [r for r in approved if (r.source_metadata or {}).get("status", "Closed") == "Closed"]
            embeddable_ids = {r.record_id for r in embeddable}
            not_yet_embeddable = [r for r in approved if r.record_id not in embeddable_ids]

            if embeddable:
                embeddings = fit_embeddings([r.raw_text for r in embeddable])
                for r, emb in zip(embeddable, embeddings):
                    r.embedding = emb
                written_approved = upsert_records(conn, embeddable, kb_version)
                for table, count in written_approved.items():
                    if count:
                        print(f"      -> {count} records written to {table} as AutoApproved")

            if not_yet_embeddable:
                written_no_embed = insert_records_no_embedding(conn, not_yet_embeddable, kb_version)
                for table, count in written_no_embed.items():
                    if count:
                        print(f"      -> {count} records written to {table} as AutoApproved (InReview -- embedding not generated yet)")

        # Non-ticket records: written with embedding=NULL,
        # curation_status='PendingReview' -- visible to a human reviewer
        # but never retrievable (search_similar only returns
        # AutoApproved/SMEApproved rows).
        written = insert_records_no_embedding(conn, held_back, kb_version)
        log_curation_audit(conn, records)
        log_refresh_run(conn, kb_version, len(records), len(approved), len(held_back))
        # Register every record's content hash now, at ingestion time --
        # not only on SME approval. "Already processed by the pipeline"
        # means "ingested + tagged this run", regardless of review status,
        # so a still-PendingReview record isn't re-tagged on every future
        # run just because no one has reviewed it yet. sme_review.py's
        # own register_approved_hash() call on approval is now redundant
        # for an unchanged record (same hash) but harmless to leave as is.
        upsert_ingestion_hashes(conn, records, kb_version)
        for table, count in written.items():
            if count:
                print(f"      -> {count} records written to {table} as PendingReview")
        print(f"      -> refresh run logged as {kb_version}")
    finally:
        conn.close()

    if held_back:
        print(f"\n{len(held_back)} record(s) await SME review. Run: python sme_review.py")
    else:
        print("\nNo records await SME review.")


if __name__ == "__main__":
    args = _parse_args()
    selected_sources = None
    if args.sources:
        selected_sources = [SOURCE_KEY_TO_TYPE[s.strip()] for s in args.sources.split(",")]
    run_pipeline(sources=selected_sources, force=args.force)