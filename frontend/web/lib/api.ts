/**
 * Platrixa API client — a THIN boundary over the existing FastAPI backend.
 *
 * This file must NEVER:
 *   - parse transactions
 *   - decide debit/credit
 *   - validate, ground, or "fix" results
 *   - reinterpret or upgrade verification status
 *   - fabricate financial conclusions when the API returned none
 *
 * It renders only what the API actually returned. All financial truth is
 * decided server-side by the deterministic authorities.
 *
 * Endpoint configuration (single place):
 *   NEXT_PUBLIC_PLATRIXA_API_BASE — absolute FastAPI base URL.
 *   Unset (default) → same-origin "" — works when the Next.js app is
 *   proxied behind the FastAPI host (the repository's documented
 *   deployment shape) or when a dev proxy forwards /api and /v1.
 *   No backend URL is invented anywhere else.
 */

import type {
  ApiErrorEnvelope,
  CapabilitiesResponse,
  DeveloperDocumentProcessResponse,
  KernelProcessResponse,
} from "./types";

export const API_BASE = (process.env.NEXT_PUBLIC_PLATRIXA_API_BASE ?? "").replace(/\/+$/, "");

export class ApiRequestError extends Error {
  readonly code: string;
  readonly httpStatus: number;
  readonly apiStatus?: string;
  readonly retryable?: boolean;

  constructor(message: string, code: string, httpStatus: number, apiStatus?: string, retryable?: boolean) {
    super(message);
    this.name = "ApiRequestError";
    this.code = code;
    this.httpStatus = httpStatus;
    this.apiStatus = apiStatus;
    this.retryable = retryable;
  }
}

async function parseError(response: Response): Promise<never> {
  let code = `HTTP_${response.status}`;
  let message = `Request failed with HTTP ${response.status}`;
  let apiStatus: string | undefined;
  let retryable: boolean | undefined;
  try {
    const body = (await response.json()) as Partial<ApiErrorEnvelope> & { detail?: string };
    if (body?.error?.code) {
      code = body.error.code;
      message = body.error.message || message;
      apiStatus = body.error.api_status;
      retryable = body.error.retryable;
    } else if (typeof body?.detail === "string") {
      message = body.detail;
    }
  } catch {
    // non-JSON error body — keep the HTTP defaults
  }
  throw new ApiRequestError(message, code, response.status, apiStatus, retryable);
}

/** Process one financial input through the Kernel (POST /api/v1/kernel/process). */
export async function processTransaction(rawInput: string, signal?: AbortSignal): Promise<KernelProcessResponse> {
  const response = await fetch(`${API_BASE}/api/v1/kernel/process`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ raw_input: rawInput }),
    signal,
  });
  if (!response.ok) await parseError(response);
  return (await response.json()) as KernelProcessResponse;
}

/** Capability discovery (GET /v1/capabilities) — read-only registry adapter. */
export async function fetchCapabilities(signal?: AbortSignal): Promise<CapabilitiesResponse> {
  const response = await fetch(`${API_BASE}/v1/capabilities`, { signal });
  if (!response.ok) await parseError(response);
  return (await response.json()) as CapabilitiesResponse;
}

/**
 * Document processing (POST /v1/process/document) — text OR one PDF/image
 * file, exactly as the endpoint accepts (multipart field `document`).
 * The file is forwarded verbatim; this client never extracts or interprets
 * document content — that is the backend document-understanding layer's job.
 */
export async function processDocument(
  input: { text?: string; file?: File },
  signal?: AbortSignal,
): Promise<DeveloperDocumentProcessResponse> {
  const form = new FormData();
  if (input.file) form.append("document", input.file);
  if (input.text) form.append("raw_input", input.text);
  const response = await fetch(`${API_BASE}/v1/process/document`, {
    method: "POST",
    body: form,
    signal,
  });
  if (!response.ok) await parseError(response);
  return (await response.json()) as DeveloperDocumentProcessResponse;
}

/** Liveness probe (GET /v1/health) — touches nothing server-side. */
export async function fetchHealth(signal?: AbortSignal): Promise<boolean> {
  try {
    const response = await fetch(`${API_BASE}/v1/health`, { signal });
    return response.ok;
  } catch {
    return false;
  }
}
