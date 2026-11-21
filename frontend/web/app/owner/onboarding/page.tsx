"use client";

import { useCallback, useEffect, useState } from "react";
import { Card, CardContent } from "@/components/ui/card";
import { cn } from "@/lib/utils";

/**
 * Private owner onboarding dashboard (server deployment only).
 *
 * Every request goes to /owner/api/*, a Node route handler that holds the
 * owner credential and the management signing secret server-side. Nothing
 * sensitive is ever written to localStorage/sessionStorage or placed in a URL.
 * The browser's own HTTP Basic prompt is the only place the owner credential
 * exists on the client, and the browser manages it — not this code.
 *
 * The static Cloudflare Pages build excludes that route handler, so this page
 * requires the server deployment (`npm run build` / `next start`) or the
 * operator ingress. It says so instead of failing silently.
 */

type Application = {
  application_id: string;
  status: string;
  status_reason: string | null;
  email: string;
  name: string;
  company: string;
  expected_usage: string;
  tenant_id: string | null;
  monthly_limit: number | null;
  email_verified: boolean;
  created_at: string | null;
  last_error?: string | null;
  provisioning_attempts?: number;
};

type Overview = {
  applications_total: number;
  by_status: Record<string, number>;
  pending_verification: number;
  review_required: number;
  suspended: number;
  active_developers: number;
  active_api_keys: number;
  provisioning_failures: number;
  usage: { current_month_usage: number; monthly_limit_total: number };
  notifications: { available: boolean; counts: Record<string, number> };
  email_provider: { provider: string; configured: boolean };
  key_delivery_configured: boolean;
};

type Detail = Application & {
  building: string;
  profile_url: string | null;
  verifications: { verification_id: string; created_at: string | null; consumed_at: string | null }[];
  key_delivery: {
    key_id: string;
    key_prefix: string;
    revealed: boolean;
    voided: boolean;
    derivable: boolean;
  }[];
  notifications: {
    notification_id: string;
    audience: string;
    kind: string;
    status: string;
    last_error: string | null;
    created_at: string | null;
  }[];
  audit: { action: string; actor: string; detail: string | null; created_at: string | null }[];
  quota: { monthly_limit: number; current_month_usage: number; usage_month: string; is_active: boolean }[];
};

const STATUS_FILTERS = [
  "ALL",
  "PENDING_VERIFICATION",
  "VERIFIED",
  "ACTIVE",
  "REVIEW_REQUIRED",
  "SUSPENDED",
  "REJECTED",
  "EXPIRED",
] as const;

const PAGE_SIZE = 10;

async function ownerApi<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/owner/api/${path}`, {
    ...init,
    cache: "no-store",
    headers: { "content-type": "application/json", ...(init?.headers || {}) },
  });
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    const code = payload?.error?.code;
    if (code === "OWNER_UNAUTHENTICATED") {
      throw new Error("Owner authentication failed. Reload the page to be prompted again.");
    }
    if (code === "OWNER_DASHBOARD_NOT_CONFIGURED") {
      throw new Error(payload.error.message);
    }
    throw new Error(payload?.error?.message || `Request failed (HTTP ${response.status}).`);
  }
  return payload as T;
}

function Stat({ label, value, hint }: { label: string; value: number | string; hint?: string }) {
  return (
    <div className="rounded-lg border border-border/70 bg-muted/30 px-3 py-2.5">
      <p className="text-[11px] uppercase tracking-wider text-muted-foreground">{label}</p>
      <p className="mt-1 text-xl font-semibold tabular-nums">{value}</p>
      {hint ? <p className="mt-0.5 text-[11px] text-muted-foreground">{hint}</p> : null}
    </div>
  );
}

function StatusBadge({ status }: { status: string }) {
  const tone =
    status === "ACTIVE"
      ? "border-accent/40 bg-accent-soft text-accent"
      : status === "REVIEW_REQUIRED" || status === "SUSPENDED"
        ? "border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-400"
        : status === "REJECTED" || status === "EXPIRED"
          ? "border-border bg-muted text-muted-foreground"
          : "border-border bg-background text-muted-foreground";
  return (
    <span className={cn("rounded-full border px-2 py-0.5 font-mono text-[10px] font-semibold tracking-wide", tone)}>
      {status}
    </span>
  );
}

export default function OwnerOnboardingPage() {
  const [overview, setOverview] = useState<Overview | null>(null);
  const [applications, setApplications] = useState<Application[]>([]);
  const [total, setTotal] = useState(0);
  const [filter, setFilter] = useState<(typeof STATUS_FILTERS)[number]>("ALL");
  const [offset, setOffset] = useState(0);
  const [detail, setDetail] = useState<Detail | null>(null);
  const [quota, setQuota] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setError("");
    try {
      const query = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(offset) });
      if (filter !== "ALL") query.set("status", filter);
      const [overviewPayload, listPayload] = await Promise.all([
        ownerApi<Overview>("overview"),
        ownerApi<{ total: number; applications: Application[] }>(`applications?${query.toString()}`),
      ]);
      setOverview(overviewPayload);
      setApplications(listPayload.applications);
      setTotal(listPayload.total);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Dashboard unavailable.");
    }
  }, [filter, offset]);

  useEffect(() => {
    void load();
  }, [load]);

  async function openDetail(applicationId: string) {
    setError("");
    setNotice("");
    try {
      const payload = await ownerApi<Detail>(`applications/${applicationId}`);
      setDetail(payload);
      setQuota(String(payload.monthly_limit ?? payload.quota[0]?.monthly_limit ?? ""));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not load the application.");
    }
  }

  async function act(path: string, body?: Record<string, unknown>, success?: string) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await ownerApi(path, {
        method: "POST",
        body: JSON.stringify(body ?? {}),
      });
      setNotice(success || "Action applied.");
      await load();
      if (detail) await openDetail(detail.application_id);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Action failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-10 sm:px-6">
      <div className="mb-6 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Developer onboarding</h1>
          <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
            Private owner view. Automatic provisioning stays the normal path; intervene only for
            review-required requests, failures, suspension and quota changes. Every action is audited.
          </p>
        </div>
        <button
          type="button"
          onClick={() => void load()}
          className="rounded-lg border border-border px-3 py-1.5 text-sm hover:bg-muted"
        >
          Refresh
        </button>
      </div>

      {error ? (
        <p className="mb-4 rounded-md border border-destructive/40 bg-destructive/5 px-3 py-2 text-sm text-destructive" role="alert">
          {error}
        </p>
      ) : null}
      {notice ? (
        <p className="mb-4 rounded-md border border-accent/40 bg-accent-soft/40 px-3 py-2 text-sm" role="status">
          {notice}
        </p>
      ) : null}

      {overview ? (
        <Card className="mb-5">
          <CardContent className="pt-6">
            <div className="grid gap-2.5 sm:grid-cols-2 lg:grid-cols-4">
              <Stat label="Applications" value={overview.applications_total} />
              <Stat label="Pending verification" value={overview.pending_verification} />
              <Stat label="Active developers" value={overview.active_developers} />
              <Stat label="Active API keys" value={overview.active_api_keys} />
              <Stat label="Review required" value={overview.review_required} />
              <Stat label="Suspended" value={overview.suspended} />
              <Stat label="Provisioning failures" value={overview.provisioning_failures} />
              <Stat
                label="Usage this month"
                value={`${overview.usage.current_month_usage}/${overview.usage.monthly_limit_total}`}
                hint="admitted requests"
              />
            </div>
            <div className="mt-3 flex flex-wrap gap-2 text-[11px] text-muted-foreground">
              <span className="rounded border border-border px-2 py-1">
                Email provider: {overview.email_provider.provider}
                {overview.email_provider.configured ? " (configured)" : " (not configured)"}
              </span>
              <span className="rounded border border-border px-2 py-1">
                Key delivery: {overview.key_delivery_configured ? "configured" : "MISSING"}
              </span>
              {Object.entries(overview.notifications.counts).map(([status, count]) => (
                <span key={status} className="rounded border border-border px-2 py-1">
                  {status}: {count}
                </span>
              ))}
            </div>
          </CardContent>
        </Card>
      ) : null}

      <Card>
        <CardContent className="pt-6">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h2 className="text-sm font-semibold">Applications</h2>
            <div className="flex flex-wrap gap-1.5">
              {STATUS_FILTERS.map((value) => (
                <button
                  key={value}
                  type="button"
                  onClick={() => {
                    setFilter(value);
                    setOffset(0);
                  }}
                  className={cn(
                    "rounded-full border px-2.5 py-1 font-mono text-[10px] tracking-wide",
                    filter === value
                      ? "border-accent/50 bg-accent-soft font-semibold text-accent"
                      : "border-border text-muted-foreground hover:text-foreground",
                  )}
                >
                  {value}
                </button>
              ))}
            </div>
          </div>

          <ul className="mt-4 space-y-2">
            {applications.length === 0 ? (
              <li className="rounded-lg border border-dashed border-border px-3 py-6 text-center text-sm text-muted-foreground">
                No applications match this filter.
              </li>
            ) : null}
            {applications.map((application) => (
              <li
                key={application.application_id}
                className="rounded-lg border border-border/70 px-3 py-2.5"
              >
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium">
                      {application.company}{" "}
                      <span className="text-muted-foreground">— {application.name}</span>
                    </p>
                    <p className="truncate font-mono text-[11px] text-muted-foreground">
                      {application.email}
                    </p>
                  </div>
                  <div className="flex items-center gap-2">
                    <StatusBadge status={application.status} />
                    <button
                      type="button"
                      onClick={() => void openDetail(application.application_id)}
                      className="rounded-lg border border-border px-2.5 py-1 text-xs hover:bg-muted"
                    >
                      Open
                    </button>
                  </div>
                </div>
                {application.last_error ? (
                  <p className="mt-1 font-mono text-[11px] text-amber-600 dark:text-amber-400">
                    last_error: {application.last_error}
                  </p>
                ) : null}
              </li>
            ))}
          </ul>

          <div className="mt-4 flex items-center justify-between text-xs text-muted-foreground">
            <span>
              {total === 0 ? 0 : offset + 1}–{Math.min(offset + PAGE_SIZE, total)} of {total}
            </span>
            <div className="flex gap-2">
              <button
                type="button"
                disabled={offset === 0}
                onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
                className="rounded-lg border border-border px-2.5 py-1 disabled:opacity-40"
              >
                Previous
              </button>
              <button
                type="button"
                disabled={offset + PAGE_SIZE >= total}
                onClick={() => setOffset(offset + PAGE_SIZE)}
                className="rounded-lg border border-border px-2.5 py-1 disabled:opacity-40"
              >
                Next
              </button>
            </div>
          </div>
        </CardContent>
      </Card>

      {detail ? (
        <Card className="mt-5">
          <CardContent className="pt-6">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <h2 className="text-sm font-semibold">{detail.company}</h2>
                <p className="font-mono text-[11px] text-muted-foreground">{detail.email}</p>
                <p className="mt-1 text-xs text-muted-foreground">
                  {detail.expected_usage} · tenant {detail.tenant_id ?? "—"} · attempts{" "}
                  {detail.provisioning_attempts ?? 0}
                </p>
              </div>
              <div className="flex items-center gap-2">
                <StatusBadge status={detail.status} />
                <button
                  type="button"
                  onClick={() => setDetail(null)}
                  className="rounded-lg border border-border px-2.5 py-1 text-xs hover:bg-muted"
                >
                  Close
                </button>
              </div>
            </div>

            <p className="mt-3 whitespace-pre-wrap rounded-lg border border-border/70 bg-muted/30 px-3 py-2 text-sm text-muted-foreground">
              {detail.building}
            </p>
            {detail.profile_url ? (
              <p className="mt-2 break-all font-mono text-[11px] text-muted-foreground">{detail.profile_url}</p>
            ) : null}

            <div className="mt-4 flex flex-wrap gap-2">
              <button
                type="button"
                disabled={busy}
                onClick={() =>
                  void act(
                    `applications/${detail.application_id}/advance`,
                    {},
                    "Eligibility re-evaluated.",
                  )
                }
                className="rounded-lg border border-border px-3 py-1.5 text-sm hover:bg-muted disabled:opacity-50"
              >
                Re-run eligibility
              </button>
              <button
                type="button"
                disabled={busy}
                onClick={() =>
                  void act(
                    `applications/${detail.application_id}/provision`,
                    {},
                    "Provisioning attempted.",
                  )
                }
                className="rounded-lg border border-border px-3 py-1.5 text-sm hover:bg-muted disabled:opacity-50"
              >
                Retry provisioning
              </button>
              <button
                type="button"
                disabled={busy}
                onClick={() =>
                  void act(
                    `applications/${detail.application_id}/suspend`,
                    { reason: "OWNER_SUSPENDED" },
                    "Access suspended; keys stopped authenticating.",
                  )
                }
                className="rounded-lg border border-amber-500/50 px-3 py-1.5 text-sm text-amber-700 hover:bg-amber-500/10 disabled:opacity-50 dark:text-amber-400"
              >
                Suspend
              </button>
              <button
                type="button"
                disabled={busy}
                onClick={() =>
                  void act(
                    `applications/${detail.application_id}/restore`,
                    {},
                    "Access restored.",
                  )
                }
                className="rounded-lg border border-border px-3 py-1.5 text-sm hover:bg-muted disabled:opacity-50"
              >
                Restore
              </button>
              <button
                type="button"
                disabled={busy}
                onClick={() =>
                  void act(
                    `applications/${detail.application_id}/reject`,
                    { reason: "OWNER_REJECTED" },
                    "Application rejected.",
                  )
                }
                className="rounded-lg border border-border px-3 py-1.5 text-sm hover:bg-muted disabled:opacity-50"
              >
                Reject
              </button>
            </div>

            {detail.tenant_id ? (
              <div className="mt-4 flex flex-wrap items-end gap-2 rounded-lg border border-border/70 px-3 py-2.5">
                <label htmlFor="quota" className="text-xs text-muted-foreground">
                  Monthly quota (admitted requests)
                </label>
                <input
                  id="quota"
                  inputMode="numeric"
                  value={quota}
                  onChange={(event) => setQuota(event.target.value.replace(/[^0-9]/g, ""))}
                  className="w-28 rounded-lg border border-border bg-background px-2 py-1 font-mono text-sm"
                />
                <button
                  type="button"
                  disabled={busy || quota === ""}
                  onClick={() =>
                    void act(
                      `tenants/${detail.tenant_id}/quota`,
                      { monthly_limit: Number(quota) },
                      "Quota updated.",
                    )
                  }
                  className="rounded-lg border border-border px-3 py-1.5 text-sm hover:bg-muted disabled:opacity-50"
                >
                  Save quota
                </button>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() =>
                    void act(
                      `tenants/${detail.tenant_id}/keys/rotate`,
                      {},
                      "Key rotated; a one-time link was queued to the developer.",
                    )
                  }
                  className="rounded-lg border border-border px-3 py-1.5 text-sm hover:bg-muted disabled:opacity-50"
                >
                  Rotate key
                </button>
              </div>
            ) : null}

            <div className="mt-5 grid gap-4 lg:grid-cols-2">
              <div>
                <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                  Key delivery
                </h3>
                <ul className="mt-2 space-y-1.5 text-xs text-muted-foreground">
                  {detail.key_delivery.length === 0 ? <li>None issued.</li> : null}
                  {detail.key_delivery.map((delivery) => (
                    <li key={delivery.key_id} className="rounded border border-border/70 px-2 py-1.5">
                      <span className="font-mono">{delivery.key_prefix}…</span> ·{" "}
                      {delivery.revealed ? "retrieved once" : delivery.voided ? "voided" : "awaiting retrieval"} ·{" "}
                      {delivery.derivable ? "link live" : "key no longer derivable"}
                    </li>
                  ))}
                </ul>

                <h3 className="mt-4 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                  Notifications
                </h3>
                <ul className="mt-2 space-y-1.5 text-xs text-muted-foreground">
                  {detail.notifications.slice(0, 8).map((notification) => (
                    <li key={notification.notification_id} className="rounded border border-border/70 px-2 py-1.5">
                      <span className="font-mono">{notification.kind}</span> · {notification.audience} ·{" "}
                      {notification.status}
                      {notification.last_error ? ` (${notification.last_error})` : ""}
                    </li>
                  ))}
                </ul>
              </div>

              <div>
                <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                  Audit trail
                </h3>
                <ul className="mt-2 space-y-1.5 text-xs text-muted-foreground">
                  {detail.audit.length === 0 ? <li>No entries.</li> : null}
                  {detail.audit.slice(0, 12).map((entry, index) => (
                    <li key={`${entry.action}-${index}`} className="rounded border border-border/70 px-2 py-1.5">
                      <span className="font-mono">{entry.action}</span> · {entry.actor}
                      {entry.detail ? ` · ${entry.detail}` : ""}
                    </li>
                  ))}
                </ul>
              </div>
            </div>
          </CardContent>
        </Card>
      ) : null}
    </main>
  );
}
