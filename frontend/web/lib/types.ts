/**
 * Types mirroring the EXISTING backend API contracts.
 *
 * Source of truth:
 *   - POST /api/v1/kernel/process → api/schemas.py:KernelProcessResponse
 *   - GET  /v1/capabilities       → api/schemas.py:DeveloperCapabilitiesResponse
 *
 * These are transport projections only. No financial semantics live here:
 * `status` is decided by the backend's deterministic authorities and the
 * frontend must render it verbatim, never reinterpret or upgrade it.
 */

export type EngineStatus =
  | "VERIFIED"
  | "REVIEW_REQUIRED"
  | "BLOCKED"
  | "VALIDATION_FAILED"
  | "GROUNDING_FAILED"
  | "FORBIDDEN_OUTPUT"
  | "MODEL_UNAVAILABLE"
  | "UNSUPPORTED_TRANSACTION"
  | (string & {});

export interface AmountInterpretation {
  value?: string;
  currency?: string;
  source?: string;
  [key: string]: unknown;
}

/** The 18-field candidate contract, as projected by the backend. */
export interface InterpretationCandidate {
  transaction_type?: string;
  transaction_type_enum?: string;
  parties?: string[];
  amounts?: AmountInterpretation[];
  payment_method?: string;
  payment_method_enum?: string;
  references?: unknown[];
  ambiguities?: string[];
  ambiguity_flags?: string[];
  suggested_status?: string;
  field_confidences?: unknown[];
  overall_confidence?: string;
  safety_flags?: string[];
  scope_flags?: string[];
  grounding?: { all_fields_explicitly_grounded?: boolean; inferred_fields?: string[] };
  [key: string]: unknown;
}

export interface AccountingResult {
  status?: string;
  journal_entries?: Array<{ debit?: string; credit?: string; amount?: number | string }>;
  debit_lines?: Array<{ account?: string; amount?: number | string; [key: string]: unknown }>;
  credit_lines?: Array<{ account?: string; amount?: number | string; [key: string]: unknown }>;
  narration?: string;
  [key: string]: unknown;
}

/** POST /api/v1/kernel/process response (KernelProcessResponse). */
export interface KernelProcessResponse {
  request_id?: string | null;
  status: EngineStatus;
  status_label?: string;
  success: boolean;
  next_action?: string | null;
  issues: string[];
  grounding_issues: string[];
  verification_status?: string | null;
  interpretation: InterpretationCandidate | null;
  accounting: AccountingResult | null;
  persisted: boolean;
  persistence_error?: { kind: string; reason: string } | null;
}

/** GET /v1/capabilities response (DeveloperCapabilitiesResponse). */
export interface CapabilityEntry {
  capability_id: string;
  authority: string;
  canonical_name: string;
  supported_status: "SUPPORTED" | "PARTIAL" | "UNSUPPORTED" | "PLANNED" | (string & {});
  description: string;
  required_inputs: string[];
  deterministic_op: string;
  implementation_ref: string;
  test_ref: string;
  source_ref: string;
  jurisdiction: string;
  framework: string;
  version: string;
  limitations: string[];
}

export interface CapabilitiesResponse {
  api_version: string;
  request_id: string | null;
  api_status: string;
  api_status_label: string;
  retryable: boolean;
  engine_status: string | null;
  registry_summary: Record<string, Record<string, number>>;
  count: number;
  capabilities: CapabilityEntry[];
}

/** Structured /v1 error envelope (docs/HOSTED_API.md). */
export interface ApiErrorEnvelope {
  api_version: string;
  error: {
    code: string;
    message: string;
    request_id?: string;
    api_status?: string;
    api_status_label?: string;
    retryable?: boolean;
  };
}
