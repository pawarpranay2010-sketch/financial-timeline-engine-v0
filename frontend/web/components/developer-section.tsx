"use client";

import { useState } from "react";
import Link from "next/link";
import { Card, CardContent } from "@/components/ui/card";
import { cn } from "@/lib/utils";

/**
 * Developer section switcher — four sections on one route (client-side
 * tabs; the page remains statically exportable). Honesty rules:
 *   - API key self-service: the backend exposes key VERIFICATION for its
 *     own gate but no issuance/rotation/revocation API, so this section
 *     shows the planned architecture and says so. No fake keys, ever.
 *   - Requests: no request-history endpoint exists, so no history is shown.
 *     The planned table columns are documentation, not data.
 */

const SECTIONS = ["Overview", "API keys", "Requests", "Documentation"] as const;
type Section = (typeof SECTIONS)[number];

const REQUEST_EXAMPLE = `curl -X POST \\
  "$PLATRIXA_HOST/api/v1/kernel/process" \\
  -H "Content-Type: application/json" \\
  -d '{"raw_input": "Purchased furniture for cash Rs. 15,000"}'`;

const RESPONSE_EXAMPLE = `{
  "request_id": "kernel:3512819723",
  "status": "REVIEW_REQUIRED",
  "status_label": "Review required",
  "success": false,
  "next_action": "…",
  "issues": ["…"],
  "grounding_issues": [],
  "interpretation": { "…18-field Candidate Semantic IR…": "…" },
  "accounting": null,
  "persisted": false
}`;

const SIX_STATES: Array<[string, string]> = [
  ["PROCESSING", "admitted, authoritative result not yet available — retryable"],
  ["VERIFIED", "deterministic execution passed (engine VERIFIED is the only source)"],
  ["REVIEW_REQUIRED", "valid but flagged for human review — never downgraded to an error"],
  ["UNSUPPORTED", "no deterministic authority accepted the input"],
  ["INVALID_INPUT", "malformed transport request or input rejected by the public contract"],
  ["FAILED", "deterministic rejection with the reason recorded, or unexpected server failure"],
];

const ERROR_EXAMPLES: Array<[string, string]> = [
  ["INPUT_INVALID", "422 — empty or oversized input"],
  ["REQUEST_MALFORMED", "400 — body could not be parsed"],
  ["UNAUTHORIZED", "401 — missing/invalid key (gated mode)"],
  ["QUOTA_EXHAUSTED", "429 — monthly quota exhausted"],
  ["METERING_UNAVAILABLE", "503 — metering store down; request not admitted (fail-closed)"],
  ["MODEL_UNAVAILABLE", "503 — provider temporarily unavailable (retryable)"],
];

function CodeBlock({ children }: { children: string }) {
  return (
    <pre className="overflow-x-auto rounded-lg border border-border bg-muted/40 p-4 font-mono text-[12.5px] leading-relaxed">
      {children}
    </pre>
  );
}

function ComingSoon({ children }: { children: React.ReactNode }) {
  return (
    <span className="rounded-full border border-border bg-muted px-2 py-0.5 font-mono text-[10px] font-semibold tracking-wide text-muted-foreground">
      {children}
    </span>
  );
}

function OverviewSection() {
  return (
    <div className="space-y-5">
      <Card>
        <CardContent className="pt-6">
          <h2 className="text-sm font-semibold">The developer API</h2>
          <p className="mt-1.5 text-sm leading-relaxed text-muted-foreground">
            The deterministic runtime behind this UI is exposed as a versioned, machine-readable API.
            The engine&apos;s terminal state is the only status authority — your integration should
            act on it, never reinterpret it.
          </p>
          <div className="mt-4 grid gap-3 sm:grid-cols-2">
            {[
              ["POST /api/v1/kernel/process", "Process one financial narration (validation console path)"],
              ["POST /v1/process", "Versioned developer path for the same engine"],
              ["POST /v1/process/document", "Text, PDF, or image documents — returns pages, evidence, timings"],
              ["GET /v1/capabilities", "Read-only projection of the live capability registry"],
              ["GET /v1/health · /v1/ready", "Liveness and readiness probes"],
            ].map(([endpoint, purpose]) => (
              <div key={endpoint} className="rounded-lg border border-border/70 bg-muted/30 px-3 py-2.5">
                <code className="font-mono text-xs font-semibold">{endpoint}</code>
                <p className="mt-1 text-xs text-muted-foreground">{purpose}</p>
              </div>
            ))}
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardContent className="pt-6">
          <h2 className="text-sm font-semibold">Quick start</h2>
          <p className="mt-1 mb-3 text-sm text-muted-foreground">Process one financial input:</p>
          <CodeBlock>{REQUEST_EXAMPLE}</CodeBlock>
          <p className="mt-3 mb-2 text-sm font-medium">Response</p>
          <CodeBlock>{RESPONSE_EXAMPLE}</CodeBlock>
        </CardContent>
      </Card>
    </div>
  );
}

function ApiKeysSection() {
  return (
    <div className="space-y-5">
      <Card className="border-dashed">
        <CardContent className="pt-6">
          <div className="flex flex-wrap items-center gap-2.5">
            <h2 className="text-sm font-semibold">API key self-service</h2>
            <ComingSoon>NOT YET ENABLED</ComingSoon>
          </div>
          <p className="mt-2.5 text-sm leading-relaxed text-muted-foreground">
            The backend currently supports key <em>verification</em> for its own metering gate
            (requests present <code className="font-mono text-xs">X-Platrixa-API-Key</code>), but it
            does not yet expose key generation, rotation, or revocation to end users. Platrixa does
            not fake this: no keys can be created from this page because no issuance API exists.
          </p>
          <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
            Keys are provisioned by the operator in the backend configuration. Contact the operator
            if you need a tenant key for the hosted gate.
          </p>
        </CardContent>
      </Card>

      <Card>
        <CardContent className="pt-6">
          <h2 className="text-sm font-semibold">Planned design</h2>
          <p className="mt-1.5 text-xs text-muted-foreground">
            Architecture prepared on this page — it activates only against a real issuance API.
          </p>
          <ol className="mt-3 space-y-2 text-sm text-muted-foreground">
            {[
              "Create key → name + environment, generated server-side",
              "Secret shown exactly once, with copy action — never displayed again",
              "List view: name, masked key, environment, created date, last used, status",
              "Revoke and rotate actions per key",
              "Tenant and quota association, mirroring the backend metering gate",
            ].map((item, i) => (
              <li key={i} className="flex gap-2.5">
                <span className="mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full border border-border font-mono text-[10px] text-muted-foreground">
                  {i + 1}
                </span>
                {item}
              </li>
            ))}
          </ol>
          <p className="mt-3 rounded-md border border-border/70 bg-muted/30 px-3 py-2 text-xs text-muted-foreground">
            Keys will never be exposed through <code className="font-mono">NEXT_PUBLIC_*</code>, client-side
            source, browser storage, or logs — they will be created and stored server-side only.
          </p>
        </CardContent>
      </Card>
    </div>
  );
}

function RequestsSection() {
  return (
    <div className="space-y-5">
      <Card className="border-dashed">
        <CardContent className="pt-6">
          <div className="flex flex-wrap items-center gap-2.5">
            <h2 className="text-sm font-semibold">Request history</h2>
            <ComingSoon>NO HISTORY ENDPOINT YET</ComingSoon>
          </div>
          <p className="mt-2.5 text-sm leading-relaxed text-muted-foreground">
            The backend does not currently expose a request-history API, so this page shows no
            historical requests — none are invented or cached client-side. Every response does carry
            a <code className="font-mono text-xs">request_id</code> you can correlate in your own
            logs.
          </p>
          <div className="mt-4 rounded-lg border border-border/70 bg-muted/30 p-4">
            <p className="font-mono text-[11px] uppercase tracking-wider text-muted-foreground">Planned view</p>
            <div className="mt-2 flex flex-wrap gap-2">
              {["Request ID", "Time", "Status", "Endpoint", "Latency"].map((col) => (
                <span key={col} className="rounded border border-border bg-card px-2 py-1 font-mono text-[11px] text-muted-foreground">{col}</span>
              ))}
            </div>
            <p className="mt-2.5 text-xs text-muted-foreground">
              Clicking a request will open its metadata, reason codes, response, evidence, and errors
              once the API exists.
            </p>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

function DocumentationSection() {
  return (
    <div className="space-y-5">
      <Card>
        <CardContent className="pt-6">
          <h2 className="text-sm font-semibold">Six-state public API status</h2>
          <p className="mt-1 mb-3 text-sm text-muted-foreground">
            The transport layer maps engine outcomes onto a closed six-state vocabulary
            (<code className="font-mono text-xs">api_status</code>) while carrying the engine state
            verbatim. No engine state maps to VERIFIED except VERIFIED itself; unmapped states fail
            closed to FAILED.
          </p>
          <dl className="space-y-2">
            {SIX_STATES.map(([state, meaning]) => (
              <div key={state} className="grid gap-1 rounded-lg border border-border/70 bg-muted/30 px-3 py-2 sm:grid-cols-[160px_1fr] sm:items-baseline">
                <dt className="font-mono text-xs font-semibold">{state}</dt>
                <dd className="text-sm text-muted-foreground">{meaning}</dd>
              </div>
            ))}
          </dl>
        </CardContent>
      </Card>

      <Card>
        <CardContent className="pt-6">
          <h2 className="text-sm font-semibold">Errors are structured, never hidden</h2>
          <p className="mt-1 mb-3 text-sm text-muted-foreground">
            Every error carries a machine-readable code, a human explanation, and (where applicable)
            an <code className="font-mono text-xs">api_status</code> mapping and retryability.
            Correlate requests with the <code className="font-mono text-xs">X-Request-Id</code> header.
          </p>
          <dl className="space-y-2">
            {ERROR_EXAMPLES.map(([code, meaning]) => (
              <div key={code} className="grid gap-1 rounded-lg border border-border/70 px-3 py-2 sm:grid-cols-[220px_1fr] sm:items-baseline">
                <dt className="font-mono text-xs">{code}</dt>
                <dd className="text-sm text-muted-foreground">{meaning}</dd>
              </div>
            ))}
          </dl>
        </CardContent>
      </Card>

      <Card>
        <CardContent className="pt-6">
          <h2 className="text-sm font-semibold">Authentication</h2>
          <p className="mt-1 text-sm leading-relaxed text-muted-foreground">
            This site never holds credentials: when the operator enables the hosted gate, keys are
            injected <em>server-side</em> by the frontend proxy and never reach the browser bundle.
            For your own integration, send your tenant key in the{" "}
            <code className="font-mono text-xs">X-Platrixa-API-Key</code> header when the gate is
            enabled. Full contract: <code className="font-mono text-xs">docs/HOSTED_API.md</code> in the repository.
          </p>
          <p className="mt-3 text-sm">
            <Link href="/capabilities" className="font-medium text-accent hover:underline">
              Browse the capability registry →
            </Link>
          </p>
        </CardContent>
      </Card>
    </div>
  );
}

export function DeveloperSection() {
  const [section, setSection] = useState<Section>("Overview");

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap gap-1.5" role="tablist" aria-label="Developer sections">
        {SECTIONS.map((s) => (
          <button
            key={s}
            type="button"
            role="tab"
            aria-selected={section === s}
            onClick={() => setSection(s)}
            className={cn(
              "rounded-full border px-3.5 py-1.5 text-sm transition-colors",
              section === s
                ? "border-accent/50 bg-accent-soft font-medium text-accent"
                : "border-border bg-muted/40 text-muted-foreground hover:text-foreground",
            )}
          >
            {s}
          </button>
        ))}
      </div>

      {section === "Overview" && <OverviewSection />}
      {section === "API keys" && <ApiKeysSection />}
      {section === "Requests" && <RequestsSection />}
      {section === "Documentation" && <DocumentationSection />}
    </div>
  );
}
