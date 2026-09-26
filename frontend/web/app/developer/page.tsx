import type { Metadata } from "next";
import Link from "next/link";
import { Card, CardContent } from "@/components/ui/card";

export const metadata: Metadata = {
  title: "Developer API — Platrixa",
  description:
    "The versioned /v1 developer contract over the deterministic Platrixa runtime: request/response examples, six-state API status, authentication, and error behavior.",
};

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

export default function DeveloperPage() {
  return (
    <main className="mx-auto w-full max-w-4xl flex-1 px-4 py-10 sm:px-6">
      <div className="mb-6">
        <h1 className="text-2xl font-semibold tracking-tight">Developer API</h1>
        <p className="mt-1.5 max-w-2xl text-sm leading-relaxed text-muted-foreground">
          The same deterministic runtime behind this UI is exposed as a versioned, machine-readable
          API. The engine&apos;s terminal state is the only status authority — this UI (and your
          integration) should act on it, never reinterpret it.
        </p>
      </div>

      <div className="space-y-5">
        <Card>
          <CardContent className="pt-6">
            <h2 className="text-sm font-semibold">Process one financial input</h2>
            <p className="mt-1 mb-3 text-sm text-muted-foreground">
              The validation console uses this endpoint through a same-origin server proxy. Your
              integration calls the FastAPI host directly.
            </p>
            <CodeBlock>{REQUEST_EXAMPLE}</CodeBlock>
            <p className="mt-3 mb-2 text-sm font-medium">Response</p>
            <CodeBlock>{RESPONSE_EXAMPLE}</CodeBlock>
          </CardContent>
        </Card>

        <Card>
          <CardContent className="pt-6">
            <h2 className="text-sm font-semibold">Six-state public API status</h2>
            <p className="mt-1 mb-3 text-sm text-muted-foreground">
              The transport layer maps engine outcomes onto a closed six-state vocabulary
              (<code className="font-mono text-xs">api_status</code>) while carrying the engine
              state verbatim. No engine state maps to VERIFIED except VERIFIED itself; unmapped
              states fail closed to FAILED.
            </p>
            <dl className="space-y-2">
              {SIX_STATES.map(([state, meaning]) => (
                <div
                  key={state}
                  className="grid gap-1 rounded-lg border border-border/70 bg-muted/30 px-3 py-2 sm:grid-cols-[160px_1fr] sm:items-baseline"
                >
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
              Every error carries a machine-readable code, a human explanation, and (where
              applicable) an <code className="font-mono text-xs">api_status</code> mapping and
              retryability. Correlate requests with the <code className="font-mono text-xs">X-Request-Id</code> header.
            </p>
            <dl className="space-y-2">
              {ERROR_EXAMPLES.map(([code, meaning]) => (
                <div
                  key={code}
                  className="grid gap-1 rounded-lg border border-border/70 px-3 py-2 sm:grid-cols-[220px_1fr] sm:items-baseline"
                >
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
              The console and this site never hold credentials: API keys, when the operator
              configures them, are injected <em>server-side</em> by the frontend proxy and never
              reach the browser bundle. For your own integration, send your tenant key in the{" "}
              <code className="font-mono text-xs">X-Platrixa-API-Key</code> header when the hosted
              gate is enabled. Full contract:{" "}
              <code className="font-mono text-xs">docs/HOSTED_API.md</code> in the repository.
            </p>
            <p className="mt-3 text-sm">
              <Link href="/capabilities" className="font-medium text-accent hover:underline">
                Browse the capability registry →
              </Link>
            </p>
          </CardContent>
        </Card>
      </div>
    </main>
  );
}
