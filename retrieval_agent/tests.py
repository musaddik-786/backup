from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from . import main
from .database import _vector_text, to_candidate, _parse_vector
from .pipeline import _temporal_boost, bm25_scores, rank_candidates, retrieve
from .providers import EMBEDDING_DIMENSIONS, Settings, embed_texts


def settings(**overrides) -> Settings:
    values = {
        "pg_host": "",
        "pg_port": 5432,
        "pg_database": "",
        "pg_user": "",
        "pg_password": "",
        "pg_sslmode": "require",
        "endpoint": "",
        "api_key": "",
        "api_version": "",
        "embedding_deployment": "",
        "chat_deployment": "",
        "min_vector_score": 0.68,
        "min_bm25_score": 1.0,
        "rrf_constant": 60,
        "temporal_boost_days": 365,
        "temporal_boost_magnitude": 0.02,
    }
    values.update(overrides)
    return Settings(**values)


class TicketRecordMappingTests(unittest.TestCase):
    def row(self, **over):
        row = {
            "source_ref_id": "INC-1", "title": "Bind fails", "raw_text": "Bind operation throws error",
            "module": "PolicyCenter", "severity": "High", "category": "UpgradeRegression",
            "resolution_type": "CodeFix", "resolution": "Patch rule", "event_date": "2025-01-02",
            "source_metadata": {"created_at": "2025-01-01T00:00:00", "assignee_team": "PC L2"},
            "embedding": json.dumps([0.0] * EMBEDDING_DIMENSIONS),
        }
        row.update(over)
        return row

    def test_ticket_records_row_maps_to_pipeline_record(self):
        record = to_candidate(self.row())
        self.assertEqual(record["ticket_id"], "INC-1")
        self.assertEqual(record["system"], "PolicyCenter")
        self.assertEqual(record["description"], "Bind operation throws error")
        self.assertEqual(record["source_metadata"]["summary"], "Bind fails")
        self.assertEqual(record["created"], "2025-01-01T00:00:00")
        self.assertTrue(record["retrieval_eligible"])
        self.assertEqual(len(record["embedding"]), EMBEDDING_DIMENSIONS)

    def test_json_string_metadata_and_event_date_fallback(self):
        record = to_candidate(self.row(source_metadata='{"x": 1}'))
        self.assertEqual(record["source_metadata"]["x"], 1)
        self.assertEqual(record["created"], "2025-01-02")

    def test_wrong_stored_dimension_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "expected"):
            _parse_vector(json.dumps([0.1, 0.2]))


class EvidenceAndRetrievalTests(unittest.TestCase):

    def test_embedding_dimension_is_enforced_at_provider_and_database_boundaries(self):
        wrong_size = [0.0] * (EMBEDDING_DIMENSIONS - 1)
        with patch("retrieval_agent.providers._post_json", return_value={
            "data": [{"index": 0, "embedding": wrong_size}],
        }):
            with self.assertRaisesRegex(RuntimeError, f"exactly {EMBEDDING_DIMENSIONS} dimensions"):
                embed_texts(
                    settings(
                        endpoint="https://example.test",
                        api_key="test-key",
                        api_version="test-version",
                        embedding_deployment="test-embedding",
                    ),
                    ["test"],
                )
        with self.assertRaisesRegex(ValueError, f"exactly {EMBEDDING_DIMENSIONS}"):
            _vector_text(wrong_size)


    def test_bm25_finds_exact_failure_terms(self):
        scores = bm25_scores(
            "PolicyPeriod bind GosuRuntimeException",
            ["PolicyPeriod bind GosuRuntimeException", "invoice reconciliation autopay"],
        )
        self.assertGreater(scores[0], scores[1])

    def test_hybrid_rank_fuses_relevant_candidates_and_applies_threshold(self):
        records = [
            {
                "ticket_id": "T1", "description": "PolicyPeriod bind error",
                "system": "PolicyCenter", "ticket_type": "Incident",
                "retrieval_eligible": True, "embedding": [1.0, 0.0],
                "resolution": None, "rca": None,
                "source_metadata": {},
            },
            {
                "ticket_id": "T2", "description": "Unrelated billing invoice",
                "system": "BillingCenter", "ticket_type": "Incident",
                "retrieval_eligible": True, "embedding": [0.0, 1.0],
                "resolution": None, "rca": None,
                "source_metadata": {},
            },
            {
                "ticket_id": "T3", "description": "PolicyPeriod bind error",
                "system": "PolicyCenter", "ticket_type": "Incident",
                "retrieval_eligible": False, "embedding": [1.0, 0.0],
                "resolution": None, "rca": None,
                "source_metadata": {},
            },
        ]
        ranked = rank_candidates(
            {"description": "PolicyPeriod bind error", "system": "PolicyCenter"},
            records,
            [1.0, 0.0],
            settings(),
        )
        self.assertEqual([row["id"] for row in ranked], ["T1"])
        self.assertGreater(ranked[0]["bm25_score"], 0)
        self.assertIn("resolution", ranked[0])

    def test_model_cannot_return_an_unretrieved_ticket_id(self):
        records = [{
            "ticket_id": "T1", "description": "PolicyPeriod bind error",
            "system": "PolicyCenter", "ticket_type": "Incident",
            "retrieval_eligible": True, "embedding": [1.0, 0.0],
            "resolution": None, "rca": None,
            "source_metadata": {},
        }]

        def fake_completion(system_prompt, user_prompt):
            self.assertTrue(system_prompt)
            self.assertEqual(json.loads(user_prompt)["candidates"][0]["id"], "T1")
            return {
                "new_ticket_assessment": {
                    "itil_type": "Incident", "suggested_level": "L2",
                    "system": "PolicyCenter", "failure_pattern": "bind error",
                    "confidence": "medium",
                },
                "matches": [{"id": "NOT-RETRIEVED", "match_strength": "strong"}],
                "no_confident_match": False,
                "suggested_next_step": "Review logs",
            }

        with self.assertRaisesRegex(RuntimeError, "outside the retrieved candidates"):
            retrieve(
                {"description": "PolicyPeriod bind error"},
                records,
                settings(),
                lambda query: [1.0, 0.0] if query else [0.0, 1.0],
                fake_completion,
            )

    def _records_and_valid_completion(self):
        records = [{
            "ticket_id": "T1", "description": "PolicyPeriod bind error",
            "system": "PolicyCenter", "ticket_type": "Incident",
            "retrieval_eligible": True, "embedding": [1.0, 0.0],
            "resolution": None, "rca": None,
            "source_metadata": {},
        }]
        completion = {
            "new_ticket_assessment": {
                "itil_type": "Incident", "suggested_level": "L2",
                "system": "PolicyCenter", "failure_pattern": "bind error",
                "confidence": "medium",
            },
            "matches": [{
                "id": "T1", "match_strength": "strong",
                "why_similar": "Same bind symptom", "what_differs": "not recorded",
                "reusable_info": "not recorded", "missing_info": [],
            }],
            "no_confident_match": False,
            "suggested_next_step": "Compare bind logs",
        }
        return records, completion

    def test_missing_assessment_field_is_rejected(self):
        records, completion = self._records_and_valid_completion()
        del completion["new_ticket_assessment"]["failure_pattern"]
        with self.assertRaisesRegex(RuntimeError, "incomplete ticket assessment"):
            retrieve(
                {"description": "PolicyPeriod bind error"}, records, settings(),
                lambda query: [1.0, 0.0] if query else [0.0, 1.0],
                lambda system_prompt, user_prompt: completion,
            )

    def test_invalid_itil_type_is_rejected(self):
        records, completion = self._records_and_valid_completion()
        completion["new_ticket_assessment"]["itil_type"] = "Bug"
        with self.assertRaisesRegex(RuntimeError, "unsupported ITIL type"):
            retrieve(
                {"description": "PolicyPeriod bind error"}, records, settings(),
                lambda query: [1.0, 0.0] if query else [0.0, 1.0],
                lambda system_prompt, user_prompt: completion,
            )

    def test_invalid_suggested_level_is_rejected(self):
        records, completion = self._records_and_valid_completion()
        completion["new_ticket_assessment"]["suggested_level"] = "L5"
        with self.assertRaisesRegex(RuntimeError, "unsupported support level"):
            retrieve(
                {"description": "PolicyPeriod bind error"}, records, settings(),
                lambda query: [1.0, 0.0] if query else [0.0, 1.0],
                lambda system_prompt, user_prompt: completion,
            )

    def test_invalid_confidence_is_rejected(self):
        records, completion = self._records_and_valid_completion()
        completion["new_ticket_assessment"]["confidence"] = "certain"
        with self.assertRaisesRegex(RuntimeError, "unsupported confidence"):
            retrieve(
                {"description": "PolicyPeriod bind error"}, records, settings(),
                lambda query: [1.0, 0.0] if query else [0.0, 1.0],
                lambda system_prompt, user_prompt: completion,
            )

    def test_assessment_not_a_dict_is_rejected(self):
        records, completion = self._records_and_valid_completion()
        completion["new_ticket_assessment"] = "Incident"
        with self.assertRaisesRegex(RuntimeError, "did not match the retrieval response schema"):
            retrieve(
                {"description": "PolicyPeriod bind error"}, records, settings(),
                lambda query: [1.0, 0.0] if query else [0.0, 1.0],
                lambda system_prompt, user_prompt: completion,
            )

    def test_retrieval_returns_prompt_shape_and_three_line_summary(self):
        records = [{
            "ticket_id": "T1", "description": "PolicyPeriod bind error",
            "system": "PolicyCenter", "ticket_type": "Incident",
            "retrieval_eligible": True, "embedding": [1.0, 0.0],
            "resolution": None, "rca": None,
            "source_metadata": {},
        }]

        def fake_completion(system_prompt, user_prompt):
            self.assertTrue(system_prompt)
            self.assertEqual(json.loads(user_prompt)["candidates"][0]["id"], "T1")
            return {
                "new_ticket_assessment": {
                    "itil_type": "Incident", "suggested_level": "L2",
                    "system": "PolicyCenter", "failure_pattern": "bind error",
                    "confidence": "medium",
                },
                "matches": [{
                    "id": "T1", "match_strength": "strong",
                    "why_similar": "Same bind symptom", "what_differs": "not recorded",
                    "reusable_info": "not recorded", "missing_info": ["resolution", "rca"],
                }],
                "no_confident_match": False,
                "suggested_next_step": "Compare bind logs",
            }

        result = retrieve(
            {"description": "PolicyPeriod bind error"},
            records,
            settings(),
            lambda query: [1.0, 0.0] if query else [0.0, 1.0],
            fake_completion,
        )
        self.assertEqual(result["matches"][0]["id"], "T1")
        self.assertEqual(len(result["plain_summary"].splitlines()), 3)

    def test_api_uses_configured_retrieval_flow_without_live_services(self):
        records = [{
            "ticket_id": "T1", "description": "PolicyPeriod bind error",
            "system": "PolicyCenter", "ticket_type": "Incident",
            "retrieval_eligible": True, "embedding": [1.0, 0.0],
            "resolution": None, "rca": None, "source_metadata": {},
        }]
        completion = {
            "new_ticket_assessment": {
                "itil_type": "Incident", "suggested_level": "L2",
                "system": "PolicyCenter", "failure_pattern": "bind error",
                "confidence": "medium",
            },
            "matches": [{
                "id": "T1", "match_strength": "strong",
                "why_similar": "Same bind symptom", "what_differs": "not recorded",
                "reusable_info": "not recorded", "missing_info": ["resolution", "rca"],
            }],
            "no_confident_match": False,
            "suggested_next_step": "Compare bind logs",
        }
        with (
            patch.object(main.Settings, "load", return_value=settings()),
            patch.object(main, "fetch_eligible_tickets", return_value=records),
            patch.object(main, "embed_texts", return_value=[[1.0, 0.0]]),
            patch.object(main, "complete_json", return_value=completion),
        ):
            response = main.retrieve_ticket(main.TicketRequest(description="PolicyPeriod bind error"))
        self.assertEqual(response["matches"][0]["id"], "T1")
        self.assertEqual(len(response["plain_summary"].splitlines()), 3)


class TemporalBoostTests(unittest.TestCase):
    def test_temporal_boost_favors_recent_tickets(self):
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone.utc)
        recent = (now - timedelta(days=30)).isoformat()
        old = (now - timedelta(days=500)).isoformat()

        recent_boost = _temporal_boost(recent, settings(temporal_boost_days=365, temporal_boost_magnitude=0.02))
        old_boost = _temporal_boost(old, settings(temporal_boost_days=365, temporal_boost_magnitude=0.02))

        self.assertGreater(recent_boost, old_boost)
        self.assertGreater(recent_boost, 0)
        self.assertLess(old_boost, 0)

    def test_temporal_boost_handles_missing_created(self):
        boost = _temporal_boost(None, settings())
        self.assertEqual(boost, 0.0)

    def test_temporal_boost_handles_invalid_dates(self):
        boost = _temporal_boost("invalid-date", settings())
        self.assertEqual(boost, 0.0)

    def test_temporal_boost_uses_configured_magnitude(self):
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone.utc)
        recent = (now - timedelta(days=30)).isoformat()

        small_boost = _temporal_boost(recent, settings(temporal_boost_magnitude=0.01))
        large_boost = _temporal_boost(recent, settings(temporal_boost_magnitude=0.05))

        self.assertLess(small_boost, large_boost)


if __name__ == "__main__":
    unittest.main()
