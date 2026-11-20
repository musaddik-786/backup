import { useEffect, useState } from "react";
import { AlertTriangle, Check, Lightbulb, Loader2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { resolveKbCard } from "@/lib/api";
import type { KbResolveResult } from "@/lib/types";

type Tier = "L1" | "L2" | "L3";

interface Props {
  query: { id?: string; title?: string; description?: string };
}

const List = ({ items, ordered = false }: { items: string[]; ordered?: boolean }) =>
  ordered ? (
    <ol className="list-decimal space-y-1 pl-5 text-sm">{items.map((t) => <li key={t}>{t}</li>)}</ol>
  ) : (
    <ul className="list-disc space-y-1 pl-5 text-sm">{items.map((t) => <li key={t}>{t}</li>)}</ul>
  );

const Section = ({ label, children }: { label: string; children: React.ReactNode }) => (
  <div className="space-y-1">
    <p className="text-sm font-medium">{label}</p>
    {children}
  </div>
);

export function SuggestedFixPanel({ query }: Props) {
  const [result, setResult] = useState<KbResolveResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [tier, setTier] = useState<Tier>("L1");

  const run = () => {
    setLoading(true);
    setError(null);
    resolveKbCard(query)
      .then(setResult)
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    run();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query.id]);

  const card = result?.unit?.content;

  return (
    <div className="space-y-4 rounded-lg border-2 border-primary/40 p-6">
      <div className="flex items-center justify-between gap-4">
        <h2 className="flex items-center gap-2 text-lg font-semibold">
          <Lightbulb className="h-5 w-5" /> Suggested fix
        </h2>
        <Button type="button" variant="outline" size="sm" onClick={run} disabled={loading}>
          {loading && <Loader2 className="h-4 w-4 animate-spin" />}
          Refresh
        </Button>
      </div>

      {error && <p className="text-sm text-destructive">Could not look up a suggested fix: {error}</p>}
      {loading && !result && <p className="text-sm text-muted-foreground">Checking the knowledge base…</p>}

      {result && !result.matched && (
        <div className="space-y-2">
          <p className="text-sm font-medium">No KB match</p>
          <p className="text-sm text-muted-foreground">{result.reason}</p>
          {result.candidates.length > 0 && (
            <p className="text-xs text-muted-foreground">
              Closest cards: {result.candidates.slice(0, 3).map((c) => `${c.unit_id} (${c.title})`).join(" · ")}
            </p>
          )}
        </div>
      )}

      {result && result.matched && result.unit && card && (
        <div className="space-y-4">
          <div className="space-y-1 rounded-md bg-muted/40 p-3">
            <p className="text-sm font-semibold">{result.unit.article_id} · {card.title}</p>
            <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
              <span>{card.root_cause_codes.join(", ")}</span>
              <span>· {card.play}</span>
              <span>· v{result.unit.version}</span>
              {result.confidence && <Badge variant="secondary">Match: {result.confidence}</Badge>}
              {!result.verified && <Badge variant="outline">Unverified</Badge>}
            </div>
            <p className="text-xs text-muted-foreground">{result.reason}</p>
          </div>

          <div className="flex gap-2">
            {(["L1", "L2", "L3"] as Tier[]).map((t) => (
              <Button key={t} type="button" size="sm" variant={tier === t ? "default" : "outline"} onClick={() => setTier(t)}>
                {t}
              </Button>
            ))}
          </div>

          {tier === "L1" && (
            <div className="space-y-3">
              <Section label="Information to collect">
                <ul className="space-y-1 text-sm">
                  {card.info_to_collect.map((item) => {
                    const missing = result.missing_info.includes(item);
                    return (
                      <li key={item} className={`flex items-start gap-2 ${missing ? "text-amber-700 dark:text-amber-400" : ""}`}>
                        {missing ? <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" /> : <Check className="mt-0.5 h-4 w-4 shrink-0 text-green-600" />}
                        <span>{item}{missing ? " — not in the ticket, ask the customer" : ""}</span>
                      </li>
                    );
                  })}
                </ul>
              </Section>
              <Section label="Escalation"><p className="text-sm">{card.escalation}</p></Section>
            </div>
          )}
          {tier === "L2" && (
            <div className="space-y-3">
              <Section label="Diagnosis"><List items={card.diagnosis} ordered /></Section>
              <Section label="Resolution"><List items={card.resolution} ordered /></Section>
              <Section label="Workaround"><p className="text-sm">{card.workaround}</p></Section>
            </div>
          )}
          {tier === "L3" && (
            <div className="space-y-3">
              <Section label="Cause"><p className="text-sm">{card.cause}</p></Section>
              <Section label="Permanent fix"><p className="text-sm">{card.permanent_fix}</p></Section>
              <Section label="Escalation"><p className="text-sm">{card.escalation}</p></Section>
              <Section label="Past tickets"><p className="text-sm">{card.related_tickets.join(", ") || "—"}</p></Section>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
