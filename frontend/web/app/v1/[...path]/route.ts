/**
 * Same-origin proxy for the backend's /v1/* developer namespace
 * (e.g. GET /v1/capabilities, GET /v1/health). Server-side only; see
 * lib/platrixa-proxy.ts for the security contract.
 */

import { platrixaProxy } from "@/lib/platrixa-proxy";

const handlers = platrixaProxy();

export const { GET, POST, PUT, PATCH, DELETE, HEAD, OPTIONS } = handlers;
export const dynamic = "force-dynamic";
export const runtime = "nodejs";
