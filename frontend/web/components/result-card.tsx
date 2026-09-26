"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { StatusBadge, StatusExplanation } from "@/components/status";
import type { KernelProcessResponse } from "@/lib/types";
import { cn } from "@/lib/utils";

/**
 * Renders ONLY what the API returned. No accounting logic, no status
 * reinterpretation, no invented journal lines. Demo results are rendered
 * identically but always carry an explicit "demo data" notice (never
 * presented as a backend answer).
 */

function FactList({ facts }: { facts: Array<[string, string]> }) {
  if (facts.length === 0) return null;
  return (
    <dl className="grid gap-x-6 gap-y-2 sm:grid-cols-2">
      {facts.map(([k, v]) => (
        <div key={k} className="flex min-w-0 items-baseline justify-between gap-3 border-b border-border/60 pb-1.5">
          <dt className="shrink-0 font-mono text-[11px] uppercase tracking-wide text-muted-foreground">{k}</dt>
          <dd className="min-w-0 truncate text-right font-mono text-sm">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

function IssueList({ title, items, tone }: { title: string; items: string[]; tone: "review" | "failed" }) {
  if (items.length === 0) return null;
  return (
    <div>
      <h4 className={cn("mb-2 text-xs font-semibold uppercase tracking-wide", tone === "review" ? "text-status-review" : "text-status-failed")}>
        {title}
      </h4>
      <ul className="space-y-1.5">
        {items.map((issue, i) => (
          <li key={i} className="flex gap-2 text-sm text-muted-foreground">
            <span aria-hidden className="mt-[7px] size-1 shrink-0 rounded-full bg-current" />
            <span className="min-w-0">{issue}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function JournalTable({ result }: { result: KernelProcessResponse }) {
  const accounting = result.accounting;
  if (!accounting) return null;

  const entries = accounting.journal_entries ?? [];
  const debits = accounting.debit_lines ?? [];
  const credits = accounting.credit_lines ?? [];
  const rows: Array<{ account: string; side: "Dr" | "Cr"; amount: string }> = [];
  for (const e of entries) {
    if (e.debit) rows.push({ account: e.debit, side: "Dr", amount: String(e.amount ?? "") });
    if (e.credit) rows.push({ account: e.credit, side: "Cr", amount: String(e.amount ?? "") });
  }
  for (const d of debits) rows.push({ account: String(d.account ?? ""), side: "Dr", amount: String(d.amount ?? "") });
  for (const c of credits) rows.push({ account: String(c.account ?? ""), side: "Cr", amount: String(c.amount ?? "") });
  if (rows.length === 0) return null;

  return (
    <div>
      <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        Deterministic journal — executed by the accounting authority
      </h4>
      <div className="overflow-hidden rounded-lg border border-border">
        <table className="w-full text-sm">
          <thead className="bg-muted/60 text-left text-[11px] uppercase tracking-wide text-muted-foreground">
            <tr>
              <th className="px-3 py-2 font-medium">Account</th>
              <th className="px-3 py-2 font-medium">Side</th>
              <th className="px-3 py-2 text-right font-medium">Amount</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border/70 font-mono">
            {rows.map((row, i) => (
              <tr key={i} className="bg-card">
                <td className="px-3 py-2">{row.account}</td>
                <td className={cn("px-3 py-2", row.side === "Dr" ? "text-foreground" : "text-muted-foreground")}>
                  {row.side}
                </td>
                <td className="px-3 py-2 text-right">{row.amount}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {accounting.narration ? (
        <p className="mt-2 text-xs text-muted-foreground">Narration: {accounting.narration}</p>
      ) : null}
    </div>
  );
}

export function ResultCard({ result, source }: { result: KernelProcessResponse; source: "api" | "demo" }) {
  const candidate = result.interpretation;
  const facts: Array<[string, string]> = [];
  if (candidate) {
    const type = candidate.transaction_type_enum ?? candidate.transaction_type;
    if (type) facts.push(["type", String(type)]);
    if (candidate.parties?.length) facts.push(["parties", candidate.parties.join(", ")]);
    if (candidate.amounts?.length) {
      const amounts = candidate.amounts
        .map((a) => [a.currency, a.value].filter(Boolean).join(" "))
        .join(", ");
      if (amounts) facts.push(["amounts", amounts]);
    }
    if (candidate.payment_method_enum ?? candidate.payment_method)
      facts.push(["payment", String(candidate.payment_method_enum ?? candidate.payment_method)]);
    const grounded = candidate.grounding?.all_fields_explicitly_grounded;
    if (typeof grounded === "boolean") facts.push(["grounded", grounded ? "explicit" : "partial"]);
    if (candidate.overall_confidence) facts.push(["confidence", String(candidate.overall_confidence)]);
  }

  return (
    <Card className="overflow-hidden">
      <CardHeader className="flex-row items-center justify-between gap-3 border-b border-border/70 bg-muted/30">
        <div className="min-w-0">
          <CardTitle className="text-sm font-medium text-muted-foreground">Result</CardTitle>
          {result.request_id ? (
            <p className="mt-0.5 truncate font-mono text-[11px] text-muted-foreground/80">
              request {result.request_id}
            </p>
          ) : null}
        </div>
        <StatusBadge status={result.status} label={result.status_label || result.status} />
      </CardHeader>
      <CardContent className="space-y-5 pt-5">
        {source === "demo" && (
          <p className="rounded-md border border-status-review/30 bg-status-review/10 px-3 py-2 text-xs text-status-review">
            Demo data — shown because the backend API is not connected in this
            environment. This is NOT a live Platrixa result.
          </p>
        )}

        <StatusExplanation status={result.status} />

        {facts.length > 0 && (
          <div>
            <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              What Platrixa understood
            </h4>
            <FactList facts={facts} />
          </div>
        )}

        <IssueList title="Review reasons" items={result.issues ?? []} tone="review" />
        <IssueList title="Grounding evidence" items={result.grounding_issues ?? []} tone="failed" />

        <JournalTable result={result} />

        {result.next_action ? (
          <div>
            <Separator className="mb-4" />
            <h4 className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              Next step
            </h4>
            <p className="text-sm">{result.next_action}</p>
          </div>
        ) : null}

        <p className="text-[11px] leading-relaxed text-muted-foreground/80">
          Status decided by the deterministic runtime — the UI never upgrades,
          downgrades, or fills in results. success={String(result.success)} · persisted=
          {String(result.persisted)}
        </p>
      </CardContent>
    </Card>
  );
}
