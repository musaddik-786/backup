import { useEffect, useMemo, useState } from "react";
import {
  RefreshCw,
  Maximize2,
  RotateCcw,
  Plus,
  Search,
  Download,
  FileText,
  Upload,
  Loader2,
  ExternalLink,
} from "lucide-react";

import { AppSidebar } from "@/components/app-sidebar";
import { StatCard } from "@/components/stat-card";
import { TicketDetailDialog } from "@/components/ticket-detail-dialog";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { cn } from "@/lib/utils";
import { fetchTickets } from "@/lib/api";
import type { CurationStatus, Ticket } from "@/lib/types";

const FILTER_TABS: { label: string; value: CurationStatus | "All" }[] = [
  { label: "All", value: "All" },
  { label: "Pending Review", value: "PendingReview" },
  { label: "Auto Approved", value: "AutoApproved" },
  { label: "SME Approved", value: "SMEApproved" },
  { label: "SME Rejected", value: "SMERejected" },
  { label: "Needs Retag", value: "NeedsRetag" },
];

const OSTICKET_URL = (import.meta.env.VITE_OSTICKET_URL as string | undefined) ?? "http://127.0.0.1:8081";

function severityBadgeClass(severity: Ticket["severity"]) {
  switch (severity) {
    case "Emergency":
      return "bg-destructive/10 text-destructive";
    case "High":
      return "bg-warning/10 text-warning";
    case "Normal":
      return "bg-primary/10 text-primary";
    case "Low":
      return "bg-muted text-muted-foreground";
  }
}

function statusBadgeClass(status: Ticket["curationStatus"]) {
  switch (status) {
    case "SMEApproved":
    case "AutoApproved":
      return "bg-success/10 text-success";
    case "SMERejected":
      return "bg-destructive/10 text-destructive";
    case "NeedsRetag":
      return "bg-warning/10 text-warning";
    case "PendingReview":
    default:
      return "bg-secondary text-secondary-foreground";
  }
}

export default function OperationsHub() {
  const [tickets, setTickets] = useState<Ticket[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [activeFilter, setActiveFilter] = useState<CurationStatus | "All">("All");
  const [appFilter, setAppFilter] = useState<string>("all");
  const [search, setSearch] = useState("");
  const [selectedTicket, setSelectedTicket] = useState<Ticket | null>(null);

  const loadTickets = () => {
    setIsLoading(true);
    setLoadError(null);
    fetchTickets()
      .then(setTickets)
      .catch((err) => setLoadError(err.message))
      .finally(() => setIsLoading(false));
  };

  useEffect(() => {
    loadTickets();
  }, []);

  const allTickets = tickets;

  const modules = useMemo(
    () => Array.from(new Set(allTickets.map((t) => t.module))).sort(),
    [allTickets],
  );

  const filteredTickets = useMemo(() => {
    return allTickets.filter((t) => {
      const matchesStatus = activeFilter === "All" || t.curationStatus === activeFilter;
      const matchesApp = appFilter === "all" || t.module === appFilter;
      const matchesSearch =
        search.trim() === "" ||
        t.title.toLowerCase().includes(search.toLowerCase()) ||
        t.id.toLowerCase().includes(search.toLowerCase());
      return matchesStatus && matchesApp && matchesSearch;
    });
  }, [allTickets, activeFilter, appFilter, search]);

  const stats = useMemo(() => {
    const pending = allTickets.filter((t) => t.curationStatus === "PendingReview").length;
    const approved = allTickets.filter(
      (t) => t.curationStatus === "AutoApproved" || t.curationStatus === "SMEApproved",
    ).length;
    return { pending, approved, total: allTickets.length };
  }, [allTickets]);

  return (
    <div className="flex h-screen w-full bg-background">
      <AppSidebar activeItem="AI Insights" />

      <main className="flex-1 overflow-y-auto">
        <div className="mx-auto max-w-[1400px] px-8 py-8">
          {/* Header */}
          <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
            <div>
              <h1 className="text-3xl font-bold tracking-tight">Guidewire AI Insights</h1>
              <p className="mt-1 text-muted-foreground">
                AI analysis of osTicket tickets: tagging, similar cases and resolution guidance. Work the queue itself in osTicket.
              </p>
            </div>
          </div>

          <div className="mb-6 flex justify-end">
            <Button asChild>
              <a href={`${OSTICKET_URL}/open.php`} target="_blank" rel="noreferrer">
                <Plus className="h-4 w-4" />
                New Ticket (osTicket)
              </a>
            </Button>
          </div>

          {loadError && (
            <div className="mb-6 rounded-md border border-destructive/30 bg-destructive/5 px-4 py-3 text-sm text-destructive">
              Failed to load tickets from the database: {loadError}. Make sure the local API
              server is running (<code>npm run server</code>).
            </div>
          )}

          {/* Stat Cards */}
          <div className="mb-6 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            <StatCard
              icon={({ className }) => (
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className={className}>
                  <circle cx="12" cy="12" r="10" />
                  <line x1="12" y1="8" x2="12" y2="12" />
                  <line x1="12" y1="16" x2="12.01" y2="16" />
                </svg>
              )}
              value={stats.pending}
              label="Pending Review"
              iconColorClass="text-muted-foreground"
              iconBgClass="bg-muted"
            />
            <StatCard
              icon={({ className }) => (
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className={className}>
                  <circle cx="12" cy="12" r="10" />
                  <polyline points="9 12 11 14 15 10" />
                </svg>
              )}
              value={stats.approved}
              label="Approved"
              iconColorClass="text-success"
              iconBgClass="bg-success/10"
            />
            <StatCard
              icon={({ className }) => (
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className={className}>
                  <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2" />
                </svg>
              )}
              value={stats.total}
              label="Total"
              iconColorClass="text-purple-500"
              iconBgClass="bg-purple-500/10"
            />
          </div>

          {/* Filters */}
          <div className="mb-4 flex flex-wrap items-center gap-3">
            <div className="relative w-48">
              <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                placeholder="Search"
                className="pl-9"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
            </div>

            <Select value={appFilter} onValueChange={setAppFilter}>
              <SelectTrigger className="w-48">
                <SelectValue placeholder="All Apps" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All Apps</SelectItem>
                {modules.map((mod) => (
                  <SelectItem key={mod} value={mod}>
                    {mod}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>

            <div className="flex flex-wrap items-center gap-1.5">
              {FILTER_TABS.map((tab) => (
                <button
                  key={tab.value}
                  onClick={() => setActiveFilter(tab.value)}
                  className={cn(
                    "rounded-full px-4 py-1.5 text-sm font-medium transition-colors",
                    activeFilter === tab.value
                      ? "bg-foreground text-background"
                      : "bg-secondary text-secondary-foreground hover:bg-secondary/70",
                  )}
                >
                  {tab.label}
                </button>
              ))}
            </div>
          </div>


          {/* Table */}
          <div className="overflow-hidden rounded-lg border border-border bg-card">
            {isLoading ? (
              <div className="flex items-center justify-center gap-2 px-6 py-16 text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" />
                Loading tickets from database...
              </div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-border text-left text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                      <th className="px-6 py-3">ID</th>
                      <th className="px-6 py-3">Issue</th>
                      <th className="px-6 py-3">Application</th>
                      <th className="px-6 py-3">Status</th>
                      <th className="px-6 py-3">Severity</th>
                      <th className="px-6 py-3">Resolution Type</th>
                      <th className="px-6 py-3">Assignee Team</th>
                      <th className="px-6 py-3">Created</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filteredTickets.map((ticket) => (
                      <tr
                        key={ticket.recordId}
                        onClick={() => setSelectedTicket(ticket)}
                        className="cursor-pointer border-b border-border last:border-0 hover:bg-muted/40"
                      >
                        <td className="px-6 py-4 align-top text-muted-foreground">
                          {ticket.sourceUrl ? (
                            <a
                              href={ticket.sourceUrl}
                              target="_blank"
                              rel="noreferrer"
                              onClick={(e) => e.stopPropagation()}
                              className="inline-flex items-center gap-1 text-primary hover:underline"
                              title="Open in osTicket"
                            >
                              #{ticket.id}
                              <ExternalLink className="h-3 w-3" />
                            </a>
                          ) : (
                            <>#{ticket.id}</>
                          )}
                        </td>
                        <td className="px-6 py-4 align-top">
                          <div className="font-semibold">{ticket.title}</div>
                          <div className="mt-0.5 max-w-sm truncate text-xs text-muted-foreground">
                            {ticket.description}
                          </div>
                        </td>
                        <td className="px-6 py-4 align-top">
                          <Badge variant="outline">{ticket.module}</Badge>
                        </td>
                        <td className="px-6 py-4 align-top">
                          <Badge className={statusBadgeClass(ticket.curationStatus)}>
                            {ticket.curationStatus}
                          </Badge>
                        </td>
                        <td className="px-6 py-4 align-top">
                          <span
                            className={cn(
                              "inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-semibold",
                              severityBadgeClass(ticket.severity),
                            )}
                          >
                            <span className="h-1.5 w-1.5 rounded-full bg-current" />
                            {ticket.severity}
                          </span>
                        </td>
                        <td className="px-6 py-4 align-top text-muted-foreground">
                          {ticket.resolutionType}
                        </td>
                        <td className="px-6 py-4 align-top text-muted-foreground">
                          {ticket.assigneeTeam ?? "—"}
                        </td>
                        <td className="px-6 py-4 align-top text-muted-foreground">
                          {ticket.createdAt
                            ? new Date(ticket.createdAt).toLocaleString("en-US", {
                                month: "short",
                                day: "numeric",
                                hour: "numeric",
                                minute: "2-digit",
                              })
                            : "—"}
                        </td>
                      </tr>
                    ))}
                    {filteredTickets.length === 0 && (
                      <tr>
                        <td colSpan={8} className="px-6 py-10 text-center text-muted-foreground">
                          No tickets match the current filters.
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>
      </main>

      <TicketDetailDialog
        ticket={selectedTicket}
        open={selectedTicket !== null}
        onOpenChange={(next) => {
          if (!next) setSelectedTicket(null);
        }}
      />
    </div>
  );
}
