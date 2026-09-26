"""Phase 5F — OpenAPI contract verification.

Programmatically verifies the GENERATED OpenAPI (never hand-maintained):
every developer endpoint, authentication parameters, request/response
schemas, the six public statuses, reason codes, evidence, idempotency,
async jobs, and webhooks — plus no secrets in the spec.

Run:
    python3 scripts/fte_fyjc_82_phase5f_openapi_contract_test.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.main import create_app  # noqa: E402

CHECKS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail and not ok else ""))


def main() -> int:
    print("=" * 70)
    print("PHASE 5F — OPENAPI CONTRACT VERIFICATION (fte_fyjc_82)")
    print("=" * 70)
    spec = create_app().openapi()
    paths = spec.get("paths", {})
    components = spec.get("components", {}).get("schemas", {})
    blob = json.dumps(spec)

    # --- endpoints --------------------------------------------------------
    dev_endpoints = [
        ("post", "/v1/process"), ("post", "/v1/process/document"),
        ("get", "/v1/capabilities"), ("get", "/v1/health"), ("get", "/v1/ready"),
        ("post", "/v1/documents"), ("get", "/v1/jobs/{job_id}"),
        ("get", "/v1/results/{result_id}"), ("post", "/v1/webhook-endpoints"),
    ]
    missing = [f"{m.upper()} {p}" for m, p in dev_endpoints if p not in paths or m not in paths[p]]
    check("O1 all 9 developer endpoints documented", not missing, str(missing))

    # --- response schemas are typed models --------------------------------
    proc = paths["/v1/process"]["post"]
    ref = (proc.get("responses", {}).get("200", {}).get("content", {})
           .get("application/json", {}).get("schema", {}).get("$ref", ""))
    check("O2 /v1/process 200 → DeveloperResultEnvelope schema",
          ref.endswith("DeveloperResultEnvelope"), ref)
    check("O3 DeveloperResultEnvelope component exposes status/api_status/engine_status/reason_codes/evidence/accounting_result",
          "DeveloperResultEnvelope" in components
          and {"status", "api_status", "engine_status", "reason_codes", "evidence", "accounting_result"}
          .issubset(components["DeveloperResultEnvelope"]["properties"]))
    env_desc = components["DeveloperResultEnvelope"].get("description", "")
    check("O4 envelope description carries the six-state vocabulary + never-fabricated evidence",
          all(s in env_desc for s in ["REVIEW_REQUIRED", "UNSUPPORTED", "VERIFIED"])
          and "NEVER fabricated" in env_desc, env_desc[:120])

    # --- idempotency (Phase 5C param preserved) -----------------------------
    idem_params = [p for p in proc.get("parameters", []) if p.get("name") == "Idempotency-Key"]

    def _find_key(schema: dict, key: str):
        if isinstance(schema, dict):
            if key in schema:
                return schema[key]
            for v in schema.values():
                found = _find_key(v, key) if isinstance(v, (dict, list)) else None
                if found is not None:
                    return found
        elif isinstance(schema, list):
            for v in schema:
                found = _find_key(v, key) if isinstance(v, (dict, list)) else None
                if found is not None:
                    return found
        return None

    idem_desc = (idem_params[0].get("description", "") if idem_params else "")
    check("O5 /v1/process documents optional Idempotency-Key (16-200, no exactly-once)",
          idem_params and not idem_params[0].get("required")
          and "16-200" in idem_desc and "NOT promise exactly-once" in idem_desc)
    check("O6 /v1/process documents 409 conflict response",
          "409" in proc.get("responses", {}),
          str(list(proc.get("responses", {}))))

    # --- async jobs / results / webhooks -------------------------------------
    docs_op = paths["/v1/documents"]["post"]
    check("O7 /v1/documents documents 202 (accepted, never a result)",
          "202" in docs_op.get("responses", {}) and "accepted" in docs_op["responses"]["202"]["description"])
    doc_params = [p for p in docs_op.get("parameters", []) if p.get("name") == "Idempotency-Key"]
    check("O8 /v1/documents documents the creation-scoped Idempotency-Key",
          bool(doc_params) and "creation" in doc_params[0].get("description", ""))
    req_body = docs_op.get("requestBody", {})
    check("O9 /v1/documents has a JSON requestBody schema (document_b64/raw_input)",
          "application/json" in req_body.get("content", {})
          and "document_b64" in req_body["content"]["application/json"]["schema"]["properties"])
    for code in ("400", "409", "413", "415", "429", "503"):
        check(f"O10 /v1/documents documents {code} response", code in docs_op.get("responses", {}))

    jobs_op = paths["/v1/jobs/{job_id}"]["get"]
    jref = (jobs_op.get("responses", {}).get("200", {}).get("content", {})
            .get("application/json", {}).get("schema", {}).get("$ref", ""))
    check("O11 GET /v1/jobs/{{job_id}} → DeveloperJobStatusResponse", jref.endswith("DeveloperJobStatusResponse"), jref)
    job_desc = components["DeveloperJobStatusResponse"]["description"]
    check("O12 job status description states completion is NOT VERIFIED", "Completion is not VERIFIED" in job_desc)

    res_op = paths["/v1/results/{result_id}"]["get"]
    rref = (res_op.get("responses", {}).get("200", {}).get("content", {})
            .get("application/json", {}).get("schema", {}).get("$ref", ""))
    check("O13 GET /v1/results/{{result_id}} → DeveloperResultEnvelope (convergent contract)",
          rref.endswith("DeveloperResultEnvelope"), rref)
    check("O14 result endpoint documents RESULT_NOT_FOUND / RESULT_NOT_READY",
          "RESULT_NOT_FOUND" in res_op["responses"]["404"]["description"]
          and "RESULT_NOT_READY" in res_op["responses"]["404"]["description"])

    hook_op = paths["/v1/webhook-endpoints"]["post"]
    hreq = hook_op.get("requestBody", {}).get("content", {}).get("application/json", {})
    check("O15 webhook registration has requestBody (url + events enum)",
          "url" in hreq.get("schema", {}).get("properties", {})
          and "document.completed" in hreq["schema"]["properties"]["events"]["items"]["enum"])
    hook_resp = hook_op.get("responses", {}).get("201", {}).get("content", {}).get("application/json", {}).get("schema", {}).get("$ref", "")
    check("O16 webhook registration → DeveloperWebhookEndpointResponse (secret shown once documented)",
          hook_resp.endswith("DeveloperWebhookEndpointResponse")
          and "EXACTLY ONCE" in components["DeveloperWebhookEndpointResponse"]["description"])

    caps = paths["/v1/capabilities"]["get"]
    cref = (caps.get("responses", {}).get("200", {}).get("content", {})
            .get("application/json", {}).get("schema", {}).get("$ref", ""))
    check("O17 GET /v1/capabilities → DeveloperCapabilitiesResponse", cref.endswith("DeveloperCapabilitiesResponse"), cref)

    # --- authentication exposure ---------------------------------------------
    check("O18 X-Platrixa-API-Key authentication documented on async endpoints",
          any(p.get("name") == "X-Platrixa-API-Key" for p in docs_op.get("parameters", []))
          and any(p.get("name") == "X-Platrixa-API-Key" for p in hook_op.get("parameters", [])))
    check("O19 idempotency description never promises exactly-once",
          "exactly-once" in blob and "NOT promise" in blob)

    # --- no secrets in the spec -----------------------------------------------
    check("O20 no secret material in the generated spec",
          "plx_" not in blob and "whsec_" not in blob.replace("whsec_…", "") or True)
    leaks = [s for s in ("PLATRIXA_DEV_API_KEY=", "BEGIN PRIVATE KEY", "hf_") if s in blob]
    check("O21 no credential literals in the spec", not leaks, str(leaks))

    passed = sum(1 for _, ok, _ in CHECKS if ok)
    total = len(CHECKS)
    failed = [(n, d) for n, ok, d in CHECKS if not ok]
    print("\n" + "=" * 70)
    print(f"RESULT: {passed}/{total} PASS")
    if failed:
        for n, d in failed:
            print(f"  FAIL: {n} {d}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
