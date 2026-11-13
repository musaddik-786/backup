"""
Demonstrates the diagram's bottom row: "One Knowledge Base. Four Tier
Agents." -- the SAME knowledge_records table in Postgres, queried
differently by each tier agent's job.

  L1 Guided Resolution Agent
      A front-line agent is live with a customer/analyst right now and
      needs the single best-matching, already-proven fix FAST.
      -> tight filter (same module, same category), small top_k,
         prefers HistoricalTicket / RunbookOrSOP sources (proven, concrete
         steps) over narrative RCA text.

  L2 Resolution Intelligence Agent
      Looks for "this looks similar to a past issue" across a wider net
      -- still filtered to the module, but open to adjacent categories
      and higher top_k, so it can say "3 similar past incidents, here's
      the pattern."

  L3 Problem Ops & RCA Agent
      Doing root-cause / problem investigation, not a single-ticket fix.
      Filters to ChatOrRCAReport + GuidewireReleaseNote sources
      specifically (the "why", not the "how"), often across severity
      SEV1/SEV2 only, and widens the module filter to catch systemic
      patterns across related modules.

  L4 MIM Coordination Agent
      Active major incident. Needs SEV1 precedent + the MIM runbook
      immediately, ranked by relevance, no category filter (a live
      outage doesn't wait for perfect categorization).

Run with:
    Fill in .env (copy .env.example) with PG_HOST / PG_PORT / PG_DATABASE / PG_USER / PG_PASSWORD
    python query_demo.py
"""

from __future__ import annotations

import sys

from knowledge_store import get_connection, search_similar
from structuring import embed_query


def _print_results(label: str, query: str, results: list[dict]) -> None:
    print(f"\n--- {label} ---")
    print(f"Query: \"{query}\"")
    if not results:
        print("  (no matches)")
        return
    for r in results:
        print(
            f"  [{r['distance']:.3f}] {r['record_id']:<16} "
            f"{r['source_type']:<20} {r['module']:<16} {r['severity']:<13} "
            f"{r['resolution_type']:<16} -- {r['title']}"
        )


def l1_guided_resolution(conn, query: str, module: str) -> list[dict]:
    """Front-line: tight scope, proven fixes only, fast."""
    return search_similar(
        conn,
        embed_query(query),
        top_k=3,
        module=module,
        source_type_in=["HistoricalTicket", "RunbookOrSOP"],
    )


def l2_resolution_intelligence(conn, query: str, module: str) -> list[dict]:
    """Pattern-matching across more history, same module, wider net."""
    return search_similar(
        conn,
        embed_query(query),
        top_k=6,
        module=module,
    )


def l3_problem_ops_rca(conn, query: str) -> list[dict]:
    """Root-cause investigation: RCA/release-note sources, high severity only."""
    return search_similar(
        conn,
        embed_query(query),
        top_k=5,
        source_type_in=["ChatOrRCAReport", "GuidewireReleaseNote"],
        severity_in=["SEV1-Critical", "SEV2-High"],
    )


def l4_mim_coordination(conn, query: str) -> list[dict]:
    """Active major incident: SEV1 precedent + runbooks, no category filter."""
    return search_similar(
        conn,
        embed_query(query),
        top_k=5,
        severity_in=["SEV1-Critical"],
    )


def main() -> None:
    try:
        conn = get_connection()
    except RuntimeError as e:
        print(f"Cannot run query demo -- {e}")
        print("(Run `python pipeline.py` against a live Postgres+pgvector instance first.)")
        sys.exit(0)

    try:
        _print_results(
            "L1 Guided Resolution Agent",
            "underwriter sees new business submissions stuck in processing after upgrade",
            l1_guided_resolution(
                conn,
                "submission stuck in processing after upgrade",
                module="PolicyCenter",
            ),
        )

        _print_results(
            "L2 Resolution Intelligence Agent",
            "PolicyCenter issues similar to post-upgrade submission problems",
            l2_resolution_intelligence(
                conn, "post-upgrade submission and rating issues", module="PolicyCenter"
            ),
        )

        _print_results(
            "L3 Problem Ops & RCA Agent",
            "why do we keep seeing issues right after Guidewire upgrades",
            l3_problem_ops_rca(conn, "root cause of failures following a Guidewire upgrade"),
        )

        _print_results(
            "L4 MIM Coordination Agent",
            "PolicyCenter completely down, all app servers unresponsive",
            l4_mim_coordination(conn, "application unresponsive full outage all threads blocked"),
        )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
