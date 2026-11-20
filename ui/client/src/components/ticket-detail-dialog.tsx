import { X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import type { Ticket } from "@/lib/types";
import { SimilarTicketsPanel } from "@/components/similar-tickets-panel";
import { SuggestedFixPanel } from "@/components/suggested-fix-panel";

interface TicketDetailDialogProps {
  ticket: Ticket | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

function formatTimestamp(value: string | null): string {
  if (!value) return "Open (not closed yet)";
  return new Date(value).toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

function Field({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="space-y-2">
      <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
        {label}
      </div>
      <div className="text-base font-medium">{value}</div>
    </div>
  );
}

export function TicketDetailDialog({ ticket, open, onOpenChange }: TicketDetailDialogProps) {
  if (!ticket || !open) return null;

  return (
    <div className="fixed inset-y-0 left-64 right-0 z-50 overflow-y-auto bg-background">
      <div className="mx-auto max-w-[1200px] px-10 py-10">
        <div className="mb-8 flex items-start justify-between gap-6 border-b border-border pb-6">
          <h1 className="text-2xl font-bold leading-snug tracking-tight">
            #{ticket.id} — {ticket.title}
          </h1>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            <X className="h-4 w-4" />
            Close
          </Button>
        </div>

        <div className="space-y-10">
          <div className="grid grid-cols-1 gap-8 rounded-lg border border-dashed border-border bg-muted/40 p-8 sm:grid-cols-3">
            <Field label="Ticket ID" value={ticket.id} />
            <Field label="Created At" value={formatTimestamp(ticket.createdAt)} />
            <Field label="Closed At" value={formatTimestamp(ticket.closedAt)} />
          </div>

          <div className="grid grid-cols-1 gap-8 sm:grid-cols-4">
            <Field label="Application / Module" value={<Badge variant="outline">{ticket.module}</Badge>} />
            <Field label="Assignee Team" value={ticket.assigneeTeam ?? "—"} />
            <Field label="Severity" value={ticket.severity} />
            <Field label="Category" value={ticket.category} />
          </div>

          <div className="grid grid-cols-1 gap-8 sm:grid-cols-3">
            <Field label="Resolution Type" value={ticket.resolutionType} />
            <Field label="Curation Status" value={<Badge>{ticket.curationStatus}</Badge>} />
            <Field label="Guidewire Version" value={ticket.guidewireVersion ?? "—"} />
          </div>

          <div className="space-y-2 border-t border-border pt-8">
            <Field
              label="Description"
              value={
                <p className="font-normal leading-relaxed text-foreground/90">
                  {ticket.description}
                </p>
              }
            />
          </div>

          <SuggestedFixPanel query={{ id: ticket.id, title: ticket.title, description: ticket.description }} />

          <SimilarTicketsPanel
            auto
            query={{
              id: ticket.id,
              title: ticket.title,
              description: ticket.description,
              module: ticket.module,
              severity: ticket.severity,
              created: ticket.createdAt ?? undefined,
            }}
          />
        </div>
      </div>
    </div>
  );
}
