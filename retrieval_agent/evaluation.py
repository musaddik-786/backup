from __future__ import annotations

import json
from typing import Any

from .pipeline import rank_candidates, retrieve
from .providers import Settings


class EvaluationTest:
    """Single evaluation test case with known similar tickets."""

    def __init__(
        self,
        test_id: str,
        new_ticket: dict[str, Any],
        oracle_similar_ids: set[str],
        description: str = "",
    ):
        self.test_id = test_id
        self.new_ticket = new_ticket
        self.oracle_similar_ids = oracle_similar_ids
        self.description = description


def recall_at_k(result: dict[str, Any], oracle_ids: set[str], k: int = 5) -> float:
    """Calculate recall@k: how many of the top-k matches are in the oracle set."""
    top_k_ids = {str(match["id"]) for match in result.get("matches", [])[:k]}
    if not oracle_ids:
        return 1.0 if not top_k_ids else 0.0
    return len(top_k_ids & oracle_ids) / len(oracle_ids)


def mean_reciprocal_rank(result: dict[str, Any], oracle_ids: set[str]) -> float:
    """Calculate MRR: 1/rank of the first relevant match."""
    matches = result.get("matches", [])
    for rank, match in enumerate(matches, start=1):
        if str(match["id"]) in oracle_ids:
            return 1.0 / rank
    return 0.0


def evaluate_retrieval(
    tests: list[EvaluationTest],
    all_records: list[dict[str, Any]],
    settings: Settings,
    embed_query: callable,
    complete_json: callable,
) -> dict[str, Any]:
    """Run evaluation suite and calculate metrics."""
    results = []
    recall_scores = []
    mrr_scores = []
    no_match_precision = 0
    total_no_match_cases = 0

    for test in tests:
        try:
            result = retrieve(
                test.new_ticket,
                all_records,
                settings,
                embed_query,
                complete_json,
            )
            recall = recall_at_k(result, test.oracle_similar_ids, k=5)
            mrr = mean_reciprocal_rank(result, test.oracle_similar_ids)
            is_correct_no_match = (
                result["no_confident_match"] and len(test.oracle_similar_ids) == 0
            )

            recall_scores.append(recall)
            mrr_scores.append(mrr)
            if len(test.oracle_similar_ids) == 0:
                total_no_match_cases += 1
                if is_correct_no_match:
                    no_match_precision += 1

            results.append({
                "test_id": test.test_id,
                "description": test.description,
                "recall@5": recall,
                "mrr": mrr,
                "matches_returned": len(result.get("matches", [])),
                "oracle_size": len(test.oracle_similar_ids),
                "no_confident_match": result.get("no_confident_match", False),
            })
        except Exception as exc:
            results.append({
                "test_id": test.test_id,
                "description": test.description,
                "error": str(exc),
            })

    avg_recall = sum(recall_scores) / len(recall_scores) if recall_scores else 0.0
    avg_mrr = sum(mrr_scores) / len(mrr_scores) if mrr_scores else 0.0
    no_match_prec = (
        no_match_precision / total_no_match_cases if total_no_match_cases > 0 else None
    )

    return {
        "num_tests": len(tests),
        "num_errors": len([r for r in results if "error" in r]),
        "recall@5_mean": avg_recall,
        "recall@5_min": min(recall_scores) if recall_scores else None,
        "recall@5_max": max(recall_scores) if recall_scores else None,
        "mrr_mean": avg_mrr,
        "mrr_min": min(mrr_scores) if mrr_scores else None,
        "mrr_max": max(mrr_scores) if mrr_scores else None,
        "no_match_precision": no_match_prec,
        "results_per_test": results,
    }


def print_evaluation_report(eval_result: dict[str, Any]) -> None:
    """Print a human-readable evaluation report."""
    print("\n" + "=" * 70)
    print("RETRIEVAL EVALUATION REPORT")
    print("=" * 70)
    print(f"Tests run: {eval_result['num_tests']}")
    print(f"Errors: {eval_result['num_errors']}")
    print()

    print("METRICS:")
    print(f"  Recall@5 (mean): {eval_result['recall@5_mean']:.3f}")
    if eval_result['recall@5_min'] is not None:
        print(f"    min: {eval_result['recall@5_min']:.3f}, max: {eval_result['recall@5_max']:.3f}")
    print(f"  MRR (mean): {eval_result['mrr_mean']:.3f}")
    if eval_result['mrr_min'] is not None:
        print(f"    min: {eval_result['mrr_min']:.3f}, max: {eval_result['mrr_max']:.3f}")
    if eval_result['no_match_precision'] is not None:
        print(f"  No-match precision: {eval_result['no_match_precision']:.3f}")
    print()

    print("PER-TEST RESULTS:")
    for test in eval_result["results_per_test"]:
        if "error" in test:
            print(f"  {test['test_id']}: ERROR - {test['error']}")
        else:
            print(f"  {test['test_id']}: recall@5={test['recall@5']:.3f}, "
                  f"mrr={test['mrr']:.3f}, matches={test['matches_returned']}/{test['oracle_size']}")
    print()
    print("=" * 70)
