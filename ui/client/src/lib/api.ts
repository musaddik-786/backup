import { mapTicketRow, type KbResolveResult, type SimilarQuery, type SimilarResult, type Ticket, type TicketRecordRow } from "./types";

async function getJson<T>(url: string): Promise<T> {
  const res = await fetch(url);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error || `Request to ${url} failed with ${res.status}`);
  }
  return res.json();
}

export async function fetchTickets(): Promise<Ticket[]> {
  const rows = await getJson<TicketRecordRow[]>("/api/tickets");
  return rows.map(mapTicketRow);
}

export async function fetchModules(): Promise<string[]> {
  return getJson<string[]>("/api/modules");
}

export async function fetchAssigneeTeams(): Promise<string[]> {
  return getJson<string[]>("/api/assignee-teams");
}

export async function fetchSimilarTickets(query: SimilarQuery): Promise<SimilarResult> {
  const res = await fetch("/api/tickets/similar", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(query),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || body.error || `Similar-ticket search failed (${res.status})`);
  }
  return res.json();
}

export async function resolveKbCard(query: { id?: string; title?: string; description?: string }): Promise<KbResolveResult> {
  const res = await fetch("/api/kb/resolve", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(query),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || body.error || `Suggested-fix lookup failed (${res.status})`);
  }
  return res.json();
}
