"""
Platrixa — Phase 7G: UI Boundary Integration Test
scripts/fte_fyjc_58_ui_boundary_test.py

Proves the browser frontend (frontend/index.html + frontend/app.js) is a
thin UI/client boundary and that ALL accounting reasoning stays behind
the Cloudflare proxy → FastAPI → Kernel chain.

Evidence produced (no browser automation dependency, no new frameworks):

  STATIC (source-level, Python):
    S1  app.js routes to exactly /api/v1/kernel/process and /api/v1/health
    S2  exactly two fetch call sites, both via the single API_BASE constant
    S3  no hardcoded backend hosts (only the documented example placeholder)
    S4  no model/provider/inference access of any kind
    S5  no direct database/persistence access of any kind
    S6  no accounting decision logic (golden rules, classify, totals, gates)
    S7  busy state disables the action control (no duplicate submissions)
    S8  no retry/timeout loop around the transaction fetch
        (the only setInterval is the health-pill refresh)
    S9  status display map covers exactly the Kernel terminal taxonomy
    S10 Cloudflare Pages Function: catch-all /api/* → API_BACKEND_URL,
        single upstream fetch, no retry, fail-fast 502 when unconfigured
    S11 security: no tokens, keys, database URLs, or secrets in frontend/

  DYNAMIC (app.js executed in a Node vm sandbox with DOM+fetch stubs):
    D1  one user action → exactly ONE fetch, POST, verbatim raw_input body
    D2  while the request is in flight the button AND input are disabled
    D3  a second action while busy does NOT issue a second request
    D4  network failure → error card, result card hidden, no retry fetch
    D5  HTTP 500/502/503/422 → error card, result card hidden, no retry
    D6  malformed 200 body (no status) → error card, never fake success
    D7  VERIFIED body → status badge, journal rows, trust note, next action
        rendered strictly from returned fields
    D8  MODEL_UNAVAILABLE body → unavailable state, NO accounting fabricated
    D9  failed responses never render a fake accounting result

Run:
    python3 scripts/fte_fyjc_58_ui_boundary_test.py
"""

from __future__ import annotations

import json
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"

CHECKS: list[bool] = []


def check(label: str, condition: bool) -> bool:
    CHECKS.append(bool(condition))
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}")
    return bool(condition)


def section(title: str) -> None:
    print(f"\n--- {title} ---")


APP_JS = (FRONTEND / "app.js").read_text()
INDEX_HTML = (FRONTEND / "index.html").read_text()
PROXY_JS = (FRONTEND / "functions" / "api" / "[[path]].js").read_text()

# ---------------------------------------------------------------------------
# Static evidence
# ---------------------------------------------------------------------------


def static_checks() -> None:
    section("S1: API routes (single source of truth)")
    check(
        "ENDPOINT is exactly /api/v1/kernel/process",
        'const ENDPOINT = "/api/v1/kernel/process";' in APP_JS,
    )
    check(
        "HEALTH_ENDPOINT is exactly /api/v1/health",
        'const HEALTH_ENDPOINT = "/api/v1/health";' in APP_JS,
    )
    check(
        "index.html references the same endpoint in documentation only",
        "/api/v1/kernel/process" in INDEX_HTML
        and "<form" in INDEX_HTML
        and "action=" not in INDEX_HTML.split("<form", 1)[1][:80],
    )

    section("S2: fetch call sites")
    fetch_sites = [ln for ln in APP_JS.splitlines() if "fetch(" in ln and "//" not in ln.split("fetch(")[0]]
    check("exactly two fetch call sites (process + health)", len(fetch_sites) == 2)
    check(
        "both fetches route through the API_BASE constant",
        all("API_BASE}" in ln for ln in fetch_sites),
    )

    section("S3: no invented backend hosts")
    urls = re.findall(r"https?://[^\"'\s)]+", APP_JS + INDEX_HTML)
    benign = {u for u in urls if "your-fastapi-host" in u or "www.w3.org" in u}
    check("only documented placeholder/example URLs present", set(urls) == benign)

    section("S4: model access audit")
    model_tokens = [
        "huggingface", "hf_", "gradio_client", "transformers", "torch",
        "peft", "spaces.GPU", "InferenceClient", "model_provider",
        "interpret_core", "hf.space",
    ]
    lowered = (APP_JS + INDEX_HTML).lower()
    hits = [t for t in model_tokens if t in lowered]
    check("no model/HF/inference access tokens in frontend", not hits)

    section("S5: persistence/database access audit")
    # Direct-access tokens only. `persisted` / `persistence_error` are API
    # contract fields the UI is REQUIRED to consume (audit category B), so
    # they are checked as contract reads below, not as violations.
    db_tokens = [
        "postgres", "postgresql", "database_url", "sqlalchemy", "sqlite",
        "psycopg", "sessionmaker", "new persistence", ".persist(",
        "supabase", "prisma",
    ]
    hits_db = [t for t in db_tokens if t in lowered]
    check("no direct database/persistence access in frontend", not hits_db)
    check(
        "persisted/persistence_error consumed strictly as API contract fields (category B)",
        "r.persisted === true" in APP_JS
        and "r.persistence_error" in APP_JS
        and "persist(" not in APP_JS,
    )

    section("S6: accounting decision logic audit")
    logic_tokens = [
        "golden", "debit_total", "credit_total", "def classify",
        "groundinggate", "validator", "trialbalance", "trial_balance",
        "ledger =", "posting", "account_rule", ".reduce(", "+= amount",
        "amount +", "totaldebit", "totalcredit",
    ]
    hits_logic = [t for t in logic_tokens if t in lowered]
    check("no accounting decision engine in frontend", not hits_logic)
    # Every occurrence must be a read of the API-provided flag
    # ("accounting.journal_balanced"), never an assignment or arithmetic.
    jb_reads = re.findall(r"journal_balanced", APP_JS)
    jb_owned = re.findall(r"accounting\.journal_balanced", APP_JS)
    jb_assign = re.findall(r"journal_balanced\s*=[^=]", APP_JS)
    jb_arith = re.findall(r"journal_balanced\s*[+\-*/]", APP_JS)
    check(
        "balanced note only MIRRORS the API-provided journal_balanced flag (never computed)",
        'typeof accounting.journal_balanced === "boolean"' in APP_JS
        and len(jb_reads) == len(jb_owned)
        and not jb_assign
        and not jb_arith,
    )

    section("S7: busy state / duplicate protection")
    check(
        "process button disabled while busy",
        "btn.disabled = busy" in APP_JS,
    )
    check(
        "transaction input disabled while busy",
        "input.disabled = busy" in APP_JS,
    )

    section("S8: no retry loop around the transaction fetch")
    check(
        "no setTimeout/setInterval wrapping processTransaction",
        "setInterval(processTransaction" not in APP_JS
        and "setTimeout(processTransaction" not in APP_JS
        and "retry" not in APP_JS.replace("#retry-btn", "").replace("retry-btn", "")
        .replace("retry.addEventListener", "").replace("retry)", "").replace("const retry", ""),
    )
    check(
        "only periodic call is the 30s health-pill refresh",
        APP_JS.count("setInterval") == 1 and "refreshConnection, 30000" in APP_JS,
    )

    section("S9: Kernel terminal taxonomy preserved in display map")
    expected_statuses = {
        "VERIFIED", "REVIEW_REQUIRED", "BLOCKED", "VALIDATION_FAILED",
        "GROUNDING_FAILED", "FORBIDDEN_OUTPUT", "UNSUPPORTED_TRANSACTION",
        "MODEL_UNAVAILABLE",
    }
    map_keys = set(re.findall(r"^  ([A-Z_]+): \{", APP_JS, re.M))
    check("STATUS_DISPLAY covers exactly the Kernel taxonomy", map_keys == expected_statuses)

    section("S10: Cloudflare Pages Function proxy")
    check("proxy is a catch-all onRequest handler", "export async function onRequest" in PROXY_JS)
    check("proxy reads API_BACKEND_URL env only (no invented URL)", "API_BACKEND_URL" in PROXY_JS)
    check("proxy fails fast with 502 when backend unconfigured", "status: 502" in PROXY_JS)
    check("proxy performs exactly one upstream fetch", PROXY_JS.count("fetch(") == 1)
    check("proxy contains no retry logic", "retry" not in PROXY_JS.lower())
    check(
        "proxy preserves method/body/status verbatim",
        "context.request.method" in PROXY_JS
        and "arrayBuffer" in PROXY_JS
        and "status: upstream.status" in PROXY_JS,
    )

    section("S11: security scan")
    secret_patterns = [
        r"hf_[A-Za-z0-9]{20,}", r"postgres(ql)?://[^\s\"']+",
        r"(api[_-]?key|secret|token)\s*[:=]\s*[\"'][^\"']{8,}",
        r"sk-[A-Za-z0-9]{20,}",
    ]
    found = []
    for pat in secret_patterns:
        for m in re.finditer(pat, APP_JS + INDEX_HTML + PROXY_JS, re.I):
            found.append(pat)
    check("no tokens/keys/database URLs in frontend sources", not found)
    check(
        "only configuration input is window.PLATRIXA_API_BASE (documented)",
        APP_JS.count("PLATRIXA_API_BASE") >= 1 and "process.env" not in APP_JS,
    )


# ---------------------------------------------------------------------------
# Dynamic evidence: run app.js in a Node vm sandbox
# ---------------------------------------------------------------------------

JS_HARNESS = r"""
const fs = require("fs");
const vm = require("vm");

const APP_PATH = process.env.APP_JS;
const src = fs.readFileSync(APP_PATH, "utf8");

function makeEnv() {
  const elements = new Map();
  function el(sel) {
    const m = /^#([\w-]+)$/.exec(sel);
    const id = m ? m[1] : sel;
    if (!elements.has(id)) {
      elements.set(id, {
        id, hidden: false, disabled: false, textContent: "", innerHTML: "",
        className: "", value: "",
        focus() {}, click() {},
        insertAdjacentHTML(_pos, html) { this.innerHTML += html; },
        querySelector() { return null; },
        listeners: {},
        addEventListener(type, fn) { this.listeners[type] = fn; },
      });
    }
    return elements.get(id);
  }
  const state = { fetches: [], domReady: null };
  const documentStub = {
    querySelector(sel) { return el(sel); },
    querySelectorAll() { return []; },
    addEventListener(type, fn) { if (type === "DOMContentLoaded") state.domReady = fn; },
  };
  const windowStub = { PLATRIXA_API_BASE: "", setInterval() { return 0; } };
  let fetchImpl = null;
  const sandbox = {
    document: documentStub,
    window: windowStub,
    console: { log() {}, warn() {}, error() {} },
    fetch(url, opts) {
      state.fetches.push({ url, opts: opts || null });
      return fetchImpl(url, opts);
    },
  };
  vm.createContext(sandbox);
  vm.runInContext(src, sandbox, { filename: "app.js" });
  if (state.domReady) state.domReady();
  const form = el("#txn-form");
  return {
    el,
    state,
    setFetcher(fn) { fetchImpl = fn; },
    setInput(text) { el("#txn-input").value = text; },
    submit() {
      // Browser model: a disabled submit control cannot fire submit events.
      const btn = el("#process-btn");
      if (btn.disabled) return false;
      if (form.listeners.submit) form.listeners.submit({ preventDefault() {} });
      return true;
    },
  };
}

function jsonResponse(status, body) {
  return { ok: status >= 200 && status < 300, status, json: async () => body };
}

// Only transaction-path fetches count for the duplication/flow proofs; the
// 30s health-pill fetch is separate UI liveness traffic, not user actions.
function procFetches(env) {
  return env.state.fetches.filter((f) => f.url && f.url.includes("/kernel/process"));
}
function findProc(env) {
  return procFetches(env)[0] || null;
}

function snapshot(env) {
  const g = (s) => env.el(s);
  return {
    resultHidden: g("#result-card").hidden,
    errorHidden: g("#error-card").hidden,
    loadingHidden: g("#loading-card").hidden,
    badge: g("#status-badge").textContent,
    badgeClass: g("#status-badge").className,
    trust: g("#trust-note").textContent,
    next: g("#next-action").textContent,
    journalRows: (g("#journal-body").innerHTML.match(/<tr>/g) || []).length,
    journalSectionHidden: g("#journal-section").hidden,
    understoodHidden: g("#understood-section").hidden,
    reviewHidden: g("#review-section").hidden,
    errorMessage: g("#error-message").textContent,
  };
}

const INPUT = "Purchased furniture for cash ₹15,000";
const out = { scenarios: {}, failures: [] };
function note(cond, label) { if (!cond) out.failures.push(label); }

// --- D1/D2/D3: one action → one fetch; busy disables; no duplicate -------
{
  const env = makeEnv();
  let pending;
  env.setFetcher((url, opts) => {
    // capture busy state DURING flight
    env.duringBtnDisabled = env.el("#process-btn").disabled;
    env.duringInputDisabled = env.el("#txn-input").disabled;
    pending = new Promise((resolve) => setTimeout(() => resolve(jsonResponse(200, {
      request_id: "req-1",
      status: "VERIFIED",
      status_label: "Verified",
      success: true,
      next_action: "Try another transaction.",
      issues: [],
      grounding_issues: [],
      interpretation: {
        transaction_type: "PURCHASE",
        parties: [{ name: "raj", role: "seller" }],
        amounts: [{ value: "25000", currency: "INR" }],
        payment_method: "CASH",
        references: [],
        ambiguities: [],
      },
      accounting: {
        status: "VERIFIED",
        journal_balanced: true,
        journal: { narration: "Purchased furniture.", calculation_records: [] },
        debit_lines: [{ account: "Furniture", amount: 25000, side: "debit" }],
        credit_lines: [{ account: "Cash", amount: 25000, side: "credit" }],
      },
      persisted: true,
      persistence_error: null,
    })), 5));
    return pending;
  });
  env.setInput(INPUT);
  const first = env.submit();
  const second = env.submit(); // must be refused (button disabled)
  await pending;
  // Let the app.js render continuation (res.json() → renderResult) flush
  // before snapshotting; otherwise the DOM state is read one microtask early.
  await new Promise((r) => setTimeout(r, 10));
  const snap = snapshot(env);
  const proc = findProc(env);
  out.scenarios.verified = {
    firstSubmitted: first,
    secondRefused: second === false,
    fetchCount: procFetches(env).length,
    duringBtnDisabled: env.duringBtnDisabled,
    duringInputDisabled: env.duringInputDisabled,
    url: proc ? proc.url : null,
    method: proc && proc.opts ? proc.opts.method : null,
    body:
      proc && proc.opts && typeof proc.opts.body === "string"
        ? JSON.parse(proc.opts.body)
        : null,
    snap,
  };
}

// --- D4: network failure -------------------------------------------------
{
  const env = makeEnv();
  env.setFetcher(() => Promise.reject(new Error("offline")));
  env.setInput(INPUT);
  env.submit();
  await new Promise((r) => setTimeout(r, 5));
  const snap = snapshot(env);
  out.scenarios.networkError = { fetchCount: procFetches(env).length, snap };
}

// --- D5: HTTP 500 / 502 / 503 / 422 --------------------------------------
for (const code of [500, 502, 503, 422]) {
  const env = makeEnv();
  env.setFetcher(() => Promise.resolve(jsonResponse(code, { detail: "boom" })));
  env.setInput(INPUT);
  env.submit();
  await new Promise((r) => setTimeout(r, 5));
  const snap = snapshot(env);
  out.scenarios["http" + code] = { fetchCount: procFetches(env).length, snap };
}

// --- D6: malformed 200 body (no status) ----------------------------------
{
  const env = makeEnv();
  env.setFetcher(() => Promise.resolve(jsonResponse(200, { unexpected: true })));
  env.setInput(INPUT);
  env.submit();
  await new Promise((r) => setTimeout(r, 5));
  const snap = snapshot(env);
  out.scenarios.malformed = { fetchCount: procFetches(env).length, snap };
}

// --- D8: MODEL_UNAVAILABLE ------------------------------------------------
{
  const env = makeEnv();
  env.setFetcher(() => Promise.resolve(jsonResponse(503, {
    status: "MODEL_UNAVAILABLE",
    status_label: "Temporarily unavailable",
    success: false,
    issues: ["remote model unavailable"],
    grounding_issues: [],
    interpretation: null,
    accounting: null,
    persisted: false,
    persistence_error: null,
  })));
  env.setInput(INPUT);
  env.submit();
  await new Promise((r) => setTimeout(r, 5));
  const snap = snapshot(env);
  out.scenarios.modelUnavailable = { fetchCount: procFetches(env).length, snap };
}

process.stdout.write(JSON.stringify(out));
"""

_HARNESS_WRAPPER = (
    "(async () => {\n" + JS_HARNESS + "\n})().catch((e) => {"
    "process.stderr.write(String(e && e.stack || e)); process.exit(1); });\n"
)


def dynamic_checks() -> None:
    section("D1–D9: app.js executed in a Node vm sandbox (DOM + fetch stubs)")

    import os
    import tempfile

    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
        f.write(_HARNESS_WRAPPER)
        harness_path = f.name
    try:
        env = dict(os.environ)
        env["APP_JS"] = str(FRONTEND / "app.js")
        proc = subprocess.run(
            ["node", harness_path],
            capture_output=True,
            text=True,
            cwd=str(ROOT),
            timeout=60,
            env=env,
        )
    finally:
        pathlib.Path(harness_path).unlink(missing_ok=True)

    if proc.returncode != 0:
        check(f"node sandbox executed app.js cleanly (failed: {proc.stderr[:300]})", False)
        return

    data = json.loads(proc.stdout.strip())
    sc = data["scenarios"]
    for failure in data.get("failures", []):
        print(f"      harness failure: {failure}")

    # D1
    v = sc["verified"]
    check("one user action → exactly one fetch (D1)", v["fetchCount"] == 1)
    check("fetch URL is /api/v1/kernel/process (D1)", v["url"] == "/api/v1/kernel/process")
    check("fetch method is POST (D1)", v["method"] == "POST")
    check(
        "body is {raw_input} verbatim, nothing pre-parsed (D1)",
        v["body"] == {"raw_input": "Purchased furniture for cash ₹15,000"},
    )

    # D2
    check("button disabled while request in flight (D2)", v["duringBtnDisabled"] is True)
    check("input disabled while request in flight (D2)", v["duringInputDisabled"] is True)

    # D3
    check("second action while busy refused — no duplicate request (D3)", v["secondRefused"] and v["fetchCount"] == 1)

    # D7 (VERIFIED rendering)
    s = v["snap"]
    check(
        "VERIFIED renders badge + status label (D7)",
        "Verified" in s["badge"] and "ok" in s["badgeClass"],
    )
    check("VERIFIED renders journal rows from returned accounting only (D7)", s["journalRows"] == 2)
    check("VERIFIED trust note mirrors persisted=true (D7)", "recorded" in s["trust"].lower())
    check("VERIFIED shows next action (D7)", "Try another transaction." in s["next"])
    check("VERIFIED shows result card, hides error card (D7)", not s["resultHidden"] and s["errorHidden"])
    check("VERIFIED renders understood facts (D7)", not s["understoodHidden"])

    # D8
    m = sc["modelUnavailable"]
    check(
        "MODEL_UNAVAILABLE shows unavailable state, no journal fabricated (D8)",
        "unavailable" in m["snap"]["badge"].lower()
        and m["snap"]["journalSectionHidden"]
        and m["snap"]["journalRows"] == 0,
    )

    # D4/D5/D6/D9 — every failure path: error card, no result, no fake rows
    for name in ("networkError", "http500", "http502", "http503", "http422", "malformed"):
        r = sc[name]
        s = r["snap"]
        ok = (
            r["fetchCount"] == 1
            and s["errorHidden"] is False
            and s["resultHidden"] is True
            and s["journalRows"] == 0
            and s["trust"] == ""
        )
        check(f"{name}: error card shown, no result, no fake accounting (D4/D5/D6/D9)", ok)

    # D5 detail: transport messages classify without inventing statuses
    check(
        "HTTP 503 surfaces the temporarily-unavailable message (D5)",
        "temporarily unavailable" in sc["http503"]["snap"]["errorMessage"].lower(),
    )
    check(
        "HTTP 502 surfaces the unreachable-backend message (D5)",
        "unreachable" in sc["http502"]["snap"]["errorMessage"].lower()
        or "not configured" in sc["http502"]["snap"]["errorMessage"].lower(),
    )


def main() -> int:
    print("=" * 70)
    print("PLATRIXA — PHASE 7G: UI BOUNDARY INTEGRATION TEST")
    print("=" * 70)
    static_checks()
    dynamic_checks()
    total = len(CHECKS)
    passed = sum(1 for c in CHECKS if c)
    failed = total - passed
    print("\n" + "=" * 70)
    print(f"RESULT: {'PASS' if failed == 0 else 'FAIL'} — {passed}/{total} checks passed")
    if failed:
        print(f"  ({failed} failed)")
    print("=" * 70)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
