/**
 * Server-only management gateway assertion (owner dashboard).
 *
 * This is the SAME canonical message the operator ingress signs
 * (frontend/functions/operator/[[path]].js) and the backend verifies
 * (backend/auth/api_keys.py):
 *
 *   issuer|audience|tenant_id|method|path|query_string|content_type|body_hash|timestamp|nonce
 *
 * It runs in a Node route handler, so the signing secret never reaches the
 * browser. Nothing here is imported by a client component.
 */

import crypto from "node:crypto";

export const MANAGEMENT_AUDIENCE = "platrixa-management";

export interface AssertionFields {
  secret: string;
  issuer: string;
  tenantId: string;
  method: string;
  path: string;
  queryString: string;
  contentType: string;
  body: Uint8Array;
}

export function managementAssertionHeaders(fields: AssertionFields): Record<string, string> {
  const timestamp = String(Math.floor(Date.now() / 1000));
  const nonce = crypto.randomUUID();
  const bodyHash = crypto.createHash("sha256").update(fields.body).digest("hex");
  const canonical = [
    fields.issuer,
    MANAGEMENT_AUDIENCE,
    fields.tenantId,
    fields.method,
    fields.path,
    fields.queryString,
    fields.contentType,
    bodyHash,
    timestamp,
    nonce,
  ].join("|");
  const signature = crypto
    .createHmac("sha256", fields.secret)
    .update(canonical)
    .digest("hex");

  return {
    "x-platrixa-assertion-signature": signature,
    "x-platrixa-assertion-issuer": fields.issuer,
    "x-platrixa-assertion-audience": MANAGEMENT_AUDIENCE,
    "x-platrixa-assertion-tenant-id": fields.tenantId,
    "x-platrixa-assertion-method": fields.method,
    "x-platrixa-assertion-path": fields.path,
    "x-platrixa-assertion-query-string": fields.queryString,
    "x-platrixa-assertion-content-type": fields.contentType,
    "x-platrixa-assertion-body-hash": bodyHash,
    "x-platrixa-assertion-timestamp": timestamp,
    "x-platrixa-assertion-nonce": nonce,
  };
}
