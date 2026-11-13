"""
Stage 1: Ingestion & Normalization
(diagram box: "Ingests all sources and tags content by Guidewire module,
issue category, severity and resolution type against a human-defined
taxonomy.")

Responsibilities:
  1. Load + schema-validate each of the 4 raw sources (fails loudly on a
     malformed record rather than silently dropping it).
  2. Map each source's native shape into the single NormalizedRecord shape.
  3. Tag every record against the taxonomy (module / category / severity /
     resolution_type) and attach a tagging_confidence score.

Tagging split:
  - module / severity are resolved deterministically from fields already
    present in the raw source (e.g. a ticket's module_reported / priority),
    with a rule-based fallback only where the raw data doesn't give a
    direct answer (see _resolve_module()).
  - category / resolution_type are tagged by an LLM (Azure OpenAI chat
    deployment, via _llm_classify()): it's given the record's narrative
    text plus the *fixed list* of valid IssueCategory/ResolutionType
    values from taxonomy.py, and returns a structured pick for each, a
    self-reported confidence (0-1), and a short reasoning string (kept in
    tagging_reasoning, so a human can still see *why* -- same transparency
    goal the original keyword classifier had, now backed by a real model
    instead of hand-written keyword lists).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from langchain_openai import AzureChatOpenAI
from pydantic import BaseModel, Field

from schemas import (
    NormalizedRecord,
    RawChatOrRCA,
    RawReleaseNote,
    RawRunbook,
    RawTicket,
)
from taxonomy import (
    GuidewireModule,
    IssueCategory,
    ResolutionType,
    Severity,
    SourceType,
)

DATA_DIR = Path(__file__).parent / "data" / "raw"

PRIORITY_TO_SEVERITY = {
    "P1": Severity.SEV1_CRITICAL,
    "P2": Severity.SEV2_HIGH,
    "P3": Severity.SEV3_MEDIUM,
    "P4": Severity.SEV4_LOW,
}

MODULE_ALIASES = {m.value.lower(): m for m in GuidewireModule}


# ---------------------------------------------------------------------------
# LLM-based category / resolution_type classifier
# ---------------------------------------------------------------------------
class _TaggingResult(BaseModel):
    category: IssueCategory = Field(description="Best-matching issue category for this record.")
    resolution_type: ResolutionType = Field(description="Best-matching resolution type for this record.")
    confidence: float = Field(ge=0.0, le=1.0, description="How confident you are in BOTH picks above, 0-1.")
    reasoning: str = Field(description="One or two sentences on why these labels fit, for a human reviewer.")


_chat_client: AzureChatOpenAI | None = None
_structured_classifier = None


def _get_classifier():
    """Lazily build the Azure OpenAI chat client + structured-output
    wrapper, and reuse it across calls. Raises RuntimeError with a clear
    message if the required env vars aren't set."""
    global _chat_client, _structured_classifier
    if _structured_classifier is not None:
        return _structured_classifier

    required = [
        "AZURE_OPENAI_API_KEY",
        "AZURE_OPENAI_ENDPOINT",
        "AZURE_OPENAI_API_VERSION",
        "AZURE_OPENAI_CHAT_DEPLOYMENT",
    ]
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise RuntimeError(
            "Missing Azure OpenAI env var(s) for tagging: "
            f"{', '.join(missing)}. Fill these in in .env (same Azure "
            "OpenAI resource/deployment as the Underwriting Solution project)."
        )

    _chat_client = AzureChatOpenAI(
        azure_deployment=os.getenv("AZURE_OPENAI_CHAT_DEPLOYMENT"),
        openai_api_key=os.getenv("AZURE_OPENAI_API_KEY"),
        openai_api_version=os.getenv("AZURE_OPENAI_API_VERSION"),
        azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
        temperature=0,
    )
    _structured_classifier = _chat_client.with_structured_output(_TaggingResult)
    return _structured_classifier


# One-line definitions for every taxonomy label, so the model isn't just
# guessing intended meaning from a bare enum name (e.g. distinguishing
# UpgradeRegression from ConfigurationError or BatchJobFailure).
CATEGORY_DESCRIPTIONS: dict[IssueCategory, str] = {
    IssueCategory.PERFORMANCE_DEGRADATION: "The system works but is too slow -- latency, timeouts, slow page loads under load.",
    IssueCategory.INTEGRATION_FAILURE: "A failure exchanging data with an external system (SFTP, bank, CRM, API) -- rejected files, failed calls, mapping mismatches at the boundary.",
    IssueCategory.DATA_CORRUPTION: "Data already in the system is wrong, duplicated, or missing -- not a live integration failure.",
    IssueCategory.CONFIGURATION_ERROR: "Wrong or outdated configuration (mapping, filter, field setting) causing incorrect behavior, NOT caused by a Guidewire version upgrade.",
    IssueCategory.UPGRADE_REGRESSION: "A break caused specifically by a Guidewire version upgrade -- a renamed/removed API, changed behavior right after deploying a new version.",
    IssueCategory.AUTHENTICATION_ACCESS: "Login, session, SSO, or access/permission failures.",
    IssueCategory.BATCH_JOB_FAILURE: "A scheduled/nightly/batch job failed or was killed, for reasons unrelated to a version upgrade or live user request.",
    IssueCategory.UI_RENDERING_BUG: "A visual/layout/rendering defect -- the underlying data and logic are correct, only the display is wrong.",
    IssueCategory.LICENSING: "A license pool, seat, or entitlement problem blocking usage.",
}

RESOLUTION_DESCRIPTIONS: dict[ResolutionType, str] = {
    ResolutionType.CONFIG_CHANGE: "Fixed by changing configuration/mapping/settings -- no code change.",
    ResolutionType.CODE_FIX: "Fixed by changing custom code (Gosu rules, widgets, etc.) and redeploying.",
    ResolutionType.PATCH_APPLIED: "Fixed by applying a Guidewire-provided patch or hotfix.",
    ResolutionType.RESTART_RECYCLE: "Fixed by restarting/recycling a service or process -- no underlying code or config change.",
    ResolutionType.DATA_REMEDIATION: "Fixed by directly cleaning up or correcting data (de-dup, backfill, reprocess).",
    ResolutionType.VENDOR_ESCALATION: "Resolution required escalating to Guidewire/vendor support -- not fixed internally.",
    ResolutionType.WORKAROUND: "A temporary mitigation was applied (feature flag, manual workaround) -- root cause not fully fixed.",
    ResolutionType.USER_ERROR_CLOSED: "Closed because there was no defect -- user error or misunderstanding.",
    ResolutionType.NO_ACTION_BY_DESIGN: "Closed with no action because the behavior was correct/expected (informational only).",
}

_SYSTEM_PROMPT = (
    "You are a precise taxonomy tagger for Guidewire support records. "
    "Pick exactly one value from each given list for category and "
    "resolution_type -- never invent a label outside the list. If the "
    "record is ambiguous, pick the closest fit and lower your confidence; "
    "never refuse to answer."
)

_FEW_SHOT_EXAMPLES = """Example 1:
Module: BillingCenter
Record title: Nightly invoice batch job failing after 2025.3 upgrade
Record text: The nightly invoice generation batch job has failed every night since the 2025.3 upgrade was deployed. Error logs show a null pointer in the new invoice calculation step introduced by the upgrade. Rolled back the custom calculation override to match the new API signature and redeployed.
-> category: UpgradeRegression (the failure is a direct consequence of the upgrade changing an API, not an independent scheduling issue), resolution_type: CodeFix, confidence: 0.9

Example 2:
Module: ContactManager
Record title: Users intermittently logged out during peak hours
Record text: Users report being logged out randomly during business hours. No pattern found in logs tying this to a recent change. Restarting the session service temporarily resolves it for a few hours.
-> category: AuthenticationAccess, resolution_type: RestartRecycle, confidence: 0.6 (temporary mitigation only, root cause not identified)
"""

_TAGGING_PROMPT = """Category options:
{category_defs}

Resolution type options:
{resolution_defs}

{few_shot}
Now tag this record:

Module: {module}
Record title: {title}
Record text:
{text}

Confidence guide: 0.85-1.0 = the text explicitly states the cause and/or \
fix; 0.6-0.84 = inferred from strong contextual clues; below 0.6 = \
ambiguous or largely guessed.

Pick exactly one category and exactly one resolution_type from the lists \
above, using only the record above. If the text doesn't clearly indicate \
a resolution yet (e.g. it's a known issue or feature note, not an \
incident), still pick the closest-fitting resolution_type and lower your \
confidence accordingly."""


def _llm_classify(module: str, title: str, text: str) -> tuple[IssueCategory, ResolutionType, float, str]:
    """Single LLM call per record: returns (category, resolution_type,
    confidence, reasoning)."""
    classifier = _get_classifier()
    category_defs = "\n".join(f"- {c.value}: {desc}" for c, desc in CATEGORY_DESCRIPTIONS.items())
    resolution_defs = "\n".join(f"- {r.value}: {desc}" for r, desc in RESOLUTION_DESCRIPTIONS.items())
    human_prompt = _TAGGING_PROMPT.format(
        category_defs=category_defs,
        resolution_defs=resolution_defs,
        few_shot=_FEW_SHOT_EXAMPLES,
        module=module,
        title=title,
        text=text,
    )
    result: _TaggingResult = classifier.invoke(
        [("system", _SYSTEM_PROMPT), ("human", human_prompt)]
    )
    return result.category, result.resolution_type, round(result.confidence, 2), result.reasoning


def _resolve_module(raw_module_text: str) -> tuple:
    key = raw_module_text.strip().lower()
    if key in MODULE_ALIASES:
        return MODULE_ALIASES[key], 1.0
    # fuzzy: substring match
    for alias, module in MODULE_ALIASES.items():
        if alias in key or key in alias:
            return module, 0.7
    return GuidewireModule.POLICY_CENTER, 0.3


# ---------------------------------------------------------------------------
# Per-source normalizers
# ---------------------------------------------------------------------------
def normalize_ticket(raw: RawTicket) -> NormalizedRecord:
    module, module_conf = _resolve_module(raw.module_reported)
    combined_text = f"{raw.summary} {raw.description} {raw.resolution_notes}"
    category, resolution, llm_conf, reasoning = _llm_classify(module.value, raw.summary, combined_text)
    severity = PRIORITY_TO_SEVERITY.get(raw.priority, Severity.SEV3_MEDIUM)
    sev_conf = 1.0 if raw.priority in PRIORITY_TO_SEVERITY else 0.4
    overall_conf = round((module_conf + sev_conf + 2 * llm_conf) / 4, 2)

    return NormalizedRecord(
        record_id=f"NR-{raw.ticket_id}",
        source_type=SourceType.TICKET,
        source_ref_id=raw.ticket_id,
        module=module,
        category=category,
        severity=severity,
        resolution_type=resolution,
        tagging_confidence=overall_conf,
        tagging_reasoning=reasoning,
        title=raw.summary,
        raw_text=combined_text,
        guidewire_version=raw.guidewire_version,
        event_date=raw.closed_at.date(),
        source_metadata={
            "priority": raw.priority,
            "assignee_team": raw.assignee_team,
            "created_at": raw.created_at.isoformat(),
            "closed_at": raw.closed_at.isoformat(),
        },
    )


def normalize_chat_or_rca(raw: RawChatOrRCA, ticket_index: dict) -> NormalizedRecord:
    module, module_conf = _resolve_module(raw.module_reported)
    combined_text = f"{raw.title} {raw.body} {raw.root_cause or ''} {raw.corrective_actions or ''}"
    category, resolution, llm_conf, reasoning = _llm_classify(module.value, raw.title, combined_text)

    # severity: inherit from the linked ticket when available (more
    # reliable than guessing from free text), otherwise a simple fallback.
    if raw.incident_id and raw.incident_id in ticket_index:
        severity = ticket_index[raw.incident_id]["severity"]
        sev_conf = 0.95
    else:
        severity = Severity.SEV2_HIGH if "outage" in combined_text.lower() else Severity.SEV3_MEDIUM
        sev_conf = 0.5

    overall_conf = round((module_conf + sev_conf + 2 * llm_conf) / 4, 2)

    return NormalizedRecord(
        record_id=f"NR-{raw.record_id}",
        source_type=SourceType.CHAT_RCA,
        source_ref_id=raw.record_id,
        module=module,
        category=category,
        severity=severity,
        resolution_type=resolution,
        tagging_confidence=overall_conf,
        tagging_reasoning=reasoning,
        title=raw.title,
        raw_text=combined_text,
        guidewire_version=None,
        event_date=raw.date,
        source_metadata={
            "record_type": raw.record_type,
            "incident_id": raw.incident_id,
            "participants": raw.participants,
            "root_cause": raw.root_cause,
            "corrective_actions": raw.corrective_actions,
        },
    )


def normalize_release_note(raw: RawReleaseNote) -> NormalizedRecord:
    module, module_conf = _resolve_module(raw.affected_modules[0] if raw.affected_modules else raw.product)
    combined_text = f"{raw.title} {raw.details}"
    category, resolution, llm_conf, reasoning = _llm_classify(module.value, raw.title, combined_text)

    severity_by_note_type = {
        "known_issue": Severity.SEV2_HIGH,
        "resolved_issue": Severity.SEV3_MEDIUM,
        "feature": Severity.SEV4_LOW,
    }
    severity = severity_by_note_type.get(raw.note_type, Severity.SEV3_MEDIUM)
    sev_conf = 0.75

    overall_conf = round((module_conf + sev_conf + 2 * llm_conf) / 4, 2)

    return NormalizedRecord(
        record_id=f"NR-{raw.release_id}",
        source_type=SourceType.RELEASE_NOTE,
        source_ref_id=raw.release_id,
        module=module,
        category=category,
        severity=severity,
        resolution_type=resolution,
        tagging_confidence=overall_conf,
        tagging_reasoning=reasoning,
        title=raw.title,
        raw_text=combined_text,
        guidewire_version=f"{raw.product} {raw.version}",
        event_date=raw.release_date,
        source_url=raw.source_url,
        source_metadata={
            "note_type": raw.note_type,
            "product": raw.product,
            "affected_modules": raw.affected_modules,
        },
    )


def normalize_runbook(raw: RawRunbook) -> NormalizedRecord:
    module, module_conf = _resolve_module(raw.module_reported)
    combined_text = f"{raw.title} {raw.trigger_condition} {' '.join(raw.steps)}"
    category, resolution, llm_conf, reasoning = _llm_classify(module.value, raw.title, combined_text)

    severity = (
        Severity.SEV1_CRITICAL
        if "outage" in raw.trigger_condition.lower() or "unresponsive" in raw.trigger_condition.lower()
        else Severity.SEV3_MEDIUM
    )
    sev_conf = 0.55  # runbooks apply across a severity range; this is a default, not a measured value
    overall_conf = round((module_conf + sev_conf + 2 * llm_conf) / 4, 2)

    return NormalizedRecord(
        record_id=f"NR-{raw.runbook_id}",
        source_type=SourceType.RUNBOOK,
        source_ref_id=raw.runbook_id,
        module=module,
        category=category,
        severity=severity,
        resolution_type=resolution,
        tagging_confidence=overall_conf,
        tagging_reasoning=reasoning,
        title=raw.title,
        raw_text=combined_text,
        guidewire_version=None,
        event_date=raw.last_updated,
        source_metadata={
            "owner_team": raw.owner_team,
            "trigger_condition": raw.trigger_condition,
            "steps": raw.steps,
        },
    )


# ---------------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------------
def load_raw(filename: str, model) -> list:
    path = DATA_DIR / filename
    raw_items = json.loads(path.read_text(encoding="utf-8"))
    return [model.model_validate(item) for item in raw_items]


def ingest_all() -> list[NormalizedRecord]:
    tickets = load_raw("tickets.json", RawTicket)
    chats_rcas = load_raw("chat_rca.json", RawChatOrRCA)
    release_notes = load_raw("release_notes.json", RawReleaseNote)
    runbooks = load_raw("runbooks.json", RawRunbook)

    normalized: list[NormalizedRecord] = []

    ticket_index = {}
    for t in tickets:
        rec = normalize_ticket(t)
        normalized.append(rec)
        ticket_index[t.ticket_id] = {"severity": rec.severity}

    for c in chats_rcas:
        normalized.append(normalize_chat_or_rca(c, ticket_index))

    for r in release_notes:
        normalized.append(normalize_release_note(r))

    for rb in runbooks:
        normalized.append(normalize_runbook(rb))

    return normalized


if __name__ == "__main__":
    records = ingest_all()
    print(f"Ingested & normalized {len(records)} records from 4 sources:")
    from collections import Counter

    by_source = Counter(r.source_type.value for r in records)
    for src, count in by_source.items():
        print(f"  - {src}: {count}")
    avg_conf = sum(r.tagging_confidence for r in records) / len(records)
    print(f"Average tagging confidence: {avg_conf:.2f}")
