// Shapes mirror the real `ticket_records` table in the Knowledgeops_gw
// Postgres DB (see server/index.js: GET /api/tickets) — no stubbed fields.

export type CurationStatus =
  | "PendingReview"
  | "AutoApproved"
  | "SMEApproved"
  | "SMERejected"
  | "NeedsRetag";

export type Severity = "Emergency" | "High" | "Normal" | "Low";

export type ResolutionType =
  | "ConfigChange"
  | "CodeFix"
  | "PatchApplied"
  | "RestartRecycle"
  | "DataRemediation"
  | "VendorEscalation"
  | "Workaround"
  | "UserErrorClosed"
  | "NoActionByDesign";

export interface TicketSourceMetadata {
  priority?: string;
  assignee_team?: string;
  created_at?: string;
  closed_at?: string | null;
}

/** Raw row shape as returned by GET /api/tickets (snake_case, straight from Postgres). */
export interface TicketRecordRow {
  record_id: string;
  source_ref_id: string;
  module: string;
  category: string;
  severity: Severity;
  resolution_type: ResolutionType | null;
  title: string;
  raw_text: string;
  structured_sop: string | null;
  guidewire_version: string | null;
  event_date: string;
  source_metadata: TicketSourceMetadata | null;
  curation_status: CurationStatus;
  curation_notes: string | null;
  kb_version: string | null;
  ingested_at: string;
  source_url: string | null; // osTicket ticket page, when synced from osTicket
}

/** Normalized camelCase shape used throughout the React UI. */
export interface Ticket {
  recordId: string;
  id: string; // source_ref_id, e.g. "INC-100234"
  title: string;
  description: string; // raw_text
  module: string;
  category: string;
  severity: Severity;
  resolutionType: ResolutionType | null;
  curationStatus: CurationStatus;
  priority: string | null; // from source_metadata
  assigneeTeam: string | null; // from source_metadata
  createdAt: string | null; // from source_metadata
  closedAt: string | null; // from source_metadata
  guidewireVersion: string | null;
  eventDate: string;
  sourceUrl: string | null; // link to the ticket in osTicket
}

export function mapTicketRow(row: TicketRecordRow): Ticket {
  const meta = row.source_metadata ?? {};
  return {
    recordId: row.record_id,
    id: row.source_ref_id,
    title: row.title,
    description: row.raw_text,
    module: row.module,
    category: row.category,
    severity: row.severity,
    resolutionType: row.resolution_type,
    curationStatus: row.curation_status,
    priority: meta.priority ?? null,
    assigneeTeam: meta.assignee_team ?? null,
    createdAt: meta.created_at ?? null,
    closedAt: meta.closed_at ?? null,
    guidewireVersion: row.guidewire_version,
    eventDate: row.event_date,
    sourceUrl: row.source_url ?? null,
  };
}

/**
 * A ticket created from the UI. This is NOT persisted to the database yet —
 * the backend API for ticket creation is still being decided (MCP vs FastAPI).
 * It is held client-side only until that backend contract exists.
 */
export interface NewTicketInput {
  ticketId: string;
  createdAt: string;
  closedAt: string | null;
  issueTitle: string;
  summary: string;
  description: string;
  module: string;
  assigneeTeam: string;
  priority: string;
  complexity: string;
  createdBy: string;
}

export interface SimilarMatch {
  id: string;
  rank: number;
  match_strength: string;
  why_similar: string;
  what_differs: string;
  reusable_info: string;
  missing_info: string[] | string;
  ticket: {
    description: string | null;
    system: string | null;
    created: string | null;
    closed: string | null;
    severity: string | null;
    resolution: string | null;
    rca: string | null;
    runbook_link: string | null;
    assignee_team: string | null;
  };
}

export interface SimilarResult {
  new_ticket_assessment: unknown;
  matches: SimilarMatch[];
  no_confident_match: boolean;
  suggested_next_step: string;
}

export interface SimilarQuery {
  id?: string;
  title?: string;
  description?: string;
  module?: string;
  severity?: string;
  created?: string;
}

export interface KbCard {
  title: string;
  category: string;
  play: string;
  root_cause_codes: string[];
  symptoms: string[];
  cause: string;
  info_to_collect: string[];
  diagnosis: string[];
  resolution: string[];
  workaround: string;
  permanent_fix: string;
  escalation: string;
  related_tickets: string[];
}

export interface KbResolveResult {
  matched: boolean;
  verified: boolean;
  confidence: "high" | "medium" | "low" | null;
  reason: string;
  missing_info: string[];
  unit: { unit_id: string; article_id: string; version: string; kb_version: string | null; content: KbCard } | null;
  candidates: { unit_id: string; title: string }[];
}
