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
 *   - Documentation (Phase 5D/5E): reflects the REAL contract — the
 *     canonical result envelope, evidence model (never fabricated), the
 *     async document workflow, idempotency, and the best-effort webhook
 *     foundation. Every endpoint and status shown exists in the API.
 */

const SECTIONS = ["Overview", "API keys", "Requests", "Documentation"] as const;
type Section = (typeof SECTIONS)[number];

const REQUEST_EXAMPLE = `curl -X POST "$PLATRIXA_HOST/v1/process" \\
  -H "Content-Type: application/json" \\
  -H "X-Platrixa-API-Key: YOUR_API_KEY" \\
  -H "Idempotency-Key: YOUR_IDEMPOTENCY_KEY" \\
  -d '{"raw_input": "Purchased furniture for cash Rs. 15,000"}'`;

const RESPONSE_EXAMPLE = `{
  "api_version": "v1",
  "request_id": "req-123",
  "status": "VERIFIED",            // engine terminal state (verbatim)
  "api_status": "VERIFIED",        // six-state public mapping
  "engine_status": "VERIFIED",
  "success": true,
  "retryable": false,
  "reason_codes": [],
  "interpretation": {
    "transaction_type": "PURCHASE",
    "amounts": [{ "value": "15000", "source": "explicit",
                  "value_origin": "EXTRACTED" }]
  },
  "accounting_result": { "debit_lines":  [{ "account": "Furniture", "amount": 15000 }],
                         "credit_lines": [{ "account": "Cash", "amount": 15000 }] },
  "evidence": [],
  "metadata": { "engine_status": "VERIFIED", "processing_time_ms": 41 }
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
  ["ASYNC_NOT_CONFIGURED", "400 — async documents need the durable store on this deployment"],
  ["IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_REQUEST", "409 — same key, different request body"],
];

const ENDPOINTS: Array<[string, string]> = [
  ["POST /v1/process", "Process one financial narration — the canonical 5D result envelope"],
  ["POST /v1/process/document", "Text, PDF, or image — returns pages, evidence, lineage, timings"],
  ["POST /v1/documents", "Async document submission (202: job_id + result_id; admitted ≠ VERIFIED)"],
  ["GET /v1/jobs/{job_id}", "Poll an async job — completion is NOT VERIFIED; the status tells the truth"],
  ["GET /v1/results/{result_id}", "Fetch the finished result — the SAME 5D envelope as /v1/process"],
  ["POST /v1/webhook-endpoints", "Register signed webhook delivery (best-effort, single-attempt)"],
  ["GET /v1/capabilities", "Read-only projection of the live capability registry"],
  ["GET /v1/health · /v1/ready", "Liveness and readiness probes"],
];

const REASON_CODES: Array<[string, string]> = [
  ["NO_SUPPORTED_CAPABILITY", "no supported capability accepted the input (UNSUPPORTED)"],
  ["SAFETY_BOUNDARY", "rejected at the authority safety boundary (UNSUPPORTED)"],
  ["VALIDATION_REJECTED", "deterministic validation rejected the input (FAILED)"],
  ["GROUNDING_REJECTED", "grounding/evidence gate failed (FAILED)"],
  ["EVIDENCE_RECORDED", "FAILED with reasons recorded in issues / grounding_issues"],
  ["RESULT_PENDING", "outcome unknown — provider unavailable (PROCESSING)"],
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
            act on it, never reinterpret it. Synchronous and asynchronous results converge on the
            same canonical result envelope.
          </p>
          <div className="mt-4 grid gap-3 sm:grid-cols-2">
            {ENDPOINTS.map(([endpoint, purpose]) => (
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
          <p className="mt-3 mb-2 text-sm font-medium">Response — the canonical result envelope</p>
          <CodeBlock>{RESPONSE_EXAMPLE}</CodeBlock>
          <p className="mt-3 text-xs text-muted-foreground">
            <code className="font-mono">status</code> is the engine&apos;s verbatim terminal state;
            <code className="font-mono"> api_status</code> is its six-state public mapping.
            <code className="font-mono"> interpretation.amounts[].value_origin</code> marks
            model-extracted values — the deterministic result lives only in{" "}
            <code className="font-mono">accounting_result</code>, and only VERIFIED carries one.
          </p>
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
            closed to FAILED. VERIFIED means the implemented schema, grounding, capability, and
            deterministic-authority requirements were satisfied — never legal/tax compliance or advice.
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
          <h2 className="text-sm font-semibold">The result contract — one envelope, sync and async</h2>
          <p className="mt-1 mb-3 text-sm leading-relaxed text-muted-foreground">
            Every processing result — <code className="font-mono text-xs">POST /v1/process</code> or{" "}
            <code className="font-mono text-xs">GET /v1/results/&#123;result_id&#125;</code> — is the same
            canonical envelope. It distinguishes <em>extracted</em> from <em>deterministic</em>:
            <code className="font-mono text-xs"> interpretation</code> is the model&apos;s suggestion
            (amounts carry <code className="font-mono text-xs">value_origin: &quot;EXTRACTED&quot;</code>), while{" "}
            <code className="font-mono text-xs">accounting_result</code> is the deterministic authority
            output — null whenever no authority ran.
          </p>
          <div className="rounded-lg border border-border/70 bg-muted/30 px-3 py-2.5">
            <code className="font-mono text-xs font-semibold">reason_codes</code>
            <p className="mt-1 text-xs text-muted-foreground">
              Stable machine-readable reasons — never raw exception messages:
            </p>
            <dl className="mt-2 space-y-1.5">
              {REASON_CODES.map(([code, meaning]) => (
                <div key={code} className="grid gap-1 sm:grid-cols-[220px_1fr] sm:items-baseline">
                  <dt className="font-mono text-[11px]">{code}</dt>
                  <dd className="text-xs text-muted-foreground">{meaning}</dd>
                </div>
              ))}
            </dl>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardContent className="pt-6">
          <h2 className="text-sm font-semibold">Evidence — explains the result, never fabricated</h2>
          <p className="mt-1 mb-3 text-sm leading-relaxed text-muted-foreground">
            Document results carry deterministic source citations: page, text span, and (when the
            engine provides one) bbox and confidence. Fields the engine did not provide stay{" "}
            <code className="font-mono text-xs">null</code> — nothing is invented. Text-only results
            have <code className="font-mono text-xs">evidence: []</code>.{" "}
            <code className="font-mono text-xs">lineage</code> maps each semantic field to the
            evidence ids that support it.
          </p>
          <CodeBlock>{`{
  "evidence_id": "doc_invoice_p1_e0003",
  "document_id": "doc_invoice_ab12cd34",
  "page": 1,
  "text": "Total: Rs. 15,000",
  "bbox": [12.0, 300.5, 200.0, 318.25],   // null when the engine had none
  "extraction_confidence": 0.98,          // null when not reported
  "source_type": "pypdf"
}`}</CodeBlock>
        </CardContent>
      </Card>

      <Card>
        <CardContent className="pt-6">
          <h2 className="text-sm font-semibold">Async documents — same contract, later arrival</h2>
          <p className="mt-1 mb-3 text-sm leading-relaxed text-muted-foreground">
            Async changes <em>when</em> the result arrives, never <em>what</em> it means:
          </p>
          <ol className="space-y-2 text-sm text-muted-foreground">
            {[
              "POST /v1/documents → 202 with job_id + result_url (admitted ≠ VERIFIED; one quota unit)",
              "GET /v1/jobs/{job_id} → poll; status stays PROCESSING while running",
              "Completion means the pipeline finished — the final status can be REVIEW_REQUIRED, UNSUPPORTED, or FAILED",
              "GET /v1/results/{result_id} → the same canonical 5D envelope as the synchronous path",
              "Jobs live in the durable PostgreSQL store — a server restart never erases a submitted job",
            ].map((item, i) => (
              <li key={i} className="flex gap-2.5">
                <span className="mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full border border-border font-mono text-[10px] text-muted-foreground">
                  {i + 1}
                </span>
                {item}
              </li>
            ))}
          </ol>
        </CardContent>
      </Card>

      <Card>
        <CardContent className="pt-6">
          <h2 className="text-sm font-semibold">Idempotency — replay-safe, not exactly-once</h2>
          <p className="mt-1 text-sm leading-relaxed text-muted-foreground">
            Send <code className="font-mono text-xs">Idempotency-Key</code> (16–200 chars of{" "}
            <code className="font-mono text-xs">[A-Za-z0-9._~-]</code>) on{" "}
            <code className="font-mono text-xs">/v1/process</code> or{" "}
            <code className="font-mono text-xs">/v1/documents</code>: the same key with the same
            request replays the original result (<code className="font-mono text-xs">Idempotent-Replayed: true</code>)
            without consuming extra quota; the same key with a different request is a deterministic
            409. Deterministic failures are stored and replayed; transient failures release the
            claim so a retry genuinely retries. Records age out after 72 hours. Platrixa does{" "}
            <strong>not</strong> promise exactly-once execution.
          </p>
        </CardContent>
      </Card>

      <Card>
        <CardContent className="pt-6">
          <div className="flex flex-wrap items-center gap-2.5">
            <h2 className="text-sm font-semibold">Webhooks</h2>
            <ComingSoon>BEST-EFFORT DELIVERY</ComingSoon>
          </div>
          <p className="mt-2.5 text-sm leading-relaxed text-muted-foreground">
            Register an https endpoint (
            <code className="font-mono text-xs">POST /v1/webhook-endpoints</code>) subscribed to{" "}
            <code className="font-mono text-xs">document.completed</code>,{" "}
            <code className="font-mono text-xs">document.review_required</code>,{" "}
            <code className="font-mono text-xs">document.unsupported</code>, or{" "}
            <code className="font-mono text-xs">document.failed</code>. The signing secret is shown
            exactly once; payloads are signed{" "}
            <code className="font-mono text-xs">Platrixa-Signature: t=&lt;unix&gt;,v1=&lt;hmac&gt;</code>{" "}
            with a ±300 s replay window and deterministic event ids. Delivery in this deployment is
            best-effort, single-attempt — <strong>not</strong> at-least-once — so polling{" "}
            <code className="font-mono text-xs">GET /v1/jobs/&#123;job_id&#125;</code> remains the
            reliable path.
          </p>
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
              <div key={code} className="grid gap-1 rounded-lg border border-border/70 px-3 py-2 sm:grid-cols-[280px_1fr] sm:items-baseline">
                <dt className="font-mono text-xs">{code}</dt>
                <dd className="text-sm text-muted-foreground">{meaning}</dd>
              </div>
            ))}
          </dl>
        </CardContent>
      </Card>

      <Card>
        <CardContent className="pt-6">
          <h2 className="text-sm font-semibold">Trust &amp; Safety</h2>
          <p className="mt-1 text-sm leading-relaxed text-muted-foreground">
            Platrixa is a financial semantic validation and developer infrastructure layer — not a
            financial, investment, tax, or legal adviser, not an autonomous accountant, and not a
            regulatory authority. A VERIFIED result does not itself establish legal, tax,
            regulatory, or factual compliance. When sufficient evidence or capability cannot be
            established, the API returns REVIEW_REQUIRED or UNSUPPORTED rather than inventing a
            conclusion.
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
