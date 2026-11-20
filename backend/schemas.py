# # # """
# # # Pydantic schemas for every stage of the pipeline shown in the architecture
# # # diagram:

# # #   4 raw sources  -->  Ingestion & Normalization  -->  Structuring Agent
# # #                  -->  SME Curation Gate  -->  Knowledge Store

# # # Each raw source keeps its own native shape (that's how it really looks
# # # coming out of a ticketing system, a chat export, a vendor release feed, or
# # # a runbook wiki). Ingestion is responsible for mapping all four into the
# # # single NormalizedRecord shape.
# # # """

# # # from __future__ import annotations

# # # from datetime import date, datetime
# # # from typing import Optional

# # # from pydantic import BaseModel, Field

# # # from taxonomy import (
# # #     CurationStatus,
# # #     GuidewireModule,
# # #     IssueCategory,
# # #     ResolutionType,
# # #     Severity,
# # #     SourceType,
# # # )


# # # # ---------------------------------------------------------------------------
# # # # RAW SOURCE 1: Historical Tickets (e.g. ServiceNow / Jira export)
# # # # ---------------------------------------------------------------------------
# # # class RawTicket(BaseModel):
# # #     ticket_id: str
# # #     created_at: datetime
# # #     closed_at: datetime
# # #     module_reported: str                 # free text as the agent typed it
# # #     summary: str
# # #     description: str
# # #     resolution_notes: str
# # #     priority: str                        # raw priority label from ticketing tool (P1..P4)
# # #     assignee_team: str
# # #     guidewire_version: Optional[str] = None
# # #     status: str = "Closed"               # ticketing-tool status (e.g. "Open", "In Progress",
# # #                                           # "Closed"). Defaults to "Closed" so existing raw
# # #                                           # data without this field keeps validating as-is.
# # #                                           # ingestion.py only ingests tickets whose status is
# # #                                           # a closed one (see ingestion.CLOSED_TICKET_STATUSES)
# # #                                           # -- an open/in-progress ticket is skipped entirely,
# # #                                           # not even sent to SME review, until it closes.


# # # # ---------------------------------------------------------------------------
# # # # RAW SOURCE 2: Chat transcripts & formal RCA reports
# # # # ---------------------------------------------------------------------------
# # # class RawChatOrRCA(BaseModel):
# # #     record_id: str
# # #     record_type: str                     # "chat_transcript" | "rca_report"
# # #     incident_id: Optional[str] = None    # links back to a Emergency/High ticket
# # #     date: date
# # #     participants: list[str]
# # #     module_reported: str
# # #     title: str
# # #     body: str                            # raw transcript text or RCA narrative
# # #     root_cause: Optional[str] = None     # populated for rca_report only
# # #     corrective_actions: Optional[str] = None


# # # # ---------------------------------------------------------------------------
# # # # RAW SOURCE 3: Guidewire release notes / vendor advisories
# # # # ---------------------------------------------------------------------------
# # # class RawReleaseNote(BaseModel):
# # #     release_id: str                      # e.g. "XENG-1875" or a Guidewire issue/release id
# # #     product: str
# # #     version: str                         # e.g. "Mammoth (2025.03)", "Palisades"
# # #     release_date: date
# # #     note_type: str                       # "feature" | "resolved_issue" | "known_issue"
# # #     title: str
# # #     details: str
# # #     affected_modules: list[str]
# # #     source_url: Optional[str] = None     # public Guidewire docs page this was sourced from


# # # # ---------------------------------------------------------------------------
# # # # RAW SOURCE 4: Runbooks / SOPs
# # # # ---------------------------------------------------------------------------
# # # class RawRunbook(BaseModel):
# # #     runbook_id: str
# # #     last_updated: date
# # #     owner_team: str
# # #     module_reported: str
# # #     title: str
# # #     trigger_condition: str               # when this runbook applies
# # #     steps: list[str]


# # # # ---------------------------------------------------------------------------
# # # # NORMALIZED RECORD — the common shape every source is mapped into
# # # # after Ingestion & Normalization. This is what the Structuring Agent,
# # # # SME Curation Gate and Knowledge Store all operate on.
# # # # ---------------------------------------------------------------------------
# # # class NormalizedRecord(BaseModel):
# # #     record_id: str
# # #     source_type: SourceType
# # #     source_ref_id: str                   # original ticket_id / release_id / etc.

# # #     # --- taxonomy tags applied during Ingestion & Normalization ---
# # #     module: GuidewireModule
# # #     category: IssueCategory
# # #     severity: Severity
# # #     resolution_type: ResolutionType
# # #     tagging_confidence: float = Field(ge=0.0, le=1.0)
# # #     tagging_reasoning: Optional[str] = None       # LLM's own explanation for category/resolution_type

# # #     # --- normalized content ---
# # #     title: str
# # #     raw_text: str                        # concatenated normalized text used for embedding
# # #     guidewire_version: Optional[str] = None
# # #     event_date: date

# # #     # --- filled in by later stages ---
# # #     structured_sop: Optional[str] = None         # set by Structuring Agent
# # #     embedding: Optional[list[float]] = None       # set by Structuring Agent
# # #     curation_status: CurationStatus = CurationStatus.PENDING_REVIEW
# # #     curation_notes: Optional[str] = None          # set by SME Curation Gate
# # #     ingested_at: datetime = Field(default_factory=datetime.utcnow)
# # #     kb_version: Optional[str] = None              # set when written to Knowledge Store
# # #     source_url: Optional[str] = None              # provenance link, where the source is public
# # #     source_metadata: Optional[dict] = None        # source-specific fields that don't fit the
# # #                                                    # common columns (e.g. priority/assignee_team
# # #                                                    # for a ticket, participants for a chat/RCA,
# # #                                                    # owner_team/steps for a runbook). Stored as
# # #                                                    # JSONB in each source's own table.
# # #     content_hash: Optional[str] = None            # hash of this record's raw source fields, set
# # #                                                    # by ingestion.py. Used only for the
# # #                                                    # ingestion_state skip-if-unchanged check
# # #                                                    # (knowledge_store.py); not written into any of
# # #                                                    # the 4 content tables themselves.


# # """
# # Pydantic schemas for every stage of the pipeline shown in the architecture
# # diagram:

# #   4 raw sources  -->  Ingestion & Normalization  -->  Structuring Agent
# #                  -->  SME Curation Gate  -->  Knowledge Store

# # Each raw source keeps its own native shape (that's how it really looks
# # coming out of a ticketing system, a chat export, a vendor release feed, or
# # a runbook wiki). Ingestion is responsible for mapping all four into the
# # single NormalizedRecord shape.
# # """

# # from __future__ import annotations

# # from datetime import date, datetime
# # from typing import Optional

# # from pydantic import BaseModel, Field

# # from taxonomy import (
# #     CurationStatus,
# #     GuidewireModule,
# #     IssueCategory,
# #     ResolutionType,
# #     Severity,
# #     SourceType,
# # )


# # # ---------------------------------------------------------------------------
# # # RAW SOURCE 1: Historical Tickets (e.g. ServiceNow / Jira export)
# # # ---------------------------------------------------------------------------
# # class RawTicket(BaseModel):
# #     ticket_id: str
# #     created_at: datetime
# #     closed_at: datetime
# #     module_reported: Optional[str] = None  # free text as the agent typed it; optional --
# #                                             # if absent, the LLM infers module from description
# #     description: str
# #     resolution_notes: str
# #     priority: str                        # raw priority label from ticketing tool (P1..P4)
# #     assignee_team: str
# #     guidewire_version: Optional[str] = None
# #     status: str = "Closed"               # ticketing-tool status (e.g. "Open", "In Progress",
# #                                           # "Closed"). Defaults to "Closed" so existing raw
# #                                           # data without this field keeps validating as-is.
# #                                           # ingestion.py only ingests tickets whose status is
# #                                           # a closed one (see ingestion.CLOSED_TICKET_STATUSES)
# #                                           # -- an open/in-progress ticket is skipped entirely,
# #                                           # not even sent to SME review, until it closes.


# # # ---------------------------------------------------------------------------
# # # RAW SOURCE 2: Chat transcripts & formal RCA reports
# # # ---------------------------------------------------------------------------
# # class RawChatOrRCA(BaseModel):
# #     record_id: str
# #     record_type: str                     # "chat_transcript" | "rca_report"
# #     incident_id: Optional[str] = None    # links back to a Emergency/High ticket
# #     date: date
# #     participants: list[str]
# #     module_reported: str
# #     title: str
# #     body: str                            # raw transcript text or RCA narrative
# #     root_cause: Optional[str] = None     # populated for rca_report only
# #     corrective_actions: Optional[str] = None


# # # ---------------------------------------------------------------------------
# # # RAW SOURCE 3: Guidewire release notes / vendor advisories
# # # ---------------------------------------------------------------------------
# # class RawReleaseNote(BaseModel):
# #     release_id: str                      # e.g. "XENG-1875" or a Guidewire issue/release id
# #     product: str
# #     version: str                         # e.g. "Mammoth (2025.03)", "Palisades"
# #     release_date: date
# #     note_type: str                       # "feature" | "resolved_issue" | "known_issue"
# #     title: str
# #     details: str
# #     affected_modules: list[str]
# #     source_url: Optional[str] = None     # public Guidewire docs page this was sourced from


# # # ---------------------------------------------------------------------------
# # # RAW SOURCE 4: Runbooks / SOPs
# # # ---------------------------------------------------------------------------
# # class RawRunbook(BaseModel):
# #     runbook_id: str
# #     last_updated: date
# #     owner_team: str
# #     module_reported: str
# #     title: str
# #     trigger_condition: str               # when this runbook applies
# #     steps: list[str]


# # # ---------------------------------------------------------------------------
# # # NORMALIZED RECORD — the common shape every source is mapped into
# # # after Ingestion & Normalization. This is what the Structuring Agent,
# # # SME Curation Gate and Knowledge Store all operate on.
# # # ---------------------------------------------------------------------------
# # class NormalizedRecord(BaseModel):
# #     record_id: str
# #     source_type: SourceType
# #     source_ref_id: str                   # original ticket_id / release_id / etc.

# #     # --- taxonomy tags applied during Ingestion & Normalization ---
# #     module: GuidewireModule
# #     category: IssueCategory
# #     severity: Severity
# #     resolution_type: ResolutionType
# #     tagging_confidence: float = Field(ge=0.0, le=1.0)
# #     tagging_reasoning: Optional[str] = None       # LLM's own explanation for category/resolution_type

# #     # --- normalized content ---
# #     title: str
# #     raw_text: str                        # concatenated normalized text used for embedding
# #     guidewire_version: Optional[str] = None
# #     event_date: date

# #     # --- filled in by later stages ---
# #     structured_sop: Optional[str] = None         # set by Structuring Agent
# #     embedding: Optional[list[float]] = None       # set by Structuring Agent
# #     curation_status: CurationStatus = CurationStatus.PENDING_REVIEW
# #     curation_notes: Optional[str] = None          # set by SME Curation Gate
# #     ingested_at: datetime = Field(default_factory=datetime.utcnow)
# #     kb_version: Optional[str] = None              # set when written to Knowledge Store
# #     source_url: Optional[str] = None              # provenance link, where the source is public
# #     source_metadata: Optional[dict] = None        # source-specific fields that don't fit the
# #                                                    # common columns (e.g. priority/assignee_team
# #                                                    # for a ticket, participants for a chat/RCA,
# #                                                    # owner_team/steps for a runbook). Stored as
# #                                                    # JSONB in each source's own table.
# #     content_hash: Optional[str] = None            # hash of this record's raw source fields, set
# #                                                    # by ingestion.py. Used only for the
# #                                                    # ingestion_state skip-if-unchanged check
# #                                                    # (knowledge_store.py); not written into any of
# #                                                    # the 4 content tables themselves.

# """
# Pydantic schemas for every stage of the pipeline shown in the architecture
# diagram:

#   4 raw sources  -->  Ingestion & Normalization  -->  Structuring Agent
#                  -->  SME Curation Gate  -->  Knowledge Store

# Each raw source keeps its own native shape (that's how it really looks
# coming out of a ticketing system, a chat export, a vendor release feed, or
# a runbook wiki). Ingestion is responsible for mapping all four into the
# single NormalizedRecord shape.
# """

# from __future__ import annotations

# from datetime import date, datetime
# from typing import Optional

# from pydantic import BaseModel, Field

# from taxonomy import (
#     CurationStatus,
#     GuidewireModule,
#     IssueCategory,
#     ResolutionType,
#     Severity,
#     SourceType,
# )


# # ---------------------------------------------------------------------------
# # RAW SOURCE 1: Historical Tickets (e.g. ServiceNow / Jira export)
# # ---------------------------------------------------------------------------
# class RawTicket(BaseModel):
#     ticket_id: str
#     created_at: datetime
#     closed_at: datetime
#     module_reported: Optional[str] = None  # free text as the agent typed it; optional --
#                                             # if absent, the LLM infers module from description
#     description: str
#     resolution_notes: str
#     priority: str                        # raw priority label from ticketing tool (P1..P4)
#     assignee_team: str
#     guidewire_version: Optional[str] = None
#     status: str = "Closed"               # ticketing-tool status (e.g. "Open", "In Progress",
#                                           # "Closed"). Defaults to "Closed" so existing raw
#                                           # data without this field keeps validating as-is.
#                                           # ingestion.py only ingests tickets whose status is
#                                           # a closed one (see ingestion.CLOSED_TICKET_STATUSES)
#                                           # -- an open/in-progress ticket is skipped entirely,
#                                           # not even sent to SME review, until it closes.


# # ---------------------------------------------------------------------------
# # RAW SOURCE 2: Chat transcripts & formal RCA reports
# # ---------------------------------------------------------------------------
# class RawChatOrRCA(BaseModel):
#     record_id: str
#     record_type: str                     # "chat_transcript" | "rca_report"
#     incident_id: Optional[str] = None    # links back to a Emergency/High ticket
#     date: date
#     participants: list[str]
#     module_reported: str
#     title: str
#     body: str                            # raw transcript text or RCA narrative
#     root_cause: Optional[str] = None     # populated for rca_report only
#     corrective_actions: Optional[str] = None


# # ---------------------------------------------------------------------------
# # RAW SOURCE 3: Guidewire release notes / vendor advisories
# # ---------------------------------------------------------------------------
# class RawReleaseNote(BaseModel):
#     release_id: str                      # e.g. "XENG-1875" or a Guidewire issue/release id
#     product: str
#     version: str                         # e.g. "Mammoth (2025.03)", "Palisades"
#     release_date: date
#     note_type: str                       # "feature" | "resolved_issue" | "known_issue"
#     title: str
#     details: str
#     affected_modules: list[str]
#     source_url: Optional[str] = None     # public Guidewire docs page this was sourced from


# # ---------------------------------------------------------------------------
# # RAW SOURCE 4: Runbooks / SOPs
# # ---------------------------------------------------------------------------
# class RawRunbook(BaseModel):
#     runbook_id: str
#     last_updated: date
#     owner_team: str
#     module_reported: str
#     title: str
#     trigger_condition: str               # when this runbook applies
#     steps: list[str]


# # ---------------------------------------------------------------------------
# # NORMALIZED RECORD — the common shape every source is mapped into
# # after Ingestion & Normalization. This is what the Structuring Agent,
# # SME Curation Gate and Knowledge Store all operate on.
# # ---------------------------------------------------------------------------
# class NormalizedRecord(BaseModel):
#     record_id: str
#     source_type: SourceType
#     source_ref_id: str                   # original ticket_id / release_id / etc.

#     # --- taxonomy tags applied during Ingestion & Normalization ---
#     module: GuidewireModule
#     category: IssueCategory
#     severity: Severity
#     resolution_type: ResolutionType
#     tagging_confidence: float = Field(ge=0.0, le=1.0)
#     tagging_reasoning: Optional[str] = None       # LLM's own explanation for category/resolution_type

#     # --- normalized content ---
#     title: str
#     raw_text: str                        # concatenated normalized text used for embedding
#     symptom_text: Optional[str] = None    # raw "what happened" narrative, used in structured_sop
#     resolution_text: Optional[str] = None # LLM-generated "how it was fixed" narrative, used in structured_sop
#     guidewire_version: Optional[str] = None
#     event_date: date

#     # --- filled in by later stages ---
#     structured_sop: Optional[str] = None         # set by Structuring Agent
#     embedding: Optional[list[float]] = None       # set by Structuring Agent
#     curation_status: CurationStatus = CurationStatus.PENDING_REVIEW
#     curation_notes: Optional[str] = None          # set by SME Curation Gate
#     ingested_at: datetime = Field(default_factory=datetime.utcnow)
#     kb_version: Optional[str] = None              # set when written to Knowledge Store
#     source_url: Optional[str] = None              # provenance link, where the source is public
#     source_metadata: Optional[dict] = None        # source-specific fields that don't fit the
#                                                    # common columns (e.g. priority/assignee_team
#                                                    # for a ticket, participants for a chat/RCA,
#                                                    # owner_team/steps for a runbook). Stored as
#                                                    # JSONB in each source's own table.
#     content_hash: Optional[str] = None            # hash of this record's raw source fields, set
#                                                    # by ingestion.py. Used only for the
#                                                    # ingestion_state skip-if-unchanged check
#                                                    # (knowledge_store.py); not written into any of
#                                                    # the 4 content tables themselves.





"""
Pydantic schemas for every stage of the pipeline shown in the architecture
diagram:

  4 raw sources  -->  Ingestion & Normalization  -->  Structuring Agent
                 -->  SME Curation Gate  -->  Knowledge Store

Each raw source keeps its own native shape (that's how it really looks
coming out of a ticketing system, a chat export, a vendor release feed, or
a runbook wiki). Ingestion is responsible for mapping all four into the
single NormalizedRecord shape.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, Field

from taxonomy import (
    CurationStatus,
    GuidewireModule,
    IssueCategory,
    ResolutionType,
    Severity,
    SourceType,
)


# ---------------------------------------------------------------------------
# RAW SOURCE 1: Historical Tickets (e.g. ServiceNow / Jira export)
# ---------------------------------------------------------------------------
class RawTicket(BaseModel):
    ticket_id: str
    created_at: datetime
    closed_at: datetime
    module_reported: Optional[str] = None  # free text as the agent typed it; optional --
                                            # if absent, the LLM infers module from description
    description: str
    resolution_notes: str
    priority: str                        # raw priority label from ticketing tool (P1..P4)
    assignee_team: str
    guidewire_version: Optional[str] = None
    status: str = "Closed"               # ticketing-tool status (e.g. "Open", "In Progress",
                                          # "Closed"). Defaults to "Closed" so existing raw
                                          # data without this field keeps validating as-is.
                                          # ingestion.py only ingests tickets whose status is
                                          # a closed one (see ingestion.CLOSED_TICKET_STATUSES)
                                          # -- an open/in-progress ticket is skipped entirely,
                                          # not even sent to SME review, until it closes.


# ---------------------------------------------------------------------------
# RAW SOURCE 2: Chat transcripts & formal RCA reports
# ---------------------------------------------------------------------------
class RawChatOrRCA(BaseModel):
    record_id: str
    record_type: str                     # "chat_transcript" | "rca_report"
    incident_id: Optional[str] = None    # links back to a Emergency/High ticket
    date: date
    participants: list[str]
    module_reported: str
    title: str
    body: str                            # raw transcript text or RCA narrative
    root_cause: Optional[str] = None     # populated for rca_report only
    corrective_actions: Optional[str] = None


# ---------------------------------------------------------------------------
# RAW SOURCE 3: Guidewire release notes / vendor advisories
# ---------------------------------------------------------------------------
class RawReleaseNote(BaseModel):
    release_id: str                      # e.g. "XENG-1875" or a Guidewire issue/release id
    product: str
    version: str                         # e.g. "Mammoth (2025.03)", "Palisades"
    release_date: date
    note_type: str                       # "feature" | "resolved_issue" | "known_issue"
    title: str
    details: str
    affected_modules: list[str]
    source_url: Optional[str] = None     # public Guidewire docs page this was sourced from


# ---------------------------------------------------------------------------
# RAW SOURCE 4: Runbooks / SOPs
# ---------------------------------------------------------------------------
class RawRunbook(BaseModel):
    runbook_id: str
    last_updated: date
    owner_team: str
    module_reported: str
    title: str
    trigger_condition: str               # when this runbook applies
    steps: list[str]


# ---------------------------------------------------------------------------
# NORMALIZED RECORD — the common shape every source is mapped into
# after Ingestion & Normalization. This is what the Structuring Agent,
# SME Curation Gate and Knowledge Store all operate on.
# ---------------------------------------------------------------------------
class NormalizedRecord(BaseModel):
    record_id: str
    source_type: SourceType
    source_ref_id: str                   # original ticket_id / release_id / etc.

    # --- taxonomy tags applied during Ingestion & Normalization ---
    module: GuidewireModule
    category: IssueCategory
    severity: Severity
    resolution_type: Optional[ResolutionType] = None   # NULL allowed (e.g. unresolved tickets)
    tagging_confidence: float = Field(ge=0.0, le=1.0)
    tagging_reasoning: Optional[str] = None       # LLM's own explanation for category/resolution_type

    # --- normalized content ---
    title: str
    raw_text: str                        # concatenated normalized text used for embedding
    symptom_text: Optional[str] = None    # raw "what happened" narrative, used in structured_sop
    resolution_text: Optional[str] = None # raw "how it was fixed" narrative (straight from the source record, never LLM-generated), used in structured_sop
    guidewire_version: Optional[str] = None
    event_date: date

    # --- filled in by later stages ---
    structured_sop: Optional[str] = None         # set by Structuring Agent
    embedding: Optional[list[float]] = None       # set by Structuring Agent
    curation_status: CurationStatus = CurationStatus.PENDING_REVIEW
    curation_notes: Optional[str] = None          # set by SME Curation Gate
    ingested_at: datetime = Field(default_factory=datetime.utcnow)
    kb_version: Optional[str] = None              # set when written to Knowledge Store
    source_url: Optional[str] = None              # provenance link, where the source is public
    source_metadata: Optional[dict] = None        # source-specific fields that don't fit the
                                                   # common columns (e.g. priority/assignee_team
                                                   # for a ticket, participants for a chat/RCA,
                                                   # owner_team/steps for a runbook). Stored as
                                                   # JSONB in each source's own table.
    content_hash: Optional[str] = None            # hash of this record's raw source fields, set
                                                   # by ingestion.py. Used only for the
                                                   # ingestion_state skip-if-unchanged check
                                                   # (knowledge_store.py); not written into any of
                                                   # the 4 content tables themselves.