import { useEffect, useState } from "react";
import { Loader2, Search } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { fetchSimilarTickets } from "@/lib/api";
import type { SimilarQuery, SimilarResult } from "@/lib/types";

interface Props {
  query: SimilarQuery;
  /** Run automatically on mount (detail view) vs only when the button is clicked (create dialog). */
  auto?: boolean;
}

const strengthVariant = (s: string): "default" | "secondary" | "outline" =>
  s.toLowerCase() === "strong" ? "default" : s.toLowerCase() === "moderate" ? "secondary" : "outline";

export function SimilarTicketsPanel({ query, auto = false }: Props) {
  const [result, setResult] = useState<SimilarResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = () => {
    setLoading(true);
    setError(null);
    fetchSimilarTickets(query)
      .then(setResult)
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    if (auto) run();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [auto, query.id]);

  const hasText = Boolean(query.title?.trim() || query.description?.trim());

  return (
    <div className="space-y-4 rounded-lg border border-border p-6">
      <div className="flex items-center justify-between gap-4">
        <h2 className="text-lg font-semibold">Similar historical tickets</h2>
        <Button type="button" variant="outline" size="sm" onClick={run} disabled={loading || !hasText}>
          {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Search className="h-4 w-4" />}
          {result ? "Refresh" : "Find similar tickets"}
        </Button>
      </div>

      {error && <p className="text-sm text-destructive">Could not search for similar tickets: {error}</p>}
      {loading && !result && <p className="text-sm text-muted-foreground">Searching the knowledge base…</p>}

      {result && result.no_confident_match && (
        <p className="text-sm text-muted-foreground">
          No confident match found. {result.suggested_next_step}
        </p>
      )}

      {result && result.matches.length > 0 && (
        <ul className="space-y-4">
          {result.matches.map((m) => (
            <li key={m.id} className="space-y-3 rounded-md border border-border bg-muted/30 p-4">
              <div className="flex flex-wrap items-center gap-3">
                <span className="font-semibold">
                  #{m.rank} · {m.id}
                </span>
                <Badge variant={strengthVariant(m.match_strength)}>{m.match_strength}</Badge>
                {m.ticket.system && <Badge variant="outline">{m.ticket.system}</Badge>}
              </div>
              {m.ticket.description && (
                <p className="line-clamp-3 text-sm text-foreground/90">{m.ticket.description}</p>
              )}
              <dl className="grid grid-cols-1 gap-3 text-sm sm:grid-cols-2">
                <div><dt className="font-medium">Why similar</dt><dd className="text-muted-foreground">{m.why_similar}</dd></div>
                <div><dt className="font-medium">What differs</dt><dd className="text-muted-foreground">{m.what_differs}</dd></div>
                <div><dt className="font-medium">Reusable resolution</dt><dd className="text-muted-foreground">{m.reusable_info}{m.ticket.resolution ? ` — ${m.ticket.resolution}` : ""}</dd></div>
                {m.ticket.rca && <div><dt className="font-medium">RCA</dt><dd className="text-muted-foreground">{m.ticket.rca}</dd></div>}
              </dl>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
