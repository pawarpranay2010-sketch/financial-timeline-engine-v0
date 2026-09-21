/* =====================================================================
   Platrixa — Financial Semantics Assistant (Phase 7G)
   ---------------------------------------------------------------------
   THIN CLIENT. All accounting reasoning happens in the backend Kernel:

     transaction input → POST /api/v1/kernel/process (Phase 7F, verbatim)
                   → response rendered here, defensively

   This file must NEVER:
     - parse transactions
     - decide debit/credit
     - generate or "fix" journal entries
     - reinterpret verification status
     - invent accounting conclusions when the API returned none

   It renders only what the API actually returned.
   ===================================================================== */
"use strict";

/* ------------------------------------------------------------------
   API base configuration — ONE place, never hardcoded per call.

   Default: same-origin (""), matching the FastAPI deployment where the
   frontend is served by the backend. On Cloudflare Pages (separate
   origin), set window.PLATRIXA_API_BASE before app.js loads, e.g.:

     <script>window.PLATRIXA_API_BASE = "https://your-fastapi-host";</script>

   No backend URL is invented anywhere in this file.
   ------------------------------------------------------------------ */
const API_BASE = String(
  (typeof window !== "undefined" && window.PLATRIXA_API_BASE) || ""
).replace(/\/+$/, "");

const ENDPOINT = "/api/v1/kernel/process";
const HEALTH_ENDPOINT = "/api/v1/health";

const $ = (sel) => document.querySelector(sel);

/* ------------------------------------------------------------------
   Kernel terminal status → display configuration.
   The Kernel taxonomy is preserved verbatim; nothing is collapsed.
   ------------------------------------------------------------------ */
const STATUS_DISPLAY = {
  VERIFIED: {
    cls: "ok",
    icon: "✅",
    explain: "Platrixa understood the transaction and verified the accounting result.",
    heading: "Verified",
  },
  REVIEW_REQUIRED: {
    cls: "review",
    icon: "🟡",
    explain: "Platrixa understood most of this, but needs one thing clarified before it can finish.",
    heading: "Needs clarification",
  },
  BLOCKED: {
    cls: "blocked",
    icon: "🛑",
    explain: "Platrixa stopped because it could not safely determine the accounting meaning.",
    heading: "Stopped for safety",
  },
  VALIDATION_FAILED: {
    cls: "blocked",
    icon: "⚠️",
    explain: "The interpretation could not be checked against the required structure.",
    heading: "Could not validate",
  },
  GROUNDING_FAILED: {
    cls: "blocked",
    icon: "⚠️",
    explain: "Some details in the interpretation are not actually supported by the words you entered.",
    heading: "Not supported by your input",
  },
  FORBIDDEN_OUTPUT: {
    cls: "blocked",
    icon: "⚠️",
    explain: "The response was rejected before it reached the accounting engine.",
    heading: "Response rejected",
  },
  UNSUPPORTED_TRANSACTION: {
    cls: "blocked",
    icon: "🤔",
    explain: "Platrixa does not support this type of transaction yet.",
    heading: "Not supported",
  },
  MODEL_UNAVAILABLE: {
    cls: "offline",
    icon: "🛠️",
    explain: "The understanding engine is temporarily unavailable. Please try again shortly.",
    heading: "Temporarily unavailable",
  },
};

/* ------------------------------------------------------------------
   Helpers
   ------------------------------------------------------------------ */
function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[c]);
}

function safeText(el, value, fallback = "") {
  if (el) el.textContent = value == null || value === "" ? fallback : String(value);
}

function show(el) { if (el) el.hidden = false; }
function hide(el) { if (el) el.hidden = true; }

function setBusy(busy) {
  const btn = $("#process-btn");
  if (btn) {
    btn.disabled = busy;
    btn.textContent = busy ? "Checking…" : "Check transaction";
  }
  const input = $("#txn-input");
  if (input) input.disabled = busy;
  if (busy) { hide($("#empty-card")); hide($("#error-card")); hide($("#result-card")); show($("#loading-card")); }
  else { hide($("#loading-card")); }
}

/* ------------------------------------------------------------------
   API call — sends raw_input VERBATIM. Nothing is parsed or pre-
   interpreted in the frontend.
   ------------------------------------------------------------------ */
async function processTransaction(rawInput) {
  const res = await fetch(`${API_BASE}${ENDPOINT}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ raw_input: rawInput }),
  });

  let body = null;
  try { body = await res.json(); } catch { /* non-JSON response */ }

  if (!res.ok && !body || (body && !body.status)) {
    // Transport-level failure (422 request-shape, 5xx, offline): build a
    // synthetic display state WITHOUT inventing accounting conclusions.
    const err = new Error(
      (body && (body.detail || body.next_action)) ||
        `The service returned an unexpected response (${res.status}).`
    );
    err.httpStatus = res.status;
    err.apiBody = body;
    throw err;
  }
  return body;
}

/* ------------------------------------------------------------------
   Rendering — only fields actually present in the response
   ------------------------------------------------------------------ */
function renderStatus(status, statusLabel) {
  const cfg = STATUS_DISPLAY[status] || {
    cls: "review",
    icon: "❔",
    explain: "Platrixa returned an unrecognized status.",
    heading: status,
  };
  const badge = $("#status-badge");
  if (badge) {
    badge.className = `status-badge ${cfg.cls}`;
    badge.textContent = `${cfg.icon} ${statusLabel || cfg.heading || status}`;
  }
  safeText($("#status-explain"), cfg.explain);
  return cfg;
}

function renderUnderstood(interpretation) {
  if (!interpretation || typeof interpretation !== "object") return;
  const list = $("#understood-list");
  if (!list) return;

  const facts = [];

  const type = interpretation.transaction_type;
  if (type) facts.push(["Transaction type", String(type).replace(/_/g, " ").toLowerCase()]);

  const parties = Array.isArray(interpretation.parties)
    ? interpretation.parties
        .map((p) => (typeof p === "string" ? p : p && p.name))
        .filter(Boolean)
        .join(", ")
    : "";
  if (parties) facts.push(["Parties", parties]);

  const amounts = Array.isArray(interpretation.amounts)
    ? interpretation.amounts
        .map((a) => {
          if (a == null) return null;
          if (typeof a === "string" || typeof a === "number") return String(a);
          return a.value || a.original || a.display || null;
        })
        .filter(Boolean)
        .join(", ")
    : "";
  if (amounts) facts.push(["Amounts", `Rs. ${amounts}`]);

  const payment = interpretation.payment_method;
  if (payment && payment !== "UNKNOWN") {
    facts.push(["Payment method", String(payment).replace(/_/g, " ").toLowerCase()]);
  }

  const references = Array.isArray(interpretation.references) ? interpretation.references : [];
  if (references.length) facts.push(["References", references.map(String).join(", ")]);

  const ambiguities = Array.isArray(interpretation.ambiguities) ? interpretation.ambiguities : [];
  if (ambiguities.length) facts.push(["Ambiguities", ambiguities.map(String).join(", ")]);

  if (!facts.length) return;

  list.innerHTML = facts
    .map(([k, v]) => `<div class="fact"><dt>${escapeHtml(k)}</dt><dd>${escapeHtml(v)}</dd></div>`)
    .join("");
  show($("#understood-section"));
}

function renderIssues(issues, groundingIssues) {
  const items = [];
  for (const i of Array.isArray(issues) ? issues : []) if (i) items.push(String(i));
  for (const g of Array.isArray(groundingIssues) ? groundingIssues : []) if (g) items.push(String(g));
  if (!items.length) return;

  const list = $("#review-list");
  if (!list) return;
  list.innerHTML = items.map((t) => `<li>${escapeHtml(t)}</li>`).join("");
  show($("#review-section"));
}

function amountCell(v) {
  if (v == null || v === "") return "";
  const n = Number(v);
  return Number.isFinite(n) ? n.toLocaleString("en-IN") : String(v);
}

function renderAccounting(accounting) {
  if (!accounting || typeof accounting !== "object") return;

  const debits = Array.isArray(accounting.debit_lines) ? accounting.debit_lines : [];
  const credits = Array.isArray(accounting.credit_lines) ? accounting.credit_lines : [];
  if (!debits.length && !credits.length) return; // nothing returned → nothing fabricated

  const narration =
    (accounting.journal && accounting.journal.narration) || accounting.narration || "";
  safeText($("#journal-narration"), narration);

  const rows = [];
  for (const d of debits) {
    if (!d || !d.account) continue;
    rows.push({ account: d.account, debit: d.amount, credit: null });
  }
  for (const c of credits) {
    if (!c || !c.account) continue;
    rows.push({ account: c.account, debit: null, credit: c.amount });
  }
  if (!rows.length) return;

  const tbody = $("#journal-body");
  if (tbody) {
    tbody.innerHTML = rows
      .map(
        (r) =>
          `<tr><td>${escapeHtml(r.account)}</td><td class="num">${
            r.debit != null ? amountCell(r.debit) : ""
          }</td><td class="num">${r.credit != null ? amountCell(r.credit) : ""}</td></tr>`
      )
      .join("");
  }

  // Balanced check — presentation only, mirrors the accounting flag if provided.
  if (typeof accounting.journal_balanced === "boolean") {
    const note = accounting.journal_balanced
      ? "✅ Journal is balanced (total debits = total credits)"
      : "⚠️ Journal does not balance — see the issues above";
    const extra = $("#accounting-extra");
    if (extra) {
      extra.insertAdjacentHTML(
        "beforeend",
        `<p class="balance-note ${accounting.journal_balanced ? "ok" : "bad"}">${note}</p>`
      );
    }
  }

  // Calculation records, if returned.
  const calcs = (accounting.journal && accounting.journal.calculation_records) || [];
  if (Array.isArray(calcs) && calcs.length) {
    const extra = $("#accounting-extra");
    if (extra) {
      extra.insertAdjacentHTML(
        "beforeend",
        `<h4 class="extra-title">Calculations shown by Platrixa</h4><ul class="calc-list">` +
          calcs
            .map((c) => {
              if (!c || typeof c !== "object") return "";
              const label = c.label || c.id || "calculation";
              return `<li><span>${escapeHtml(label)}</span><span class="mono">${escapeHtml(
                c.result != null ? String(c.result) : ""
              )}</span></li>`;
            })
            .join("") +
          `</ul>`
      );
    }
  }

  show($("#journal-section"));
  const detail = $("#journal-detail");
  if (detail && detail.querySelector(".balance-note, .calc-list")) show(detail);
}

function renderResult(r) {
  hide($("#empty-card"));
  hide($("#error-card"));

  const status = r.status || "";
  const cfg = renderStatus(status, r.status_label);

  // Trust footnote — mirrors persisted flag only, never invents wording.
  const trust = $("#trust-note");
  if (trust) {
    if (r.persisted === true) {
      trust.textContent = "This result has been checked and recorded by Platrixa.";
    } else if (r.persistence_error) {
      trust.textContent =
        "Note: the accounting result above is valid, but recording it in the database failed.";
    } else {
      trust.textContent = "";
    }
  }

  renderUnderstood(r.interpretation);
  renderIssues(r.issues, r.grounding_issues);
  renderAccounting(r.accounting);

  const next = r.next_action || (cfg && cfg.explain) || "";
  if (next) {
    safeText($("#next-action"), next);
    show($("#next-section"));
  }

  show($("#result-card"));
}

/* ------------------------------------------------------------------
   Transport-error classification — distinguishes real causes instead of
   labeling every failure an internet problem. Only the HTTP status is
   interpreted; no accounting conclusions are ever invented here.
   ------------------------------------------------------------------ */
function transportMessage(httpStatus) {
  if (httpStatus === 405) {
    return (
      "API routing or method misconfiguration (HTTP 405): the request reached " +
      "a host that does not accept POST at /api/v1/kernel/process. The Platrixa " +
      "backend is not serving this address — check the API base configuration."
    );
  }
  if (httpStatus === 404) {
    return "API endpoint not found at this address (HTTP 404). Check the API base configuration.";
  }
  if (httpStatus === 502) {
    return "The Platrixa backend is not configured or unreachable (HTTP 502).";
  }
  if (httpStatus === 503) {
    return "The Platrixa backend is temporarily unavailable (HTTP 503). Try again shortly.";
  }
  if (httpStatus === 422) {
    return "The request was rejected as invalid (HTTP 422). Review the transaction text.";
  }
  if (httpStatus >= 500) {
    return `The Platrixa backend returned an error (HTTP ${httpStatus}).`;
  }
  return null; // network-level failure → generic connection message
}

function renderTransportError(message, httpStatus) {
  hide($("#empty-card"));
  hide($("#result-card"));
  const specific = transportMessage(httpStatus);
  safeText(
    $("#error-message"),
    specific || message || "Could not reach the Platrixa service."
  );
  show($("#error-card"));
}

/* ------------------------------------------------------------------
   Submit flow
   ------------------------------------------------------------------ */
async function onSubmit(event) {
  event.preventDefault();
  const input = $("#txn-input");
  const raw = (input && input.value || "").trim();
  if (!raw) return;

  // Reset any previous render so stale results never linger.
  ["#understood-section", "#review-section", "#journal-section", "#next-section"].forEach((s) => hide($(s)));
  const extra = $("#accounting-extra");
  if (extra) extra.innerHTML = "";
  const narration = $("#journal-narration");
  if (narration) narration.textContent = "";
  const tbody = $("#journal-body");
  if (tbody) tbody.innerHTML = "";
  const trust = $("#trust-note");
  if (trust) trust.textContent = "";

  setBusy(true);
  try {
    const result = await processTransaction(raw);
    renderResult(result);
  } catch (err) {
    renderTransportError(
      err && err.message
        ? `Could not reach the Platrixa service. (${err.message})`
        : "Could not reach the Platrixa service.",
      err && err.httpStatus
    );
  } finally {
    setBusy(false);
  }
}

/* ------------------------------------------------------------------
   API connection pill (liveness only — no provider/key details shown)
   ------------------------------------------------------------------ */
async function refreshConnection() {
  const pill = $("#api-pill");
  const label = $("#api-label");
  if (!pill || !label) return;
  try {
    const res = await fetch(`${API_BASE}${HEALTH_ENDPOINT}`, { method: "GET" });
    if (res.ok) {
      pill.className = "gateway-pill ok";
      label.textContent = "Connected";
    } else {
      pill.className = "gateway-pill warn";
      label.textContent = "Service issue";
    }
  } catch {
    pill.className = "gateway-pill bad";
    label.textContent = "Offline";
  }
}

/* ------------------------------------------------------------------
   Wire up
   ------------------------------------------------------------------ */
document.addEventListener("DOMContentLoaded", () => {
  const form = $("#txn-form");
  if (form) form.addEventListener("submit", onSubmit);

  const retry = $("#retry-btn");
  if (retry) retry.addEventListener("click", () => { hide($("#error-card")); $("#txn-input").focus(); });

  document.querySelectorAll(".example-chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      const input = $("#txn-input");
      if (input) {
        input.value = chip.dataset.example || "";
        input.focus();
      }
    });
  });

  refreshConnection();
  window.setInterval(refreshConnection, 30000);
});
