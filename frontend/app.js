/* =====================================================================
   Financial Timeline Engine — Institutional Terminal (Phase 0–1)
   ---------------------------------------------------------------------
   Talks only to the existing FastAPI backend at /api/v1/*. No financial
   value is ever invented here: the grid renders (a) verified facts from
   /intelligence/analyze, (b) facts the backend already marks as derived,
   and (c) known-missing rows as "—" placeholders with an "Unanalyzed"
   status. DOM structure for the grid is created once; on data arrival
   only cell textContent/class update (zero layout shift).
   ===================================================================== */
"use strict";

const $ = (sel) => document.querySelector(sel);

const fmt = new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 });
const money = (v) => (v == null ? "—" : fmt.format(Number(v)));

const REDUCED_MOTION = window.matchMedia("(prefers-reduced-motion: reduce)");

const AI_PROVIDER_ORDER = [
  "google", "groq", "openrouter", "nvidia", "rapidapi",
  "sambanova", "github", "cerebras", "cohere",
];
const PROVIDER_LABELS = {
  google: "Google", groq: "Groq", openrouter: "OpenRouter", nvidia: "NVIDIA",
  rapidapi: "RapidAPI", sambanova: "SambaNova", github: "GitHub", cerebras: "Cerebras",
  cohere: "Cohere",
};

/* ------------------------------------------------------------------
   Canonical metric rows — presentation labels ONLY. No values.
   Unknown metrics simply stay "—" (known-missing) until the backend
   returns a verified fact for them.
   ------------------------------------------------------------------ */
const METRIC_ROWS = [
  { id: "revenue",            label: "Revenue" },
  { id: "net_income",         label: "Net Income" },
  { id: "eps",                label: "Earnings per Share (EPS)" },
  { id: "ebitda",             label: "EBITDA" },
  { id: "operating_income",   label: "Operating Income" },
  { id: "gross_profit",       label: "Gross Profit" },
  { id: "total_assets",       label: "Total Assets" },
  { id: "total_liabilities",  label: "Total Liabilities" },
  { id: "equity",             label: "Shareholders' Equity" },
  { id: "operating_cash_flow", label: "Operating Cash Flow" },
  { id: "free_cash_flow",     label: "Free Cash Flow" },
  { id: "roe",                label: "Return on Equity (ROE)" },
  { id: "roa",                label: "Return on Assets (ROA)" },
  { id: "current_ratio",      label: "Current Ratio" },
  { id: "debt_to_equity",     label: "Debt / Equity" },
  { id: "profit_margin",      label: "Profit Margin" },
  { id: "operating_margin",   label: "Operating Margin" },
  { id: "revenue_growth",     label: "Revenue Growth" },
];

/* ---------------------- API helpers ---------------------- */
async function api(path, options) {
  const res = await fetch(`/api/v1${path}`, options);
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail || `HTTP ${res.status}`);
  return body;
}

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function truncate(s, n) {
  s = String(s ?? "");
  return s.length > n ? s.slice(0, n - 1) + "…" : s;
}

function norm(s) {
  return String(s ?? "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, " ");
}

function isDerivedFact(f) {
  const src = norm(f.source) + " " + norm(f.origin) + " " + norm(f.method);
  return (
    f.derived === true ||
    /calculated|derived|formula|computed|ratio|margin|growth|\.calc/.test(src)
  );
}

/* Source-tier → human origin label (tiers from the frozen SourceResolver) */
function tierOrigin(tier) {
  if (tier === 3) return "Authoritative filing";
  if (tier === 2) return "Verified provider";
  if (tier === 1) return "Public source";
  return "Verified source";
}

/* ---------------------- Grid ---------------------- */
const gridState = {
  rows: new Map(),          // metric id -> { tr, cells, fact }
  selectedId: null,
};

function buildGrid() {
  const body = $("#grid-body");
  body.innerHTML = "";
  gridState.rows.clear();

  METRIC_ROWS.forEach((row) => {
    const tr = document.createElement("tr");
    tr.dataset.metric = row.id;

    const tdMetric = document.createElement("td");
    tdMetric.className = "grid-cell-metric";
    tdMetric.textContent = row.label;

    const tdValue = document.createElement("td");
    tdValue.className = "grid-cell-value missing";
    tdValue.textContent = "—";

    const tdPeriod = document.createElement("td");
    tdPeriod.className = "grid-cell-period";
    tdPeriod.textContent = "—";

    const tdSource = document.createElement("td");
    tdSource.className = "grid-cell-source";
    tdSource.textContent = "Not analyzed";

    const tdStatus = document.createElement("td");
    tdStatus.innerHTML =
      '<span class="status-chip missing"><span class="chip-dot"></span>Unanalyzed</span>';

    tr.append(tdMetric, tdValue, tdPeriod, tdSource, tdStatus);
    tr.addEventListener("click", () => selectMetric(row.id));

    body.appendChild(tr);
    gridState.rows.set(row.id, { tr, cells: { tdValue, tdPeriod, tdSource, tdStatus } });
  });
}

/* Match a resolved fact to the best canonical row by token overlap. */
function matchRow(fact) {
  const fTokens = new Set(norm(fact.metric_name || fact.metric).split(" "));
  let best = null, bestScore = 0;
  for (const row of METRIC_ROWS) {
    const rTokens = new Set(norm(row.id + " " + row.label).split(" "));
    let score = 0;
    fTokens.forEach((t) => {
      if (t && rTokens.has(t)) score += 1;
    });
    if (score > bestScore) { bestScore = score; best = row; }
  }
  return bestScore > 0 ? best : null;
}

function formatValue(f) {
  const v = f.value;
  if (v == null || v === "") return "—";
  let s = fmt.format(Number(v));
  if (f.scale) s += " " + String(f.scale);
  if (/percent|%/.test(norm(f.unit))) s += "%";
  return s;
}

/* Update the grid in place from resolved facts — textContent only. */
function renderGrid(facts) {
  const seen = new Set();
  for (const fact of facts || []) {
    const row = matchRow(fact);
    if (!row || seen.has(row.id)) continue;
    seen.add(row.id);
    const entry = gridState.rows.get(row.id);
    if (!entry) continue;
    entry.fact = fact;
    const { tdValue, tdPeriod, tdSource, tdStatus } = entry.cells;
    const value = formatValue(fact);
    const neg = Number(fact.value) < 0;
    tdValue.textContent = value;
    tdValue.className = "grid-cell-value " + (value === "—" ? "missing" : neg ? "neg" : "");
    tdValue.classList.add("cell-fade");
    tdPeriod.textContent = fact.fiscal_period || fact.reporting_period || fact.period || "—";
    tdPeriod.classList.add("cell-fade");
    const src = fact.source || "Verified source";
    tdSource.textContent = truncate(src, 40);
    tdSource.className = "grid-cell-source" + (isDerivedFact(fact) ? " derived" : "");
    tdSource.classList.add("cell-fade");
    if (isDerivedFact(fact)) {
      tdStatus.innerHTML = '<span class="status-chip derived"><span class="chip-dot"></span>Derived</span>';
    } else {
      tdStatus.innerHTML = '<span class="status-chip verified"><span class="chip-dot"></span>Verified</span>';
    }
    tdStatus.classList.add("cell-fade");
  }
  // Re-select the previously selected row so selection survives updates.
  if (gridState.selectedId) selectMetric(gridState.selectedId);
}

/* ---------------------- Provenance tray (anchored, 150ms crossfade) ---------------------- */
function setTray(html) {
  const body = $("#tray-body");
  const swap = () => {
    body.innerHTML = html;
    body.classList.remove("fading");
  };
  body.classList.add("fading");
  if (REDUCED_MOTION.matches) { swap(); return; }
  window.setTimeout(swap, 150);
}

function trayFactRow(key, value, primary = false) {
  const v = value && String(value).trim() ? value : "—";
  return `<div class="p-key">${escapeHtml(key)}</div>` +
    `<div class="p-val${primary ? " primary" : ""}" title="${escapeHtml(v)}">${escapeHtml(v)}</div>`;
}

function renderTray(entry, rowLabel) {
  const fact = entry.fact;
  if (!fact) {
    const html =
      `<div class="prov-grid">
         ${trayFactRow("Metric", rowLabel, true)}
         ${trayFactRow("Origin", "Not verified for the current analysis")}
         ${trayFactRow("Period", "—")}
         ${trayFactRow("Source", "No verified source")}
       </div>
       <p class="tray-empty" style="margin-top:8px">No verified value exists for this metric in the current evidence set. Broaden the analysis goal to attempt retrieval — the terminal will not estimate it.</p>`;
    $("#tray-status").textContent = "⚪ Unanalyzed";
    setTray(html);
    return;
  }

  const derived = isDerivedFact(fact);
  const origin = derived ? "Derived calculation" : tierOrigin(fact.source_tier);
  const location = [
    fact.page != null && fact.page !== "" ? `Page ${fact.page}` : "",
    fact.table_id ? `Table ${fact.table_id}` : "",
    fact.chunk_id ? `Chunk ${fact.chunk_id}` : "",
  ].filter(Boolean).join(" · ") || "—";
  const evidence = fact.evidence_text_anchor || fact.evidence_anchor || fact.context || fact.evidence;

  $("#tray-status").textContent = derived ? "🟡 Derived" : "🟢 Verified";

  let html =
    `<div class="prov-grid">
       ${trayFactRow("Metric", rowLabel, true)}
       ${trayFactRow("Value", formatValue(fact), true)}
       ${trayFactRow("Origin", origin)}
       ${trayFactRow("Period", fact.fiscal_period || fact.reporting_period || fact.period || "—")}
       ${trayFactRow("Source", fact.source || "Verified source")}
       ${trayFactRow("Location", location)}
       ${trayFactRow("Scale", [fact.scale, fact.unit].filter(Boolean).join(" · ") || "—")}
       ${trayFactRow("Currency", fact.currency_code || fact.currency || "—")}
     </div>`;

  if (evidence) {
    html +=
      `<details class="tray-evidence">
         <summary>View source fragment</summary>
         <pre>${escapeHtml(String(evidence))}</pre>
       </details>`;
  }
  setTray(html);
}

function selectMetric(id) {
  gridState.selectedId = id;
  const row = METRIC_ROWS.find((r) => r.id === id);
  gridState.rows.forEach((entry, key) => {
    entry.tr.classList.toggle("selected", key === id);
  });
  renderTray(gridState.rows.get(id), row ? row.label : id);
}

/* ---------------------- Co-Pilot / analysis ---------------------- */
let lastAnalysis = null;

async function runAnalysis(ticker, goal) {
  const btn = $("#analyze-btn");
  const errBox = $("#analyze-error");
  const statusEl = $("#grid-status");
  const capability = $("#capability-dots");

  errBox.hidden = true;
  btn.disabled = true;
  btn.textContent = "Analyzing…";
  capability.textContent = "● retrieving evidence";
  statusEl.textContent = "Retrieving verified evidence…";

  // Skeleton grid while the analysis runs (reserved row heights).
  const body = $("#grid-body");
  body.innerHTML = "";
  gridState.rows.clear();
  gridState.selectedId = null;
  METRIC_ROWS.forEach((row) => {
    const tr = document.createElement("tr");
    tr.className = "skeleton-row";
    tr.innerHTML =
      `<td><span class="skeleton-cell" style="width:70%"></span></td>` +
      `<td><span class="skeleton-cell" style="width:60%;margin-left:auto"></span></td>` +
      `<td><span class="skeleton-cell" style="width:50%"></span></td>` +
      `<td><span class="skeleton-cell" style="width:80%"></span></td>` +
      `<td><span class="skeleton-line"></span></td>`;
    body.appendChild(tr);
  });

  try {
    const r = await api("/intelligence/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ticker, goal, max_iterations: 3 }),
    });
    lastAnalysis = r;

    // Rebuild the stable grid and hydrate cells in place.
    buildGrid();
    renderGrid(r.resolved_facts);

    const complete = r.terminal_state === "COMPLETE";
    const stateEl = $("#intel-state");
    stateEl.textContent = complete ? "Analysis complete" : r.terminal_state === "BLOCKED" ? "Analysis blocked" : "Analysis incomplete";
    stateEl.className = "intel-state " + (complete ? "complete" : "blocked");

    $("#intel-goal").textContent = `${r.ticker} — ${r.goal}`;
    $("#intel-summary").textContent = r.summary_text || "(No summary returned by the pipeline.)";
    $("#intel-meta").textContent =
      `${r.resolved_count} verified facts · ${r.evidence_count} evidence items · ${r.iterations_used} retrieval passes`;
    renderIntelFacts(r.resolved_facts);

    // Grid toolbar context.
    $("#grid-ticker").textContent = r.ticker || ticker.toUpperCase();
    $("#grid-goal").textContent = r.goal || goal;
    $("#grid-status").textContent = complete
      ? `🟢 ${r.resolved_count} verified facts`
      : "🟡 Analysis blocked — data may be incomplete";

    // Co-Pilot rail: direct answer first, structured detail behind accordions.
    renderCopilot(r);

    // Capability indicator: dynamic, quiet.
    capability.textContent = `● ${r.resolved_count} facts indexed`;

    if (!complete) {
      errBox.hidden = false;
      errBox.innerHTML =
        `<div class="err-title">Analysis blocked</div>` +
        `<div class="err-detail">${escapeHtml(r.terminal_reason || "The pipeline could not verify sufficient evidence for this goal.")}</div>` +
        `<div class="err-actions"><button type="button" class="btn btn-ghost btn-sm" id="retry-analyze">Retry analysis</button></div>`;
      $("#retry-analyze").addEventListener("click", () => {
        runAnalysis($("#ticker").value.trim(), $("#goal").value.trim());
      });
    }
  } catch (e) {
    buildGrid(); // restore stable rows (known-missing) instead of a blank pane
    errBox.hidden = false;
    errBox.innerHTML =
      `<div class="err-title">Analysis temporarily unavailable</div>` +
      `<div class="err-detail">${escapeHtml(e.message)}</div>` +
      `<div class="err-actions"><button type="button" class="btn btn-ghost btn-sm" id="retry-analyze-2">Retry analysis</button></div>`;
    $("#retry-analyze-2").addEventListener("click", () => {
      runAnalysis($("#ticker").value.trim(), $("#goal").value.trim());
    });
    $("#grid-status").textContent = "⚠️ Analysis unavailable — verified data still shown below";
    capability.textContent = "○ retry";
  } finally {
    btn.disabled = false;
    btn.textContent = "Analyze";
  }
}

function renderIntelFacts(facts) {
  const el = $("#intel-facts");
  if (!facts || !facts.length) {
    el.innerHTML = '<p class="empty-note">No verified facts returned.</p>';
    return;
  }
  el.innerHTML = facts.map((f) => {
    const src = f.source || "Verified source";
    return `<div class="intel-fact">
      <div class="f-metric" title="${escapeHtml(f.metric_name || f.metric || "")}">${escapeHtml(f.metric_name || f.metric || "Metric")}</div>
      <div class="f-value">${escapeHtml(formatValue(f))}</div>
      <div class="f-period">${escapeHtml(f.fiscal_period || f.reporting_period || f.period || "—")}</div>
      <div class="f-source" title="${escapeHtml(src)}">${escapeHtml(truncate(src, 48))}</div>
    </div>`;
  }).join("");
}

function renderCopilot(r) {
  const el = $("#copilot-response");
  const facts = r.resolved_facts || [];
  const derived = facts.filter(isDerivedFact).length;
  const verified = facts.length - derived;

  let html = `<div class="copilot-answer">${escapeHtml(r.summary_text || "The pipeline returned no narrative summary.")}</div>`;
  html += `<div class="copilot-meta">Every figure verified against evidence · <strong>${r.resolved_count} verified facts</strong></div>`;
  html += `
    <details class="accordion">
      <summary>🟢 Verified facts <span class="acc-count">${verified}</span></summary>
      <div class="accordion-body">
        ${facts.filter((f) => !isDerivedFact(f)).map((f) =>
          `<span>${escapeHtml(f.metric_name || "Metric")}: ${escapeHtml(formatValue(f))} — ${escapeHtml(f.fiscal_period || f.period || "—")}</span>`
        ).join("") || "<span>None</span>"}
      </div>
    </details>
    <details class="accordion">
      <summary>🟡 Derived values <span class="acc-count">${derived}</span></summary>
      <div class="accordion-body">
        ${facts.filter(isDerivedFact).map((f) =>
          `<span>${escapeHtml(f.metric_name || "Metric")}: ${escapeHtml(formatValue(f))} — ${escapeHtml(f.source || "calculated")}</span>`
        ).join("") || "<span>None</span>"}
      </div>
    </details>
    <details class="accordion">
      <summary>⚪ Not retrieved <span class="acc-count">${Math.max(0, METRIC_ROWS.length - facts.length)}</span></summary>
      <div class="accordion-body"><span>Metrics in the grid with no verified value were not retrieved for this goal. Broaden the goal to attempt them — the terminal never estimates missing figures.</span></div>
    </details>`;
  el.innerHTML = html;
}

/* ---------------------- Left rail: market context ---------------------- */
async function loadMarket(ticker) {
  const stack = $("#market-result");
  const errBox = $("#market-error");
  errBox.hidden = true;
  stack.innerHTML = "";
  for (let i = 0; i < 4; i++) {
    stack.insertAdjacentHTML(
      "beforeend",
      '<div class="context-card"><div class="k">…</div><div class="v"><span class="skeleton-line"></span></div></div>'
    );
  }
  try {
    const r = await api(`/market/${encodeURIComponent(ticker)}`);
    const d = r.data || {};
    const profile = (d.company_profile && d.company_profile.data) || {};
    const price = (d.market_price && d.market_price.data) || {};
    const fin = (d.financials && d.financials.data) || {};
    const news = (d.news && d.news.data) || [];

    const cards = [
      card("Company", profile.company_name || ticker.toUpperCase(), profile.exchange || profile.sector || ""),
      card("Market cap", money(profile.market_cap), profile.currency || ""),
      card("Price", price.price != null ? money(price.price) : "—",
        `high ${money(price.high_price)} · low ${money(price.low_price)}`),
      card("Volume", price.volume != null ? fmt.format(price.volume) : "—", price.trading_date || ""),
      card("Latest news", news[0] ? truncate(news[0].headline, 54) : "no articles",
        news[0] ? (news[0].source || "") : ""),
    ];
    stack.innerHTML = cards.join("");
    $("#copilot-context").innerHTML =
      `<span class="ctx-company">${escapeHtml(profile.company_name || ticker.toUpperCase())}</span> · ${escapeHtml(ticker.toUpperCase())}`;

    if (!r.success && r.error) {
      errBox.hidden = false;
      errBox.textContent = r.error;
    }
  } catch (e) {
    stack.innerHTML = "";
    errBox.hidden = false;
    errBox.textContent = e.message;
  }
}

function card(k, v, sub) {
  return `<div class="context-card">
    <div class="k">${escapeHtml(k)}</div>
    <div class="v">${escapeHtml(v)}</div>
    ${sub ? `<div class="sub">${escapeHtml(sub)}</div>` : ""}
  </div>`;
}

/* ---------------------- Gateway pill + popover ---------------------- */
let healthLatencyMs = null;

async function refreshHealth() {
  const pill = $("#gateway-pill");
  const start = performance.now();
  try {
    const h = await api("/health");
    healthLatencyMs = Math.round(performance.now() - start);
    const dbOk = !!(h.database && h.database.reachable);
    const ai = (h.providers && h.providers.ai) || {};
    const active = AI_PROVIDER_ORDER.find((name) => ai[name] && ai[name].key_configured);

    pill.classList.remove("bad", "warn");
    pill.classList.add(dbOk ? "ok" : "warn");

    const providerLabel = active ? `🟢 ${PROVIDER_LABELS[active] || active}` : "⚪ no AI key";
    $("#gateway-label").textContent = `Gateway · ${providerLabel} · ${healthLatencyMs}ms`;
    renderPopover(h);
    return h;
  } catch (e) {
    pill.classList.remove("ok", "warn");
    pill.classList.add("bad");
    $("#gateway-label").textContent = "Gateway · offline";
    renderPopover(null);
    return null;
  }
}

function statusRow(name, state, detail) {
  const dot = state === "ok" ? "ok" : state === "bad" ? "bad" : "no";
  const label = state === "ok" ? "ok" : state === "bad" ? "down" : "off";
  return `<div class="popover-row"><span class="dot ${dot}"></span>
    <span class="popover-row-name">${escapeHtml(name)}</span>
    <span class="popover-row-state">${label}</span>
    <span class="popover-row-state" style="color:var(--muted)">${escapeHtml(detail)}</span>
  </div>`;
}

function renderPopover(h) {
  const body = $("#popover-status");
  const tech = $("#popover-technical");
  if (!h) {
    body.innerHTML = `<div class="popover-row"><span class="dot bad"></span><span class="popover-row-name">API</span><span class="popover-row-state">offline</span></div>`;
    tech.textContent = "Could not reach /api/v1/health.";
    return;
  }
  const db = h.database || {};
  const redis = h.redis || {};
  const ai = (h.providers && h.providers.ai) || {};
  const fin = (h.providers && h.providers.financial) || {};
  const dbState = db.reachable ? "ok" : db.configured ? "bad" : "no";
  const dbDetail = db.reachable ? "SELECT 1 ok" : db.error || "not configured";

  let rows = [
    statusRow("API", "ok", `v${h.version} · stage ${h.stage}`),
    statusRow("PostgreSQL", dbState, dbDetail),
    statusRow("Redis", redis.configured ? "ok" : "no", redis.configured ? "configured" : "optional"),
  ];
  for (const [name, info] of Object.entries(ai)) {
    rows.push(statusRow(`AI · ${PROVIDER_LABELS[name] || name}`,
      info.key_configured ? "ok" : "no", info.key_configured ? "key set" : "no key"));
  }
  for (const [name, info] of Object.entries(fin)) {
    rows.push(statusRow(`Data · ${name}`,
      info.key_configured ? "ok" : "no", info.key_configured ? "key set" : "no key"));
  }
  body.innerHTML = rows.join("");
  $("#popover-latency").textContent = healthLatencyMs != null ? `${healthLatencyMs}ms round-trip` : "";
  tech.textContent = JSON.stringify(
    {
      service: h.service,
      version: h.version,
      uptime_seconds: h.uptime_seconds,
      database: { configured: db.configured, reachable: db.reachable },
      redis: redis.configured,
      providers: { ai: Object.keys(ai), financial: Object.keys(fin) },
    },
    null, 2
  );
}

function togglePopover(open) {
  const pop = $("#gateway-popover");
  const pill = $("#gateway-pill");
  if (open) {
    pop.hidden = false;
    pill.setAttribute("aria-expanded", "true");
  } else {
    pop.hidden = true;
    pill.setAttribute("aria-expanded", "false");
  }
}

/* ---------------------- System tab ---------------------- */
async function loadStatus() {
  const el = $("#status-result");
  const h = await refreshHealth().catch(() => null);
  if (!h) {
    el.innerHTML = `<div class="error-box">System status unavailable.</div>`;
    return;
  }
  const db = h.database || {};
  const redis = h.redis || {};
  const prov = h.providers || {};

  const rows = [
    statusCard("API", "ok", `v${h.version} · stage ${h.stage}`),
    statusCard("PostgreSQL", db.reachable ? "ok" : db.configured ? "bad" : "no",
      db.reachable ? "SELECT 1 ok" : db.error || "not configured"),
    statusCard("Redis", redis.configured ? "ok" : "no",
      redis.configured ? "configured" : "disabled (optional)"),
  ];
  for (const [name, info] of Object.entries(prov.ai || {})) {
    rows.push(statusCard(`AI · ${PROVIDER_LABELS[name] || name}`, info.key_configured ? "ok" : "no",
      info.key_configured ? "key set" : "no key"));
  }
  for (const [name, info] of Object.entries(prov.financial || {})) {
    rows.push(statusCard(`Data · ${name}`, info.key_configured ? "ok" : "no",
      info.key_configured ? "key set" : "no key"));
  }
  el.innerHTML = rows.join("");
}

function statusCard(name, state, detail) {
  const dotCls = state === "ok" ? "ok" : state === "bad" ? "bad" : "no";
  const label = state === "ok" ? "ok" : state === "bad" ? "down" : "off";
  return `<div class="status-card">
    <div class="name"><span class="dot ${dotCls}"></span>${escapeHtml(name)}
      <span class="state-label">${label}</span></div>
    <div class="detail">${escapeHtml(detail)}</div>
  </div>`;
}

/* ---------------------- Tabs / rails ---------------------- */
const TABS = [
  { btn: "tab-grid", panel: "panel-grid" },
  { btn: "tab-intel", panel: "panel-intel" },
  { btn: "tab-system", panel: "panel-system" },
];

function activateTab(name) {
  TABS.forEach(({ btn, panel }) => {
    const b = $(`#${btn}`), p = $(`#${panel}`);
    const active = btn === name;
    b.classList.toggle("active", active);
    b.setAttribute("aria-selected", String(active));
    p.hidden = !active;
  });
}

function applyRailState() {
  const ws = $("#workspace");
  const leftOff = localStorage.getItem("fte.rail-left") === "off";
  const rightOff = localStorage.getItem("fte.rail-right") === "off";
  ws.classList.toggle("rail-left-off", leftOff);
  ws.classList.toggle("rail-right-off", rightOff);
  $("#rail-left-toggle").innerHTML = leftOff ? "»" : "«";
  $("#rail-left-toggle").title = leftOff ? "Expand source rail" : "Collapse source rail";
  $("#rail-right-toggle").innerHTML = rightOff ? "«" : "»";
  $("#rail-right-toggle").title = rightOff ? "Expand Co-Pilot rail" : "Collapse Co-Pilot rail";
}

/* ---------------------- Wire up ---------------------- */
document.addEventListener("DOMContentLoaded", () => {
  buildGrid();           // stable rows immediately (zero layout shift from the start)
  applyRailState();
  activateTab("tab-grid");

  refreshHealth();
  loadStatus();
  loadMarket($("#mkt-ticker").value.trim());

  // Gateway popover
  $("#gateway-pill").addEventListener("click", (e) => {
    e.stopPropagation();
    togglePopover($("#gateway-popover").hidden);
  });
  document.addEventListener("click", (e) => {
    const pop = $("#gateway-popover");
    if (!pop.hidden && !pop.contains(e.target) && !$("#gateway-pill").contains(e.target)) {
      togglePopover(false);
    }
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") togglePopover(false);
  });

  // Tabs
  TABS.forEach(({ btn }) => {
    $(`#${btn}`).addEventListener("click", () => activateTab(btn));
  });

  // Co-Pilot analysis
  $("#analyze-form").addEventListener("submit", (e) => {
    e.preventDefault();
    runAnalysis($("#ticker").value.trim(), $("#goal").value.trim());
  });

  // Market context
  $("#market-form").addEventListener("submit", (e) => {
    e.preventDefault();
    loadMarket($("#mkt-ticker").value.trim());
  });
  $("#refresh-market").addEventListener("click", () => loadMarket($("#mkt-ticker").value.trim()));

  // System
  $("#refresh-status").addEventListener("click", () => {
    refreshHealth();
    loadStatus();
  });

  // Rail collapse (user-driven only; never on data updates)
  $("#rail-left-toggle").addEventListener("click", () => {
    const cur = localStorage.getItem("fte.rail-left") === "off" ? "on" : "off";
    localStorage.setItem("fte.rail-left", cur);
    applyRailState();
  });
  $("#rail-right-toggle").addEventListener("click", () => {
    const cur = localStorage.getItem("fte.rail-right") === "off" ? "on" : "off";
    localStorage.setItem("fte.rail-right", cur);
    applyRailState();
  });

  // Quiet health re-poll; never touches the grid or tray.
  window.setInterval(() => refreshHealth(), 30000);
});
