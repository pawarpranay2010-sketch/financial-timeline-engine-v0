"use client";

import { useState } from "react";
import { ChevronDown } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { StatusBadge, StatusExplanation, MetaField } from "@/components/status";
import { ValidationJourney } from "@/components/validation-journey";
import type { KernelProcessResponse } from "@/lib/types";
import { cn } from "@/lib/utils";

/**
 * Result experience — hierarchy fixed by spec:
 *   STATUS → interpretation → validation → authority → accounting → evidence
 *
 * Renders ONLY what the backend returned. Semantic fields are rendered
 * dynamically from the interpretation object (the 18-field Candidate
 * Semantic IR as projected by the API) — never renamed or reinterpreted.
 * An "engine_status" field, when present, is shown verbatim next to the
 * API-level status without conflating the two vocabularies.
 */

/** Fields rendered in the curated top grid, in this order, when present. */
const CURATED_FIELDS = [
  "transaction_type_enum",
  "transaction_type",
  "parties",
  "amounts",
  "payment_method_enum",
  "payment_method",
  "overall_confidence",
  "suggested_status",
] as const;

function formatFieldValue(value: unknown): string | null {
  if (value === null || value === undefined) return null;
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (Array.isArray(value)) {
    const parts = value
      .map((item) => {
        if (item == null) return null;
        if (typeof item === "string" || typeof item === "number" || typeof item === "boolean") {
          return String(item);
        }
        if (typeof item === "object") {
          const rec = item as Record<string, unknown>;
          const pair = [rec.currency, rec.value].filter((x) => x !== undefined && x !== null);
          if (pair.length) return pair.join(" ");
          return null;
        }
        return null;
      })
      .filter(Boolean);
    return parts.length ? parts.join(" · ") : null;
  }
  if (typeof value === "object") {
    const rec = value as Record<string, unknown>;
    const grounded = rec.all_fields_explicitly_grounded;
    if (typeof grounded === "boolean") return grounded ? "explicit" : "partial";
  }
  return null;
}

function FieldGrid({ fields }: { fields: Array<[string, string]> }) {
  if (!fields.length) return null;
  return (
    <dl className="grid gap-x-6 gap-y-2.5 sm:grid-cols-2">
      {fields.map(([key, value]) => (
        <div key={key} className="min-w-0 border-b border-border/60 pb-1.5">
          <dt className="font-mono text-[10.5px] uppercase tracking-[0.12em] text-muted-foreground/85">
            {key}
          </dt>
          <dd className="mt-0.5 truncate font-mono text-[13px] text-foreground/95" title={value}>
            {value}
          </dd>
        </div>
      ))}
    </dl>
  );
}

function Collapsible({
  title,
  badge,
  defaultOpen = false,
  children,
}: {
  title: string;
  badge?: string;
  defaultOpen?: boolean;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <section className="overflow-hidden rounded-lg border border-border/70">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-center justify-between gap-3 bg-muted/40 px-4 py-2.5 text-left transition-colors hover:bg-muted/70"
      >
        <span className="flex items-center gap-2.5">
          <span className="text-sm font-medium">{title}</span>
          {badge ? (
            <span className="rounded-full border border-border bg-card px-2 py-0.5 font-mono text-[10.5px] text-muted-foreground">
              {badge}
            </span>
          ) : null}
        </span>
        <ChevronDown
          className={cn("size-4 shrink-0 text-muted-foreground transition-transform", open && "rotate-180")}
          aria-hidden
        />
      </button>
      {open && <div className="border-t border-border/70 px-4 py-4">{children}</div>}
    </section>
  );
}

function IssueList({ title, items, tone }: { title: string; items: string[]; tone: "review" | "failed" }) {
  if (!items?.length) return null;
  return (
    <div>
      <h4
        className={cn(
          "mb-2 text-xs font-semibold uppercase tracking-wide",
          tone === "review" ? "text-status-review" : "text-status-failed",
        )}
      >
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

function AccountingView({ accounting }: { accounting: NonNullable<KernelProcessResponse["accounting"]> }) {
  const entries = accounting.journal_entries ?? [];
  const debits = accounting.debit_lines ?? [];
  const credits = accounting.credit_lines ?? [];
  const rows: Array<{ account: string; side: "Dr" | "Cr"; amount: string; why?: string }> = [];
  for (const e of entries) {
    if (e.debit) rows.push({ account: e.debit, side: "Dr", amount: String(e.amount ?? "") });
    if (e.credit) rows.push({ account: e.credit, side: "Cr", amount: String(e.amount ?? "") });
  }
  for (const d of debits) {
    const why = typeof d.why === "string" ? d.why : undefined;
    rows.push({ account: String(d.account ?? ""), side: "Dr", amount: String(d.amount ?? ""), why });
  }
  for (const c of credits) {
    const why = typeof c.why === "string" ? c.why : undefined;
    rows.push({ account: String(c.account ?? ""), side: "Cr", amount: String(c.amount ?? ""), why });
  }
  if (!rows.length) {
    return (
      <p className="text-sm text-muted-foreground">
        No deterministic accounting result was returned for this status.
      </p>
    );
  }
  return (
    <div className="space-y-2">
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
        <p className="text-xs text-muted-foreground">Narration: {accounting.narration}</p>
      ) : null}
    </div>
  );
}

function EvidenceView({ result }: { result: KernelProcessResponse }) {
  const interp = result.interpretation ?? {};
  const blocks: Array<{ title: string; items: Array<{ label: string; value: string }> }> = [];

  const grounding = interp.grounding as Record<string, unknown> | undefined;
  if (grounding && typeof grounding === "object") {
    const inferred = Array.isArray(grounding.inferred_fields) ? grounding.inferred_fields : [];
    const explicit = grounding.all_fields_explicitly_grounded;
    blocks.push({
      title: "Grounding",
      items: [
        ...(typeof explicit === "boolean"
          ? [{ label: "all_fields_explicitly_grounded", value: String(explicit) }]
          : []),
        ...inferred.map((f, i) => ({ label: `inferred_field[${i}]`, value: String(f) })),
      ],
    });
  }
  if (result.grounding_issues?.length) {
    blocks.push({
      title: "Grounding issues (backend-reported)",
      items: result.grounding_issues.map((g, i) => ({ label: `issue[${i}]`, value: g })),
    });
  }
  if (result.issues?.length) {
    blocks.push({
      title: "Review issues (backend-reported)",
      items: result.issues.map((issue, i) => ({ label: `issue[${i}]`, value: issue })),
    });
  }
  const fieldConfidences = interp.field_confidences;
  if (Array.isArray(fieldConfidences) && fieldConfidences.length) {
    blocks.push({
      title: "Field confidences",
      items: fieldConfidences.map((fc, i) => ({ label: `field[${i}]`, value: JSON.stringify(fc) })),
    });
  }

  if (!blocks.length) {
    return <p className="text-sm text-muted-foreground">No evidence supplied by the backend.</p>;
  }
  return (
    <div className="space-y-4">
      {blocks.map((block) => (
        <div key={block.title}>
          <h4 className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            {block.title}
          </h4>
          {block.items.length ? (
            <dl className="space-y-1 rounded-md border border-border/70 bg-muted/30 p-3">
              {block.items.map((item, i) => (
                <div key={i} className="grid gap-0.5 text-xs sm:grid-cols-[220px_1fr]">
                  <dt className="font-mono text-muted-foreground">{item.label}</dt>
                  <dd className="min-w-0 break-words font-mono text-foreground/90">{item.value}</dd>
                </div>
              ))}
            </dl>
          ) : (
            <p className="text-xs text-muted-foreground">No entries.</p>
          )}
        </div>
      ))}
    </div>
  );
}

export function ResultCard({ result, source }: { result: KernelProcessResponse; source: "api" | "demo" }) {
  const interp = result.interpretation;
  const candidate = (interp ?? {}) as Record<string, unknown>;
  const curated: Array<[string, string]> = [];
  for (const key of CURATED_FIELDS) {
    const formatted = formatFieldValue(candidate[key]);
    if (formatted !== null) curated.push([key, formatted]);
  }
  const extra = Object.keys(candidate)
    .filter((k) => !(CURATED_FIELDS as readonly string[]).includes(k) && k !== "grounding" && k !== "field_confidences")
    .map((k) => [k, formatFieldValue(candidate[k])] as [string, string | null])
    .filter((pair): pair is [string, string] => pair[1] !== null);

  const engineStatus = result.status;
  // The kernel route carries the ENGINE status verbatim but no Phase 5A
  // api_status field. Only display an API status if the backend actually
  // supplied one — never synthesize one (transport ≠ authority, and an
  // unreported value must not be invented in either direction).
  const rawApiStatus = (result as { api_status?: unknown }).api_status;
  const apiStatus = typeof rawApiStatus === "string" && rawApiStatus ? rawApiStatus : null;

  return (
    <Card className="overflow-hidden">
      <CardHeader className="gap-4 border-b border-border/70 bg-muted/30">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <CardTitle className="text-sm font-medium text-muted-foreground">Result</CardTitle>
            <StatusExplanation status={engineStatus} />
          </div>
          <StatusBadge status={engineStatus} label={result.status_label || engineStatus} size="lg" />
        </div>
        <div className="flex flex-wrap items-end gap-x-8 gap-y-3">
          <MetaField label="Request ID" value={result.request_id ?? "—"} />
          <MetaField
            label="API status"
            value={apiStatus ? apiStatus : "Not reported by this endpoint"}
          />
          <MetaField label="Engine status" value={engineStatus} />
          <MetaField
            label="Success"
            value={
              <span className={result.success ? "text-status-verified" : "text-muted-foreground"}>
                {String(result.success)}
              </span>
            }
            mono={false}
          />
        </div>
      </CardHeader>

      <CardContent className="space-y-5 pt-5">
        {source === "demo" && (
          <p className="rounded-md border border-status-review/30 bg-status-review/10 px-3 py-2 text-xs font-medium text-status-review">
            DEMO DATA — PREVIEW, NOT A LIVE RESULT. Rendered from a labeled fixture because the
            backend is not connected; never substituted silently for a real response.
          </p>
        )}

        <ValidationJourney result={result} />

        {curated.length > 0 && (
          <Collapsible title="Semantic Interpretation" badge="Candidate Semantic IR" defaultOpen>
            <FieldGrid fields={curated} />
            {extra.length > 0 && (
              <>
                <Separator className="my-4" />
                <p className="mb-2 text-[10.5px] uppercase tracking-[0.12em] text-muted-foreground/80">
                  Additional fields returned by the backend
                </p>
                <FieldGrid fields={extra} />
              </>
            )}
          </Collapsible>
        )}

        {(result.issues?.length ?? 0) + (result.grounding_issues?.length ?? 0) > 0 && (
          <Collapsible title="Validation Notes" badge={`${(result.issues?.length ?? 0) + (result.grounding_issues?.length ?? 0)} recorded`}>
            <div className="space-y-4">
              <IssueList title="Review issues" items={result.issues ?? []} tone="review" />
              <IssueList title="Grounding issues" items={result.grounding_issues ?? []} tone="failed" />
            </div>
          </Collapsible>
        )}

        {result.accounting && (
          <Collapsible
            title="Deterministic Accounting Result"
            badge={result.accounting.status ?? "executed by authority"}
          >
            <AccountingView accounting={result.accounting} />
          </Collapsible>
        )}

        <Collapsible title="Evidence" badge="backend-supplied">
          <EvidenceView result={result} />
        </Collapsible>

        {result.next_action ? (
          <div className="rounded-lg border border-border/70 bg-muted/30 px-4 py-3">
            <h4 className="text-[10.5px] font-medium uppercase tracking-[0.12em] text-muted-foreground/80">
              Next step
            </h4>
            <p className="mt-1 text-sm">{result.next_action}</p>
          </div>
        ) : null}

        <p className="text-[11px] leading-relaxed text-muted-foreground/75">
          Status and accounting decided by the deterministic runtime — this UI never upgrades,
          downgrades, or fills in results.
        </p>
      </CardContent>
    </Card>
  );
}
