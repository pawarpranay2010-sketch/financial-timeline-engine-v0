import { cn } from "@/lib/utils";
import type { ReactNode } from "react";

/**
 * Status presentation ONLY.
 *
 * Two distinct vocabularies are rendered side by side, never conflated:
 *
 *  - API status  — the Phase 5A six-state PUBLIC transport contract
 *                  (PROCESSING / VERIFIED / REVIEW_REQUIRED / UNSUPPORTED /
 *                   INVALID_INPUT / FAILED). Transport mapping only.
 *  - Engine status — the backend's own terminal state taxonomy, carried
 *                  verbatim. This is the financial authority.
 *
 * TRANSPORT STATUS ≠ FINANCIAL AUTHORITY. Nothing here upgrades,
 * downgrades, or reinterprets a backend value; maps decide presentation
 * (color/copy) only.
 */

export type Tone =
  | "verified"
  | "review"
  | "unsupported"
  | "processing"
  | "failed"
  | "invalid"
  | "neutral";

const TONE_CLASS: Record<Tone, string> = {
  verified: "bg-status-verified/12 text-status-verified border-status-verified/35",
  review: "bg-status-review/12 text-status-review border-status-review/35",
  unsupported: "bg-status-unsupported/12 text-status-unsupported border-status-unsupported/35",
  processing: "bg-status-processing/12 text-status-processing border-status-processing/35",
  failed: "bg-status-failed/12 text-status-failed border-status-failed/35",
  invalid: "bg-status-unsupported/12 text-status-unsupported border-status-unsupported/30",
  neutral: "bg-muted text-muted-foreground border-border",
};

const TONE_DOT: Record<Tone, string> = {
  verified: "bg-status-verified",
  review: "bg-status-review",
  unsupported: "bg-status-unsupported",
  processing: "bg-status-processing",
  failed: "bg-status-failed",
  invalid: "bg-status-unsupported",
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
    case "PROCESSING":
      return "processing";
    case "MODEL_UNAVAILABLE":
      return "processing";
    case "INVALID_INPUT":
      return "invalid";
    case "VALIDATION_FAILED":
    case "GROUNDING_FAILED":
    case "FAILED":
      return "failed";
    default:
      return "neutral";
  }
}

const API_STATUS_LABEL: Record<string, string> = {
  PROCESSING: "Processing",
  VERIFIED: "Verified",
  REVIEW_REQUIRED: "Review required",
  UNSUPPORTED: "Unsupported",
  INVALID_INPUT: "Invalid input",
  FAILED: "Failed",
};

export function apiStatusLabel(status: string): string {
  return API_STATUS_LABEL[status] ?? status;
}

export function StatusBadge({
  status,
  label,
  size = "md",
  className,
}: {
  status: string;
  label?: string;
  size?: "sm" | "md" | "lg";
  className?: string;
}) {
  const tone = toneForStatus(status);
  return (
    <span
      className={cn(
        "inline-flex items-center gap-2 rounded-full border font-mono font-medium tracking-tight",
        size === "sm" && "px-2.5 py-0.5 text-[11px]",
        size === "md" && "px-3 py-1 text-xs",
        size === "lg" && "px-4 py-1.5 text-sm",
        TONE_CLASS[tone],
        className,
      )}
    >
      <span className={cn("size-1.5 rounded-full", TONE_DOT[tone])} aria-hidden />
      {label || status}
    </span>
  );
}

/** Human explanation for ENGINE states (backend truth, phrased plainly). */
export function engineStatusExplanation(status: string): string {
  switch (status) {
    case "VERIFIED":
      return "Deterministic execution passed: schema verified, grounded in the input, executed by the accounting authority.";
    case "REVIEW_REQUIRED":
      return "Understood, but the evidence was insufficient or ambiguous — flagged for human review instead of guessed.";
    case "BLOCKED":
      return "Rejected by a safety boundary: no deterministic authority could accept this input.";
    case "UNSUPPORTED_TRANSACTION":
      return "No supported capability covers this input yet — the runtime refuses rather than improvising.";
    case "FORBIDDEN_OUTPUT":
      return "The suggested structure was rejected at the authority boundary.";
    case "VALIDATION_FAILED":
      return "Schema verification failed — the interpretation did not satisfy the strict field contract.";
    case "GROUNDING_FAILED":
      return "Grounding failed — the interpretation could not be tied back to explicit input evidence.";
    case "MODEL_UNAVAILABLE":
      return "The semantic provider is temporarily unavailable; the outcome is unknown.";
    default:
      return "Terminal state reported by the deterministic runtime.";
  }
}

/** Human explanation for ERROR CODES (structured /v1 envelopes). */
export function errorCodeExplanation(code: string): string {
  switch (code) {
    case "REQUEST_MALFORMED":
      return "The request could not be parsed as a valid process request.";
    case "REQUEST_TOO_LARGE":
      return "The request body exceeded the transport size limit.";
    case "INPUT_INVALID":
      return "The input was rejected by the public input contract.";
    case "UNAUTHORIZED":
      return "Missing or invalid API key for a gated endpoint.";
    case "QUOTA_EXHAUSTED":
      return "The monthly quota for this API key is exhausted.";
    case "BACKEND_NOT_CONFIGURED":
      return "This frontend environment has no backend configured. Set the server-side PLATRIXA_API_BASE_URL to connect.";
    case "BACKEND_UNREACHABLE":
      return "The Platrixa backend could not be reached from the proxy.";
    case "PROVIDER_UNAVAILABLE":
      return "The upstream provider is temporarily unavailable.";
    case "METERING_UNAVAILABLE":
      return "The metering store is unavailable; the request was not admitted (fail-closed).";
    case "MODEL_UNAVAILABLE":
      return "The model provider is temporarily unavailable.";
    default:
      return "The request did not complete successfully.";
  }
}

export function StatusExplanation({
  status,
  children,
}: {
  status: string;
  children?: ReactNode;
}) {
  return (
    <p className="text-sm leading-relaxed text-muted-foreground">
      {children ?? engineStatusExplanation(status)}
    </p>
  );
}

/**
 * Small labeled field used for Request ID / API Status / Engine Status —
 * keeps the "two vocabularies" visually distinct everywhere.
 */
export function MetaField({
  label,
  value,
  mono = true,
  className,
}: {
  label: string;
  value: ReactNode;
  mono?: boolean;
  className?: string;
}) {
  return (
    <div className={cn("min-w-0", className)}>
      <p className="text-[10px] font-medium uppercase tracking-[0.14em] text-muted-foreground/80">
        {label}
      </p>
      <p
        className={cn(
          "mt-0.5 truncate text-sm",
          mono && "font-mono",
          "text-foreground/90",
        )}
        title={typeof value === "string" ? value : undefined}
      >
        {value}
      </p>
    </div>
  );
}
