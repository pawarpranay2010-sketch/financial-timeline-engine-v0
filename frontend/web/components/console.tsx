"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Pipeline, type PipelineStageKey } from "@/components/pipeline";
import { ResultCard } from "@/components/result-card";
import { ApiRequestError, processTransaction } from "@/lib/api";
import type { KernelProcessResponse } from "@/lib/types";

const EXAMPLES = [
  "Purchased furniture for cash Rs. 15,000",
  "Sold goods to Anil on credit Rs. 25,000",
  "Paid salary Rs. 20,000 by cheque",
  "Purchased goods from Raj on credit Rs. 25,000",
];

/** Clearly-labeled demo result — NEVER presented as a backend answer. */
const DEMO_RESULT: KernelProcessResponse = {
  request_id: "demo-pipeline-preview",
  status: "REVIEW_REQUIRED",
  status_label: "Review required",
  success: false,
  next_action:
    "Connect the FastAPI backend (NEXT_PUBLIC_PLATRIXA_API_BASE) to process real inputs through the deterministic kernel.",
  issues: [
    "demo shell: the payment mode is not stated — the real runtime never guesses between cash and credit",
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

export function Console() {
  const [input, setInput] = useState("");
  const [phase, setPhase] = useState<Phase>("idle");
  const [result, setResult] = useState<KernelProcessResponse | null>(null);
  const [source, setSource] = useState<"api" | "demo">("api");
  const [error, setError] = useState<{ code: string; message: string; retryable?: boolean } | null>(null);
  const [stages, setStages] = useState<PipelineStageKey[]>([]);

  async function run(raw?: string) {
    const text = (raw ?? input).trim();
    if (!text) return;
    setInput(text);
    setPhase("loading");
    setError(null);
    setResult(null);
    setStages(["input"]);

    // Stage choreography mirrors the backend's documented flow. The timeouts
    // only animate the visualization; all actual reasoning is server-side.
    const t1 = setTimeout(() => setStages(["input", "interpretation"]), 350);
    const t2 = setTimeout(() => setStages(["input", "interpretation", "validation"]), 750);
    const t3 = setTimeout(
      () => setStages(["input", "interpretation", "validation", "authority"]),
      1150,
    );

    try {
      const response = await processTransaction(text);
      [t1, t2, t3].forEach(clearTimeout);
      setStages(["input", "interpretation", "validation", "authority", "result"]);
      setResult(response);
      setSource("api");
      setPhase("done");
    } catch (err) {
      [t1, t2, t3].forEach(clearTimeout);
      setStages(["input"]);
      if (err instanceof ApiRequestError) {
        setError({ code: err.code, message: err.message, retryable: err.retryable });
      } else {
        setError({
          code: "NETWORK_ERROR",
          message: "Could not reach the Platrixa API. The backend may be offline in this environment.",
        });
      }
      setPhase("error");
    }
  }

  function showDemo() {
    setPhase("done");
    setResult(DEMO_RESULT);
    setSource("demo");
    setStages(["input", "interpretation", "validation", "authority", "result"]);
  }

  return (
    <div id="console" className="space-y-4">
      <Card>
        <CardContent className="pt-6">
          <form
            onSubmit={(e) => {
              e.preventDefault();
              void run();
            }}
            className="space-y-3"
          >
            <label htmlFor="raw_input" className="block text-sm font-medium">
              Financial input
            </label>
            <textarea
              id="raw_input"
              name="raw_input"
              rows={2}
              maxLength={2000}
              required
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="e.g. Purchased furniture for cash Rs. 15,000"
              className="w-full resize-none rounded-lg border border-border bg-card px-3.5 py-3 text-sm outline-none transition-colors placeholder:text-muted-foreground/70 focus-visible:border-accent/60 focus-visible:ring-2 focus-visible:ring-ring"
            />
            <div className="flex flex-wrap items-center gap-2">
              <Button type="submit" disabled={phase === "loading" || input.trim().length === 0}>
                {phase === "loading" ? "Validating…" : "Validate input"}
              </Button>
              <Button type="button" variant="outline" size="sm" onClick={showDemo}>
                Preview with demo data
              </Button>
              <span className="ml-auto hidden font-mono text-[11px] text-muted-foreground sm:block">
                POST /api/v1/kernel/process
              </span>
            </div>
          </form>

          <div className="mt-4 flex flex-wrap items-center gap-2">
            <span className="text-[11px] uppercase tracking-wide text-muted-foreground">Try:</span>
            {EXAMPLES.map((example) => (
              <button
                key={example}
                type="button"
                onClick={() => void run(example)}
                disabled={phase === "loading"}
                className="rounded-full border border-border bg-muted/50 px-3 py-1 text-xs text-muted-foreground transition-colors hover:border-accent/40 hover:bg-accent-soft hover:text-foreground disabled:opacity-50"
              >
                {example}
              </button>
            ))}
          </div>
        </CardContent>
      </Card>

      <Pipeline active={stages} />

      {phase === "loading" && (
        <Card>
          <CardContent className="space-y-3 pt-6">
            <Skeleton className="h-4 w-28" />
            <Skeleton className="h-4 w-full" />
            <Skeleton className="h-4 w-3/4" />
            <p className="pt-1 text-xs text-muted-foreground">
              Understanding the words → verifying the schema → grounding in the input → executing the
              deterministic authority. No result is guessed while this runs.
            </p>
          </CardContent>
        </Card>
      )}

      {phase === "error" && error && (
        <Card className="border-status-failed/30">
          <CardContent className="pt-6">
            <p className="font-mono text-xs text-status-failed">{error.code}</p>
            <p className="mt-1 text-sm">{error.message}</p>
            {error.retryable && (
              <p className="mt-1 text-xs text-muted-foreground">This failure is retryable.</p>
            )}
            <Button variant="outline" size="sm" className="mt-4" onClick={() => void run()}>
              Try again
            </Button>
          </CardContent>
        </Card>
      )}

      {phase === "done" && result && <ResultCard result={result} source={source} />}
    </div>
  );
}
