"use client";

import { Button } from "@/components/ui/button";
import { errorCodeExplanation, MetaField, toneForStatus, apiStatusLabel } from "@/components/status";
import { cn } from "@/lib/utils";

/**
 * Polished error experience for structured /v1 error envelopes and network
 * failures. Shows the real code, a human explanation, retryability, and the
 * request ID when supplied. Never exposes stack traces; never converts a
 * failure into a success state.
 */

const ICON_BY_TONE: Record<string, string> = {
  failed: "text-status-failed",
  invalid: "text-status-unsupported",
  processing: "text-status-processing",
  review: "text-status-review",
  unsupported: "text-status-unsupported",
  verified: "text-status-verified",
  neutral: "text-muted-foreground",
};

export function ErrorPanel({
  error,
  onRetry,
}: {
  error: {
    code: string;
    message?: string;
    apiStatus?: string;
    retryable?: boolean;
    requestId?: string;
  } | null;
  onRetry?: () => void;
}) {
  if (!error) return null;
  const tone = toneForStatus(error.apiStatus ?? (error.retryable ? "PROCESSING" : "FAILED"));
  const apiLabel = error.apiStatus ? apiStatusLabel(error.apiStatus) : "Failed";

  return (
    <section
      role="alert"
      className={cn(
        "rounded-xl border bg-card p-5",
        tone === "processing"
          ? "border-status-processing/30"
          : tone === "review" || tone === "invalid"
            ? "border-status-review/30"
            : "border-status-failed/30",
      )}
      aria-live="assertive"
    >
      <div className="flex flex-wrap items-center gap-2.5">
        <span className="font-mono text-sm font-semibold text-status-failed">{error.code}</span>
        <span
          className={cn(
            "rounded-full border px-2.5 py-0.5 font-mono text-[11px]",
            ICON_BY_TONE[tone] ?? "text-muted-foreground",
            tone === "processing"
              ? "border-status-processing/35 bg-status-processing/10"
              : tone === "review" || tone === "invalid"
                ? "border-status-review/35 bg-status-review/10"
                : "border-status-failed/35 bg-status-failed/10",
          )}
        >
          API status: {apiLabel}
        </span>
      </div>

      <p className="mt-3 text-sm text-foreground/90">
        {error.message || errorCodeExplanation(error.code)}
      </p>
      <p className="mt-1 text-sm text-muted-foreground">{errorCodeExplanation(error.code)}</p>

      <div className="mt-4 flex flex-wrap items-end gap-x-8 gap-y-3">
        {error.requestId ? <MetaField label="Request ID" value={error.requestId} /> : null}
        {typeof error.retryable === "boolean" ? (
          <MetaField
            label="Retryable"
            value={
              <span className={error.retryable ? "text-status-verified" : "text-muted-foreground"}>
                {error.retryable ? "Yes" : "No"}
              </span>
            }
            mono={false}
          />
        ) : null}
        {onRetry ? (
          <Button size="sm" variant="outline" onClick={onRetry} className="ml-auto">
            Try again
          </Button>
        ) : null}
      </div>
    </section>
  );
}
