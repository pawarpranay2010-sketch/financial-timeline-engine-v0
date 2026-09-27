"use client";

import { useMemo, useState } from "react";
import { ChevronDown, FileText, Landmark, Users, CalendarClock, CircleAlert } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { StatusBadge, StatusExplanation, MetaField } from "@/components/status";
import { ValidationJourney } from "@/components/validation-journey";
import type {
  AccountingResult,
  DocumentEvidence,
  DocumentProvenance,
  EngineStatus,
  InterpretationCandidate,
} from "@/lib/types";
import { cn } from "@/lib/utils";

/**
 * Professional validation report — hierarchy fixed by the product spec:
 *   1 final status · 2 why · 3 interpretation · 4 validation journey ·
 *   5 authority · 6 evidence · 7 accounting · 8 audit metadata
 *
 * Every displayed field comes from the actual response. The 18-field
 * Candidate Semantic IR is grouped for readability (Transaction / Parties /
 * Amounts / Settlement / Ambiguity & grounding) — never renamed, never
 * reinterpreted; anything not in a known group is shown verbatim under
 * "Additional fields".
 */

export interface ReportInput {
  status: EngineStatus;
  status_label?: string;
  success: boolean;
  next_action?: string | null;
  issues: string[];
  grounding_issues: string[];
  interpretation: InterpretationCandidate | null;
  accounting: AccountingResult | null;
  request_id?: string | null;
  api_status?: string;
  engine_status?: string | null;
  retryable?: boolean;
  reason_code?: string | null;
  document?: DocumentProvenance;
  evidence?: DocumentEvidence[];
  timings_ms?: Record<string, unknown>;
  notes?: string[];
}

const IR_GROUPS: Array<{ key: string; icon: React.ReactNode; fields: string[] }> = [
  { key: "Transaction", icon: <Landmark className="size-3.5" aria-hidden />, fields: ["transaction_type", "transaction_type_enum"] },
  { key: "Parties", icon: <Users className="size-3.5" aria-hidden />, fields: ["parties"] },
  { key: "Amounts", icon: <Landmark className="size-3.5" aria-hidden />, fields: ["amounts", "overall_confidence", "field_confidences"] },
  { key: "Settlement", icon: <FileText className="size-3.5" aria-hidden />, fields: ["payment_method", "payment_method_enum", "references"] },
  {
    key: "Ambiguity & grounding",
    icon: <CircleAlert className="size-3.5" aria-hidden />,
    fields: ["ambiguities", "ambiguity_flags", "safety_flags", "scope_flags", "suggested_status", "grounding"],
  },
  { key: "Timing", icon: <CalendarClock className="size-3.5" aria-hidden />, fields: ["date", "transaction_date", "dates", "period"] },
];

function formatFieldValue(value: unknown): string | null {
  if (value === null || value === undefined) return null;
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (Array.isArray(value)) {
    const parts = value
      .map((item) => {
        if (item == null) return null;
        if (typeof item === "string" || typeof item === "number" || typeof item === "boolean") return String(item);
        if (typeof item === "object") {
          const rec = item as Record<string, unknown>;
          const pair = [rec.currency, rec.value, rec.account, rec.name].filter(
            (x) => x !== undefined && x !== null,
          );
          if (pair.length) return pair.join(" ");
          if ("confidence" in rec) return String(rec.confidence);
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
    if (typeof grounded === "boolean") return grounded ? "explicitly grounded" : "partially grounded";
    if (Array.isArray(rec.inferred_fields)) return `inferred: ${rec.inferred_fields.join(", ") || "none"}`;
  }
  return null;
}

function FieldList({ entries }: { entries: Array<[string, string]> }) {
  if (!entries.length) return null;
  return (
    <dl className="space-y-2.5">
      {entries.map(([key, value]) => (
        <div key={key} className="grid gap-0.5 sm:grid-cols-[minmax(140px,200px)_1fr] sm:gap-4">
          <dt className="font-mono text-[10.5px] uppercase tracking-[0.12em] text-muted-foreground/85">{key}</dt>
          <dd className="min-w-0 break-words font-mono text-[13px] text-foreground/95">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

function Collapsible({
  title,
  icon,
  badge,
  defaultOpen = false,
  children,
}: {
  title: string;
  icon?: React.ReactNode;
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
          {icon ? <span className="text-accent">{icon}</span> : null}
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

function InterpretationPanels({ interpretation }: { interpretation: InterpretationCandidate }) {
  const entries = Object.entries(interpretation).filter(([, v]) => v !== null && v !== undefined);
  const grouped = IR_GROUPS.map((group) => ({
    ...group,
    items: group.fields
      .map((field) => {
        const value = entries.find(([k]) => k === field)?.[1];
        return [field, value] as [string, unknown];
      })
      .filter(([, v]) => v !== undefined),
  })).filter((g) => g.items.length > 0);

  const groupedKeys = new Set(IR_GROUPS.flatMap((g) => g.fields));
  const extra = entries.filter(([k]) => !groupedKeys.has(k));

  return (
    <div className="space-y-2.5">
      {grouped.map((group) => (
        <Collapsible
          key={group.key}
          title={group.key}
          icon={group.icon}
          badge={`${group.items.length} field${group.items.length === 1 ? "" : "s"}`}
        >
          <FieldList
            entries={group.items.map(([k, v]) => {
              const formatted = formatFieldValue(v);
              return [k, formatted ?? JSON.stringify(v)] as [string, string];
            })}
          />
        </Collapsible>
      ))}
      {extra.length > 0 && (
        <Collapsible title="Additional fields returned by the backend" badge={`${extra.length}`}>
          <FieldList
            entries={extra.map(([k, v]) => {
              const formatted = formatFieldValue(v);
              return [k, formatted ?? JSON.stringify(v)] as [string, string];
            })}
          />
        </Collapsible>
      )}
    </div>
  );
}

function DocumentProvenancePanel({
  document,
  evidence,
  timings,
}: {
  document: DocumentProvenance;
  evidence: DocumentEvidence[];
  timings?: Record<string, unknown>;
}) {
  const meta: Array<[string, string]> = [];
  for (const key of ["source_name", "source_type", "content_type", "page_count", "size_bytes"]) {
    const value = document[key];
    if (value !== undefined && value !== null) meta.push([key, String(value)]);
  }
  return (
    <div className="space-y-3">
      {meta.length > 0 && <FieldList entries={meta} />}
      {timings && Object.keys(timings).length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {Object.entries(timings).map(([k, v]) => (
            <span key={k} className="rounded border border-border bg-muted/40 px-2 py-0.5 font-mono text-[11px] text-muted-foreground">
              {k}: {String(v)}
            </span>
          ))}
        </div>
      )}
      {evidence.length > 0 ? (
        <ul className="space-y-2">
          {evidence.slice(0, 50).map((item, i) => (
            <li key={i} className="rounded-md border border-border/70 bg-muted/30 p-2.5">
              <div className="flex flex-wrap items-center gap-2">
                {item.page !== undefined && (
                  <span className="rounded bg-accent-soft px-1.5 py-0.5 font-mono text-[10px] text-accent">
                    page {String(item.page)}
                  </span>
                )}
                {item.source_id ? (
                  <span className="font-mono text-[10.5px] text-muted-foreground">{String(item.source_id)}</span>
                ) : null}
              </div>
              {item.text_span ? (
                <p className="mt-1.5 font-mono text-[12px] leading-relaxed text-foreground/90">{String(item.text_span)}</p>
              ) : null}
            </li>
          ))}
          {evidence.length > 50 && (
            <li className="text-xs text-muted-foreground">+ {evidence.length - 50} more evidence records returned by the backend</li>
          )}
        </ul>
      ) : (
        <p className="text-sm text-muted-foreground">No evidence records were supplied by the backend.</p>
      )}
    </div>
  );
}

function AccountingPanel({ accounting }: { accounting: AccountingResult }) {
  const rows: Array<{ account: string; amount: string; side: "debit" | "credit" }> = [];
  const push = (side: "debit" | "credit", list: AccountingResult["debit_lines"] | AccountingResult["credit_lines"]) => {
    for (const line of list ?? []) {
      if (line?.account) rows.push({ account: line.account, amount: String(line.amount ?? ""), side });
    }
  };
  push("debit", accounting.debit_lines);
  push("credit", accounting.credit_lines);
  for (const entry of accounting.journal_entries ?? []) {
    if (entry?.debit) rows.push({ account: entry.debit, amount: String(entry.amount ?? ""), side: "debit" });
    if (entry?.credit) rows.push({ account: entry.credit, amount: String(entry.amount ?? ""), side: "credit" });
  }
  return (
    <div className="space-y-3">
      {rows.length > 0 && (
        <div className="overflow-hidden rounded-lg border border-border/70">
          <table className="w-full text-sm">
            <tbody>
              {rows.map((row, i) => (
                <tr key={i} className={cn("border-b border-border/60 last:border-0", row.side === "debit" ? "bg-muted/20" : "")}>
                  <td className="px-3 py-2 font-mono text-[12.5px]">{row.account}</td>
                  <td className="px-3 py-2 text-right font-mono text-[10.5px] uppercase tracking-wider text-muted-foreground">
                    {row.side}
                  </td>
                  <td className="px-3 py-2 text-right font-mono text-[12.5px]">{row.amount}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {accounting.narration ? <p className="text-sm text-muted-foreground">{accounting.narration}</p> : null}
      {!rows.length && !accounting.narration && (
        <FieldList entries={Object.entries(accounting).filter(([, v]) => v !== null && v !== undefined).map(([k, v]) => {
          const formatted = formatFieldValue(v);
          return [k, formatted ?? JSON.stringify(v)] as [string, string];
        })} />
      )}
    </div>
  );
}

export function ResultReport({ report, source }: { report: ReportInput; source: "api" | "demo" }) {
  const hasDocument = report.document != null;
  const evidenceCount = report.evidence?.length ?? 0;
  const irEntries = useMemo(
    () => (report.interpretation ? Object.keys(report.interpretation).filter((k) => report.interpretation?.[k] !== null && report.interpretation?.[k] !== undefined).length : 0),
    [report.interpretation],
  );

  return (
    <div className="space-y-4">
      {source === "demo" && (
        <Card className="border-status-review/40 bg-status-review/5">
          <CardContent className="flex items-center gap-2 py-3 text-sm">
            <span className="rounded bg-status-review/15 px-2 py-0.5 font-mono text-[11px] font-semibold text-status-review">
              DEMO DATA
            </span>
            <span className="text-muted-foreground">Preview fixture — not a live result.</span>
          </CardContent>
        </Card>
      )}

      <Card className="overflow-hidden border-border/70">
        <CardHeader className="border-b border-border/70 pb-4">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <CardTitle className="flex flex-wrap items-center gap-2.5 text-base">
                <StatusBadge status={report.status as EngineStatus} label={report.status_label || report.status} />
                {report.reason_code ? (
                  <span className="rounded border border-border px-1.5 py-0.5 font-mono text-[10.5px] text-muted-foreground">
                    {report.reason_code}
                  </span>
                ) : null}
                {report.retryable === true ? (
                  <span className="rounded border border-status-processing/40 bg-status-processing/10 px-1.5 py-0.5 font-mono text-[10.5px] text-status-processing">
                    retryable
                  </span>
                ) : null}
              </CardTitle>
              <StatusExplanation status={report.status as EngineStatus} />
            </div>
          </div>
          <div className="mt-3 flex flex-wrap gap-x-6 gap-y-1.5">
            <MetaField label="Request ID" value={report.request_id ?? "—"} mono />
            <MetaField label="API status" value={report.api_status || "Not reported by this endpoint"} mono />
            <MetaField label="Engine status" value={report.engine_status || report.status} mono />
          </div>
        </CardHeader>
        <CardContent className="space-y-3 pt-4">
          {report.next_action ? (
            <p className="rounded-lg border border-border/70 bg-muted/30 px-3.5 py-2.5 text-sm text-muted-foreground">
              <span className="font-medium text-foreground">Next action: </span>
              {report.next_action}
            </p>
          ) : null}

          <ValidationJourney result={{ ...report, issues: report.issues, grounding_issues: report.grounding_issues, interpretation: report.interpretation, status: report.status } as never} />

          <Collapsible title="Semantic interpretation" badge={irEntries ? `${irEntries} IR fields` : "not returned"} defaultOpen={irEntries > 0 && irEntries <= 10}>
            {report.interpretation ? (
              <InterpretationPanels interpretation={report.interpretation} />
            ) : (
              <p className="text-sm text-muted-foreground">No interpretation was returned for this outcome.</p>
            )}
          </Collapsible>

          {hasDocument && (
            <Collapsible title="Document & evidence" badge={evidenceCount ? `${evidenceCount} evidence` : "no evidence"} defaultOpen>
              <DocumentProvenancePanel document={report.document!} evidence={report.evidence ?? []} timings={report.timings_ms} />
            </Collapsible>
          )}

          {(report.issues.length > 0 || report.grounding_issues.length > 0) && (
            <Collapsible title="Validation notes" badge={`${report.issues.length + report.grounding_issues.length}`} defaultOpen>
              <ul className="space-y-1.5 text-sm text-muted-foreground">
                {report.issues.map((issue, i) => (
                  <li key={`i-${i}`} className="flex gap-2"><span className="mt-1.5 size-1 shrink-0 rounded-full bg-status-review" />{issue}</li>
                ))}
                {report.grounding_issues.map((issue, i) => (
                  <li key={`g-${i}`} className="flex gap-2"><span className="mt-1.5 size-1 shrink-0 rounded-full bg-status-processing" />{issue}</li>
                ))}
              </ul>
            </Collapsible>
          )}

          <Collapsible title="Deterministic accounting result" badge={report.accounting ? "returned" : "none"}>
            {report.accounting ? (
              <AccountingPanel accounting={report.accounting} />
            ) : (
              <p className="text-sm text-muted-foreground">
                No accounting result — the engine did not reach deterministic execution for this input.
              </p>
            )}
          </Collapsible>
        </CardContent>
      </Card>
    </div>
  );
}
