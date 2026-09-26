"use client";

import { useEffect, useMemo, useState } from "react";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { fetchCapabilities } from "@/lib/api";
import type { CapabilityEntry, CapabilitiesResponse } from "@/lib/types";
import { cn } from "@/lib/utils";

const STATUSES = ["ALL", "SUPPORTED", "PARTIAL", "UNSUPPORTED", "PLANNED"] as const;

const STATUS_TONE: Record<string, string> = {
  SUPPORTED: "bg-status-verified/12 text-status-verified border-status-verified/35",
  PARTIAL: "bg-status-processing/12 text-status-processing border-status-processing/35",
  UNSUPPORTED: "bg-status-unsupported/12 text-status-unsupported border-status-unsupported/35",
  PLANNED: "bg-muted text-muted-foreground border-border",
};

const STATUS_HINT: Record<string, string> = {
  SUPPORTED: "implemented + proven by a deterministic gate",
  PARTIAL: "implemented with a documented boundary",
  UNSUPPORTED: "provably refused — refusal evidence recorded",
  PLANNED: "declared, not implemented — routing fails closed",
};

function CapabilityCard({ cap }: { cap: CapabilityEntry }) {
  const [open, setOpen] = useState(false);
  return (
    <Card className="border-border/70 transition-colors hover:border-accent/30">
      <CardContent className="pt-5">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <code className="text-sm font-semibold">{cap.capability_id}</code>
          <span
            className={cn(
              "rounded-full border px-2.5 py-0.5 font-mono text-[11px]",
              STATUS_TONE[cap.supported_status] ?? "",
            )}
            title={STATUS_HINT[cap.supported_status]}
          >
            {cap.supported_status}
          </span>
        </div>
        <p className="mt-1 text-sm text-muted-foreground">{cap.canonical_name}</p>
        {cap.description && (
          <p className="mt-2 text-sm leading-relaxed">{cap.description}</p>
        )}
        {cap.required_inputs.length > 0 && (
          <p className="mt-2 text-xs text-muted-foreground">
            <span className="font-mono">required_inputs:</span>{" "}
            <span className="font-mono text-foreground/80">{cap.required_inputs.join(", ")}</span>
          </p>
        )}
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          className="mt-2.5 text-xs font-medium text-accent hover:underline"
        >
          {open ? "Hide registry metadata" : "Registry metadata"}
        </button>
        {open && (
          <dl className="mt-3 space-y-1.5 rounded-md border border-border/70 bg-muted/30 p-3 text-xs">
            {[
              ["authority", cap.authority],
              ["deterministic_op", cap.deterministic_op],
              ["implementation_ref", cap.implementation_ref],
              ["test_ref", cap.test_ref],
              ["source_ref", cap.source_ref],
              ["framework", cap.framework],
              ["jurisdiction", cap.jurisdiction],
              ["version", cap.version],
              ["limitations", cap.limitations.join(" · ")],
            ]
              .filter(([, v]) => v)
              .map(([k, v]) => (
                <div key={k} className="grid gap-0.5 sm:grid-cols-[170px_1fr]">
                  <dt className="font-mono text-muted-foreground">{k}</dt>
                  <dd className="min-w-0 break-words font-mono text-foreground/90">{v}</dd>
                </div>
              ))}
          </dl>
        )}
      </CardContent>
    </Card>
  );
}

export function CapabilityExplorer() {
  const [data, setData] = useState<CapabilitiesResponse | null>(null);
  const [error, setError] = useState<{ code: string; message: string } | null>(null);
  const [statusFilter, setStatusFilter] = useState<string>("ALL");
  const [authorityFilter, setAuthorityFilter] = useState<string>("ALL");

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

  const filtered = useMemo(() => {
    if (!data) return [];
    return data.capabilities.filter(
      (c) =>
        (statusFilter === "ALL" || c.supported_status === statusFilter) &&
        (authorityFilter === "ALL" || c.authority === authorityFilter),
    );
  }, [data, statusFilter, authorityFilter]);

  const authorities = useMemo(
    () => (data ? Object.keys(data.registry_summary).sort() : []),
    [data],
  );

  return (
    <div className="space-y-5">
      {error && (
        <Card className="border-status-review/30">
          <CardContent className="pt-5">
            <p className="font-mono text-xs text-status-review">{error.code}</p>
            <p className="mt-1 text-sm">{error.message}</p>
            <p className="mt-1 text-xs text-muted-foreground">
              Capabilities come from the live registry (GET /v1/capabilities). No substitute data is
              shown.
            </p>
          </CardContent>
        </Card>
      )}

      {!data && !error && (
        <div className="space-y-3">
          <Skeleton className="h-9 w-full max-w-md" />
          <div className="grid gap-3 md:grid-cols-2">
            <Skeleton className="h-36 w-full" />
            <Skeleton className="h-36 w-full" />
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
            {STATUSES.map((s) => (
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
            <span className="mx-1 hidden w-px self-stretch bg-border sm:block" aria-hidden />
            {["ALL", ...authorities].map((a) => (
              <button
                key={a}
                type="button"
                onClick={() => setAuthorityFilter(a)}
                aria-pressed={authorityFilter === a}
                className={cn(
                  "rounded-full border px-3 py-1 font-mono text-[11px] transition-colors",
                  authorityFilter === a
                    ? "border-accent/50 bg-accent-soft text-accent"
                    : "border-border bg-muted/40 text-muted-foreground hover:text-foreground",
                )}
              >
                {a}
              </button>
            ))}
          </div>

          <p className="text-xs text-muted-foreground">
            {filtered.length} of {data.count} registry entries
          </p>

          <div className="grid gap-3 lg:grid-cols-2">
            {filtered.map((cap) => (
              <CapabilityCard key={cap.capability_id} cap={cap} />
            ))}
          </div>
        </>
      )}
    </div>
  );
}
