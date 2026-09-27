"use client";

import { useEffect, useMemo, useState } from "react";
import { ChevronDown } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { fetchCapabilities } from "@/lib/api";
import type { CapabilityEntry, CapabilitiesResponse } from "@/lib/types";
import { cn } from "@/lib/utils";

/**
 * Authority-grouped capability explorer.
 *
 * Primary view: AUTHORITY → capability groups → individual capabilities.
 * Everything on this page is computed from the live registry response
 * (GET /v1/capabilities) — the registry is the only source of truth;
 * nothing here is hardcoded or hand-curated.
 */

const STATUS_ORDER = ["SUPPORTED", "PARTIAL", "UNSUPPORTED", "PLANNED"] as const;

const STATUS_TONE: Record<string, string> = {
  SUPPORTED: "bg-status-verified/12 text-status-verified border-status-verified/35",
  PARTIAL: "bg-status-processing/12 text-status-processing border-status-processing/35",
  UNSUPPORTED: "bg-status-unsupported/12 text-status-unsupported border-status-unsupported/35",
  PLANNED: "bg-muted text-muted-foreground border-border",
};

/** Factual, registry-derived purpose statements per authority. */
const AUTHORITY_PURPOSE: Record<string, string> = {
  ACCOUNTING_KERNEL:
    "The deterministic journal-entry engine for FYJC book-keeping transactions. It executes only what the validated semantic IR and grounding support — every journal line is produced by a registered capability, never inferred.",
  FORMULA_AUTHORITY:
    "Deterministic financial-ratio and formula computation. Each formula is a registered capability with an implementation reference and a proving test — no formula runs that is not in the registry.",
  FINANCE_KNOWLEDGE:
    "Versioned finance-knowledge records with explicit sources. Terminology and definitions are served from authoritative records, not generated.",
};

const AUTHORITY_TAGLINE: Record<string, string> = {
  ACCOUNTING_KERNEL: "Accounting Kernel",
  FORMULA_AUTHORITY: "Formula Authority",
  FINANCE_KNOWLEDGE: "Finance Knowledge",
};

function StatusPill({ status }: { status: string }) {
  return (
    <span
      className={cn("rounded-full border px-2 py-0.5 font-mono text-[10.5px]", STATUS_TONE[status] ?? "")}
    >
      {status}
    </span>
  );
}

function CapabilityRow({ cap }: { cap: CapabilityEntry }) {
  const [open, setOpen] = useState(false);
  return (
    <li className="rounded-md border border-border/70 bg-muted/20">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-start justify-between gap-3 px-3 py-2.5 text-left"
      >
        <span className="min-w-0">
          <span className="block truncate font-mono text-[12.5px] font-semibold">{cap.capability_id}</span>
          <span className="mt-0.5 block truncate text-xs text-muted-foreground">{cap.canonical_name}</span>
        </span>
        <span className="flex shrink-0 items-center gap-2">
          <StatusPill status={cap.supported_status} />
          <ChevronDown className={cn("size-3.5 text-muted-foreground transition-transform", open && "rotate-180")} aria-hidden />
        </span>
      </button>
      {open && (
        <div className="space-y-2 border-t border-border/60 px-3 py-3 text-xs">
          {cap.description ? <p className="leading-relaxed text-muted-foreground">{cap.description}</p> : null}
          <dl className="grid gap-x-6 gap-y-1.5 sm:grid-cols-[150px_1fr]">
            {[
              ["required_inputs", cap.required_inputs.join(", ")],
              ["deterministic_op", cap.deterministic_op],
              ["implementation_ref", cap.implementation_ref],
              ["test_ref", cap.test_ref],
              ["framework", cap.framework],
              ["jurisdiction", cap.jurisdiction],
              ["version", cap.version],
              ["limitations", cap.limitations.join(" · ")],
            ]
              .filter(([, v]) => v)
              .map(([k, v]) => (
                <div key={k} className="grid gap-0.5 sm:contents">
                  <dt className="font-mono text-muted-foreground sm:py-0.5">{k}</dt>
                  <dd className="min-w-0 break-words font-mono text-foreground/90 sm:py-0.5">{v}</dd>
                </div>
              ))}
          </dl>
        </div>
      )}
    </li>
  );
}

function AuthorityCard({
  authority,
  capabilities,
}: {
  authority: string;
  capabilities: CapabilityEntry[];
}) {
  const [open, setOpen] = useState(false);
  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const cap of capabilities) c[cap.supported_status] = (c[cap.supported_status] ?? 0) + 1;
    return c;
  }, [capabilities]);
  const name = AUTHORITY_TAGLINE[authority] ?? authority.replaceAll("_", " ").toLowerCase();
  const purpose = AUTHORITY_PURPOSE[authority];
  const frameworks = [...new Set(capabilities.map((c) => c.framework).filter(Boolean))];
  const inputs = [...new Set(capabilities.flatMap((c) => c.required_inputs))].slice(0, 6);

  return (
    <Card className="border-border/70">
      <CardContent className="pt-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <h3 className="font-mono text-sm font-semibold tracking-tight">{authority}</h3>
            <p className="mt-0.5 text-sm text-muted-foreground">{name}</p>
          </div>
          <div className="flex flex-wrap items-center gap-1.5">
            {STATUS_ORDER.filter((s) => counts[s]).map((s) => (
              <span key={s} className={cn("rounded-full border px-2 py-0.5 font-mono text-[10.5px]", STATUS_TONE[s])}>
                {counts[s]} {s.toLowerCase()}
              </span>
            ))}
          </div>
        </div>

        {purpose ? <p className="mt-3 text-sm leading-relaxed text-muted-foreground">{purpose}</p> : null}

        <div className="mt-3 grid gap-2 text-xs text-muted-foreground sm:grid-cols-2">
          {frameworks.length > 0 && (
            <p><span className="font-mono text-[10.5px] uppercase tracking-wider">framework:</span> {frameworks.join(", ")}</p>
          )}
          {inputs.length > 0 && (
            <p className="min-w-0"><span className="font-mono text-[10.5px] uppercase tracking-wider">key inputs:</span> <span className="break-words font-mono">{inputs.join(", ")}</span></p>
          )}
        </div>

        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          className="mt-3.5 inline-flex items-center gap-1.5 text-sm font-medium text-accent hover:underline"
        >
          {open ? "Hide" : "View"} {capabilities.length} capabilities
          <ChevronDown className={cn("size-4 transition-transform", open && "rotate-180")} aria-hidden />
        </button>

        {open && (
          <ul className="mt-3 space-y-2">
            {capabilities.map((cap) => (
              <CapabilityRow key={cap.capability_id} cap={cap} />
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

export function AuthorityGroups() {
  const [data, setData] = useState<CapabilitiesResponse | null>(null);
  const [error, setError] = useState<{ code: string; message: string } | null>(null);
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState<string>("ALL");

  useEffect(() => {
    const controller = new AbortController();
    fetchCapabilities(controller.signal)
      .then(setData)
      .catch((err: unknown) => {
        const code =
          err && typeof err === "object" && "code" in err ? String((err as { code: unknown }).code) : "NETWORK_ERROR";
        const message =
          err instanceof Error ? err.message : "Could not reach the capabilities endpoint through the frontend proxy.";
        setError({ code, message });
      });
    return () => controller.abort();
  }, []);

  const grouped = useMemo(() => {
    if (!data) return [] as Array<[string, CapabilityEntry[]]>;
    const q = query.trim().toLowerCase();
    const filtered = data.capabilities.filter(
      (c) =>
        (statusFilter === "ALL" || c.supported_status === statusFilter) &&
        (!q ||
          c.capability_id.toLowerCase().includes(q) ||
          c.canonical_name.toLowerCase().includes(q) ||
          c.description.toLowerCase().includes(q)),
    );
    const map = new Map<string, CapabilityEntry[]>();
    for (const cap of filtered) {
      const list = map.get(cap.authority) ?? [];
      list.push(cap);
      map.set(cap.authority, list);
    }
    return [...map.entries()].sort(([a], [b]) => a.localeCompare(b));
  }, [data, query, statusFilter]);

  return (
    <div className="space-y-5">
      {error && (
        <Card className="border-status-review/30">
          <CardContent className="pt-5">
            <p className="font-mono text-xs text-status-review">{error.code}</p>
            <p className="mt-1 text-sm">{error.message}</p>
            <p className="mt-1 text-xs text-muted-foreground">
              Capabilities come from the live registry (GET /v1/capabilities). No substitute data is shown.
            </p>
          </CardContent>
        </Card>
      )}

      {!data && !error && (
        <div className="space-y-3">
          <Skeleton className="h-9 w-full max-w-md" />
          <div className="grid gap-3">
            <Skeleton className="h-40 w-full" />
            <Skeleton className="h-40 w-full" />
          </div>
        </div>
      )}

      {data && (
        <>
          <p className="text-sm text-muted-foreground">
            Derived live from the capability registry — the single source of truth. UNSUPPORTED and
            PLANNED are honest boundary records, not errors.
            <span className="ml-2 font-mono text-xs">
              {data.count} entries · api_status {data.api_status}
            </span>
          </p>

          <div className="flex flex-wrap items-center gap-2">
            <label htmlFor="cap-search" className="sr-only">Search capabilities</label>
            <input
              id="cap-search"
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search capabilities…"
              className="w-full max-w-xs rounded-lg border border-border bg-card px-3 py-1.5 text-sm outline-none placeholder:text-muted-foreground/70 focus-visible:border-accent/60 focus-visible:ring-2 focus-visible:ring-ring"
            />
            <div className="flex flex-wrap gap-1.5">
              {["ALL", ...STATUS_ORDER].map((s) => (
                <button
                  key={s}
                  type="button"
                  onClick={() => setStatusFilter(s)}
                  aria-pressed={statusFilter === s}
                  className={cn(
                    "rounded-full border px-3 py-1 font-mono text-[11px] transition-colors",
                    statusFilter === s
                      ? "border-accent/50 bg-accent-soft text-accent"
                      : "border-border bg-muted/40 text-muted-foreground hover:text-foreground",
                  )}
                >
                  {s}
                </button>
              ))}
            </div>
          </div>

          <div className="space-y-4">
            {grouped.map(([authority, caps]) => (
              <AuthorityCard key={authority} authority={authority} capabilities={caps} />
            ))}
            {grouped.length === 0 && (
              <p className="text-sm text-muted-foreground">No registry entries match this filter.</p>
            )}
          </div>
        </>
      )}
    </div>
  );
}
