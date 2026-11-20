"""Synthetic test cases for retrieval evaluation during development."""

from __future__ import annotations

from .evaluation import EvaluationTest


def get_synthetic_test_set() -> list[EvaluationTest]:
    """Return a small set of synthetic test cases for development."""
    return [
        EvaluationTest(
            test_id="SYN-001",
            new_ticket={
                "id": "T9999",
                "created": "2025-11-15",
                "short_description": "PolicyPeriod bind fails with timeout",
                "description": (
                    "When trying to bind a policy period in PolicyCenter, "
                    "the operation times out after 30 seconds. This happens for all users."
                ),
                "system": "PolicyCenter",
                "severity": "High",
                "ticket_type": "Incident",
            },
            oracle_similar_ids={"T0001", "T0002"},
            description="PolicyCenter bind timeout - should match tickets with similar issues",
        ),
        EvaluationTest(
            test_id="SYN-002",
            new_ticket={
                "id": "T9998",
                "created": "2025-11-14",
                "short_description": "Batch job terminates unexpectedly",
                "description": (
                    "The nightly batch processing job (BATCH-RENEWAL) terminates "
                    "without error message after running for 10 minutes. "
                    "No data is processed."
                ),
                "system": "PolicyCenter",
                "severity": "Critical",
                "ticket_type": "Incident",
            },
            oracle_similar_ids={"T0010", "T0011", "T0012"},
            description="Batch job failure - should match other batch termination issues",
        ),
        EvaluationTest(
            test_id="SYN-003",
            new_ticket={
                "id": "T9997",
                "created": "2025-11-13",
                "short_description": "Report generation slow",
                "description": (
                    "Claims report generation is running very slowly. "
                    "Queries that used to take 5 seconds now take 60+ seconds. "
                    "Database CPU is at 95%."
                ),
                "system": "ClaimCenter",
                "severity": "High",
            },
            oracle_similar_ids={"T0020"},
            description="Performance degradation - should match slow query issues",
        ),
        EvaluationTest(
            test_id="SYN-004",
            new_ticket={
                "id": "T9996",
                "short_description": "Authentication fails sporadically",
                "description": (
                    "Users report intermittent login failures with 'Session timeout' error. "
                    "Errors appear in auth logs but no clear cause identified."
                ),
                "system": "Authentication",
                "severity": "Medium",
            },
            oracle_similar_ids=set(),
            description="Intermittent auth issue - no clear match (should return no_confident_match)",
        ),
        EvaluationTest(
            test_id="SYN-005",
            new_ticket={
                "id": "T9995",
                "created": "2025-11-10",
                "short_description": "Integration timeout with external service",
                "description": (
                    "Calls to the external rating service (RatingEngine) are timing out. "
                    "The service responds on average in 2000ms. "
                    "Current timeout is 1500ms. Network latency is high."
                ),
                "system": "Integration",
                "severity": "High",
                "ticket_type": "Incident",
            },
            oracle_similar_ids={"T0030", "T0031"},
            description="Integration timeout - should match other timeout issues",
        ),
    ]


if __name__ == "__main__":
    tests = get_synthetic_test_set()
    for test in tests:
        print(f"{test.test_id}: {test.description}")
        print(f"  Oracle similar IDs: {test.oracle_similar_ids}")
        print()
