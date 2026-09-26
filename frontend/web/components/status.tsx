import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { EngineStatus } from "@/lib/types";
import type { ReactNode } from "react";

/**
 * Status presentation ONLY. This mapping decides colors and copy for the
 * six-state public API status; it never changes or upgrades the status
 * value itself, which is decided by the backend's deterministic runtime.
 */

type Tone = "verified" | "review" | "unsupported" | "processing" | "failed" | "neutral";

const TONE_CLASS: Record<Tone, string> = {
  verified: "bg-status-verified/12 text-status-verified border-status-verified/35",
  review: "bg-status-review/12 text-status-review border-status-review/35",
  unsupported: "bg-status-unsupported/12 text-status-unsupported border-status-unsupported/35",
  processing: "bg-status-processing/12 text-status-processing border-status-processing/35",
  failed: "bg-status-failed/12 text-status-failed border-status-failed/35",
  neutral: "bg-muted text-muted-foreground border-border",
};

const TONE_DOT: Record<Tone, string> = {
  verified: "bg-status-verified",
  review: "bg-status-review",
  unsupported: "bg-status-unsupported",
  processing: "bg-status-processing",
  failed: "bg-status-failed",
  neutral: "bg-muted-foreground",
};

export function toneForStatus(status: string): Tone {
  switch (status) {
    case "VERIFIED":
      return "verified";
    case "REVIEW_REQUIRED":
    case "BLOCKED":
      return "review";
    case "UNSUPPORTED_TRANSACTION":
    case "FORBIDDEN_OUTPUT":
      return "unsupported";
    case "MODEL_UNAVAILABLE":
      return "processing";
    case "VALIDATION_FAILED":
    case "GROUNDING_FAILED":
      return "failed";
    default:
      return "neutral";
  }
}

/** Plainer developer copy for each engine state (evidence, not marketing). */
export function statusExplanation(status: string): string {
  switch (status) {
    case "VERIFIED":
      return "Deterministic execution passed: the interpretation was schema-verified, grounded in the input, and executed by the accounting authority.";
    case "REVIEW_REQUIRED":
      return "The input was understood, but the evidence was insufficient or ambiguous — flagged for human review instead of guessed.";
    case "BLOCKED":
      return "Rejected by a safety boundary: no deterministic authority could accept this input.";
    case "UNSUPPORTED_TRANSACTION":
      return "No supported capability covers this input yet — the runtime refuses rather than improvising.";
    case "FORBIDDEN_OUTPUT":
      return "The suggested structure was rejected at the authority boundary.";
    case "VALIDATION_FAILED":
      return "Schema verification failed — the interpretation did not match the strict field contract, with the reasons recorded below.";
    case "GROUNDING_FAILED":
      return "Grounding failed — the interpretation could not be tied back to explicit input evidence.";
    case "MODEL_UNAVAILABLE":
      return "The semantic provider is temporarily unavailable; the outcome is unknown. Retry later.";
    default:
      return "Terminal state reported by the deterministic runtime.";
  }
}

export function StatusBadge({
  status,
  label,
  className,
}: {
  status: EngineStatus;
  label?: string;
  className?: string;
}) {
  const tone = toneForStatus(status);
  return (
    <Badge
      variant="outline"
      className={cn(
        "gap-1.5 rounded-full border px-3 py-1 font-mono text-xs font-medium tracking-tight",
        TONE_CLASS[tone],
        className,
      )}
    >
      <span className={cn("size-1.5 rounded-full", TONE_DOT[tone])} aria-hidden />
      {label || status}
    </Badge>
  );
}

export function StatusExplanation({ status, children }: { status: string; children?: ReactNode }) {
  const tone = toneForStatus(status);
  return (
    <p className={cn("text-sm leading-relaxed text-muted-foreground")}>
      {children ?? statusExplanation(status)}
      <span className="sr-only"> (status tone: {tone})</span>
    </p>
  );
}
