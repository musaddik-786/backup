# # # # """
# # # # Stage 1: Ingestion & Normalization
# # # # (diagram box: "Ingests all sources and tags content by Guidewire module,
# # # # issue category, severity and resolution type against a human-defined
# # # # taxonomy.")

# # # # Responsibilities:
# # # #   1. Load + schema-validate each of the 4 raw sources (fails loudly on a
# # # #      malformed record rather than silently dropping it).
# # # #   2. Map each source's native shape into the single NormalizedRecord shape.
# # # #   3. Tag every record against the taxonomy (module / category / severity /
# # # #      resolution_type) and attach a tagging_confidence score.

# # # # Tagging split:
# # # #   - module / severity are resolved deterministically from fields already
# # # #     present in the raw source (e.g. a ticket's module_reported / priority),
# # # #     with a rule-based fallback only where the raw data doesn't give a
# # # #     direct answer (see _resolve_module()). When that deterministic
# # # #     resolution is itself low-confidence (the fallback branch, not a real
# # # #     match), the record falls back to the LLM's own independent module/
# # # #     severity read of the narrative text instead -- see
# # # #     MODULE_LLM_FALLBACK_THRESHOLD / SEVERITY_LLM_FALLBACK_THRESHOLD below.
# # # #     High-confidence deterministic signals (an exact module_reported match,
# # # #     a ticket's priority code, severity inherited from a linked ticket) are
# # # #     never overridden -- those are more reliable than an LLM guess.
# # # #   - category / resolution_type are always tagged by an LLM (Azure OpenAI
# # # #     chat deployment, via _llm_classify()): it's given the record's narrative
# # # #     text plus the *fixed list* of valid IssueCategory/ResolutionType
# # # #     values from taxonomy.py, and returns a structured pick for each, a
# # # #     self-reported confidence (0-1), and a short reasoning string (kept in
# # # #     tagging_reasoning, so a human can still see *why* -- same transparency
# # # #     goal the original keyword classifier had, now backed by a real model
# # # #     instead of hand-written keyword lists). The same single LLM call also
# # # #     returns an independent module/severity opinion (used only as the
# # # #     fallback described above), so this costs no extra API call per record.

# # # # Ticket status filter:
# # # #   - Only a ticket whose `status` is in CLOSED_TICKET_STATUSES is ingested
# # # #     at all -- an open/in-progress ticket is skipped entirely (not even
# # # #     sent to SME review) until a later refresh run sees it closed. A
# # # #     closed ticket's resolution_notes is a finished, trustworthy SOP; an
# # # #     open one's isn't.

# # # # Selective ingestion / duplicate-run prevention:
# # # #   - ingest_all(sources=...) lets a caller ingest only a subset of the 4
# # # #     sources. Omitting `sources` (the default) ingests all 4, same as
# # # #     before.
# # # #   - ingest_all(existing_hashes=...) lets a caller skip re-running the LLM
# # # #     tagging call entirely for any record whose raw source fields are
# # # #     byte-for-byte identical to the last run that fully processed it (see
# # # #     _content_hash() and knowledge_store.fetch_ingestion_hashes()). Omitting
# # # #     `existing_hashes` (the default) re-processes everything selected, same
# # # #     as before.
# # # # """

# # # # from __future__ import annotations

# # # # import hashlib
# # # # import json
# # # # import os
# # # # from pathlib import Path

# # # # from langchain_openai import AzureChatOpenAI
# # # # from pydantic import BaseModel, Field
# # # # from dotenv import load_dotenv
# # # # load_dotenv()

# # # # from schemas import (
# # # #     NormalizedRecord,
# # # #     RawChatOrRCA,
# # # #     RawReleaseNote,
# # # #     RawRunbook,
# # # #     RawTicket,
# # # # )
# # # # from taxonomy import (
# # # #     GuidewireModule,
# # # #     IssueCategory,
# # # #     ResolutionType,
# # # #     Severity,
# # # #     SourceType,
# # # # )

# # # # DATA_DIR = Path(__file__).parent / "data" / "raw"

# # # # CLOSED_TICKET_STATUSES = {"Closed"}

# # # # PRIORITY_TO_SEVERITY = {
# # # #     "P1": Severity.EMERGENCY,
# # # #     "P2": Severity.HIGH,
# # # #     "P3": Severity.NORMAL,
# # # #     "P4": Severity.LOW,
# # # # }

# # # # MODULE_ALIASES = {m.value.lower(): m for m in GuidewireModule}

# # # # # Below these thresholds, the deterministic module/severity resolution is
# # # # # itself just a fallback guess (not a real match) -- so the LLM's own
# # # # # independent read of the record's narrative text is used instead. These
# # # # # line up with the existing confidence values already in this file: the
# # # # # module fallback (0.3) and the weakest severity heuristics (ticket
# # # # # priority not recognized: 0.4; chat/RCA with no linked ticket: 0.5;
# # # # # runbook keyword guess: 0.55) all fall below their threshold and get
# # # # # overridden; every high-confidence deterministic path (exact module
# # # # # match: 1.0, fuzzy match: 0.7, ticket priority code: 1.0, severity
# # # # # inherited from a linked ticket: 0.95, release-note note_type: 0.75)
# # # # # stays untouched.
# # # # MODULE_LLM_FALLBACK_THRESHOLD = 0.5
# # # # SEVERITY_LLM_FALLBACK_THRESHOLD = 0.6

# # # # # Maps the CLI/API-friendly source keys used for selective ingestion to
# # # # # their SourceType enum value.
# # # # SOURCE_KEY_TO_TYPE: dict[str, SourceType] = {
# # # #     "tickets": SourceType.TICKET,
# # # #     "chat_rca": SourceType.CHAT_RCA,
# # # #     "release_notes": SourceType.RELEASE_NOTE,
# # # #     "runbooks": SourceType.RUNBOOK,
# # # # }


# # # # # ---------------------------------------------------------------------------
# # # # # LLM-based classifier: category / resolution_type (always used) plus an
# # # # # independent module / severity opinion (used only as a fallback -- see
# # # # # MODULE_LLM_FALLBACK_THRESHOLD / SEVERITY_LLM_FALLBACK_THRESHOLD).
# # # # # ---------------------------------------------------------------------------
# # # # class _TaggingResult(BaseModel):
# # # #     category: IssueCategory = Field(description="Best-matching issue category for this record.")
# # # #     resolution_type: ResolutionType = Field(description="Best-matching resolution type for this record.")
# # # #     module: GuidewireModule = Field(
# # # #         description="Best-matching Guidewire module for this record, judged independently from the record text."
# # # #     )
# # # #     severity: Severity = Field(
# # # #         description="Best-matching severity for this record, judged independently from the record text."
# # # #     )
# # # #     confidence: float = Field(ge=0.0, le=1.0, description="How confident you are in ALL FOUR picks above, 0-1.")
# # # #     reasoning: str = Field(description="One or two sentences on why these labels fit, for a human reviewer.")


# # # # _chat_client: AzureChatOpenAI | None = None
# # # # _structured_classifier = None


# # # # def _get_classifier():
# # # #     """Lazily build the Azure OpenAI chat client + structured-output
# # # #     wrapper, and reuse it across calls. Raises RuntimeError with a clear
# # # #     message if the required env vars aren't set."""
# # # #     global _chat_client, _structured_classifier
# # # #     if _structured_classifier is not None:
# # # #         return _structured_classifier

# # # #     required = [
# # # #         "AZURE_OPENAI_API_KEY",
# # # #         "AZURE_OPENAI_ENDPOINT",
# # # #         "AZURE_OPENAI_API_VERSION",
# # # #         "AZURE_OPENAI_CHAT_DEPLOYMENT",
# # # #     ]
# # # #     missing = [name for name in required if not os.getenv(name)]
# # # #     if missing:
# # # #         raise RuntimeError(
# # # #             "Missing Azure OpenAI env var(s) for tagging: "
# # # #             f"{', '.join(missing)}. Fill these in in .env (same Azure "
# # # #             "OpenAI resource/deployment as the Underwriting Solution project)."
# # # #         )

# # # #     _chat_client = AzureChatOpenAI(
# # # #         azure_deployment=os.getenv("AZURE_OPENAI_CHAT_DEPLOYMENT"),
# # # #         openai_api_key=os.getenv("AZURE_OPENAI_API_KEY"),
# # # #         openai_api_version=os.getenv("AZURE_OPENAI_API_VERSION"),
# # # #         azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
# # # #         temperature=0,
# # # #     )
# # # #     _structured_classifier = _chat_client.with_structured_output(_TaggingResult)
# # # #     return _structured_classifier


# # # # # One-line definitions for every taxonomy label, so the model isn't just
# # # # # guessing intended meaning from a bare enum name (e.g. distinguishing
# # # # # UpgradeRegression from ConfigurationError or BatchJobFailure).
# # # # CATEGORY_DESCRIPTIONS: dict[IssueCategory, str] = {
# # # #     IssueCategory.PERFORMANCE_DEGRADATION: "The system works but is too slow -- latency, timeouts, slow page loads under load.",
# # # #     IssueCategory.INTEGRATION_FAILURE: "A failure exchanging data with an external system (SFTP, bank, CRM, API) -- rejected files, failed calls, mapping mismatches at the boundary.",
# # # #     IssueCategory.DATA_CORRUPTION: "Data already in the system is wrong, duplicated, or missing -- not a live integration failure.",
# # # #     IssueCategory.CONFIGURATION_ERROR: "Wrong or outdated configuration (mapping, filter, field setting) causing incorrect behavior, NOT caused by a Guidewire version upgrade.",
# # # #     IssueCategory.UPGRADE_REGRESSION: "A break caused specifically by a Guidewire version upgrade -- a renamed/removed API, changed behavior right after deploying a new version.",
# # # #     IssueCategory.AUTHENTICATION_ACCESS: "Login, session, SSO, or access/permission failures.",
# # # #     IssueCategory.BATCH_JOB_FAILURE: "A scheduled/nightly/batch job failed or was killed, for reasons unrelated to a version upgrade or live user request.",
# # # #     IssueCategory.UI_RENDERING_BUG: "A visual/layout/rendering defect -- the underlying data and logic are correct, only the display is wrong.",
# # # #     IssueCategory.LICENSING: "A license pool, seat, or entitlement problem blocking usage.",
# # # # }

# # # # RESOLUTION_DESCRIPTIONS: dict[ResolutionType, str] = {
# # # #     ResolutionType.CONFIG_CHANGE: "Fixed by changing configuration/mapping/settings -- no code change.",
# # # #     ResolutionType.CODE_FIX: "Fixed by changing custom code (Gosu rules, widgets, etc.) and redeploying.",
# # # #     ResolutionType.PATCH_APPLIED: "Fixed by applying a Guidewire-provided patch or hotfix.",
# # # #     ResolutionType.RESTART_RECYCLE: "Fixed by restarting/recycling a service or process -- no underlying code or config change.",
# # # #     ResolutionType.DATA_REMEDIATION: "Fixed by directly cleaning up or correcting data (de-dup, backfill, reprocess).",
# # # #     ResolutionType.VENDOR_ESCALATION: "Resolution required escalating to Guidewire/vendor support -- not fixed internally.",
# # # #     ResolutionType.WORKAROUND: "A temporary mitigation was applied (feature flag, manual workaround) -- root cause not fully fixed.",
# # # #     ResolutionType.USER_ERROR_CLOSED: "Closed because there was no defect -- user error or misunderstanding.",
# # # #     ResolutionType.NO_ACTION_BY_DESIGN: "Closed with no action because the behavior was correct/expected (informational only).",
# # # # }

# # # # MODULE_DESCRIPTIONS: dict[GuidewireModule, str] = {
# # # #     GuidewireModule.POLICY_CENTER: "Policy administration -- quoting, binding, policy transactions, underwriting workflow.",
# # # #     GuidewireModule.CLAIM_CENTER: "Claims administration -- FNOL, claim handling, claims workflow, adjuster activities.",
# # # #     GuidewireModule.BILLING_CENTER: "Billing and invoicing -- payment plans, invoices, disbursements, billing schedules.",
# # # #     GuidewireModule.CONTACT_MANAGER: "Shared contact/account data used across Policy/Claim/BillingCenter -- contacts, addresses, duplicate/merge issues.",
# # # #     GuidewireModule.RATING_GOSU: "Rating engine or custom Gosu code -- rate calculation, custom business rules/scripts.",
# # # #     GuidewireModule.INTEGRATION_GATEWAY: "Integration layer between Guidewire and external systems -- SFTP, APIs, messaging, data exchange gateways.",
# # # #     GuidewireModule.DIGITAL_PORTAL: "Customer/agent-facing self-service portal or digital UI, separate from the core center application.",
# # # #     GuidewireModule.DATA_PLATFORM: "Underlying data/reporting/analytics platform -- data warehouse, ETL, reporting infrastructure.",
# # # # }

# # # # SEVERITY_DESCRIPTIONS: dict[Severity, str] = {
# # # #     Severity.EMERGENCY: "Production down or a major incident affecting business operations broadly.",
# # # #     Severity.HIGH: "A major feature is broken, but a workaround exists.",
# # # #     Severity.NORMAL: "Degraded but usable -- a non-blocking defect or noticeable but tolerable issue.",
# # # #     Severity.LOW: "Cosmetic or minor issue with negligible business impact.",
# # # # }

# # # # _SYSTEM_PROMPT = (
# # # #     "You are a precise taxonomy tagger for Guidewire support records. "
# # # #     "Pick exactly one value from each given list for category, "
# # # #     "resolution_type, module, and severity -- never invent a label outside "
# # # #     "any list. If the record is ambiguous, pick the closest fit and lower "
# # # #     "your confidence; never refuse to answer."
# # # # )

# # # # _FEW_SHOT_EXAMPLES = """Example 1:
# # # # Module (hint): BillingCenter
# # # # Record title: Nightly invoice batch job failing after 2025.3 upgrade
# # # # Record text: The nightly invoice generation batch job has failed every night since the 2025.3 upgrade was deployed. Error logs show a null pointer in the new invoice calculation step introduced by the upgrade. Rolled back the custom calculation override to match the new API signature and redeployed.
# # # # -> category: UpgradeRegression (the failure is a direct consequence of the upgrade changing an API, not an independent scheduling issue), resolution_type: CodeFix, module: BillingCenter (invoice generation is a billing function), severity: High (a nightly job is broken but the core application is otherwise usable), confidence: 0.9

# # # # Example 2:
# # # # Module (hint): ContactManager
# # # # Record title: Users intermittently logged out during peak hours
# # # # Record text: Users report being logged out randomly during business hours. No pattern found in logs tying this to a recent change. Restarting the session service temporarily resolves it for a few hours.
# # # # -> category: AuthenticationAccess, resolution_type: RestartRecycle, module: ContactManager (session/login handling spans all centers via shared contact/access infrastructure), severity: High (a major feature -- staying logged in -- is broken, but restarting the service is a working mitigation), confidence: 0.6 (temporary mitigation only, root cause not identified)
# # # # """

# # # # _TAGGING_PROMPT = """Category options:
# # # # {category_defs}

# # # # Resolution type options:
# # # # {resolution_defs}

# # # # Module options:
# # # # {module_defs}

# # # # Severity options:
# # # # {severity_defs}

# # # # {few_shot}
# # # # Now tag this record:

# # # # Module (hint): {module}
# # # # Record title: {title}
# # # # Record text:
# # # # {text}

# # # # The "Module (hint)" above comes from raw source metadata and may be wrong \
# # # # or missing -- independently judge the best-fitting module from the record \
# # # # text itself for your `module` answer, the same way you judge category and \
# # # # resolution_type.

# # # # Confidence guide: 0.85-1.0 = the text explicitly states the cause and/or \
# # # # fix; 0.6-0.84 = inferred from strong contextual clues; below 0.6 = \
# # # # ambiguous or largely guessed.

# # # # Pick exactly one category, resolution_type, module, and severity from the \
# # # # lists above, using only the record above. If the text doesn't clearly \
# # # # indicate a resolution yet (e.g. it's a known issue or feature note, not an \
# # # # incident), still pick the closest-fitting resolution_type and lower your \
# # # # confidence accordingly."""


# # # # def _llm_classify(module: str, title: str, text: str) -> _TaggingResult:
# # # #     """Single LLM call per record: returns a _TaggingResult with category,
# # # #     resolution_type, module, severity, confidence, reasoning.

# # # #     category/resolution_type are always used by the caller. module/severity
# # # #     are an independent LLM opinion that the caller (normalize_*() below)
# # # #     only falls back to when its own deterministic resolution for that
# # # #     specific field was itself low-confidence -- see
# # # #     MODULE_LLM_FALLBACK_THRESHOLD / SEVERITY_LLM_FALLBACK_THRESHOLD."""
# # # #     classifier = _get_classifier()
# # # #     category_defs = "\n".join(f"- {c.value}: {desc}" for c, desc in CATEGORY_DESCRIPTIONS.items())
# # # #     resolution_defs = "\n".join(f"- {r.value}: {desc}" for r, desc in RESOLUTION_DESCRIPTIONS.items())
# # # #     module_defs = "\n".join(f"- {m.value}: {desc}" for m, desc in MODULE_DESCRIPTIONS.items())
# # # #     severity_defs = "\n".join(f"- {s.value}: {desc}" for s, desc in SEVERITY_DESCRIPTIONS.items())
# # # #     human_prompt = _TAGGING_PROMPT.format(
# # # #         category_defs=category_defs,
# # # #         resolution_defs=resolution_defs,
# # # #         module_defs=module_defs,
# # # #         severity_defs=severity_defs,
# # # #         few_shot=_FEW_SHOT_EXAMPLES,
# # # #         module=module,
# # # #         title=title,
# # # #         text=text,
# # # #     )
# # # #     result: _TaggingResult = classifier.invoke(
# # # #         [("system", _SYSTEM_PROMPT), ("human", human_prompt)]
# # # #     )
# # # #     result.confidence = round(result.confidence, 2)
# # # #     return result


# # # # def _resolve_module(raw_module_text: str) -> tuple:
# # # #     key = raw_module_text.strip().lower()
# # # #     if key in MODULE_ALIASES:
# # # #         return MODULE_ALIASES[key], 1.0
# # # #     # fuzzy: substring match
# # # #     for alias, module in MODULE_ALIASES.items():
# # # #         if alias in key or key in alias:
# # # #             return module, 0.7
# # # #     return GuidewireModule.POLICY_CENTER, 0.3


# # # # # ---------------------------------------------------------------------------
# # # # # Per-source normalizers
# # # # # ---------------------------------------------------------------------------
# # # # def normalize_ticket(raw: RawTicket) -> NormalizedRecord:
# # # #     module, module_conf = _resolve_module(raw.module_reported)
# # # #     combined_text = f"{raw.summary} {raw.description} {raw.resolution_notes}"
# # # #     severity = PRIORITY_TO_SEVERITY.get(raw.priority, Severity.NORMAL)
# # # #     sev_conf = 1.0 if raw.priority in PRIORITY_TO_SEVERITY else 0.4

# # # #     tag_result = _llm_classify(module.value, raw.summary, combined_text)
# # # #     category, resolution, llm_conf, reasoning = (
# # # #         tag_result.category, tag_result.resolution_type, tag_result.confidence, tag_result.reasoning
# # # #     )

# # # #     # module/severity stay deterministic unless their own deterministic
# # # #     # confidence was itself a low-confidence fallback -- see
# # # #     # MODULE_LLM_FALLBACK_THRESHOLD / SEVERITY_LLM_FALLBACK_THRESHOLD.
# # # #     if module_conf < MODULE_LLM_FALLBACK_THRESHOLD:
# # # #         module, module_conf = tag_result.module, llm_conf
# # # #     if sev_conf < SEVERITY_LLM_FALLBACK_THRESHOLD:
# # # #         severity, sev_conf = tag_result.severity, llm_conf

# # # #     overall_conf = round((module_conf + sev_conf + 2 * llm_conf) / 4, 2)

# # # #     return NormalizedRecord(
# # # #         record_id=f"NR-{raw.ticket_id}",
# # # #         source_type=SourceType.TICKET,
# # # #         source_ref_id=raw.ticket_id,
# # # #         module=module,
# # # #         category=category,
# # # #         severity=severity,
# # # #         resolution_type=resolution,
# # # #         tagging_confidence=overall_conf,
# # # #         tagging_reasoning=reasoning,
# # # #         title=raw.summary,
# # # #         raw_text=combined_text,
# # # #         guidewire_version=raw.guidewire_version,
# # # #         event_date=raw.closed_at.date(),
# # # #         source_metadata={
# # # #             "priority": raw.priority,
# # # #             "assignee_team": raw.assignee_team,
# # # #             "created_at": raw.created_at.isoformat(),
# # # #             "closed_at": raw.closed_at.isoformat(),
# # # #         },
# # # #     )


# # # # def normalize_chat_or_rca(raw: RawChatOrRCA, ticket_index: dict) -> NormalizedRecord:
# # # #     module, module_conf = _resolve_module(raw.module_reported)
# # # #     combined_text = f"{raw.title} {raw.body} {raw.root_cause or ''} {raw.corrective_actions or ''}"

# # # #     # severity: inherit from the linked ticket when available (more
# # # #     # reliable than guessing from free text), otherwise a simple fallback.
# # # #     if raw.incident_id and raw.incident_id in ticket_index:
# # # #         severity = ticket_index[raw.incident_id]["severity"]
# # # #         sev_conf = 0.95
# # # #     else:
# # # #         severity = Severity.HIGH if "outage" in combined_text.lower() else Severity.NORMAL
# # # #         sev_conf = 0.5

# # # #     tag_result = _llm_classify(module.value, raw.title, combined_text)
# # # #     category, resolution, llm_conf, reasoning = (
# # # #         tag_result.category, tag_result.resolution_type, tag_result.confidence, tag_result.reasoning
# # # #     )

# # # #     if module_conf < MODULE_LLM_FALLBACK_THRESHOLD:
# # # #         module, module_conf = tag_result.module, llm_conf
# # # #     if sev_conf < SEVERITY_LLM_FALLBACK_THRESHOLD:
# # # #         severity, sev_conf = tag_result.severity, llm_conf

# # # #     overall_conf = round((module_conf + sev_conf + 2 * llm_conf) / 4, 2)

# # # #     return NormalizedRecord(
# # # #         record_id=f"NR-{raw.record_id}",
# # # #         source_type=SourceType.CHAT_RCA,
# # # #         source_ref_id=raw.record_id,
# # # #         module=module,
# # # #         category=category,
# # # #         severity=severity,
# # # #         resolution_type=resolution,
# # # #         tagging_confidence=overall_conf,
# # # #         tagging_reasoning=reasoning,
# # # #         title=raw.title,
# # # #         raw_text=combined_text,
# # # #         guidewire_version=None,
# # # #         event_date=raw.date,
# # # #         source_metadata={
# # # #             "record_type": raw.record_type,
# # # #             "incident_id": raw.incident_id,
# # # #             "participants": raw.participants,
# # # #             "root_cause": raw.root_cause,
# # # #             "corrective_actions": raw.corrective_actions,
# # # #         },
# # # #     )


# # # # def normalize_release_note(raw: RawReleaseNote) -> NormalizedRecord:
# # # #     module, module_conf = _resolve_module(raw.affected_modules[0] if raw.affected_modules else raw.product)
# # # #     combined_text = f"{raw.title} {raw.details}"

# # # #     severity_by_note_type = {
# # # #         "known_issue": Severity.HIGH,
# # # #         "resolved_issue": Severity.NORMAL,
# # # #         "feature": Severity.LOW,
# # # #     }
# # # #     severity = severity_by_note_type.get(raw.note_type, Severity.NORMAL)
# # # #     sev_conf = 0.75

# # # #     tag_result = _llm_classify(module.value, raw.title, combined_text)
# # # #     category, resolution, llm_conf, reasoning = (
# # # #         tag_result.category, tag_result.resolution_type, tag_result.confidence, tag_result.reasoning
# # # #     )

# # # #     if module_conf < MODULE_LLM_FALLBACK_THRESHOLD:
# # # #         module, module_conf = tag_result.module, llm_conf
# # # #     if sev_conf < SEVERITY_LLM_FALLBACK_THRESHOLD:
# # # #         severity, sev_conf = tag_result.severity, llm_conf

# # # #     overall_conf = round((module_conf + sev_conf + 2 * llm_conf) / 4, 2)

# # # #     return NormalizedRecord(
# # # #         record_id=f"NR-{raw.release_id}",
# # # #         source_type=SourceType.RELEASE_NOTE,
# # # #         source_ref_id=raw.release_id,
# # # #         module=module,
# # # #         category=category,
# # # #         severity=severity,
# # # #         resolution_type=resolution,
# # # #         tagging_confidence=overall_conf,
# # # #         tagging_reasoning=reasoning,
# # # #         title=raw.title,
# # # #         raw_text=combined_text,
# # # #         guidewire_version=f"{raw.product} {raw.version}",
# # # #         event_date=raw.release_date,
# # # #         source_url=raw.source_url,
# # # #         source_metadata={
# # # #             "note_type": raw.note_type,
# # # #             "product": raw.product,
# # # #             "affected_modules": raw.affected_modules,
# # # #         },
# # # #     )


# # # # def normalize_runbook(raw: RawRunbook) -> NormalizedRecord:
# # # #     module, module_conf = _resolve_module(raw.module_reported)
# # # #     combined_text = f"{raw.title} {raw.trigger_condition} {' '.join(raw.steps)}"

# # # #     severity = (
# # # #         Severity.EMERGENCY
# # # #         if "outage" in raw.trigger_condition.lower() or "unresponsive" in raw.trigger_condition.lower()
# # # #         else Severity.NORMAL
# # # #     )
# # # #     sev_conf = 0.55  # runbooks apply across a severity range; this is a default, not a measured value

# # # #     tag_result = _llm_classify(module.value, raw.title, combined_text)
# # # #     category, resolution, llm_conf, reasoning = (
# # # #         tag_result.category, tag_result.resolution_type, tag_result.confidence, tag_result.reasoning
# # # #     )

# # # #     if module_conf < MODULE_LLM_FALLBACK_THRESHOLD:
# # # #         module, module_conf = tag_result.module, llm_conf
# # # #     if sev_conf < SEVERITY_LLM_FALLBACK_THRESHOLD:
# # # #         severity, sev_conf = tag_result.severity, llm_conf

# # # #     overall_conf = round((module_conf + sev_conf + 2 * llm_conf) / 4, 2)

# # # #     return NormalizedRecord(
# # # #         record_id=f"NR-{raw.runbook_id}",
# # # #         source_type=SourceType.RUNBOOK,
# # # #         source_ref_id=raw.runbook_id,
# # # #         module=module,
# # # #         category=category,
# # # #         severity=severity,
# # # #         resolution_type=resolution,
# # # #         tagging_confidence=overall_conf,
# # # #         tagging_reasoning=reasoning,
# # # #         title=raw.title,
# # # #         raw_text=combined_text,
# # # #         guidewire_version=None,
# # # #         event_date=raw.last_updated,
# # # #         source_metadata={
# # # #             "owner_team": raw.owner_team,
# # # #             "trigger_condition": raw.trigger_condition,
# # # #             "steps": raw.steps,
# # # #         },
# # # #     )


# # # # # ---------------------------------------------------------------------------
# # # # # Public entrypoint
# # # # # ---------------------------------------------------------------------------
# # # # def load_raw(filename: str, model) -> list:
# # # #     path = DATA_DIR / filename
# # # #     raw_items = json.loads(path.read_text(encoding="utf-8"))
# # # #     return [model.model_validate(item) for item in raw_items]


# # # # def _content_hash(raw: BaseModel) -> str:
# # # #     """Stable hash of a raw record's own fields (before any tagging),
# # # #     used to detect whether it has changed since the last run that fully
# # # #     processed it. Deliberately hashes the raw input, not the normalized/
# # # #     tagged output, so a hash match means "nothing to re-tag" -- not just
# # # #     "the last tagging result happened to look the same"."""
# # # #     payload = raw.model_dump(mode="json")
# # # #     return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


# # # # def ingest_all(
# # # #     sources: list[SourceType] | None = None,
# # # #     existing_hashes: dict[str, str] | None = None,
# # # # ) -> list[NormalizedRecord]:
# # # #     """
# # # #     sources: which of the 4 source types to ingest this run. None (the
# # # #         default) ingests all 4 -- identical to the original behavior.
# # # #     existing_hashes: {record_id: content_hash} already stored in the
# # # #         Knowledge Store from a prior run (see
# # # #         knowledge_store.fetch_ingestion_hashes()). A record whose freshly
# # # #         computed content hash matches the stored one is skipped entirely --
# # # #         no LLM tagging call, not included in the returned list -- since
# # # #         nothing about its raw source fields has changed since it was last
# # # #         fully processed. None/{} (the default) re-processes everything
# # # #         selected, identical to the original behavior.
# # # #     """
# # # #     if sources is None:
# # # #         sources = list(SourceType)
# # # #     existing_hashes = existing_hashes or {}

# # # #     tickets = load_raw("tickets.json", RawTicket)
# # # #     chats_rcas = load_raw("chat_rca.json", RawChatOrRCA)
# # # #     release_notes = load_raw("release_notes.json", RawReleaseNote)
# # # #     runbooks = load_raw("runbooks.json", RawRunbook)

# # # #     # Built from ALL tickets regardless of `sources`/skip state: chat/RCA
# # # #     # severity inheritance needs it, and ticket severity is a free,
# # # #     # deterministic lookup (no LLM call involved), so there's no cost to
# # # #     # always having it available even when tickets themselves aren't
# # # #     # selected or are being skipped as unchanged this run.
# # # #     ticket_index = {
# # # #         t.ticket_id: {"severity": PRIORITY_TO_SEVERITY.get(t.priority, Severity.NORMAL)}
# # # #         for t in tickets
# # # #     }

# # # #     normalized: list[NormalizedRecord] = []

# # # #     if SourceType.TICKET in sources:
# # # #         for t in tickets:
# # # #             if t.status not in CLOSED_TICKET_STATUSES:
# # # #                 # Open/in-progress tickets are never ingested into the KB --
# # # #                 # only a closed ticket's resolution is a trustworthy,
# # # #                 # finished SOP. It'll be picked up on a later refresh run
# # # #                 # once its status flips to Closed.
# # # #                 continue
# # # #             record_id = f"NR-{t.ticket_id}"
# # # #             content_hash = _content_hash(t)
# # # #             if existing_hashes.get(record_id) == content_hash:
# # # #                 continue
# # # #             rec = normalize_ticket(t)
# # # #             rec.content_hash = content_hash
# # # #             normalized.append(rec)

# # # #     if SourceType.CHAT_RCA in sources:
# # # #         for c in chats_rcas:
# # # #             record_id = f"NR-{c.record_id}"
# # # #             content_hash = _content_hash(c)
# # # #             if existing_hashes.get(record_id) == content_hash:
# # # #                 continue
# # # #             rec = normalize_chat_or_rca(c, ticket_index)
# # # #             rec.content_hash = content_hash
# # # #             normalized.append(rec)

# # # #     if SourceType.RELEASE_NOTE in sources:
# # # #         for r in release_notes:
# # # #             record_id = f"NR-{r.release_id}"
# # # #             content_hash = _content_hash(r)
# # # #             if existing_hashes.get(record_id) == content_hash:
# # # #                 continue
# # # #             rec = normalize_release_note(r)
# # # #             rec.content_hash = content_hash
# # # #             normalized.append(rec)

# # # #     if SourceType.RUNBOOK in sources:
# # # #         for rb in runbooks:
# # # #             record_id = f"NR-{rb.runbook_id}"
# # # #             content_hash = _content_hash(rb)
# # # #             if existing_hashes.get(record_id) == content_hash:
# # # #                 continue
# # # #             rec = normalize_runbook(rb)
# # # #             rec.content_hash = content_hash
# # # #             normalized.append(rec)

# # # #     return normalized


# # # # if __name__ == "__main__":
# # # #     records = ingest_all()
# # # #     print(f"Ingested & normalized {len(records)} records from 4 sources:")
# # # #     from collections import Counter

# # # #     by_source = Counter(r.source_type.value for r in records)
# # # #     for src, count in by_source.items():
# # # #         print(f"  - {src}: {count}")
# # # #     avg_conf = sum(r.tagging_confidence for r in records) / len(records)
# # # #     print(f"Average tagging confidence: {avg_conf:.2f}")



# # # """
# # # Stage 1: Ingestion & Normalization
# # # (diagram box: "Ingests all sources and tags content by Guidewire module,
# # # issue category, severity and resolution type against a human-defined
# # # taxonomy.")

# # # Responsibilities:
# # #   1. Load + schema-validate each of the 4 raw sources (fails loudly on a
# # #      malformed record rather than silently dropping it).
# # #   2. Map each source's native shape into the single NormalizedRecord shape.
# # #   3. Tag every record against the taxonomy (module / category / severity /
# # #      resolution_type) and attach a tagging_confidence score.

# # # Tagging split:
# # #   - module / severity are resolved deterministically from fields already
# # #     present in the raw source (e.g. a ticket's module_reported / priority),
# # #     with a rule-based fallback only where the raw data doesn't give a
# # #     direct answer (see _resolve_module()). When that deterministic
# # #     resolution is itself low-confidence (the fallback branch, not a real
# # #     match), the record falls back to the LLM's own independent module/
# # #     severity read of the narrative text instead -- see
# # #     MODULE_LLM_FALLBACK_THRESHOLD / SEVERITY_LLM_FALLBACK_THRESHOLD below.
# # #     High-confidence deterministic signals (an exact module_reported match,
# # #     a ticket's priority code, severity inherited from a linked ticket) are
# # #     never overridden -- those are more reliable than an LLM guess.
# # #   - category / resolution_type are always tagged by an LLM (Azure OpenAI
# # #     chat deployment, via _llm_classify()): it's given the record's narrative
# # #     text plus the *fixed list* of valid IssueCategory/ResolutionType
# # #     values from taxonomy.py, and returns a structured pick for each, a
# # #     self-reported confidence (0-1), and a short reasoning string (kept in
# # #     tagging_reasoning, so a human can still see *why* -- same transparency
# # #     goal the original keyword classifier had, now backed by a real model
# # #     instead of hand-written keyword lists). The same single LLM call also
# # #     returns an independent module/severity opinion (used only as the
# # #     fallback described above), so this costs no extra API call per record.

# # # Ticket status filter:
# # #   - Only a ticket whose `status` is in CLOSED_TICKET_STATUSES is ingested
# # #     at all -- an open/in-progress ticket is skipped entirely (not even
# # #     sent to SME review) until a later refresh run sees it closed. A
# # #     closed ticket's resolution_notes is a finished, trustworthy SOP; an
# # #     open one's isn't.

# # # Selective ingestion / duplicate-run prevention:
# # #   - ingest_all(sources=...) lets a caller ingest only a subset of the 4
# # #     sources. Omitting `sources` (the default) ingests all 4, same as
# # #     before.
# # #   - ingest_all(existing_hashes=...) lets a caller skip re-running the LLM
# # #     tagging call entirely for any record whose raw source fields are
# # #     byte-for-byte identical to the last run that fully processed it (see
# # #     _content_hash() and knowledge_store.fetch_ingestion_hashes()). Omitting
# # #     `existing_hashes` (the default) re-processes everything selected, same
# # #     as before.
# # # """

# # # from __future__ import annotations

# # # import hashlib
# # # import json
# # # import os
# # # from pathlib import Path

# # # from langchain_openai import AzureChatOpenAI
# # # from pydantic import BaseModel, Field
# # # from dotenv import load_dotenv
# # # load_dotenv()

# # # from schemas import (
# # #     NormalizedRecord,
# # #     RawChatOrRCA,
# # #     RawReleaseNote,
# # #     RawRunbook,
# # #     RawTicket,
# # # )
# # # from taxonomy import (
# # #     GuidewireModule,
# # #     IssueCategory,
# # #     ResolutionType,
# # #     Severity,
# # #     SourceType,
# # # )

# # # DATA_DIR = Path(__file__).parent / "data" / "raw"

# # # CLOSED_TICKET_STATUSES = {"Closed"}

# # # PRIORITY_TO_SEVERITY = {
# # #     "P1": Severity.EMERGENCY,
# # #     "P2": Severity.HIGH,
# # #     "P3": Severity.NORMAL,
# # #     "P4": Severity.LOW,
# # # }

# # # MODULE_ALIASES = {m.value.lower(): m for m in GuidewireModule}

# # # # Below these thresholds, the deterministic module/severity resolution is
# # # # itself just a fallback guess (not a real match) -- so the LLM's own
# # # # independent read of the record's narrative text is used instead. These
# # # # line up with the existing confidence values already in this file: the
# # # # module fallback (0.3) and the weakest severity heuristics (ticket
# # # # priority not recognized: 0.4; chat/RCA with no linked ticket: 0.5;
# # # # runbook keyword guess: 0.55) all fall below their threshold and get
# # # # overridden; every high-confidence deterministic path (exact module
# # # # match: 1.0, fuzzy match: 0.7, ticket priority code: 1.0, severity
# # # # inherited from a linked ticket: 0.95, release-note note_type: 0.75)
# # # # stays untouched.
# # # MODULE_LLM_FALLBACK_THRESHOLD = 0.5
# # # SEVERITY_LLM_FALLBACK_THRESHOLD = 0.6

# # # # Maps the CLI/API-friendly source keys used for selective ingestion to
# # # # their SourceType enum value.
# # # SOURCE_KEY_TO_TYPE: dict[str, SourceType] = {
# # #     "tickets": SourceType.TICKET,
# # #     "chat_rca": SourceType.CHAT_RCA,
# # #     "release_notes": SourceType.RELEASE_NOTE,
# # #     "runbooks": SourceType.RUNBOOK,
# # # }


# # # # ---------------------------------------------------------------------------
# # # # LLM-based classifier: category / resolution_type (always used) plus an
# # # # independent module / severity opinion (used only as a fallback -- see
# # # # MODULE_LLM_FALLBACK_THRESHOLD / SEVERITY_LLM_FALLBACK_THRESHOLD).
# # # # ---------------------------------------------------------------------------
# # # class _TaggingResult(BaseModel):
# # #     summary: str = Field(description="One-line title (10-15 words max) capturing the core issue and resolution, for display to human reviewers.")
# # #     category: IssueCategory = Field(description="Best-matching issue category for this record.")
# # #     resolution_type: ResolutionType = Field(description="Best-matching resolution type for this record.")
# # #     module: GuidewireModule = Field(
# # #         description="Best-matching Guidewire module for this record, judged independently from the record text."
# # #     )
# # #     severity: Severity = Field(
# # #         description="Best-matching severity for this record, judged independently from the record text."
# # #     )
# # #     confidence: float = Field(ge=0.0, le=1.0, description="How confident you are in ALL FOUR picks above, 0-1.")
# # #     reasoning: str = Field(description="One or two sentences on why these labels fit, for a human reviewer.")


# # # _chat_client: AzureChatOpenAI | None = None
# # # _structured_classifier = None


# # # def _get_classifier():
# # #     """Lazily build the Azure OpenAI chat client + structured-output
# # #     wrapper, and reuse it across calls. Raises RuntimeError with a clear
# # #     message if the required env vars aren't set."""
# # #     global _chat_client, _structured_classifier
# # #     if _structured_classifier is not None:
# # #         return _structured_classifier

# # #     required = [
# # #         "AZURE_OPENAI_API_KEY",
# # #         "AZURE_OPENAI_ENDPOINT",
# # #         "AZURE_OPENAI_API_VERSION",
# # #         "AZURE_OPENAI_CHAT_DEPLOYMENT",
# # #     ]
# # #     missing = [name for name in required if not os.getenv(name)]
# # #     if missing:
# # #         raise RuntimeError(
# # #             "Missing Azure OpenAI env var(s) for tagging: "
# # #             f"{', '.join(missing)}. Fill these in in .env (same Azure "
# # #             "OpenAI resource/deployment as the Underwriting Solution project)."
# # #         )

# # #     _chat_client = AzureChatOpenAI(
# # #         azure_deployment=os.getenv("AZURE_OPENAI_CHAT_DEPLOYMENT"),
# # #         openai_api_key=os.getenv("AZURE_OPENAI_API_KEY"),
# # #         openai_api_version=os.getenv("AZURE_OPENAI_API_VERSION"),
# # #         azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
# # #         temperature=0,
# # #     )
# # #     _structured_classifier = _chat_client.with_structured_output(_TaggingResult)
# # #     return _structured_classifier


# # # # One-line definitions for every taxonomy label, so the model isn't just
# # # # guessing intended meaning from a bare enum name (e.g. distinguishing
# # # # UpgradeRegression from ConfigurationError or BatchJobFailure).
# # # CATEGORY_DESCRIPTIONS: dict[IssueCategory, str] = {
# # #     IssueCategory.PERFORMANCE_DEGRADATION: "The system works but is too slow -- latency, timeouts, slow page loads under load.",
# # #     IssueCategory.INTEGRATION_FAILURE: "A failure exchanging data with an external system (SFTP, bank, CRM, API) -- rejected files, failed calls, mapping mismatches at the boundary.",
# # #     IssueCategory.DATA_CORRUPTION: "Data already in the system is wrong, duplicated, or missing -- not a live integration failure.",
# # #     IssueCategory.CONFIGURATION_ERROR: "Wrong or outdated configuration (mapping, filter, field setting) causing incorrect behavior, NOT caused by a Guidewire version upgrade.",
# # #     IssueCategory.UPGRADE_REGRESSION: "A break caused specifically by a Guidewire version upgrade -- a renamed/removed API, changed behavior right after deploying a new version.",
# # #     IssueCategory.AUTHENTICATION_ACCESS: "Login, session, SSO, or access/permission failures.",
# # #     IssueCategory.BATCH_JOB_FAILURE: "A scheduled/nightly/batch job failed or was killed, for reasons unrelated to a version upgrade or live user request.",
# # #     IssueCategory.UI_RENDERING_BUG: "A visual/layout/rendering defect -- the underlying data and logic are correct, only the display is wrong.",
# # #     IssueCategory.LICENSING: "A license pool, seat, or entitlement problem blocking usage.",
# # # }

# # # RESOLUTION_DESCRIPTIONS: dict[ResolutionType, str] = {
# # #     ResolutionType.CONFIG_CHANGE: "Fixed by changing configuration/mapping/settings -- no code change.",
# # #     ResolutionType.CODE_FIX: "Fixed by changing custom code (Gosu rules, widgets, etc.) and redeploying.",
# # #     ResolutionType.PATCH_APPLIED: "Fixed by applying a Guidewire-provided patch or hotfix.",
# # #     ResolutionType.RESTART_RECYCLE: "Fixed by restarting/recycling a service or process -- no underlying code or config change.",
# # #     ResolutionType.DATA_REMEDIATION: "Fixed by directly cleaning up or correcting data (de-dup, backfill, reprocess).",
# # #     ResolutionType.VENDOR_ESCALATION: "Resolution required escalating to Guidewire/vendor support -- not fixed internally.",
# # #     ResolutionType.WORKAROUND: "A temporary mitigation was applied (feature flag, manual workaround) -- root cause not fully fixed.",
# # #     ResolutionType.USER_ERROR_CLOSED: "Closed because there was no defect -- user error or misunderstanding.",
# # #     ResolutionType.NO_ACTION_BY_DESIGN: "Closed with no action because the behavior was correct/expected (informational only).",
# # # }

# # # MODULE_DESCRIPTIONS: dict[GuidewireModule, str] = {
# # #     GuidewireModule.POLICY_CENTER: "Policy administration -- quoting, binding, policy transactions, underwriting workflow.",
# # #     GuidewireModule.CLAIM_CENTER: "Claims administration -- FNOL, claim handling, claims workflow, adjuster activities.",
# # #     GuidewireModule.BILLING_CENTER: "Billing and invoicing -- payment plans, invoices, disbursements, billing schedules.",
# # #     GuidewireModule.CONTACT_MANAGER: "Shared contact/account data used across Policy/Claim/BillingCenter -- contacts, addresses, duplicate/merge issues.",
# # #     GuidewireModule.RATING_GOSU: "Rating engine or custom Gosu code -- rate calculation, custom business rules/scripts.",
# # #     GuidewireModule.INTEGRATION_GATEWAY: "Integration layer between Guidewire and external systems -- SFTP, APIs, messaging, data exchange gateways.",
# # #     GuidewireModule.DIGITAL_PORTAL: "Customer/agent-facing self-service portal or digital UI, separate from the core center application.",
# # #     GuidewireModule.DATA_PLATFORM: "Underlying data/reporting/analytics platform -- data warehouse, ETL, reporting infrastructure.",
# # # }

# # # SEVERITY_DESCRIPTIONS: dict[Severity, str] = {
# # #     Severity.EMERGENCY: "Production down or a major incident affecting business operations broadly.",
# # #     Severity.HIGH: "A major feature is broken, but a workaround exists.",
# # #     Severity.NORMAL: "Degraded but usable -- a non-blocking defect or noticeable but tolerable issue.",
# # #     Severity.LOW: "Cosmetic or minor issue with negligible business impact.",
# # # }

# # # _SYSTEM_PROMPT = (
# # #     "You are a precise taxonomy tagger for Guidewire support records. "
# # #     "Pick exactly one value from each given list for category, "
# # #     "resolution_type, module, and severity -- never invent a label outside "
# # #     "any list. If the record is ambiguous, pick the closest fit and lower "
# # #     "your confidence; never refuse to answer."
# # # )

# # # _FEW_SHOT_EXAMPLES = """Example 1:
# # # Module (hint): BillingCenter
# # # Record title: Nightly invoice batch job failing after 2025.3 upgrade
# # # Record text: The nightly invoice generation batch job has failed every night since the 2025.3 upgrade was deployed. Error logs show a null pointer in the new invoice calculation step introduced by the upgrade. Rolled back the custom calculation override to match the new API signature and redeployed.
# # # -> category: UpgradeRegression (the failure is a direct consequence of the upgrade changing an API, not an independent scheduling issue), resolution_type: CodeFix, module: BillingCenter (invoice generation is a billing function), severity: High (a nightly job is broken but the core application is otherwise usable), confidence: 0.9

# # # Example 2:
# # # Module (hint): ContactManager
# # # Record title: Users intermittently logged out during peak hours
# # # Record text: Users report being logged out randomly during business hours. No pattern found in logs tying this to a recent change. Restarting the session service temporarily resolves it for a few hours.
# # # -> category: AuthenticationAccess, resolution_type: RestartRecycle, module: ContactManager (session/login handling spans all centers via shared contact/access infrastructure), severity: High (a major feature -- staying logged in -- is broken, but restarting the service is a working mitigation), confidence: 0.6 (temporary mitigation only, root cause not identified)
# # # """

# # # _TAGGING_PROMPT = """Category options:
# # # {category_defs}

# # # Resolution type options:
# # # {resolution_defs}

# # # Module options:
# # # {module_defs}

# # # Severity options:
# # # {severity_defs}

# # # {few_shot}
# # # Now tag this record:

# # # Module (hint): {module}
# # # Record title: {title}
# # # Record text:
# # # {text}

# # # The "Module (hint)" above comes from raw source metadata and may be wrong \
# # # or missing (shown as "Unknown" when not provided) -- independently judge \
# # # the best-fitting module from the record text itself for your `module` \
# # # answer, the same way you judge category and resolution_type.

# # # Confidence guide: 0.85-1.0 = the text explicitly states the cause and/or \
# # # fix; 0.6-0.84 = inferred from strong contextual clues; below 0.6 = \
# # # ambiguous or largely guessed.

# # # Pick exactly one category, resolution_type, module, and severity from the \
# # # lists above, using only the record above. If the text doesn't clearly \
# # # indicate a resolution yet (e.g. it's a known issue or feature note, not an \
# # # incident), still pick the closest-fitting resolution_type and lower your \
# # # confidence accordingly.

# # # Also generate a concise one-line summary (10-15 words max) that captures \
# # # the core issue and resolution for display to human reviewers. If a \
# # # "Record title" is already provided above, you may use it as-is or refine \
# # # it; if the title is "(not provided)", generate one from the record text."""


# # # def _llm_classify(module: str, title: str, text: str) -> _TaggingResult:
# # #     """Single LLM call per record: returns a _TaggingResult with category,
# # #     resolution_type, module, severity, confidence, reasoning.

# # #     category/resolution_type are always used by the caller. module/severity
# # #     are an independent LLM opinion that the caller (normalize_*() below)
# # #     only falls back to when its own deterministic resolution for that
# # #     specific field was itself low-confidence -- see
# # #     MODULE_LLM_FALLBACK_THRESHOLD / SEVERITY_LLM_FALLBACK_THRESHOLD."""
# # #     classifier = _get_classifier()
# # #     category_defs = "\n".join(f"- {c.value}: {desc}" for c, desc in CATEGORY_DESCRIPTIONS.items())
# # #     resolution_defs = "\n".join(f"- {r.value}: {desc}" for r, desc in RESOLUTION_DESCRIPTIONS.items())
# # #     module_defs = "\n".join(f"- {m.value}: {desc}" for m, desc in MODULE_DESCRIPTIONS.items())
# # #     severity_defs = "\n".join(f"- {s.value}: {desc}" for s, desc in SEVERITY_DESCRIPTIONS.items())
# # #     human_prompt = _TAGGING_PROMPT.format(
# # #         category_defs=category_defs,
# # #         resolution_defs=resolution_defs,
# # #         module_defs=module_defs,
# # #         severity_defs=severity_defs,
# # #         few_shot=_FEW_SHOT_EXAMPLES,
# # #         module=module,
# # #         title=title,
# # #         text=text,
# # #     )
# # #     result: _TaggingResult = classifier.invoke(
# # #         [("system", _SYSTEM_PROMPT), ("human", human_prompt)]
# # #     )
# # #     result.confidence = round(result.confidence, 2)
# # #     return result


# # # def _resolve_module(raw_module_text: str | None) -> tuple:
# # #     if not raw_module_text or not raw_module_text.strip():
# # #         return GuidewireModule.POLICY_CENTER, 0.0  # always below threshold → LLM decides
# # #     key = raw_module_text.strip().lower()
# # #     if key in MODULE_ALIASES:
# # #         return MODULE_ALIASES[key], 1.0
# # #     # fuzzy: substring match
# # #     for alias, module in MODULE_ALIASES.items():
# # #         if alias in key or key in alias:
# # #             return module, 0.7
# # #     return GuidewireModule.POLICY_CENTER, 0.3


# # # # ---------------------------------------------------------------------------
# # # # Per-source normalizers
# # # # ---------------------------------------------------------------------------
# # # def normalize_ticket(raw: RawTicket) -> NormalizedRecord:
# # #     # module_reported is optional: if present, resolve deterministically;
# # #     # if absent, pass "Unknown" as the LLM hint and let the LLM decide.
# # #     if raw.module_reported:
# # #         module, module_conf = _resolve_module(raw.module_reported)
# # #         module_hint = module.value
# # #     else:
# # #         module = GuidewireModule.POLICY_CENTER  # placeholder; overridden below (conf < threshold)
# # #         module_conf = 0.0
# # #         module_hint = "Unknown"

# # #     # summary is no longer in RawTicket -- the LLM generates it.
# # #     # raw_text is description + resolution_notes only (no summary);
# # #     # the embedding is generated from raw_text alone (not title), so
# # #     # it's based purely on the original raw data.
# # #     combined_text = f"{raw.description} {raw.resolution_notes}"
# # #     severity = PRIORITY_TO_SEVERITY.get(raw.priority, Severity.NORMAL)
# # #     sev_conf = 1.0 if raw.priority in PRIORITY_TO_SEVERITY else 0.4

# # #     tag_result = _llm_classify(module_hint, "(not provided)", combined_text)
# # #     category, resolution, llm_conf, reasoning = (
# # #         tag_result.category, tag_result.resolution_type, tag_result.confidence, tag_result.reasoning
# # #     )

# # #     # module/severity stay deterministic unless their own deterministic
# # #     # confidence was itself a low-confidence fallback -- see
# # #     # MODULE_LLM_FALLBACK_THRESHOLD / SEVERITY_LLM_FALLBACK_THRESHOLD.
# # #     if module_conf < MODULE_LLM_FALLBACK_THRESHOLD:
# # #         module, module_conf = tag_result.module, llm_conf
# # #     if sev_conf < SEVERITY_LLM_FALLBACK_THRESHOLD:
# # #         severity, sev_conf = tag_result.severity, llm_conf

# # #     overall_conf = round((module_conf + sev_conf + 2 * llm_conf) / 4, 2)

# # #     return NormalizedRecord(
# # #         record_id=f"NR-{raw.ticket_id}",
# # #         source_type=SourceType.TICKET,
# # #         source_ref_id=raw.ticket_id,
# # #         module=module,
# # #         category=category,
# # #         severity=severity,
# # #         resolution_type=resolution,
# # #         tagging_confidence=overall_conf,
# # #         tagging_reasoning=reasoning,
# # #         title=tag_result.summary,   # LLM-generated one-liner
# # #         raw_text=combined_text,     # description + resolution_notes only (used for embedding)
# # #         guidewire_version=raw.guidewire_version,
# # #         event_date=raw.closed_at.date(),
# # #         source_metadata={
# # #             "priority": raw.priority,
# # #             "assignee_team": raw.assignee_team,
# # #             "created_at": raw.created_at.isoformat(),
# # #             "closed_at": raw.closed_at.isoformat(),
# # #         },
# # #     )


# # # def normalize_chat_or_rca(raw: RawChatOrRCA, ticket_index: dict) -> NormalizedRecord:
# # #     module, module_conf = _resolve_module(raw.module_reported)
# # #     combined_text = f"{raw.title} {raw.body} {raw.root_cause or ''} {raw.corrective_actions or ''}"

# # #     # severity: inherit from the linked ticket when available (more
# # #     # reliable than guessing from free text), otherwise a simple fallback.
# # #     if raw.incident_id and raw.incident_id in ticket_index:
# # #         severity = ticket_index[raw.incident_id]["severity"]
# # #         sev_conf = 0.95
# # #     else:
# # #         severity = Severity.HIGH if "outage" in combined_text.lower() else Severity.NORMAL
# # #         sev_conf = 0.5

# # #     tag_result = _llm_classify(module.value, raw.title, combined_text)
# # #     category, resolution, llm_conf, reasoning = (
# # #         tag_result.category, tag_result.resolution_type, tag_result.confidence, tag_result.reasoning
# # #     )

# # #     if module_conf < MODULE_LLM_FALLBACK_THRESHOLD:
# # #         module, module_conf = tag_result.module, llm_conf
# # #     if sev_conf < SEVERITY_LLM_FALLBACK_THRESHOLD:
# # #         severity, sev_conf = tag_result.severity, llm_conf

# # #     overall_conf = round((module_conf + sev_conf + 2 * llm_conf) / 4, 2)

# # #     return NormalizedRecord(
# # #         record_id=f"NR-{raw.record_id}",
# # #         source_type=SourceType.CHAT_RCA,
# # #         source_ref_id=raw.record_id,
# # #         module=module,
# # #         category=category,
# # #         severity=severity,
# # #         resolution_type=resolution,
# # #         tagging_confidence=overall_conf,
# # #         tagging_reasoning=reasoning,
# # #         title=raw.title,
# # #         raw_text=combined_text,
# # #         guidewire_version=None,
# # #         event_date=raw.date,
# # #         source_metadata={
# # #             "record_type": raw.record_type,
# # #             "incident_id": raw.incident_id,
# # #             "participants": raw.participants,
# # #             "root_cause": raw.root_cause,
# # #             "corrective_actions": raw.corrective_actions,
# # #         },
# # #     )


# # # def normalize_release_note(raw: RawReleaseNote) -> NormalizedRecord:
# # #     module, module_conf = _resolve_module(raw.affected_modules[0] if raw.affected_modules else raw.product)
# # #     combined_text = f"{raw.title} {raw.details}"

# # #     severity_by_note_type = {
# # #         "known_issue": Severity.HIGH,
# # #         "resolved_issue": Severity.NORMAL,
# # #         "feature": Severity.LOW,
# # #     }
# # #     severity = severity_by_note_type.get(raw.note_type, Severity.NORMAL)
# # #     sev_conf = 0.75

# # #     tag_result = _llm_classify(module.value, raw.title, combined_text)
# # #     category, resolution, llm_conf, reasoning = (
# # #         tag_result.category, tag_result.resolution_type, tag_result.confidence, tag_result.reasoning
# # #     )

# # #     if module_conf < MODULE_LLM_FALLBACK_THRESHOLD:
# # #         module, module_conf = tag_result.module, llm_conf
# # #     if sev_conf < SEVERITY_LLM_FALLBACK_THRESHOLD:
# # #         severity, sev_conf = tag_result.severity, llm_conf

# # #     overall_conf = round((module_conf + sev_conf + 2 * llm_conf) / 4, 2)

# # #     return NormalizedRecord(
# # #         record_id=f"NR-{raw.release_id}",
# # #         source_type=SourceType.RELEASE_NOTE,
# # #         source_ref_id=raw.release_id,
# # #         module=module,
# # #         category=category,
# # #         severity=severity,
# # #         resolution_type=resolution,
# # #         tagging_confidence=overall_conf,
# # #         tagging_reasoning=reasoning,
# # #         title=raw.title,
# # #         raw_text=combined_text,
# # #         guidewire_version=f"{raw.product} {raw.version}",
# # #         event_date=raw.release_date,
# # #         source_url=raw.source_url,
# # #         source_metadata={
# # #             "note_type": raw.note_type,
# # #             "product": raw.product,
# # #             "affected_modules": raw.affected_modules,
# # #         },
# # #     )


# # # def normalize_runbook(raw: RawRunbook) -> NormalizedRecord:
# # #     module, module_conf = _resolve_module(raw.module_reported)
# # #     combined_text = f"{raw.title} {raw.trigger_condition} {' '.join(raw.steps)}"

# # #     severity = (
# # #         Severity.EMERGENCY
# # #         if "outage" in raw.trigger_condition.lower() or "unresponsive" in raw.trigger_condition.lower()
# # #         else Severity.NORMAL
# # #     )
# # #     sev_conf = 0.55  # runbooks apply across a severity range; this is a default, not a measured value

# # #     tag_result = _llm_classify(module.value, raw.title, combined_text)
# # #     category, resolution, llm_conf, reasoning = (
# # #         tag_result.category, tag_result.resolution_type, tag_result.confidence, tag_result.reasoning
# # #     )

# # #     if module_conf < MODULE_LLM_FALLBACK_THRESHOLD:
# # #         module, module_conf = tag_result.module, llm_conf
# # #     if sev_conf < SEVERITY_LLM_FALLBACK_THRESHOLD:
# # #         severity, sev_conf = tag_result.severity, llm_conf

# # #     overall_conf = round((module_conf + sev_conf + 2 * llm_conf) / 4, 2)

# # #     return NormalizedRecord(
# # #         record_id=f"NR-{raw.runbook_id}",
# # #         source_type=SourceType.RUNBOOK,
# # #         source_ref_id=raw.runbook_id,
# # #         module=module,
# # #         category=category,
# # #         severity=severity,
# # #         resolution_type=resolution,
# # #         tagging_confidence=overall_conf,
# # #         tagging_reasoning=reasoning,
# # #         title=raw.title,
# # #         raw_text=combined_text,
# # #         guidewire_version=None,
# # #         event_date=raw.last_updated,
# # #         source_metadata={
# # #             "owner_team": raw.owner_team,
# # #             "trigger_condition": raw.trigger_condition,
# # #             "steps": raw.steps,
# # #         },
# # #     )


# # # # ---------------------------------------------------------------------------
# # # # Public entrypoint
# # # # ---------------------------------------------------------------------------
# # # def load_raw(filename: str, model) -> list:
# # #     path = DATA_DIR / filename
# # #     raw_items = json.loads(path.read_text(encoding="utf-8"))
# # #     return [model.model_validate(item) for item in raw_items]


# # # def _content_hash(raw: BaseModel) -> str:
# # #     """Stable hash of a raw record's own fields (before any tagging),
# # #     used to detect whether it has changed since the last run that fully
# # #     processed it. Deliberately hashes the raw input, not the normalized/
# # #     tagged output, so a hash match means "nothing to re-tag" -- not just
# # #     "the last tagging result happened to look the same"."""
# # #     payload = raw.model_dump(mode="json")
# # #     return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


# # # def ingest_all(
# # #     sources: list[SourceType] | None = None,
# # #     existing_hashes: dict[str, str] | None = None,
# # # ) -> list[NormalizedRecord]:
# # #     """
# # #     sources: which of the 4 source types to ingest this run. None (the
# # #         default) ingests all 4 -- identical to the original behavior.
# # #     existing_hashes: {record_id: content_hash} already stored in the
# # #         Knowledge Store from a prior run (see
# # #         knowledge_store.fetch_ingestion_hashes()). A record whose freshly
# # #         computed content hash matches the stored one is skipped entirely --
# # #         no LLM tagging call, not included in the returned list -- since
# # #         nothing about its raw source fields has changed since it was last
# # #         fully processed. None/{} (the default) re-processes everything
# # #         selected, identical to the original behavior.
# # #     """
# # #     if sources is None:
# # #         sources = list(SourceType)
# # #     existing_hashes = existing_hashes or {}

# # #     tickets = load_raw("tickets.json", RawTicket)
# # #     chats_rcas = load_raw("chat_rca.json", RawChatOrRCA)
# # #     release_notes = load_raw("release_notes.json", RawReleaseNote)
# # #     runbooks = load_raw("runbooks.json", RawRunbook)

# # #     # Built from ALL tickets regardless of `sources`/skip state: chat/RCA
# # #     # severity inheritance needs it, and ticket severity is a free,
# # #     # deterministic lookup (no LLM call involved), so there's no cost to
# # #     # always having it available even when tickets themselves aren't
# # #     # selected or are being skipped as unchanged this run.
# # #     ticket_index = {
# # #         t.ticket_id: {"severity": PRIORITY_TO_SEVERITY.get(t.priority, Severity.NORMAL)}
# # #         for t in tickets
# # #     }

# # #     normalized: list[NormalizedRecord] = []

# # #     if SourceType.TICKET in sources:
# # #         for t in tickets:
# # #             if t.status not in CLOSED_TICKET_STATUSES:
# # #                 # Open/in-progress tickets are never ingested into the KB --
# # #                 # only a closed ticket's resolution is a trustworthy,
# # #                 # finished SOP. It'll be picked up on a later refresh run
# # #                 # once its status flips to Closed.
# # #                 continue
# # #             record_id = f"NR-{t.ticket_id}"
# # #             content_hash = _content_hash(t)
# # #             if existing_hashes.get(record_id) == content_hash:
# # #                 continue
# # #             rec = normalize_ticket(t)
# # #             rec.content_hash = content_hash
# # #             normalized.append(rec)

# # #     if SourceType.CHAT_RCA in sources:
# # #         for c in chats_rcas:
# # #             record_id = f"NR-{c.record_id}"
# # #             content_hash = _content_hash(c)
# # #             if existing_hashes.get(record_id) == content_hash:
# # #                 continue
# # #             rec = normalize_chat_or_rca(c, ticket_index)
# # #             rec.content_hash = content_hash
# # #             normalized.append(rec)

# # #     if SourceType.RELEASE_NOTE in sources:
# # #         for r in release_notes:
# # #             record_id = f"NR-{r.release_id}"
# # #             content_hash = _content_hash(r)
# # #             if existing_hashes.get(record_id) == content_hash:
# # #                 continue
# # #             rec = normalize_release_note(r)
# # #             rec.content_hash = content_hash
# # #             normalized.append(rec)

# # #     if SourceType.RUNBOOK in sources:
# # #         for rb in runbooks:
# # #             record_id = f"NR-{rb.runbook_id}"
# # #             content_hash = _content_hash(rb)
# # #             if existing_hashes.get(record_id) == content_hash:
# # #                 continue
# # #             rec = normalize_runbook(rb)
# # #             rec.content_hash = content_hash
# # #             normalized.append(rec)

# # #     return normalized


# # # if __name__ == "__main__":
# # #     records = ingest_all()
# # #     print(f"Ingested & normalized {len(records)} records from 4 sources:")
# # #     from collections import Counter

# # #     by_source = Counter(r.source_type.value for r in records)
# # #     for src, count in by_source.items():
# # #         print(f"  - {src}: {count}")
# # #     avg_conf = sum(r.tagging_confidence for r in records) / len(records)
# # #     print(f"Average tagging confidence: {avg_conf:.2f}")


# # """
# # Stage 1: Ingestion & Normalization
# # (diagram box: "Ingests all sources and tags content by Guidewire module,
# # issue category, severity and resolution type against a human-defined
# # taxonomy.")

# # Responsibilities:
# #   1. Load + schema-validate each of the 4 raw sources (fails loudly on a
# #      malformed record rather than silently dropping it).
# #   2. Map each source's native shape into the single NormalizedRecord shape.
# #   3. Tag every record against the taxonomy (module / category / severity /
# #      resolution_type) and attach a tagging_confidence score.

# # Tagging split:
# #   - module / severity are resolved deterministically from fields already
# #     present in the raw source (e.g. a ticket's module_reported / priority),
# #     with a rule-based fallback only where the raw data doesn't give a
# #     direct answer (see _resolve_module()). When that deterministic
# #     resolution is itself low-confidence (the fallback branch, not a real
# #     match), the record falls back to the LLM's own independent module/
# #     severity read of the narrative text instead -- see
# #     MODULE_LLM_FALLBACK_THRESHOLD / SEVERITY_LLM_FALLBACK_THRESHOLD below.
# #     High-confidence deterministic signals (an exact module_reported match,
# #     a ticket's priority code, severity inherited from a linked ticket) are
# #     never overridden -- those are more reliable than an LLM guess.
# #   - category / resolution_type are always tagged by an LLM (Azure OpenAI
# #     chat deployment, via _llm_classify()): it's given the record's narrative
# #     text plus the *fixed list* of valid IssueCategory/ResolutionType
# #     values from taxonomy.py, and returns a structured pick for each, a
# #     self-reported confidence (0-1), and a short reasoning string (kept in
# #     tagging_reasoning, so a human can still see *why* -- same transparency
# #     goal the original keyword classifier had, now backed by a real model
# #     instead of hand-written keyword lists). The same single LLM call also
# #     returns an independent module/severity opinion (used only as the
# #     fallback described above), so this costs no extra API call per record.

# # Ticket status filter:
# #   - Only a ticket whose `status` is in CLOSED_TICKET_STATUSES is ingested
# #     at all -- an open/in-progress ticket is skipped entirely (not even
# #     sent to SME review) until a later refresh run sees it closed. A
# #     closed ticket's resolution_notes is a finished, trustworthy SOP; an
# #     open one's isn't.

# # Selective ingestion / duplicate-run prevention:
# #   - ingest_all(sources=...) lets a caller ingest only a subset of the 4
# #     sources. Omitting `sources` (the default) ingests all 4, same as
# #     before.
# #   - ingest_all(existing_hashes=...) lets a caller skip re-running the LLM
# #     tagging call entirely for any record whose raw source fields are
# #     byte-for-byte identical to the last run that fully processed it (see
# #     _content_hash() and knowledge_store.fetch_ingestion_hashes()). Omitting
# #     `existing_hashes` (the default) re-processes everything selected, same
# #     as before.
# # """

# # from __future__ import annotations

# # import hashlib
# # import json
# # import os
# # from pathlib import Path

# # from langchain_openai import AzureChatOpenAI
# # from pydantic import BaseModel, Field
# # from dotenv import load_dotenv
# # load_dotenv()

# # from schemas import (
# #     NormalizedRecord,
# #     RawChatOrRCA,
# #     RawReleaseNote,
# #     RawRunbook,
# #     RawTicket,
# # )
# # from taxonomy import (
# #     GuidewireModule,
# #     IssueCategory,
# #     ResolutionType,
# #     Severity,
# #     SourceType,
# # )

# # DATA_DIR = Path(__file__).parent / "data" / "raw"

# # CLOSED_TICKET_STATUSES = {"Closed"}

# # PRIORITY_TO_SEVERITY = {
# #     "P1": Severity.EMERGENCY,
# #     "P2": Severity.HIGH,
# #     "P3": Severity.NORMAL,
# #     "P4": Severity.LOW,
# # }

# # MODULE_ALIASES = {m.value.lower(): m for m in GuidewireModule}

# # # Below these thresholds, the deterministic module/severity resolution is
# # # itself just a fallback guess (not a real match) -- so the LLM's own
# # # independent read of the record's narrative text is used instead. These
# # # line up with the existing confidence values already in this file: the
# # # module fallback (0.3) and the weakest severity heuristics (ticket
# # # priority not recognized: 0.4; chat/RCA with no linked ticket: 0.5;
# # # runbook keyword guess: 0.55) all fall below their threshold and get
# # # overridden; every high-confidence deterministic path (exact module
# # # match: 1.0, fuzzy match: 0.7, ticket priority code: 1.0, severity
# # # inherited from a linked ticket: 0.95, release-note note_type: 0.75)
# # # stays untouched.
# # MODULE_LLM_FALLBACK_THRESHOLD = 0.5
# # SEVERITY_LLM_FALLBACK_THRESHOLD = 0.6

# # # Maps the CLI/API-friendly source keys used for selective ingestion to
# # # their SourceType enum value.
# # SOURCE_KEY_TO_TYPE: dict[str, SourceType] = {
# #     "tickets": SourceType.TICKET,
# #     "chat_rca": SourceType.CHAT_RCA,
# #     "release_notes": SourceType.RELEASE_NOTE,
# #     "runbooks": SourceType.RUNBOOK,
# # }


# # # ---------------------------------------------------------------------------
# # # LLM-based classifier: category / resolution_type (always used) plus an
# # # independent module / severity opinion (used only as a fallback -- see
# # # MODULE_LLM_FALLBACK_THRESHOLD / SEVERITY_LLM_FALLBACK_THRESHOLD).
# # # ---------------------------------------------------------------------------
# # class _TaggingResult(BaseModel):
# #     summary: str = Field(description="One-line title (10-15 words max) capturing the core issue and resolution, for display to human reviewers.")
# #     resolution_summary: str = Field(
# #         description="A clear narrative (2-4 sentences) of how this issue was actually resolved -- "
# #         "the root cause and the concrete fix/action taken, written for an engineer who hits the "
# #         "same issue later. Synthesize this from the record text, don't just copy it verbatim. If "
# #         "the record is a known issue, feature note, or runbook without a single incident "
# #         "resolution, describe what it is and the recommended action/procedure instead."
# #     )
# #     category: IssueCategory = Field(description="Best-matching issue category for this record.")
# #     resolution_type: ResolutionType = Field(description="Best-matching resolution type for this record.")
# #     module: GuidewireModule = Field(
# #         description="Best-matching Guidewire module for this record, judged independently from the record text."
# #     )
# #     severity: Severity = Field(
# #         description="Best-matching severity for this record, judged independently from the record text."
# #     )
# #     confidence: float = Field(ge=0.0, le=1.0, description="How confident you are in ALL FOUR picks above, 0-1.")
# #     reasoning: str = Field(description="One or two sentences on why these labels fit, for a human reviewer.")


# # _chat_client: AzureChatOpenAI | None = None
# # _structured_classifier = None


# # def _get_classifier():
# #     """Lazily build the Azure OpenAI chat client + structured-output
# #     wrapper, and reuse it across calls. Raises RuntimeError with a clear
# #     message if the required env vars aren't set."""
# #     global _chat_client, _structured_classifier
# #     if _structured_classifier is not None:
# #         return _structured_classifier

# #     required = [
# #         "AZURE_OPENAI_API_KEY",
# #         "AZURE_OPENAI_ENDPOINT",
# #         "AZURE_OPENAI_API_VERSION",
# #         "AZURE_OPENAI_CHAT_DEPLOYMENT",
# #     ]
# #     missing = [name for name in required if not os.getenv(name)]
# #     if missing:
# #         raise RuntimeError(
# #             "Missing Azure OpenAI env var(s) for tagging: "
# #             f"{', '.join(missing)}. Fill these in in .env (same Azure "
# #             "OpenAI resource/deployment as the Underwriting Solution project)."
# #         )

# #     _chat_client = AzureChatOpenAI(
# #         azure_deployment=os.getenv("AZURE_OPENAI_CHAT_DEPLOYMENT"),
# #         openai_api_key=os.getenv("AZURE_OPENAI_API_KEY"),
# #         openai_api_version=os.getenv("AZURE_OPENAI_API_VERSION"),
# #         azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
# #         temperature=0,
# #     )
# #     _structured_classifier = _chat_client.with_structured_output(_TaggingResult)
# #     return _structured_classifier


# # # One-line definitions for every taxonomy label, so the model isn't just
# # # guessing intended meaning from a bare enum name (e.g. distinguishing
# # # UpgradeRegression from ConfigurationError or BatchJobFailure).
# # CATEGORY_DESCRIPTIONS: dict[IssueCategory, str] = {
# #     IssueCategory.PERFORMANCE_DEGRADATION: "The system works but is too slow -- latency, timeouts, slow page loads under load.",
# #     IssueCategory.INTEGRATION_FAILURE: "A failure exchanging data with an external system (SFTP, bank, CRM, API) -- rejected files, failed calls, mapping mismatches at the boundary.",
# #     IssueCategory.DATA_CORRUPTION: "Data already in the system is wrong, duplicated, or missing -- not a live integration failure.",
# #     IssueCategory.CONFIGURATION_ERROR: "Wrong or outdated configuration (mapping, filter, field setting) causing incorrect behavior, NOT caused by a Guidewire version upgrade.",
# #     IssueCategory.UPGRADE_REGRESSION: "A break caused specifically by a Guidewire version upgrade -- a renamed/removed API, changed behavior right after deploying a new version.",
# #     IssueCategory.AUTHENTICATION_ACCESS: "Login, session, SSO, or access/permission failures.",
# #     IssueCategory.BATCH_JOB_FAILURE: "A scheduled/nightly/batch job failed or was killed, for reasons unrelated to a version upgrade or live user request.",
# #     IssueCategory.UI_RENDERING_BUG: "A visual/layout/rendering defect -- the underlying data and logic are correct, only the display is wrong.",
# #     IssueCategory.LICENSING: "A license pool, seat, or entitlement problem blocking usage.",
# # }

# # RESOLUTION_DESCRIPTIONS: dict[ResolutionType, str] = {
# #     ResolutionType.CONFIG_CHANGE: "Fixed by changing configuration/mapping/settings -- no code change.",
# #     ResolutionType.CODE_FIX: "Fixed by changing custom code (Gosu rules, widgets, etc.) and redeploying.",
# #     ResolutionType.PATCH_APPLIED: "Fixed by applying a Guidewire-provided patch or hotfix.",
# #     ResolutionType.RESTART_RECYCLE: "Fixed by restarting/recycling a service or process -- no underlying code or config change.",
# #     ResolutionType.DATA_REMEDIATION: "Fixed by directly cleaning up or correcting data (de-dup, backfill, reprocess).",
# #     ResolutionType.VENDOR_ESCALATION: "Resolution required escalating to Guidewire/vendor support -- not fixed internally.",
# #     ResolutionType.WORKAROUND: "A temporary mitigation was applied (feature flag, manual workaround) -- root cause not fully fixed.",
# #     ResolutionType.USER_ERROR_CLOSED: "Closed because there was no defect -- user error or misunderstanding.",
# #     ResolutionType.NO_ACTION_BY_DESIGN: "Closed with no action because the behavior was correct/expected (informational only).",
# # }

# # MODULE_DESCRIPTIONS: dict[GuidewireModule, str] = {
# #     GuidewireModule.POLICY_CENTER: "Policy administration -- quoting, binding, policy transactions, underwriting workflow.",
# #     GuidewireModule.CLAIM_CENTER: "Claims administration -- FNOL, claim handling, claims workflow, adjuster activities.",
# #     GuidewireModule.BILLING_CENTER: "Billing and invoicing -- payment plans, invoices, disbursements, billing schedules.",
# #     GuidewireModule.CONTACT_MANAGER: "Shared contact/account data used across Policy/Claim/BillingCenter -- contacts, addresses, duplicate/merge issues.",
# #     GuidewireModule.RATING_GOSU: "Rating engine or custom Gosu code -- rate calculation, custom business rules/scripts.",
# #     GuidewireModule.INTEGRATION_GATEWAY: "Integration layer between Guidewire and external systems -- SFTP, APIs, messaging, data exchange gateways.",
# #     GuidewireModule.DIGITAL_PORTAL: "Customer/agent-facing self-service portal or digital UI, separate from the core center application.",
# #     GuidewireModule.DATA_PLATFORM: "Underlying data/reporting/analytics platform -- data warehouse, ETL, reporting infrastructure.",
# # }

# # SEVERITY_DESCRIPTIONS: dict[Severity, str] = {
# #     Severity.EMERGENCY: "Production down or a major incident affecting business operations broadly.",
# #     Severity.HIGH: "A major feature is broken, but a workaround exists.",
# #     Severity.NORMAL: "Degraded but usable -- a non-blocking defect or noticeable but tolerable issue.",
# #     Severity.LOW: "Cosmetic or minor issue with negligible business impact.",
# # }

# # _SYSTEM_PROMPT = (
# #     "You are a precise taxonomy tagger for Guidewire support records. "
# #     "Pick exactly one value from each given list for category, "
# #     "resolution_type, module, and severity -- never invent a label outside "
# #     "any list. If the record is ambiguous, pick the closest fit and lower "
# #     "your confidence; never refuse to answer."
# # )

# # _FEW_SHOT_EXAMPLES = """Example 1:
# # Module (hint): BillingCenter
# # Record title: Nightly invoice batch job failing after 2025.3 upgrade
# # Record text: The nightly invoice generation batch job has failed every night since the 2025.3 upgrade was deployed. Error logs show a null pointer in the new invoice calculation step introduced by the upgrade. Rolled back the custom calculation override to match the new API signature and redeployed.
# # -> category: UpgradeRegression (the failure is a direct consequence of the upgrade changing an API, not an independent scheduling issue), resolution_type: CodeFix, module: BillingCenter (invoice generation is a billing function), severity: High (a nightly job is broken but the core application is otherwise usable), confidence: 0.9

# # Example 2:
# # Module (hint): ContactManager
# # Record title: Users intermittently logged out during peak hours
# # Record text: Users report being logged out randomly during business hours. No pattern found in logs tying this to a recent change. Restarting the session service temporarily resolves it for a few hours.
# # -> category: AuthenticationAccess, resolution_type: RestartRecycle, module: ContactManager (session/login handling spans all centers via shared contact/access infrastructure), severity: High (a major feature -- staying logged in -- is broken, but restarting the service is a working mitigation), confidence: 0.6 (temporary mitigation only, root cause not identified)
# # """

# # _TAGGING_PROMPT = """Category options:
# # {category_defs}

# # Resolution type options:
# # {resolution_defs}

# # Module options:
# # {module_defs}

# # Severity options:
# # {severity_defs}

# # {few_shot}
# # Now tag this record:

# # Module (hint): {module}
# # Record title: {title}
# # Record text:
# # {text}

# # The "Module (hint)" above comes from raw source metadata and may be wrong \
# # or missing (shown as "Unknown" when not provided) -- independently judge \
# # the best-fitting module from the record text itself for your `module` \
# # answer, the same way you judge category and resolution_type.

# # Confidence guide: 0.85-1.0 = the text explicitly states the cause and/or \
# # fix; 0.6-0.84 = inferred from strong contextual clues; below 0.6 = \
# # ambiguous or largely guessed.

# # Pick exactly one category, resolution_type, module, and severity from the \
# # lists above, using only the record above. If the text doesn't clearly \
# # indicate a resolution yet (e.g. it's a known issue or feature note, not an \
# # incident), still pick the closest-fitting resolution_type and lower your \
# # confidence accordingly.

# # Also generate a concise one-line summary (10-15 words max) that captures \
# # the core issue and resolution for display to human reviewers. If a \
# # "Record title" is already provided above, you may use it as-is or refine \
# # it; if the title is "(not provided)", generate one from the record text.

# # Also generate a resolution_summary: a clear, synthesized narrative (2-4 \
# # sentences) of how this issue was actually resolved -- state the root \
# # cause and the concrete fix/action taken, written for an engineer who \
# # hits the same issue later. Do not just copy a sentence from the record \
# # text verbatim -- rewrite it as a clean, standalone explanation. If this \
# # record has no single incident resolution (e.g. a feature note or a \
# # runbook procedure), describe what it is and the recommended action \
# # instead."""


# # def _llm_classify(module: str, title: str, text: str) -> _TaggingResult:
# #     """Single LLM call per record: returns a _TaggingResult with category,
# #     resolution_type, module, severity, confidence, reasoning.

# #     category/resolution_type are always used by the caller. module/severity
# #     are an independent LLM opinion that the caller (normalize_*() below)
# #     only falls back to when its own deterministic resolution for that
# #     specific field was itself low-confidence -- see
# #     MODULE_LLM_FALLBACK_THRESHOLD / SEVERITY_LLM_FALLBACK_THRESHOLD."""
# #     classifier = _get_classifier()
# #     category_defs = "\n".join(f"- {c.value}: {desc}" for c, desc in CATEGORY_DESCRIPTIONS.items())
# #     resolution_defs = "\n".join(f"- {r.value}: {desc}" for r, desc in RESOLUTION_DESCRIPTIONS.items())
# #     module_defs = "\n".join(f"- {m.value}: {desc}" for m, desc in MODULE_DESCRIPTIONS.items())
# #     severity_defs = "\n".join(f"- {s.value}: {desc}" for s, desc in SEVERITY_DESCRIPTIONS.items())
# #     human_prompt = _TAGGING_PROMPT.format(
# #         category_defs=category_defs,
# #         resolution_defs=resolution_defs,
# #         module_defs=module_defs,
# #         severity_defs=severity_defs,
# #         few_shot=_FEW_SHOT_EXAMPLES,
# #         module=module,
# #         title=title,
# #         text=text,
# #     )
# #     result: _TaggingResult = classifier.invoke(
# #         [("system", _SYSTEM_PROMPT), ("human", human_prompt)]
# #     )
# #     result.confidence = round(result.confidence, 2)
# #     return result


# # def _resolve_module(raw_module_text: str | None) -> tuple:
# #     if not raw_module_text or not raw_module_text.strip():
# #         return GuidewireModule.POLICY_CENTER, 0.0  # always below threshold → LLM decides
# #     key = raw_module_text.strip().lower()
# #     if key in MODULE_ALIASES:
# #         return MODULE_ALIASES[key], 1.0
# #     # fuzzy: substring match
# #     for alias, module in MODULE_ALIASES.items():
# #         if alias in key or key in alias:
# #             return module, 0.7
# #     return GuidewireModule.POLICY_CENTER, 0.3


# # # ---------------------------------------------------------------------------
# # # Per-source normalizers
# # # ---------------------------------------------------------------------------
# # def normalize_ticket(raw: RawTicket) -> NormalizedRecord:
# #     # module_reported is optional: if present, resolve deterministically;
# #     # if absent, pass "Unknown" as the LLM hint and let the LLM decide.
# #     if raw.module_reported:
# #         module, module_conf = _resolve_module(raw.module_reported)
# #         module_hint = module.value
# #     else:
# #         module = GuidewireModule.POLICY_CENTER  # placeholder; overridden below (conf < threshold)
# #         module_conf = 0.0
# #         module_hint = "Unknown"

# #     # summary is no longer in RawTicket -- the LLM generates it.
# #     # raw_text is description + resolution_notes only (no summary);
# #     # the embedding is generated from raw_text alone (not title), so
# #     # it's based purely on the original raw data.
# #     combined_text = f"{raw.description} {raw.resolution_notes}"
# #     severity = PRIORITY_TO_SEVERITY.get(raw.priority, Severity.NORMAL)
# #     sev_conf = 1.0 if raw.priority in PRIORITY_TO_SEVERITY else 0.4

# #     tag_result = _llm_classify(module_hint, "(not provided)", combined_text)
# #     category, resolution, llm_conf, reasoning = (
# #         tag_result.category, tag_result.resolution_type, tag_result.confidence, tag_result.reasoning
# #     )

# #     # module/severity stay deterministic unless their own deterministic
# #     # confidence was itself a low-confidence fallback -- see
# #     # MODULE_LLM_FALLBACK_THRESHOLD / SEVERITY_LLM_FALLBACK_THRESHOLD.
# #     if module_conf < MODULE_LLM_FALLBACK_THRESHOLD:
# #         module, module_conf = tag_result.module, llm_conf
# #     if sev_conf < SEVERITY_LLM_FALLBACK_THRESHOLD:
# #         severity, sev_conf = tag_result.severity, llm_conf

# #     overall_conf = round((module_conf + sev_conf + 2 * llm_conf) / 4, 2)

# #     return NormalizedRecord(
# #         record_id=f"NR-{raw.ticket_id}",
# #         source_type=SourceType.TICKET,
# #         source_ref_id=raw.ticket_id,
# #         module=module,
# #         category=category,
# #         severity=severity,
# #         resolution_type=resolution,
# #         tagging_confidence=overall_conf,
# #         tagging_reasoning=reasoning,
# #         title=tag_result.summary,   # LLM-generated one-liner
# #         raw_text=combined_text,     # description + resolution_notes only (used for embedding)
# #         symptom_text=raw.description,                  # raw, shown as-is in structured_sop
# #         resolution_text=tag_result.resolution_summary, # LLM-generated, shown as-is in structured_sop
# #         guidewire_version=raw.guidewire_version,
# #         event_date=raw.closed_at.date(),
# #         source_metadata={
# #             "priority": raw.priority,
# #             "assignee_team": raw.assignee_team,
# #             "created_at": raw.created_at.isoformat(),
# #             "closed_at": raw.closed_at.isoformat(),
# #         },
# #     )


# # def normalize_chat_or_rca(raw: RawChatOrRCA, ticket_index: dict) -> NormalizedRecord:
# #     module, module_conf = _resolve_module(raw.module_reported)
# #     combined_text = f"{raw.title} {raw.body} {raw.root_cause or ''} {raw.corrective_actions or ''}"

# #     # severity: inherit from the linked ticket when available (more
# #     # reliable than guessing from free text), otherwise a simple fallback.
# #     if raw.incident_id and raw.incident_id in ticket_index:
# #         severity = ticket_index[raw.incident_id]["severity"]
# #         sev_conf = 0.95
# #     else:
# #         severity = Severity.HIGH if "outage" in combined_text.lower() else Severity.NORMAL
# #         sev_conf = 0.5

# #     tag_result = _llm_classify(module.value, raw.title, combined_text)
# #     category, resolution, llm_conf, reasoning = (
# #         tag_result.category, tag_result.resolution_type, tag_result.confidence, tag_result.reasoning
# #     )

# #     if module_conf < MODULE_LLM_FALLBACK_THRESHOLD:
# #         module, module_conf = tag_result.module, llm_conf
# #     if sev_conf < SEVERITY_LLM_FALLBACK_THRESHOLD:
# #         severity, sev_conf = tag_result.severity, llm_conf

# #     overall_conf = round((module_conf + sev_conf + 2 * llm_conf) / 4, 2)

# #     return NormalizedRecord(
# #         record_id=f"NR-{raw.record_id}",
# #         source_type=SourceType.CHAT_RCA,
# #         source_ref_id=raw.record_id,
# #         module=module,
# #         category=category,
# #         severity=severity,
# #         resolution_type=resolution,
# #         tagging_confidence=overall_conf,
# #         tagging_reasoning=reasoning,
# #         title=raw.title,
# #         raw_text=combined_text,
# #         symptom_text=raw.body,
# #         resolution_text=tag_result.resolution_summary,
# #         guidewire_version=None,
# #         event_date=raw.date,
# #         source_metadata={
# #             "record_type": raw.record_type,
# #             "incident_id": raw.incident_id,
# #             "participants": raw.participants,
# #             "root_cause": raw.root_cause,
# #             "corrective_actions": raw.corrective_actions,
# #         },
# #     )


# # def normalize_release_note(raw: RawReleaseNote) -> NormalizedRecord:
# #     module, module_conf = _resolve_module(raw.affected_modules[0] if raw.affected_modules else raw.product)
# #     combined_text = f"{raw.title} {raw.details}"

# #     severity_by_note_type = {
# #         "known_issue": Severity.HIGH,
# #         "resolved_issue": Severity.NORMAL,
# #         "feature": Severity.LOW,
# #     }
# #     severity = severity_by_note_type.get(raw.note_type, Severity.NORMAL)
# #     sev_conf = 0.75

# #     tag_result = _llm_classify(module.value, raw.title, combined_text)
# #     category, resolution, llm_conf, reasoning = (
# #         tag_result.category, tag_result.resolution_type, tag_result.confidence, tag_result.reasoning
# #     )

# #     if module_conf < MODULE_LLM_FALLBACK_THRESHOLD:
# #         module, module_conf = tag_result.module, llm_conf
# #     if sev_conf < SEVERITY_LLM_FALLBACK_THRESHOLD:
# #         severity, sev_conf = tag_result.severity, llm_conf

# #     overall_conf = round((module_conf + sev_conf + 2 * llm_conf) / 4, 2)

# #     return NormalizedRecord(
# #         record_id=f"NR-{raw.release_id}",
# #         source_type=SourceType.RELEASE_NOTE,
# #         source_ref_id=raw.release_id,
# #         module=module,
# #         category=category,
# #         severity=severity,
# #         resolution_type=resolution,
# #         tagging_confidence=overall_conf,
# #         tagging_reasoning=reasoning,
# #         title=raw.title,
# #         raw_text=combined_text,
# #         symptom_text=raw.details,
# #         resolution_text=tag_result.resolution_summary,
# #         guidewire_version=f"{raw.product} {raw.version}",
# #         event_date=raw.release_date,
# #         source_url=raw.source_url,
# #         source_metadata={
# #             "note_type": raw.note_type,
# #             "product": raw.product,
# #             "affected_modules": raw.affected_modules,
# #         },
# #     )


# # def normalize_runbook(raw: RawRunbook) -> NormalizedRecord:
# #     module, module_conf = _resolve_module(raw.module_reported)
# #     combined_text = f"{raw.title} {raw.trigger_condition} {' '.join(raw.steps)}"

# #     severity = (
# #         Severity.EMERGENCY
# #         if "outage" in raw.trigger_condition.lower() or "unresponsive" in raw.trigger_condition.lower()
# #         else Severity.NORMAL
# #     )
# #     sev_conf = 0.55  # runbooks apply across a severity range; this is a default, not a measured value

# #     tag_result = _llm_classify(module.value, raw.title, combined_text)
# #     category, resolution, llm_conf, reasoning = (
# #         tag_result.category, tag_result.resolution_type, tag_result.confidence, tag_result.reasoning
# #     )

# #     if module_conf < MODULE_LLM_FALLBACK_THRESHOLD:
# #         module, module_conf = tag_result.module, llm_conf
# #     if sev_conf < SEVERITY_LLM_FALLBACK_THRESHOLD:
# #         severity, sev_conf = tag_result.severity, llm_conf

# #     overall_conf = round((module_conf + sev_conf + 2 * llm_conf) / 4, 2)

# #     return NormalizedRecord(
# #         record_id=f"NR-{raw.runbook_id}",
# #         source_type=SourceType.RUNBOOK,
# #         source_ref_id=raw.runbook_id,
# #         module=module,
# #         category=category,
# #         severity=severity,
# #         resolution_type=resolution,
# #         tagging_confidence=overall_conf,
# #         tagging_reasoning=reasoning,
# #         title=raw.title,
# #         raw_text=combined_text,
# #         symptom_text=raw.trigger_condition,
# #         # Runbook steps are an exact operational procedure -- format them
# #         # directly rather than letting the LLM paraphrase them, so exact
# #         # command/script names in the steps are never altered.
# #         resolution_text="\n".join(f"{i}. {step}" for i, step in enumerate(raw.steps, start=1)),
# #         guidewire_version=None,
# #         event_date=raw.last_updated,
# #         source_metadata={
# #             "owner_team": raw.owner_team,
# #             "trigger_condition": raw.trigger_condition,
# #             "steps": raw.steps,
# #         },
# #     )


# # # ---------------------------------------------------------------------------
# # # Public entrypoint
# # # ---------------------------------------------------------------------------
# # def load_raw(filename: str, model) -> list:
# #     path = DATA_DIR / filename
# #     raw_items = json.loads(path.read_text(encoding="utf-8"))
# #     return [model.model_validate(item) for item in raw_items]


# # def _content_hash(raw: BaseModel) -> str:
# #     """Stable hash of a raw record's own fields (before any tagging),
# #     used to detect whether it has changed since the last run that fully
# #     processed it. Deliberately hashes the raw input, not the normalized/
# #     tagged output, so a hash match means "nothing to re-tag" -- not just
# #     "the last tagging result happened to look the same"."""
# #     payload = raw.model_dump(mode="json")
# #     return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


# # def ingest_all(
# #     sources: list[SourceType] | None = None,
# #     existing_hashes: dict[str, str] | None = None,
# # ) -> list[NormalizedRecord]:
# #     """
# #     sources: which of the 4 source types to ingest this run. None (the
# #         default) ingests all 4 -- identical to the original behavior.
# #     existing_hashes: {record_id: content_hash} already stored in the
# #         Knowledge Store from a prior run (see
# #         knowledge_store.fetch_ingestion_hashes()). A record whose freshly
# #         computed content hash matches the stored one is skipped entirely --
# #         no LLM tagging call, not included in the returned list -- since
# #         nothing about its raw source fields has changed since it was last
# #         fully processed. None/{} (the default) re-processes everything
# #         selected, identical to the original behavior.
# #     """
# #     if sources is None:
# #         sources = list(SourceType)
# #     existing_hashes = existing_hashes or {}

# #     tickets = load_raw("tickets.json", RawTicket)
# #     chats_rcas = load_raw("chat_rca.json", RawChatOrRCA)
# #     release_notes = load_raw("release_notes.json", RawReleaseNote)
# #     runbooks = load_raw("runbooks.json", RawRunbook)

# #     # Built from ALL tickets regardless of `sources`/skip state: chat/RCA
# #     # severity inheritance needs it, and ticket severity is a free,
# #     # deterministic lookup (no LLM call involved), so there's no cost to
# #     # always having it available even when tickets themselves aren't
# #     # selected or are being skipped as unchanged this run.
# #     ticket_index = {
# #         t.ticket_id: {"severity": PRIORITY_TO_SEVERITY.get(t.priority, Severity.NORMAL)}
# #         for t in tickets
# #     }

# #     normalized: list[NormalizedRecord] = []

# #     if SourceType.TICKET in sources:
# #         for t in tickets:
# #             if t.status not in CLOSED_TICKET_STATUSES:
# #                 # Open/in-progress tickets are never ingested into the KB --
# #                 # only a closed ticket's resolution is a trustworthy,
# #                 # finished SOP. It'll be picked up on a later refresh run
# #                 # once its status flips to Closed.
# #                 continue
# #             record_id = f"NR-{t.ticket_id}"
# #             content_hash = _content_hash(t)
# #             if existing_hashes.get(record_id) == content_hash:
# #                 continue
# #             rec = normalize_ticket(t)
# #             rec.content_hash = content_hash
# #             normalized.append(rec)

# #     if SourceType.CHAT_RCA in sources:
# #         for c in chats_rcas:
# #             record_id = f"NR-{c.record_id}"
# #             content_hash = _content_hash(c)
# #             if existing_hashes.get(record_id) == content_hash:
# #                 continue
# #             rec = normalize_chat_or_rca(c, ticket_index)
# #             rec.content_hash = content_hash
# #             normalized.append(rec)

# #     if SourceType.RELEASE_NOTE in sources:
# #         for r in release_notes:
# #             record_id = f"NR-{r.release_id}"
# #             content_hash = _content_hash(r)
# #             if existing_hashes.get(record_id) == content_hash:
# #                 continue
# #             rec = normalize_release_note(r)
# #             rec.content_hash = content_hash
# #             normalized.append(rec)

# #     if SourceType.RUNBOOK in sources:
# #         for rb in runbooks:
# #             record_id = f"NR-{rb.runbook_id}"
# #             content_hash = _content_hash(rb)
# #             if existing_hashes.get(record_id) == content_hash:
# #                 continue
# #             rec = normalize_runbook(rb)
# #             rec.content_hash = content_hash
# #             normalized.append(rec)

# #     return normalized


# # if __name__ == "__main__":
# #     records = ingest_all()
# #     print(f"Ingested & normalized {len(records)} records from 4 sources:")
# #     from collections import Counter

# #     by_source = Counter(r.source_type.value for r in records)
# #     for src, count in by_source.items():
# #         print(f"  - {src}: {count}")
# #     avg_conf = sum(r.tagging_confidence for r in records) / len(records)
# #     print(f"Average tagging confidence: {avg_conf:.2f}")


# """
# Stage 1: Ingestion & Normalization
# (diagram box: "Ingests all sources and tags content by Guidewire module,
# issue category, severity and resolution type against a human-defined
# taxonomy.")

# Responsibilities:
#   1. Load + schema-validate each of the 4 raw sources (fails loudly on a
#      malformed record rather than silently dropping it).
#   2. Map each source's native shape into the single NormalizedRecord shape.
#   3. Tag every record against the taxonomy (module / category / severity /
#      resolution_type) and attach a tagging_confidence score.

# Tagging split:
#   - module / severity are resolved deterministically from fields already
#     present in the raw source (e.g. a ticket's module_reported / priority),
#     with a rule-based fallback only where the raw data doesn't give a
#     direct answer (see _resolve_module()). When that deterministic
#     resolution is itself low-confidence (the fallback branch, not a real
#     match), the record falls back to the LLM's own independent module/
#     severity read of the narrative text instead -- see
#     MODULE_LLM_FALLBACK_THRESHOLD / SEVERITY_LLM_FALLBACK_THRESHOLD below.
#     High-confidence deterministic signals (an exact module_reported match,
#     a ticket's priority code, severity inherited from a linked ticket) are
#     never overridden -- those are more reliable than an LLM guess.
#   - category / resolution_type are always tagged by an LLM (Azure OpenAI
#     chat deployment, via _llm_classify()): it's given the record's narrative
#     text plus the *fixed list* of valid IssueCategory/ResolutionType
#     values from taxonomy.py, and returns a structured pick for each, a
#     self-reported confidence (0-1), and a short reasoning string (kept in
#     tagging_reasoning, so a human can still see *why* -- same transparency
#     goal the original keyword classifier had, now backed by a real model
#     instead of hand-written keyword lists). The same single LLM call also
#     returns an independent module/severity opinion (used only as the
#     fallback described above), so this costs no extra API call per record.

# Ticket status filter:
#   - Only a ticket whose `status` is in CLOSED_TICKET_STATUSES is ingested
#     at all -- an open/in-progress ticket is skipped entirely (not even
#     sent to SME review) until a later refresh run sees it closed. A
#     closed ticket's resolution_notes is a finished, trustworthy SOP; an
#     open one's isn't.

# Selective ingestion / duplicate-run prevention:
#   - ingest_all(sources=...) lets a caller ingest only a subset of the 4
#     sources. Omitting `sources` (the default) ingests all 4, same as
#     before.
#   - ingest_all(existing_hashes=...) lets a caller skip re-running the LLM
#     tagging call entirely for any record whose raw source fields are
#     byte-for-byte identical to the last run that fully processed it (see
#     _content_hash() and knowledge_store.fetch_ingestion_hashes()). Omitting
#     `existing_hashes` (the default) re-processes everything selected, same
#     as before.
# """

# from __future__ import annotations

# import hashlib
# import json
# import os
# from pathlib import Path

# from langchain_openai import AzureChatOpenAI
# from pydantic import BaseModel, Field
# from dotenv import load_dotenv
# load_dotenv()

# from schemas import (
#     NormalizedRecord,
#     RawChatOrRCA,
#     RawReleaseNote,
#     RawRunbook,
#     RawTicket,
# )
# from taxonomy import (
#     GuidewireModule,
#     IssueCategory,
#     ResolutionType,
#     Severity,
#     SourceType,
# )

# DATA_DIR = Path(__file__).parent / "data" / "raw"

# CLOSED_TICKET_STATUSES = {"Closed"}

# PRIORITY_TO_SEVERITY = {
#     "P1": Severity.EMERGENCY,
#     "P2": Severity.HIGH,
#     "P3": Severity.NORMAL,
#     "P4": Severity.LOW,
# }

# MODULE_ALIASES = {m.value.lower(): m for m in GuidewireModule}

# # Below these thresholds, the deterministic module/severity resolution is
# # itself just a fallback guess (not a real match) -- so the LLM's own
# # independent read of the record's narrative text is used instead. These
# # line up with the existing confidence values already in this file: the
# # module fallback (0.3) and the weakest severity heuristics (ticket
# # priority not recognized: 0.4; chat/RCA with no linked ticket: 0.5;
# # runbook keyword guess: 0.55) all fall below their threshold and get
# # overridden; every high-confidence deterministic path (exact module
# # match: 1.0, fuzzy match: 0.7, ticket priority code: 1.0, severity
# # inherited from a linked ticket: 0.95, release-note note_type: 0.75)
# # stays untouched.
# MODULE_LLM_FALLBACK_THRESHOLD = 0.5
# SEVERITY_LLM_FALLBACK_THRESHOLD = 0.6

# # Maps the CLI/API-friendly source keys used for selective ingestion to
# # their SourceType enum value.
# SOURCE_KEY_TO_TYPE: dict[str, SourceType] = {
#     "tickets": SourceType.TICKET,
#     "chat_rca": SourceType.CHAT_RCA,
#     "release_notes": SourceType.RELEASE_NOTE,
#     "runbooks": SourceType.RUNBOOK,
# }


# # ---------------------------------------------------------------------------
# # LLM-based classifier: category / resolution_type (always used) plus an
# # independent module / severity opinion (used only as a fallback -- see
# # MODULE_LLM_FALLBACK_THRESHOLD / SEVERITY_LLM_FALLBACK_THRESHOLD).
# # ---------------------------------------------------------------------------
# class _TaggingResult(BaseModel):
#     summary: str = Field(description="One-line title (10-15 words max) capturing the core issue and resolution, for display to human reviewers.")
#     resolution_summary: str = Field(
#         description="A clear narrative (2-4 sentences) of how this issue was actually resolved -- "
#         "the root cause and the concrete fix/action taken, written for an engineer who hits the "
#         "same issue later. Synthesize this from the record text, don't just copy it verbatim. If "
#         "the record is a known issue, feature note, or runbook without a single incident "
#         "resolution, describe what it is and the recommended action/procedure instead."
#     )
#     category: IssueCategory = Field(description="Best-matching issue category for this record.")
#     resolution_type: ResolutionType = Field(description="Best-matching resolution type for this record.")
#     module: GuidewireModule = Field(
#         description="Best-matching Guidewire module for this record, judged independently from the record text."
#     )
#     severity: Severity = Field(
#         description="Best-matching severity for this record, judged independently from the record text."
#     )
#     confidence: float = Field(ge=0.0, le=1.0, description="How confident you are in ALL FOUR picks above, 0-1.")
#     reasoning: str = Field(
#         description="One or two sentences on why these labels fit, for a human reviewer. "
#         "MUST reference the exact severity, module, category, and resolution_type values "
#         "you returned in the structured fields above -- never mention an alternative value."
#     )


# _chat_client: AzureChatOpenAI | None = None
# _structured_classifier = None


# def _get_classifier():
#     """Lazily build the Azure OpenAI chat client + structured-output
#     wrapper, and reuse it across calls. Raises RuntimeError with a clear
#     message if the required env vars aren't set."""
#     global _chat_client, _structured_classifier
#     if _structured_classifier is not None:
#         return _structured_classifier

#     required = [
#         "AZURE_OPENAI_API_KEY",
#         "AZURE_OPENAI_ENDPOINT",
#         "AZURE_OPENAI_API_VERSION",
#         "AZURE_OPENAI_CHAT_DEPLOYMENT",
#     ]
#     missing = [name for name in required if not os.getenv(name)]
#     if missing:
#         raise RuntimeError(
#             "Missing Azure OpenAI env var(s) for tagging: "
#             f"{', '.join(missing)}. Fill these in in .env (same Azure "
#             "OpenAI resource/deployment as the Underwriting Solution project)."
#         )

#     _chat_client = AzureChatOpenAI(
#         azure_deployment=os.getenv("AZURE_OPENAI_CHAT_DEPLOYMENT"),
#         openai_api_key=os.getenv("AZURE_OPENAI_API_KEY"),
#         openai_api_version=os.getenv("AZURE_OPENAI_API_VERSION"),
#         azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
#         temperature=0,
#     )
#     _structured_classifier = _chat_client.with_structured_output(_TaggingResult)
#     return _structured_classifier


# # One-line definitions for every taxonomy label, so the model isn't just
# # guessing intended meaning from a bare enum name (e.g. distinguishing
# # UpgradeRegression from ConfigurationError or BatchJobFailure).
# CATEGORY_DESCRIPTIONS: dict[IssueCategory, str] = {
#     IssueCategory.PERFORMANCE_DEGRADATION: "The system works but is too slow -- latency, timeouts, slow page loads under load.",
#     IssueCategory.INTEGRATION_FAILURE: "A failure exchanging data with an external system (SFTP, bank, CRM, API) -- rejected files, failed calls, mapping mismatches at the boundary.",
#     IssueCategory.DATA_CORRUPTION: "Data already in the system is wrong, duplicated, or missing -- not a live integration failure.",
#     IssueCategory.CONFIGURATION_ERROR: "Wrong or outdated configuration (mapping, filter, field setting) causing incorrect behavior, NOT caused by a Guidewire version upgrade.",
#     IssueCategory.UPGRADE_REGRESSION: "A break caused specifically by a Guidewire version upgrade -- a renamed/removed API, changed behavior right after deploying a new version.",
#     IssueCategory.AUTHENTICATION_ACCESS: "Login, session, SSO, or access/permission failures.",
#     IssueCategory.BATCH_JOB_FAILURE: "A scheduled/nightly/batch job failed or was killed, for reasons unrelated to a version upgrade or live user request.",
#     IssueCategory.UI_RENDERING_BUG: "A visual/layout/rendering defect -- the underlying data and logic are correct, only the display is wrong.",
#     IssueCategory.LICENSING: "A license pool, seat, or entitlement problem blocking usage.",
# }

# RESOLUTION_DESCRIPTIONS: dict[ResolutionType, str] = {
#     ResolutionType.CONFIG_CHANGE: "Fixed by changing configuration/mapping/settings -- no code change.",
#     ResolutionType.CODE_FIX: "Fixed by changing custom code (Gosu rules, widgets, etc.) and redeploying.",
#     ResolutionType.PATCH_APPLIED: "Fixed by applying a Guidewire-provided patch or hotfix.",
#     ResolutionType.RESTART_RECYCLE: "Fixed by restarting/recycling a service or process -- no underlying code or config change.",
#     ResolutionType.DATA_REMEDIATION: "Fixed by directly cleaning up or correcting data (de-dup, backfill, reprocess).",
#     ResolutionType.VENDOR_ESCALATION: "Resolution required escalating to Guidewire/vendor support -- not fixed internally.",
#     ResolutionType.WORKAROUND: "A temporary mitigation was applied (feature flag, manual workaround) -- root cause not fully fixed.",
#     ResolutionType.USER_ERROR_CLOSED: "Closed because there was no defect -- user error or misunderstanding.",
#     ResolutionType.NO_ACTION_BY_DESIGN: "Closed with no action because the behavior was correct/expected (informational only).",
# }

# MODULE_DESCRIPTIONS: dict[GuidewireModule, str] = {
#     GuidewireModule.POLICY_CENTER: "Policy administration -- quoting, binding, policy transactions, underwriting workflow.",
#     GuidewireModule.CLAIM_CENTER: "Claims administration -- FNOL, claim handling, claims workflow, adjuster activities.",
#     GuidewireModule.BILLING_CENTER: "Billing and invoicing -- payment plans, invoices, disbursements, billing schedules.",
#     GuidewireModule.CONTACT_MANAGER: "Shared contact/account data used across Policy/Claim/BillingCenter -- contacts, addresses, duplicate/merge issues.",
#     GuidewireModule.RATING_GOSU: "Rating engine or custom Gosu code -- rate calculation, custom business rules/scripts.",
#     GuidewireModule.INTEGRATION_GATEWAY: "Integration layer between Guidewire and external systems -- SFTP, APIs, messaging, data exchange gateways.",
#     GuidewireModule.DIGITAL_PORTAL: "Customer/agent-facing self-service portal or digital UI, separate from the core center application.",
#     GuidewireModule.DATA_PLATFORM: "Underlying data/reporting/analytics platform -- data warehouse, ETL, reporting infrastructure.",
# }

# SEVERITY_DESCRIPTIONS: dict[Severity, str] = {
#     Severity.EMERGENCY: "Production down or a major incident affecting business operations broadly.",
#     Severity.HIGH: "A major feature is broken, but a workaround exists.",
#     Severity.NORMAL: "Degraded but usable -- a non-blocking defect or noticeable but tolerable issue.",
#     Severity.LOW: "Cosmetic or minor issue with negligible business impact.",
# }

# _SYSTEM_PROMPT = (
#     "You are a precise taxonomy tagger for Guidewire support records. "
#     "Pick exactly one value from each given list for category, "
#     "resolution_type, module, and severity -- never invent a label outside "
#     "any list. If the record is ambiguous, pick the closest fit and lower "
#     "your confidence; never refuse to answer."
# )

# _FEW_SHOT_EXAMPLES = """Example 1:
# Module (hint): BillingCenter
# Record title: Nightly invoice batch job failing after 2025.3 upgrade
# Record text: The nightly invoice generation batch job has failed every night since the 2025.3 upgrade was deployed. Error logs show a null pointer in the new invoice calculation step introduced by the upgrade. Rolled back the custom calculation override to match the new API signature and redeployed.
# -> category: UpgradeRegression (the failure is a direct consequence of the upgrade changing an API, not an independent scheduling issue), resolution_type: CodeFix, module: BillingCenter (invoice generation is a billing function), severity: High (a nightly job is broken but the core application is otherwise usable), confidence: 0.9

# Example 2:
# Module (hint): ContactManager
# Record title: Users intermittently logged out during peak hours
# Record text: Users report being logged out randomly during business hours. No pattern found in logs tying this to a recent change. Restarting the session service temporarily resolves it for a few hours.
# -> category: AuthenticationAccess, resolution_type: RestartRecycle, module: ContactManager (session/login handling spans all centers via shared contact/access infrastructure), severity: High (a major feature -- staying logged in -- is broken, but restarting the service is a working mitigation), confidence: 0.6 (temporary mitigation only, root cause not identified)
# """

# _TAGGING_PROMPT = """Category options:
# {category_defs}

# Resolution type options:
# {resolution_defs}

# Module options:
# {module_defs}

# Severity options:
# {severity_defs}

# {few_shot}
# Now tag this record:

# Module (hint): {module}
# Record title: {title}
# Record text:
# {text}

# The "Module (hint)" above comes from raw source metadata and may be wrong \
# or missing (shown as "Unknown" when not provided) -- independently judge \
# the best-fitting module from the record text itself for your `module` \
# answer, the same way you judge category and resolution_type.

# Confidence guide: 0.85-1.0 = the text explicitly states the cause and/or \
# fix; 0.6-0.84 = inferred from strong contextual clues; below 0.6 = \
# ambiguous or largely guessed.

# Pick exactly one category, resolution_type, module, and severity from the \
# lists above, using only the record above. If the text doesn't clearly \
# indicate a resolution yet (e.g. it's a known issue or feature note, not an \
# incident), still pick the closest-fitting resolution_type and lower your \
# confidence accordingly.

# Also generate a concise one-line summary (10-15 words max) that captures \
# the core issue and resolution for display to human reviewers. If a \
# "Record title" is already provided above, you may use it as-is or refine \
# it; if the title is "(not provided)", generate one from the record text.

# Also generate a resolution_summary: a clear, synthesized narrative (2-4 \
# sentences) of how this issue was actually resolved -- state the root \
# cause and the concrete fix/action taken, written for an engineer who \
# hits the same issue later. Do not just copy a sentence from the record \
# text verbatim -- rewrite it as a clean, standalone explanation. If this \
# record has no single incident resolution (e.g. a feature note or a \
# runbook procedure), describe what it is and the recommended action \
# instead.

# IMPORTANT: Your reasoning field must reference the exact severity, \
# module, category, and resolution_type values you return in the \
# structured output -- never name an alternative value in the reasoning \
# that contradicts your actual pick."""


# # def _sanitize_reasoning(result: _TaggingResult) -> _TaggingResult:
# #     """Hard guarantee: if the reasoning text names a severity or module value
# #     that contradicts the actual structured fields, wipe it rather than show
# #     contradictory information to SME reviewers. The structured fields are
# #     always authoritative; the reasoning is informational only."""
# #     reasoning_lower = (result.reasoning or "").lower()

# #     # Severity values (e.g. "sev2-high") are distinctive enough that any
# #     # mention of a *wrong* one in the reasoning is a genuine contradiction.
# #     wrong_severities = {s.value.lower() for s in Severity if s != result.severity}
# #     # Module values (e.g. "policycenter") are also distinctive.
# #     wrong_modules = {m.value.lower() for m in GuidewireModule if m != result.module}

# #     if any(sv in reasoning_lower for sv in wrong_severities) or \
# #        any(mod in reasoning_lower for mod in wrong_modules):
# #         result.reasoning = None  # wipe -- contradictory reasoning misleads SME reviewers
# #     return result

# def _clean_reasoning(
#     reasoning: str | None,
#     final_severity: Severity,
#     final_module: GuidewireModule,
# ) -> str | None:
#     if not reasoning:
#         return reasoning
#     r = reasoning.lower()
#     wrong_severities = {s.value.lower() for s in Severity if s != final_severity}
#     wrong_modules = {m.value.lower() for m in GuidewireModule if m != final_module}
#     if any(sv in r for sv in wrong_severities) or any(mod in r for mod in wrong_modules):
#         return None
#     return reasoning


# def _llm_classify(module: str, title: str, text: str) -> _TaggingResult:
#     """Single LLM call per record: returns a _TaggingResult with category,
#     resolution_type, module, severity, confidence, reasoning.

#     category/resolution_type are always used by the caller. module/severity
#     are an independent LLM opinion that the caller (normalize_*() below)
#     only falls back to when its own deterministic resolution for that
#     specific field was itself low-confidence -- see
#     MODULE_LLM_FALLBACK_THRESHOLD / SEVERITY_LLM_FALLBACK_THRESHOLD."""
#     classifier = _get_classifier()
#     category_defs = "\n".join(f"- {c.value}: {desc}" for c, desc in CATEGORY_DESCRIPTIONS.items())
#     resolution_defs = "\n".join(f"- {r.value}: {desc}" for r, desc in RESOLUTION_DESCRIPTIONS.items())
#     module_defs = "\n".join(f"- {m.value}: {desc}" for m, desc in MODULE_DESCRIPTIONS.items())
#     severity_defs = "\n".join(f"- {s.value}: {desc}" for s, desc in SEVERITY_DESCRIPTIONS.items())
#     human_prompt = _TAGGING_PROMPT.format(
#         category_defs=category_defs,
#         resolution_defs=resolution_defs,
#         module_defs=module_defs,
#         severity_defs=severity_defs,
#         few_shot=_FEW_SHOT_EXAMPLES,
#         module=module,
#         title=title,
#         text=text,
#     )
#     result: _TaggingResult = classifier.invoke(
#         [("system", _SYSTEM_PROMPT), ("human", human_prompt)]
#     )
#     result.confidence = round(result.confidence, 2)
#     return result


# def _resolve_module(raw_module_text: str | None) -> tuple:
#     if not raw_module_text or not raw_module_text.strip():
#         return GuidewireModule.POLICY_CENTER, 0.0  # always below threshold → LLM decides
#     key = raw_module_text.strip().lower()
#     if key in MODULE_ALIASES:
#         return MODULE_ALIASES[key], 1.0
#     # fuzzy: substring match
#     for alias, module in MODULE_ALIASES.items():
#         if alias in key or key in alias:
#             return module, 0.7
#     return GuidewireModule.POLICY_CENTER, 0.3


# # ---------------------------------------------------------------------------
# # Per-source normalizers
# # ---------------------------------------------------------------------------
# def normalize_ticket(raw: RawTicket) -> NormalizedRecord:
#     # module_reported is optional: if present, resolve deterministically;
#     # if absent, pass "Unknown" as the LLM hint and let the LLM decide.
#     if raw.module_reported:
#         module, module_conf = _resolve_module(raw.module_reported)
#         module_hint = module.value
#     else:
#         module = GuidewireModule.POLICY_CENTER  # placeholder; overridden below (conf < threshold)
#         module_conf = 0.0
#         module_hint = "Unknown"

#     # summary is no longer in RawTicket -- the LLM generates it.
#     # raw_text is description + resolution_notes only (no summary);
#     # the embedding is generated from raw_text alone (not title), so
#     # it's based purely on the original raw data.
#     combined_text = f"{raw.description} {raw.resolution_notes}"
#     severity = PRIORITY_TO_SEVERITY.get(raw.priority, Severity.NORMAL)
#     sev_conf = 1.0 if raw.priority in PRIORITY_TO_SEVERITY else 0.4

#     tag_result = _llm_classify(module_hint, "(not provided)", combined_text)
#     category, resolution, llm_conf, reasoning = (
#         tag_result.category, tag_result.resolution_type, tag_result.confidence, tag_result.reasoning
#     )

#     # module/severity stay deterministic unless their own deterministic
#     # confidence was itself a low-confidence fallback -- see
#     # MODULE_LLM_FALLBACK_THRESHOLD / SEVERITY_LLM_FALLBACK_THRESHOLD.
#     if module_conf < MODULE_LLM_FALLBACK_THRESHOLD:
#         module, module_conf = tag_result.module, llm_conf
#     if sev_conf < SEVERITY_LLM_FALLBACK_THRESHOLD:
#         severity, sev_conf = tag_result.severity, llm_conf
#     reasoning = _clean_reasoning(reasoning, severity, module)   # ADD THIS
#     overall_conf = round((module_conf + sev_conf + 2 * llm_conf) / 4, 2)

#     return NormalizedRecord(
#         record_id=f"NR-{raw.ticket_id}",
#         source_type=SourceType.TICKET,
#         source_ref_id=raw.ticket_id,
#         module=module,
#         category=category,
#         severity=severity,
#         resolution_type=resolution,
#         tagging_confidence=overall_conf,
#         tagging_reasoning=reasoning,
#         title=tag_result.summary,   # LLM-generated one-liner
#         raw_text=combined_text,     # description + resolution_notes only (used for embedding)
#         symptom_text=raw.description,                  # raw, shown as-is in structured_sop
#         resolution_text=tag_result.resolution_summary, # LLM-generated, shown as-is in structured_sop
#         guidewire_version=raw.guidewire_version,
#         event_date=raw.closed_at.date(),
#         source_metadata={
#             "priority": raw.priority,
#             "assignee_team": raw.assignee_team,
#             "created_at": raw.created_at.isoformat(),
#             "closed_at": raw.closed_at.isoformat(),
#         },
#     )


# def normalize_chat_or_rca(raw: RawChatOrRCA, ticket_index: dict) -> NormalizedRecord:
#     module, module_conf = _resolve_module(raw.module_reported)
#     combined_text = f"{raw.title} {raw.body} {raw.root_cause or ''} {raw.corrective_actions or ''}"

#     # severity: inherit from the linked ticket when available (more
#     # reliable than guessing from free text), otherwise a simple fallback.
#     if raw.incident_id and raw.incident_id in ticket_index:
#         severity = ticket_index[raw.incident_id]["severity"]
#         sev_conf = 0.95
#     else:
#         severity = Severity.HIGH if "outage" in combined_text.lower() else Severity.NORMAL
#         sev_conf = 0.5

#     tag_result = _llm_classify(module.value, raw.title, combined_text)
#     category, resolution, llm_conf, reasoning = (
#         tag_result.category, tag_result.resolution_type, tag_result.confidence, tag_result.reasoning
#     )

#     if module_conf < MODULE_LLM_FALLBACK_THRESHOLD:
#         module, module_conf = tag_result.module, llm_conf
#     if sev_conf < SEVERITY_LLM_FALLBACK_THRESHOLD:
#         severity, sev_conf = tag_result.severity, llm_conf
#     reasoning = _clean_reasoning(reasoning, severity, module)   # ADD THIS
#     overall_conf = round((module_conf + sev_conf + 2 * llm_conf) / 4, 2)

#     return NormalizedRecord(
#         record_id=f"NR-{raw.record_id}",
#         source_type=SourceType.CHAT_RCA,
#         source_ref_id=raw.record_id,
#         module=module,
#         category=category,
#         severity=severity,
#         resolution_type=resolution,
#         tagging_confidence=overall_conf,
#         tagging_reasoning=reasoning,
#         title=raw.title,
#         raw_text=combined_text,
#         symptom_text=raw.body,
#         resolution_text=tag_result.resolution_summary,
#         guidewire_version=None,
#         event_date=raw.date,
#         source_metadata={
#             "record_type": raw.record_type,
#             "incident_id": raw.incident_id,
#             "participants": raw.participants,
#             "root_cause": raw.root_cause,
#             "corrective_actions": raw.corrective_actions,
#         },
#     )


# def normalize_release_note(raw: RawReleaseNote) -> NormalizedRecord:
#     module, module_conf = _resolve_module(raw.affected_modules[0] if raw.affected_modules else raw.product)
#     combined_text = f"{raw.title} {raw.details}"

#     severity_by_note_type = {
#         "known_issue": Severity.HIGH,
#         "resolved_issue": Severity.NORMAL,
#         "feature": Severity.LOW,
#     }
#     severity = severity_by_note_type.get(raw.note_type, Severity.NORMAL)
#     sev_conf = 0.75

#     tag_result = _llm_classify(module.value, raw.title, combined_text)
#     category, resolution, llm_conf, reasoning = (
#         tag_result.category, tag_result.resolution_type, tag_result.confidence, tag_result.reasoning
#     )

#     if module_conf < MODULE_LLM_FALLBACK_THRESHOLD:
#         module, module_conf = tag_result.module, llm_conf
#     if sev_conf < SEVERITY_LLM_FALLBACK_THRESHOLD:
#         severity, sev_conf = tag_result.severity, llm_conf
#     reasoning = _clean_reasoning(reasoning, severity, module)   # ADD THIS
#     overall_conf = round((module_conf + sev_conf + 2 * llm_conf) / 4, 2)

#     return NormalizedRecord(
#         record_id=f"NR-{raw.release_id}",
#         source_type=SourceType.RELEASE_NOTE,
#         source_ref_id=raw.release_id,
#         module=module,
#         category=category,
#         severity=severity,
#         resolution_type=resolution,
#         tagging_confidence=overall_conf,
#         tagging_reasoning=reasoning,
#         title=raw.title,
#         raw_text=combined_text,
#         symptom_text=raw.details,
#         resolution_text=tag_result.resolution_summary,
#         guidewire_version=f"{raw.product} {raw.version}",
#         event_date=raw.release_date,
#         source_url=raw.source_url,
#         source_metadata={
#             "note_type": raw.note_type,
#             "product": raw.product,
#             "affected_modules": raw.affected_modules,
#         },
#     )


# def normalize_runbook(raw: RawRunbook) -> NormalizedRecord:
#     module, module_conf = _resolve_module(raw.module_reported)
#     combined_text = f"{raw.title} {raw.trigger_condition} {' '.join(raw.steps)}"

#     severity = (
#         Severity.EMERGENCY
#         if "outage" in raw.trigger_condition.lower() or "unresponsive" in raw.trigger_condition.lower()
#         else Severity.NORMAL
#     )
#     sev_conf = 0.55  # runbooks apply across a severity range; this is a default, not a measured value

#     tag_result = _llm_classify(module.value, raw.title, combined_text)
#     category, resolution, llm_conf, reasoning = (
#         tag_result.category, tag_result.resolution_type, tag_result.confidence, tag_result.reasoning
#     )

#     if module_conf < MODULE_LLM_FALLBACK_THRESHOLD:
#         module, module_conf = tag_result.module, llm_conf
#     if sev_conf < SEVERITY_LLM_FALLBACK_THRESHOLD:
#         severity, sev_conf = tag_result.severity, llm_conf
#     reasoning = _clean_reasoning(reasoning, severity, module)   # ADD THIS
#     overall_conf = round((module_conf + sev_conf + 2 * llm_conf) / 4, 2)

#     return NormalizedRecord(
#         record_id=f"NR-{raw.runbook_id}",
#         source_type=SourceType.RUNBOOK,
#         source_ref_id=raw.runbook_id,
#         module=module,
#         category=category,
#         severity=severity,
#         resolution_type=resolution,
#         tagging_confidence=overall_conf,
#         tagging_reasoning=reasoning,
#         title=raw.title,
#         raw_text=combined_text,
#         symptom_text=raw.trigger_condition,
#         # Runbook steps are an exact operational procedure -- format them
#         # directly rather than letting the LLM paraphrase them, so exact
#         # command/script names in the steps are never altered.
#         resolution_text="\n".join(f"{i}. {step}" for i, step in enumerate(raw.steps, start=1)),
#         guidewire_version=None,
#         event_date=raw.last_updated,
#         source_metadata={
#             "owner_team": raw.owner_team,
#             "trigger_condition": raw.trigger_condition,
#             "steps": raw.steps,
#         },
#     )


# # ---------------------------------------------------------------------------
# # Public entrypoint
# # ---------------------------------------------------------------------------
# def load_raw(filename: str, model) -> list:
#     path = DATA_DIR / filename
#     raw_items = json.loads(path.read_text(encoding="utf-8"))
#     return [model.model_validate(item) for item in raw_items]


# def _content_hash(raw: BaseModel) -> str:
#     """Stable hash of a raw record's own fields (before any tagging),
#     used to detect whether it has changed since the last run that fully
#     processed it. Deliberately hashes the raw input, not the normalized/
#     tagged output, so a hash match means "nothing to re-tag" -- not just
#     "the last tagging result happened to look the same"."""
#     payload = raw.model_dump(mode="json")
#     return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


# def ingest_all(
#     sources: list[SourceType] | None = None,
#     existing_hashes: dict[str, str] | None = None,
# ) -> list[NormalizedRecord]:
#     """
#     sources: which of the 4 source types to ingest this run. None (the
#         default) ingests all 4 -- identical to the original behavior.
#     existing_hashes: {record_id: content_hash} already stored in the
#         Knowledge Store from a prior run (see
#         knowledge_store.fetch_ingestion_hashes()). A record whose freshly
#         computed content hash matches the stored one is skipped entirely --
#         no LLM tagging call, not included in the returned list -- since
#         nothing about its raw source fields has changed since it was last
#         fully processed. None/{} (the default) re-processes everything
#         selected, identical to the original behavior.
#     """
#     if sources is None:
#         sources = list(SourceType)
#     existing_hashes = existing_hashes or {}

#     tickets = load_raw("tickets.json", RawTicket)
#     chats_rcas = load_raw("chat_rca.json", RawChatOrRCA)
#     release_notes = load_raw("release_notes.json", RawReleaseNote)
#     runbooks = load_raw("runbooks.json", RawRunbook)

#     # Built from ALL tickets regardless of `sources`/skip state: chat/RCA
#     # severity inheritance needs it, and ticket severity is a free,
#     # deterministic lookup (no LLM call involved), so there's no cost to
#     # always having it available even when tickets themselves aren't
#     # selected or are being skipped as unchanged this run.
#     ticket_index = {
#         t.ticket_id: {"severity": PRIORITY_TO_SEVERITY.get(t.priority, Severity.NORMAL)}
#         for t in tickets
#     }

#     normalized: list[NormalizedRecord] = []

#     if SourceType.TICKET in sources:
#         for t in tickets:
#             if t.status not in CLOSED_TICKET_STATUSES:
#                 # Open/in-progress tickets are never ingested into the KB --
#                 # only a closed ticket's resolution is a trustworthy,
#                 # finished SOP. It'll be picked up on a later refresh run
#                 # once its status flips to Closed.
#                 continue
#             record_id = f"NR-{t.ticket_id}"
#             content_hash = _content_hash(t)
#             if existing_hashes.get(record_id) == content_hash:
#                 continue
#             rec = normalize_ticket(t)
#             rec.content_hash = content_hash
#             normalized.append(rec)

#     if SourceType.CHAT_RCA in sources:
#         for c in chats_rcas:
#             record_id = f"NR-{c.record_id}"
#             content_hash = _content_hash(c)
#             if existing_hashes.get(record_id) == content_hash:
#                 continue
#             rec = normalize_chat_or_rca(c, ticket_index)
#             rec.content_hash = content_hash
#             normalized.append(rec)

#     if SourceType.RELEASE_NOTE in sources:
#         for r in release_notes:
#             record_id = f"NR-{r.release_id}"
#             content_hash = _content_hash(r)
#             if existing_hashes.get(record_id) == content_hash:
#                 continue
#             rec = normalize_release_note(r)
#             rec.content_hash = content_hash
#             normalized.append(rec)

#     if SourceType.RUNBOOK in sources:
#         for rb in runbooks:
#             record_id = f"NR-{rb.runbook_id}"
#             content_hash = _content_hash(rb)
#             if existing_hashes.get(record_id) == content_hash:
#                 continue
#             rec = normalize_runbook(rb)
#             rec.content_hash = content_hash
#             normalized.append(rec)

#     return normalized


# if __name__ == "__main__":
#     records = ingest_all()
#     print(f"Ingested & normalized {len(records)} records from 4 sources:")
#     from collections import Counter

#     by_source = Counter(r.source_type.value for r in records)
#     for src, count in by_source.items():
#         print(f"  - {src}: {count}")
#     avg_conf = sum(r.tagging_confidence for r in records) / len(records)
#     print(f"Average tagging confidence: {avg_conf:.2f}")





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
    direct answer (see _resolve_module()). When that deterministic
    resolution is itself low-confidence (the fallback branch, not a real
    match), the record falls back to the LLM's own independent module/
    severity read of the narrative text instead -- see
    MODULE_LLM_FALLBACK_THRESHOLD / SEVERITY_LLM_FALLBACK_THRESHOLD below.
    High-confidence deterministic signals (an exact module_reported match,
    a ticket's priority code, severity inherited from a linked ticket) are
    never overridden -- those are more reliable than an LLM guess.
  - category / resolution_type are always tagged by an LLM (Azure OpenAI
    chat deployment, via _llm_classify()): it's given the record's narrative
    text plus the *fixed list* of valid IssueCategory/ResolutionType
    values from taxonomy.py, and returns a structured pick for each, a
    self-reported confidence (0-1), and a short reasoning string (kept in
    tagging_reasoning, so a human can still see *why* -- same transparency
    goal the original keyword classifier had, now backed by a real model
    instead of hand-written keyword lists). The same single LLM call also
    returns an independent module/severity opinion (used only as the
    fallback described above), so this costs no extra API call per record.

Ticket status filter:
  - A ticket whose `status` is in INGESTIBLE_TICKET_STATUSES ("Closed" or
    "InReview") goes through ingestion/tagging/structuring/auto-approval
    just like before -- any other status (e.g. "Open", "In Progress") is
    skipped entirely, same as the old Closed-only filter.
  - Embedding is narrower than ingestion, though: only a ticket whose
    status is in CLOSED_TICKET_STATUSES ("Closed") actually gets embedded
    (see pipeline.py / knowledge_store.fetch_rows_missing_embedding()). An
    "InReview" ticket is still tagged and auto-approved, but is written
    with embedding=NULL and stays unembedded/unretrievable until a later
    refresh sees its status flip to "Closed".

Selective ingestion / duplicate-run prevention:
  - ingest_all(sources=...) lets a caller ingest only a subset of the 4
    sources. Omitting `sources` (the default) ingests all 4, same as
    before.
  - ingest_all(existing_hashes=...) lets a caller skip re-running the LLM
    tagging call entirely for any record whose raw source fields are
    byte-for-byte identical to the last run that fully processed it (see
    _content_hash() and knowledge_store.fetch_ingestion_hashes()). Omitting
    `existing_hashes` (the default) re-processes everything selected, same
    as before.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from langchain_openai import AzureChatOpenAI
from pydantic import BaseModel, Field
from dotenv import load_dotenv
load_dotenv()

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

DATA_DIR = Path(os.environ.get("KB_DATA_DIR") or Path(__file__).resolve().parents[1] / "data" / "raw")

CLOSED_TICKET_STATUSES = {"Closed"}
# Tickets with any of these statuses go through the full pipeline
# (ingestion/tagging/structuring/auto-approval); embedding itself still
# only happens for CLOSED_TICKET_STATUSES (see pipeline.py).
INGESTIBLE_TICKET_STATUSES = {"Closed", "InReview"}

PRIORITY_TO_SEVERITY = {
    "P1": Severity.EMERGENCY,
    "P2": Severity.HIGH,
    "P3": Severity.NORMAL,
    "P4": Severity.LOW,
}

MODULE_ALIASES = {m.value.lower(): m for m in GuidewireModule}

# Below these thresholds, the deterministic module/severity resolution is
# itself just a fallback guess (not a real match) -- so the LLM's own
# independent read of the record's narrative text is used instead. These
# line up with the existing confidence values already in this file: the
# module fallback (0.3) and the weakest severity heuristics (ticket
# priority not recognized: 0.4; chat/RCA with no linked ticket: 0.5;
# runbook keyword guess: 0.55) all fall below their threshold and get
# overridden; every high-confidence deterministic path (exact module
# match: 1.0, fuzzy match: 0.7, ticket priority code: 1.0, severity
# inherited from a linked ticket: 0.95, release-note note_type: 0.75)
# stays untouched.
MODULE_LLM_FALLBACK_THRESHOLD = 0.5
SEVERITY_LLM_FALLBACK_THRESHOLD = 0.6

# Maps the CLI/API-friendly source keys used for selective ingestion to
# their SourceType enum value.
SOURCE_KEY_TO_TYPE: dict[str, SourceType] = {
    "tickets": SourceType.TICKET,
    "chat_rca": SourceType.CHAT_RCA,
    "release_notes": SourceType.RELEASE_NOTE,
    "runbooks": SourceType.RUNBOOK,
}


# ---------------------------------------------------------------------------
# LLM-based classifier: category / resolution_type (always used) plus an
# independent module / severity opinion (used only as a fallback -- see
# MODULE_LLM_FALLBACK_THRESHOLD / SEVERITY_LLM_FALLBACK_THRESHOLD).
# ---------------------------------------------------------------------------
class _TaggingResult(BaseModel):
    summary: str = Field(description="One-line title (10-15 words max) capturing the core issue and resolution, for display to human reviewers.")
    category: IssueCategory = Field(description="Best-matching issue category for this record.")
    resolution_type: ResolutionType = Field(description="Best-matching resolution type for this record.")
    module: GuidewireModule = Field(
        description="Best-matching Guidewire module for this record, judged independently from the record text."
    )
    severity: Severity = Field(
        description="Best-matching severity for this record, judged independently from the record text."
    )
    confidence: float = Field(ge=0.0, le=1.0, description="How confident you are in ALL FOUR picks above, 0-1.")
    reasoning: str = Field(
        description="One or two sentences on why these labels fit, for a human reviewer. "
        "MUST reference the exact severity, module, category, and resolution_type values "
        "you returned in the structured fields above -- never mention an alternative value."
    )


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

MODULE_DESCRIPTIONS: dict[GuidewireModule, str] = {
    GuidewireModule.POLICY_CENTER: "Policy administration -- quoting, binding, policy transactions, underwriting workflow.",
    GuidewireModule.CLAIM_CENTER: "Claims administration -- FNOL, claim handling, claims workflow, adjuster activities.",
    GuidewireModule.BILLING_CENTER: "Billing and invoicing -- payment plans, invoices, disbursements, billing schedules.",
    GuidewireModule.CONTACT_MANAGER: "Shared contact/account data used across Policy/Claim/BillingCenter -- contacts, addresses, duplicate/merge issues.",
    GuidewireModule.RATING_GOSU: "Rating engine or custom Gosu code -- rate calculation, custom business rules/scripts.",
    GuidewireModule.INTEGRATION_GATEWAY: "Integration layer between Guidewire and external systems -- SFTP, APIs, messaging, data exchange gateways.",
    GuidewireModule.DIGITAL_PORTAL: "Customer/agent-facing self-service portal or digital UI, separate from the core center application.",
    GuidewireModule.DATA_PLATFORM: "Underlying data/reporting/analytics platform -- data warehouse, ETL, reporting infrastructure.",
}

SEVERITY_DESCRIPTIONS: dict[Severity, str] = {
    Severity.EMERGENCY: "Production down or a major incident affecting business operations broadly.",
    Severity.HIGH: "A major feature is broken, but a workaround exists.",
    Severity.NORMAL: "Degraded but usable -- a non-blocking defect or noticeable but tolerable issue.",
    Severity.LOW: "Cosmetic or minor issue with negligible business impact.",
}

_SYSTEM_PROMPT = (
    "You are a precise taxonomy tagger for Guidewire support records. "
    "Pick exactly one value from each given list for category, "
    "resolution_type, module, and severity -- never invent a label outside "
    "any list. If the record is ambiguous, pick the closest fit and lower "
    "your confidence; never refuse to answer."
)

_FEW_SHOT_EXAMPLES = """Example 1:
Module (hint): BillingCenter
Record title: Nightly invoice batch job failing after 2025.3 upgrade
Record text: The nightly invoice generation batch job has failed every night since the 2025.3 upgrade was deployed. Error logs show a null pointer in the new invoice calculation step introduced by the upgrade. Rolled back the custom calculation override to match the new API signature and redeployed.
-> category: UpgradeRegression (the failure is a direct consequence of the upgrade changing an API, not an independent scheduling issue), resolution_type: CodeFix, module: BillingCenter (invoice generation is a billing function), severity: High (a nightly job is broken but the core application is otherwise usable), confidence: 0.9

Example 2:
Module (hint): ContactManager
Record title: Users intermittently logged out during peak hours
Record text: Users report being logged out randomly during business hours. No pattern found in logs tying this to a recent change. Restarting the session service temporarily resolves it for a few hours.
-> category: AuthenticationAccess, resolution_type: RestartRecycle, module: ContactManager (session/login handling spans all centers via shared contact/access infrastructure), severity: High (a major feature -- staying logged in -- is broken, but restarting the service is a working mitigation), confidence: 0.6 (temporary mitigation only, root cause not identified)
"""

_TAGGING_PROMPT = """Category options:
{category_defs}

Resolution type options:
{resolution_defs}

Module options:
{module_defs}

Severity options:
{severity_defs}

{few_shot}
Now tag this record:

Module (hint): {module}
Record title: {title}
Record text:
{text}

The "Module (hint)" above comes from raw source metadata and may be wrong \
or missing (shown as "Unknown" when not provided) -- independently judge \
the best-fitting module from the record text itself for your `module` \
answer, the same way you judge category and resolution_type.

Confidence guide: 0.85-1.0 = the text explicitly states the cause and/or \
fix; 0.6-0.84 = inferred from strong contextual clues; below 0.6 = \
ambiguous or largely guessed.

Pick exactly one category, resolution_type, module, and severity from the \
lists above, using only the record above. If the text doesn't clearly \
indicate a resolution yet (e.g. it's a known issue or feature note, not an \
incident), still pick the closest-fitting resolution_type and lower your \
confidence accordingly.

Also generate a concise one-line summary (10-15 words max) that captures \
the core issue and resolution for display to human reviewers. If a \
"Record title" is already provided above, you may use it as-is or refine \
it; if the title is "(not provided)", generate one from the record text.

IMPORTANT: Your reasoning field must reference the exact severity, \
module, category, and resolution_type values you return in the \
structured output -- never name an alternative value in the reasoning \
that contradicts your actual pick."""


def _clean_reasoning(
    reasoning: str | None,
    final_severity: Severity,
    final_module: GuidewireModule,
) -> str | None:
    """Wipe the LLM reasoning if it names a severity or module that
    contradicts the FINAL tagged values (after deterministic overrides).

    Must be called after the override logic in each normalize_*() so it
    checks the values that actually get stored, not the LLM's raw guess.
    The structured fields are always authoritative; reasoning is display-only."""
    if not reasoning:
        return reasoning
    r = reasoning.lower()
    wrong_severities = {s.value.lower() for s in Severity if s != final_severity}
    wrong_modules = {m.value.lower() for m in GuidewireModule if m != final_module}
    if any(sv in r for sv in wrong_severities) or any(mod in r for mod in wrong_modules):
        return None
    return reasoning


def _llm_classify(module: str, title: str, text: str) -> _TaggingResult:
    """Single LLM call per record: returns a _TaggingResult with category,
    resolution_type, module, severity, confidence, reasoning.

    category/resolution_type are always used by the caller. module/severity
    are an independent LLM opinion that the caller (normalize_*() below)
    only falls back to when its own deterministic resolution for that
    specific field was itself low-confidence -- see
    MODULE_LLM_FALLBACK_THRESHOLD / SEVERITY_LLM_FALLBACK_THRESHOLD."""
    classifier = _get_classifier()
    category_defs = "\n".join(f"- {c.value}: {desc}" for c, desc in CATEGORY_DESCRIPTIONS.items())
    resolution_defs = "\n".join(f"- {r.value}: {desc}" for r, desc in RESOLUTION_DESCRIPTIONS.items())
    module_defs = "\n".join(f"- {m.value}: {desc}" for m, desc in MODULE_DESCRIPTIONS.items())
    severity_defs = "\n".join(f"- {s.value}: {desc}" for s, desc in SEVERITY_DESCRIPTIONS.items())
    human_prompt = _TAGGING_PROMPT.format(
        category_defs=category_defs,
        resolution_defs=resolution_defs,
        module_defs=module_defs,
        severity_defs=severity_defs,
        few_shot=_FEW_SHOT_EXAMPLES,
        module=module,
        title=title,
        text=text,
    )
    result: _TaggingResult = classifier.invoke(
        [("system", _SYSTEM_PROMPT), ("human", human_prompt)]
    )
    result.confidence = round(result.confidence, 2)
    return result


def _resolve_module(raw_module_text: str | None) -> tuple:
    if not raw_module_text or not raw_module_text.strip():
        return GuidewireModule.POLICY_CENTER, 0.0  # always below threshold → LLM decides
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
    # module_reported is optional: if present, resolve deterministically;
    # if absent, pass "Unknown" as the LLM hint and let the LLM decide.
    if raw.module_reported:
        module, module_conf = _resolve_module(raw.module_reported)
        module_hint = module.value
    else:
        module = GuidewireModule.POLICY_CENTER  # placeholder; overridden below (conf < threshold)
        module_conf = 0.0
        module_hint = "Unknown"

    # summary is no longer in RawTicket -- the LLM generates it.
    # raw_text is description + resolution_notes only (no summary);
    # the embedding is generated from raw_text alone (not title), so
    # it's based purely on the original raw data.
    combined_text = f"{raw.description} {raw.resolution_notes}"
    severity = PRIORITY_TO_SEVERITY.get(raw.priority, Severity.NORMAL)
    sev_conf = 1.0 if raw.priority in PRIORITY_TO_SEVERITY else 0.4

    tag_result = _llm_classify(module_hint, "(not provided)", combined_text)
    category, resolution, llm_conf, reasoning = (
        tag_result.category, tag_result.resolution_type, tag_result.confidence, tag_result.reasoning
    )

    # module/severity stay deterministic unless their own deterministic
    # confidence was itself a low-confidence fallback -- see
    # MODULE_LLM_FALLBACK_THRESHOLD / SEVERITY_LLM_FALLBACK_THRESHOLD.
    if module_conf < MODULE_LLM_FALLBACK_THRESHOLD:
        module, module_conf = tag_result.module, llm_conf
    if sev_conf < SEVERITY_LLM_FALLBACK_THRESHOLD:
        severity, sev_conf = tag_result.severity, llm_conf

    reasoning = _clean_reasoning(reasoning, severity, module)
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
        title=tag_result.summary,   # LLM-generated one-liner
        raw_text=combined_text,     # description + resolution_notes only (used for embedding)
        symptom_text=raw.description,       # raw, shown as-is in structured_sop
        resolution_text=raw.resolution_notes, # raw, straight from the ticketing tool -- no LLM involved
        guidewire_version=raw.guidewire_version,
        event_date=raw.closed_at.date(),
        source_metadata={
            "priority": raw.priority,
            "assignee_team": raw.assignee_team,
            "created_at": raw.created_at.isoformat(),
            "closed_at": raw.closed_at.isoformat(),
            "status": raw.status,  # used by pipeline.py to decide whether this
                                    # ticket is eligible for embedding (Closed) or
                                    # not yet (InReview)
        },
    )


def normalize_chat_or_rca(raw: RawChatOrRCA, ticket_index: dict) -> NormalizedRecord:
    module, module_conf = _resolve_module(raw.module_reported)
    combined_text = f"{raw.title} {raw.body} {raw.root_cause or ''} {raw.corrective_actions or ''}"

    # severity: inherit from the linked ticket when available (more
    # reliable than guessing from free text), otherwise a simple fallback.
    if raw.incident_id and raw.incident_id in ticket_index:
        severity = ticket_index[raw.incident_id]["severity"]
        sev_conf = 0.95
    else:
        severity = Severity.HIGH if "outage" in combined_text.lower() else Severity.NORMAL
        sev_conf = 0.5

    tag_result = _llm_classify(module.value, raw.title, combined_text)
    category, resolution, llm_conf, reasoning = (
        tag_result.category, tag_result.resolution_type, tag_result.confidence, tag_result.reasoning
    )

    if module_conf < MODULE_LLM_FALLBACK_THRESHOLD:
        module, module_conf = tag_result.module, llm_conf
    if sev_conf < SEVERITY_LLM_FALLBACK_THRESHOLD:
        severity, sev_conf = tag_result.severity, llm_conf

    reasoning = _clean_reasoning(reasoning, severity, module)
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
        symptom_text=raw.body,
        resolution_text=raw.corrective_actions,  # raw, as recorded -- no LLM involved
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

    severity_by_note_type = {
        "known_issue": Severity.HIGH,
        "resolved_issue": Severity.NORMAL,
        "feature": Severity.LOW,
    }
    severity = severity_by_note_type.get(raw.note_type, Severity.NORMAL)
    sev_conf = 0.75

    tag_result = _llm_classify(module.value, raw.title, combined_text)
    category, resolution, llm_conf, reasoning = (
        tag_result.category, tag_result.resolution_type, tag_result.confidence, tag_result.reasoning
    )

    if module_conf < MODULE_LLM_FALLBACK_THRESHOLD:
        module, module_conf = tag_result.module, llm_conf
    if sev_conf < SEVERITY_LLM_FALLBACK_THRESHOLD:
        severity, sev_conf = tag_result.severity, llm_conf

    reasoning = _clean_reasoning(reasoning, severity, module)
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
        symptom_text=raw.details,
        # No separate raw "resolution" field for a release note -- `details`
        # already covers it (and is shown above as symptom_text); leave
        # resolution_text unset and let _render_sop fall back to it.
        resolution_text=None,
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

    severity = (
        Severity.EMERGENCY
        if "outage" in raw.trigger_condition.lower() or "unresponsive" in raw.trigger_condition.lower()
        else Severity.NORMAL
    )
    sev_conf = 0.55  # runbooks apply across a severity range; this is a default, not a measured value

    tag_result = _llm_classify(module.value, raw.title, combined_text)
    category, resolution, llm_conf, reasoning = (
        tag_result.category, tag_result.resolution_type, tag_result.confidence, tag_result.reasoning
    )

    if module_conf < MODULE_LLM_FALLBACK_THRESHOLD:
        module, module_conf = tag_result.module, llm_conf
    if sev_conf < SEVERITY_LLM_FALLBACK_THRESHOLD:
        severity, sev_conf = tag_result.severity, llm_conf

    reasoning = _clean_reasoning(reasoning, severity, module)
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
        symptom_text=raw.trigger_condition,
        # Runbook steps are an exact operational procedure -- format them
        # directly rather than letting the LLM paraphrase them, so exact
        # command/script names in the steps are never altered.
        resolution_text="\n".join(f"{i}. {step}" for i, step in enumerate(raw.steps, start=1)),
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


def _content_hash(raw: BaseModel) -> str:
    """Stable hash of a raw record's own fields (before any tagging),
    used to detect whether it has changed since the last run that fully
    processed it. Deliberately hashes the raw input, not the normalized/
    tagged output, so a hash match means "nothing to re-tag" -- not just
    "the last tagging result happened to look the same"."""
    payload = raw.model_dump(mode="json")
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def ingest_all(
    sources: list[SourceType] | None = None,
    existing_hashes: dict[str, str] | None = None,
) -> list[NormalizedRecord]:
    """
    sources: which of the 4 source types to ingest this run. None (the
        default) ingests all 4 -- identical to the original behavior.
    existing_hashes: {record_id: content_hash} already stored in the
        Knowledge Store from a prior run (see
        knowledge_store.fetch_ingestion_hashes()). A record whose freshly
        computed content hash matches the stored one is skipped entirely --
        no LLM tagging call, not included in the returned list -- since
        nothing about its raw source fields has changed since it was last
        fully processed. None/{} (the default) re-processes everything
        selected, identical to the original behavior.
    """
    if sources is None:
        sources = list(SourceType)
    existing_hashes = existing_hashes or {}

    tickets = load_raw("tickets.json", RawTicket)
    chats_rcas = load_raw("chat_rca.json", RawChatOrRCA)
    release_notes = load_raw("release_notes.json", RawReleaseNote)
    runbooks = load_raw("runbooks.json", RawRunbook)

    # Built from ALL tickets regardless of `sources`/skip state: chat/RCA
    # severity inheritance needs it, and ticket severity is a free,
    # deterministic lookup (no LLM call involved), so there's no cost to
    # always having it available even when tickets themselves aren't
    # selected or are being skipped as unchanged this run.
    ticket_index = {
        t.ticket_id: {"severity": PRIORITY_TO_SEVERITY.get(t.priority, Severity.NORMAL)}
        for t in tickets
    }

    normalized: list[NormalizedRecord] = []

    if SourceType.TICKET in sources:
        for t in tickets:
            if t.status not in INGESTIBLE_TICKET_STATUSES:
                # Only Closed/InReview tickets are ingested into the KB --
                # anything else (Open, In Progress) isn't ready to be
                # tagged yet. It'll be picked up on a later refresh run
                # once its status reaches one of those. Note: being
                # ingestible here does NOT mean embeddable -- that's a
                # narrower, Closed-only check done later in pipeline.py.
                continue
            record_id = f"NR-{t.ticket_id}"
            content_hash = _content_hash(t)
            if existing_hashes.get(record_id) == content_hash:
                continue
            rec = normalize_ticket(t)
            rec.content_hash = content_hash
            normalized.append(rec)

    if SourceType.CHAT_RCA in sources:
        for c in chats_rcas:
            record_id = f"NR-{c.record_id}"
            content_hash = _content_hash(c)
            if existing_hashes.get(record_id) == content_hash:
                continue
            rec = normalize_chat_or_rca(c, ticket_index)
            rec.content_hash = content_hash
            normalized.append(rec)

    if SourceType.RELEASE_NOTE in sources:
        for r in release_notes:
            record_id = f"NR-{r.release_id}"
            content_hash = _content_hash(r)
            if existing_hashes.get(record_id) == content_hash:
                continue
            rec = normalize_release_note(r)
            rec.content_hash = content_hash
            normalized.append(rec)

    if SourceType.RUNBOOK in sources:
        for rb in runbooks:
            record_id = f"NR-{rb.runbook_id}"
            content_hash = _content_hash(rb)
            if existing_hashes.get(record_id) == content_hash:
                continue
            rec = normalize_runbook(rb)
            rec.content_hash = content_hash
            normalized.append(rec)

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