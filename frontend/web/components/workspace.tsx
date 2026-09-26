"use client";

import { useCallback, useRef, useState } from "react";
import { FileText, Layers, Server, Type } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { TrustGate } from "@/components/trust-gate";
import { ResultReport, type ReportInput } from "@/components/result-report";
import { ErrorPanel } from "@/components/error-panel";
import { ApiRequestError, processDocument, processTransaction } from "@/lib/api";
import { cn } from "@/lib/utils";

/**
 * Validation workspace — one product surface, four ingestion modes.
 *
 * Honency rule: a mode is marked LIVE only when its backend endpoint
 * actually exists and was verified:
 *   Text      → LIVE   (POST /api/v1/kernel/process)
 *   Documents → LIVE   (POST /v1/process/document — PDF/image/text)
 *   Bulk      → COMING SOON (no batch endpoint exists — never simulated)
 *   API       → pointer to the developer contract (no fake playground)
 */

const EXAMPLES = [
  "Purchased furniture for cash Rs. 15,000",
  "Paid office rent Rs. 25,000 in cash",
  "Received Rs. 20,000 from customer against outstanding invoice",
];

const MAX_DOCUMENT_BYTES = 10 * 1024 * 1024; // mirrors backend/document_understanding/inputs.py
const ACCEPTED = ".pdf,.png,.jpg,.jpeg,.tif,.tiff,.bmp,.webp,.txt";

/** Clearly-labeled fixture for UI development — NEVER a silent substitute. */
const DEMO_RESULT: ReportInput = {
  status: "REVIEW_REQUIRED",
  status_label: "Review required",
  success: false,
  next_action:
    "Connect the backend to process real inputs through the deterministic kernel.",
  issues: [
    "demo fixture: the payment mode is not stated — the real runtime never guesses between cash and credit",
  ],
  grounding_issues: [],
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
  request_id: "demo-fixture-review-required",
};

type Mode = "text" | "documents" | "bulk" | "api";

const MODES: Array<{ key: Mode; label: string; icon: React.ReactNode; availability: "LIVE" | "COMING SOON"; hint: string }> = [
  { key: "text", label: "Text", icon: <Type className="size-4" aria-hidden />, availability: "LIVE", hint: "Financial narration, verbatim" },
  { key: "documents", label: "Documents", icon: <FileText className="size-4" aria-hidden />, availability: "LIVE", hint: "PDF / image / plain text, ≤ 10 MB" },
  { key: "bulk", label: "Bulk", icon: <Layers className="size-4" aria-hidden />, availability: "COMING SOON", hint: "CSV / JSON batches — no batch endpoint yet" },
  { key: "api", label: "API", icon: <Server className="size-4" aria-hidden />, availability: "LIVE", hint: "Call the same endpoints your integration will" },
];

type Phase = "idle" | "loading" | "done" | "error";

interface ErrorState {
  code: string;
  message?: string;
  apiStatus?: string;
  retryable?: boolean;
  requestId?: string;
}

function ModeBadge({ availability }: { availability: string }) {
  return availability === "LIVE" ? (
    <span className="rounded-full border border-status-verified/40 bg-status-verified/10 px-2 py-0.5 font-mono text-[10px] font-semibold tracking-wide text-status-verified">
      LIVE
    </span>
  ) : (
    <span className="rounded-full border border-border bg-muted px-2 py-0.5 font-mono text-[10px] font-semibold tracking-wide text-muted-foreground">
      COMING SOON
    </span>
  );
}

export function ValidationWorkspace() {
  const [mode, setMode] = useState<Mode>("text");
  const [input, setInput] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [phase, setPhase] = useState<Phase>("idle");
  const [report, setReport] = useState<ReportInput | null>(null);
  const [source, setSource] = useState<"api" | "demo">("api");
  const [error, setError] = useState<ErrorState | null>(null);
  const [fileNote, setFileNote] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const reset = useCallback(() => {
    setPhase("idle");
    setReport(null);
    setError(null);
  }, []);

  async function runText(raw?: string) {
    const text = (raw ?? input).trim();
    if (!text) return;
    setInput(text);
    setPhase("loading");
    setError(null);
    setReport(null);
    try {
      const response = await processTransaction(text);
      setReport({ ...response });
      setSource("api");
      setPhase("done");
    } catch (err) {
      setError(
        err instanceof ApiRequestError
          ? { code: err.code, message: err.message, apiStatus: err.apiStatus, retryable: err.retryable }
          : { code: "NETWORK_ERROR", message: "Could not reach the Platrixa API through the frontend proxy." },
      );
      setPhase("error");
    }
  }

  async function runDocument() {
    if (!file && !input.trim()) return;
    if (file && file.size > MAX_DOCUMENT_BYTES) {
      setFileNote(`File exceeds the backend's 10 MiB limit (${(file.size / 1024 / 1024).toFixed(1)} MB).`);
      return;
    }
    setPhase("loading");
    setError(null);
    setReport(null);
    try {
      const response = await processDocument({ text: input.trim() || undefined, file: file ?? undefined });
      setReport({
        ...response,
        engine_status: response.engine_status ?? response.status,
      });
      setSource("api");
      setPhase("done");
    } catch (err) {
      setError(
        err instanceof ApiRequestError
          ? { code: err.code, message: err.message, apiStatus: err.apiStatus, retryable: err.retryable }
          : { code: "NETWORK_ERROR", message: "Could not reach the Platrixa API through the frontend proxy." },
      );
      setPhase("error");
    }
  }

  function showDemo() {
    setPhase("done");
    setReport(DEMO_RESULT);
    setSource("demo");
    setError(null);
  }

  function onFileChange(fileList: FileList | null) {
    const picked = fileList?.[0] ?? null;
    setFileNote(null);
    if (picked && picked.size > MAX_DOCUMENT_BYTES) {
      setFileNote(`File exceeds the backend's 10 MiB limit (${(picked.size / 1024 / 1024).toFixed(1)} MB).`);
      return;
    }
    setFile(picked);
  }

  const loading = phase === "loading";

  return (
    <div className="space-y-4">
      <TrustGate />

      {/* Mode selector */}
      <div className="grid gap-2 sm:grid-cols-4" role="tablist" aria-label="Ingestion mode">
        {MODES.map((m) => (
          <button
            key={m.key}
            type="button"
            role="tab"
            aria-selected={mode === m.key}
            disabled={loading}
            onClick={() => {
              setMode(m.key);
              reset();
              setFileNote(null);
            }}
            className={cn(
              "rounded-lg border px-3 py-2.5 text-left transition-colors disabled:opacity-60",
              mode === m.key
                ? "border-accent/50 bg-accent-soft"
                : "border-border/70 bg-card hover:border-accent/30",
            )}
          >
            <span className="flex items-center justify-between gap-2">
              <span className="flex items-center gap-2 text-sm font-medium">
                <span className={mode === m.key ? "text-accent" : "text-muted-foreground"}>{m.icon}</span>
                {m.label}
              </span>
              <ModeBadge availability={m.availability} />
            </span>
            <span className="mt-0.5 block text-[11px] leading-snug text-muted-foreground">{m.hint}</span>
          </button>
        ))}
      </div>

      {/* Input card per mode */}
      {mode === "text" && (
        <Card>
          <CardContent className="pt-6">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <h2 className="text-base font-semibold tracking-tight">Process financial input</h2>
              <code className="font-mono text-[11px] text-muted-foreground">POST /api/v1/kernel/process</code>
            </div>
            <p className="mt-1 text-sm text-muted-foreground">
              Enter a narration the way it appears in your records. The deterministic runtime — not this UI — decides the outcome.
            </p>
            <form
              onSubmit={(e) => {
                e.preventDefault();
                void runText();
              }}
              className="mt-4 space-y-3"
            >
              <label htmlFor="raw_input" className="sr-only">Financial input</label>
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
                  onClick={() => void runText(example)}
                  disabled={loading}
                  className="rounded-full border border-border bg-muted/50 px-3 py-1 text-xs text-muted-foreground transition-colors hover:border-accent/40 hover:bg-accent-soft hover:text-foreground disabled:opacity-50"
                >
                  {example}
                </button>
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      {mode === "documents" && (
        <Card>
          <CardContent className="pt-6">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <h2 className="text-base font-semibold tracking-tight">Process a document</h2>
              <code className="font-mono text-[11px] text-muted-foreground">POST /v1/process/document</code>
            </div>
            <p className="mt-1 text-sm text-muted-foreground">
              Upload a PDF, image, or plain-text financial document (max 10 MB). Pages, provenance, and evidence are returned by the backend.
            </p>
            <form
              onSubmit={(e) => {
                e.preventDefault();
                void runDocument();
              }}
              className="mt-4 space-y-3"
            >
              <label
                htmlFor="document"
                className="flex min-h-28 cursor-pointer flex-col items-center justify-center gap-1.5 rounded-lg border border-dashed border-border bg-muted/30 px-4 py-6 text-center transition-colors hover:border-accent/50 hover:bg-accent-soft/40"
                onDragOver={(e) => e.preventDefault()}
                onDrop={(e) => {
                  e.preventDefault();
                  onFileChange(e.dataTransfer.files);
                }}
              >
                <FileText className="size-5 text-muted-foreground" aria-hidden />
                <span className="text-sm">
                  {file ? file.name : "Drag & drop or choose a file"}
                </span>
                <span className="text-[11px] text-muted-foreground">
                  {file
                    ? `${(file.size / 1024).toFixed(0)} KB · ${file.type || "unknown type"} — click to replace`
                    : "PDF, PNG, JPG, TIFF, BMP, WebP, or TXT"}
                </span>
              </label>
              <input
                ref={fileInputRef}
                id="document"
                name="document"
                type="file"
                accept={ACCEPTED}
                className="sr-only"
                onChange={(e) => onFileChange(e.target.files)}
              />
              {fileNote && (
                <p className="rounded-md border border-status-review/40 bg-status-review/10 px-3 py-2 text-sm text-status-review">{fileNote}</p>
              )}
              <div className="flex flex-wrap items-center gap-2">
                <Button type="submit" disabled={loading || (!file && input.trim().length === 0)}>
                  {loading ? "Processing…" : "Process document"}
                </Button>
                {file && (
                  <Button type="button" variant="ghost" size="sm" onClick={() => { setFile(null); setFileNote(null); if (fileInputRef.current) fileInputRef.current.value = ""; }}>
                    Clear file
                  </Button>
                )}
              </div>
            </form>
          </CardContent>
        </Card>
      )}

      {mode === "bulk" && (
        <Card className="border-dashed">
          <CardContent className="pt-6">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <h2 className="text-base font-semibold tracking-tight">Bulk validation</h2>
              <ModeBadge availability="COMING SOON" />
            </div>
            <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
              Batch validation (CSV / JSON / multiple documents) is designed but the backend does not
              yet expose a batch endpoint, so there is nothing to run against — Platrixa does not
              simulate results.
            </p>
            <div className="mt-4 rounded-lg border border-border/70 bg-muted/30 p-4">
              <p className="font-mono text-[11px] uppercase tracking-wider text-muted-foreground">Planned result model</p>
              <div className="mt-2 flex flex-wrap gap-2">
                {["total", "verified", "review_required", "unsupported", "invalid_input", "failed"].map((s) => (
                  <span key={s} className="rounded border border-border bg-card px-2 py-1 font-mono text-[11px] text-muted-foreground">{s}</span>
                ))}
              </div>
              <p className="mt-2.5 text-xs text-muted-foreground">
                Plus a review queue for everything that lands in review_required.
              </p>
            </div>
          </CardContent>
        </Card>
      )}

      {mode === "api" && (
        <Card>
          <CardContent className="pt-6">
            <h2 className="text-base font-semibold tracking-tight">Use the API directly</h2>
            <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
              Everything this workspace does is a thin view over the versioned developer API. Your
              integration talks to the same deterministic runtime — the six-state contract, structured
              errors, and the capability registry are documented, not approximated here.
            </p>
            <pre className="mt-4 overflow-x-auto rounded-lg border border-border bg-muted/40 p-4 font-mono text-[12.5px] leading-relaxed">{`curl -X POST "$PLATRIXA_HOST/api/v1/kernel/process" \\
  -H "Content-Type: application/json" \\
  -d '{"raw_input": "Purchased furniture for cash Rs. 15,000"}'`}</pre>
            <div className="mt-3 flex gap-3 text-sm">
              <a href="/developer/documentation" className="font-medium text-accent hover:underline">API reference →</a>
              <a href="/developer/overview" className="font-medium text-accent hover:underline">Developer overview →</a>
            </div>
          </CardContent>
        </Card>
      )}

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
        <ErrorPanel error={error} onRetry={error?.retryable ? (mode === "documents" ? () => void runDocument() : () => void runText()) : undefined} />
      )}

      {phase === "done" && report && <ResultReport report={report} source={source} />}
    </div>
  );
}
