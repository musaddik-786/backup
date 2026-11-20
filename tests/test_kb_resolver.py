import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

import kb_load  # noqa: E402
from retrieval_agent import kb_resolver  # noqa: E402

SETTINGS = SimpleNamespace(rrf_constant=60)


def _units():
    out = []
    for u in kb_load.load_units():
        out.append({"unit_id": u["unit_id"], "article_id": u["article_id"], "title": u["title"],
                    "match_text": kb_load.match_text(u), "version": u["version"], "review_status": "x",
                    "kb_version": "kb-test", "content": u, "embedding": [1.0, 0.0]})
    return out


EMBED = lambda text: [1.0, 0.0]  # noqa: E731
AUDIT_TICKET = {"title": "Audit premium decreased although payroll increased",
                "description": "GL final audit shows a credit but the audited payroll went up."}


def test_ranking_puts_audit_card_first():
    ranked = kb_resolver.rank_units(kb_resolver.ticket_text(AUDIT_TICKET), _units(), [1.0, 0.0])
    assert ranked[0]["unit"]["unit_id"] == "PC-AUD-01"


def test_resolve_returns_verbatim_card_and_missing_items():
    llm = lambda s, u: {"unit_id": "PC-AUD-01", "confidence": "high", "reason": "same inversion", "missing_info": [2, 99]}  # noqa: E731
    out = kb_resolver.resolve(AUDIT_TICKET, _units(), SETTINGS, EMBED, llm)
    assert out["matched"] and out["verified"] and out["unit"]["unit_id"] == "PC-AUD-01"
    assert len(out["missing_info"]) == 1 and out["missing_info"][0].startswith("Each GL exposure")  # index 99 ignored
    assert out["unit"]["content"]["escalation"].startswith("PC L3 Rating & Audit")


def test_llm_can_abstain():
    llm = lambda s, u: {"unit_id": None, "confidence": "high", "reason": "different area", "missing_info": []}  # noqa: E731
    out = kb_resolver.resolve({"title": "ClaimCenter reserve screen layout is misaligned"}, _units(), SETTINGS, EMBED, llm)
    assert out["matched"] is False and out["unit"] is None


def test_llm_cannot_invent_a_card():
    llm = lambda s, u: {"unit_id": "PC-FAKE-99", "confidence": "high", "reason": "x", "missing_info": []}  # noqa: E731
    out = kb_resolver.resolve(AUDIT_TICKET, _units(), SETTINGS, EMBED, llm)
    assert out["verified"] is False and out["confidence"] == "low"  # fell back, flagged unverified


def test_azure_down_falls_back_to_unverified_top_candidate():
    def boom(s, u):
        raise RuntimeError("Could not reach Azure OpenAI")
    out = kb_resolver.resolve(AUDIT_TICKET, _units(), SETTINGS, EMBED, boom)
    assert out["matched"] and out["verified"] is False and out["unit"]["unit_id"] == "PC-AUD-01"
