"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { StatusBadge } from "@/components/status";
import { ResultCard } from "@/components/result-card";
import { ErrorPanel } from "@/components/error-panel";
import { ApiRequestError, processTransaction } from "@/lib/api";
import type { EngineStatus, KernelProcessResponse } from "@/lib/types";

const EXAMPLES = [
  "Purchased furniture for cash Rs. 15,000",
  "Paid office rent Rs. 25,000 in cash",
  "Received Rs. 20,000 from customer against outstanding invoice",
];

/** Clearly-labeled fixture for UI development — NEVER a silent substitute. */
const DEMO_RESULT: KernelProcessResponse = {
  request_id: "demo-fixture-review-required",
  status: "REVIEW_REQUIRED",
  status_label: "Review required",
  success: false,
  next_action:
    "Connect the backend (server-side PLATRIXA_API_BASE_URL) to process real inputs through the deterministic kernel.",
  issues: [
    "demo fixture: the payment mode is not stated — the real runtime never guesses between cash and credit",
  ],
  grounding_issues: [],
  verification_status: null,
  interpretation: {
    transaction_type_enum: "PURCHASE",
    parties: ["Raj"],
    amounts: [{ value: "25000", currency: "INR", source: "explicit" }],
    payment_method_enum: "UNKNOWN",
    ambiguities: ["payment method not stated (demo copy of a real engine output)"],
    ambiguity_flags: ["MISSING_PAYMENT_MODE"],
    overall_confidence: "0.50",
    suggested_status: "REVIEW_REQUIRED",
    grounding: { all_fields_explicitly_grounded: false, inferred_fields: [] },
  },
  accounting: null,
  persisted: false,
  persistence_error: null,
};

type Phase = "idle" | "loading" | "done" | "error";

export function ValidationConsole() {
  const [input, setInput] = useState("");
  const [phase, setPhase] = useState<Phase>("idle");
  const [result, setResult] = useState<KernelProcessResponse | null>(null);
  const [source, setSource] = useState<"api" | "demo">("api");
  const [error, setError] = useState<{
    code: string;
    message?: string;
    apiStatus?: string;
    retryable?: boolean;
    requestId?: string;
  } | null>(null);

  async function run(raw?: string) {
    const text = (raw ?? input).trim();
    if (!text) return;
    setInput(text);
    setPhase("loading");
    setError(null);
    setResult(null);

    try {
      const response = await processTransaction(text);
      setResult(response);
      setSource("api");
      setPhase("done");
    } catch (err) {
      if (err instanceof ApiRequestError) {
        setError({
          code: err.code,
          message: err.message,
          apiStatus: err.apiStatus,
          retryable: err.retryable,
        });
      } else {
        setError({
          code: "NETWORK_ERROR",
          message: "Could not reach the Platrixa API through the frontend proxy.",
        });
      }
      setPhase("error");
    }
  }

  function showDemo() {
    setPhase("done");
    setResult(DEMO_RESULT);
    setSource("demo");
    setError(null);
  }

  const loading = phase === "loading";

  return (
    <div className="space-y-4">
      <Card>
        <CardContent className="pt-6">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <h2 className="text-lg font-semibold tracking-tight">Process financial input</h2>
            <code className="font-mono text-[11px] text-muted-foreground">
              POST /api/v1/kernel/process
            </code>
          </div>
          <p className="mt-1 text-sm text-muted-foreground">
            Enter a narration the way it appears in your records. The deterministic runtime — not
            this UI — decides the outcome.
          </p>

          <form
            onSubmit={(e) => {
              e.preventDefault();
              void run();
            }}
            className="mt-4 space-y-3"
          >
            <label htmlFor="raw_input" className="sr-only">
              Financial input
            </label>
            <textarea
              id="raw_input"
              name="raw_input"
              rows={3}
              maxLength={2000}
              required
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="e.g. Purchased furniture for cash Rs. 15,000"
              className="w-full resize-none rounded-lg border border-border bg-card px-3.5 py-3 font-mono text-sm outline-none transition-colors placeholder:text-muted-foreground/70 focus-visible:border-accent/60 focus-visible:ring-2 focus-visible:ring-ring"
            />
            <div className="flex flex-wrap items-center gap-2">
              <Button type="submit" disabled={loading || input.trim().length === 0}>
                {loading ? "Processing…" : "Process"}
              </Button>
              <Button type="button" variant="outline" size="sm" onClick={showDemo}>
                Load demo fixture
              </Button>
              {loading && (
                <span className="font-mono text-[11px] text-muted-foreground" role="status">
                  interpretation → schema → grounding → authority…
                </span>
              )}
            </div>
          </form>

          <div className="mt-4 flex flex-wrap items-center gap-2">
            <span className="text-[11px] uppercase tracking-wide text-muted-foreground">Try:</span>
            {EXAMPLES.map((example) => (
              <button
                key={example}
                type="button"
                onClick={() => void run(example)}
                disabled={loading}
                className="rounded-full border border-border bg-muted/50 px-3 py-1 text-xs text-muted-foreground transition-colors hover:border-accent/40 hover:bg-accent-soft hover:text-foreground disabled:opacity-50"
              >
                {example}
              </button>
            ))}
          </div>
        </CardContent>
      </Card>

      {loading && (
        <Card>
          <CardContent className="space-y-3 pt-6">
            <Skeleton className="h-5 w-40" />
            <Skeleton className="h-4 w-full" />
            <Skeleton className="h-4 w-2/3" />
            <p className="pt-1 text-xs text-muted-foreground">
              The engine is interpreting the input, verifying the schema, grounding the claims, and
              consulting the capability registry. No result is guessed while this runs.
            </p>
          </CardContent>
        </Card>
      )}

      {phase === "error" && (
        <ErrorPanel error={error} onRetry={error?.retryable ? () => void run() : undefined} />
      )}

      {phase === "done" && result && (
        <>
          <div className="flex flex-wrap items-center gap-2">
            <StatusBadge status={result.status as EngineStatus} label={result.status_label || result.status} />
            <span className="font-mono text-[11px] text-muted-foreground">
              request {result.request_id ?? "—"}
            </span>
          </div>
          <ResultCard result={result} source={source} />
        </>
      )}
    </div>
  );
}
