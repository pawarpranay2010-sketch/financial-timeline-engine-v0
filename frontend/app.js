/* Stage 2 frontend — talks to the FastAPI backend at /api/v1/*. */
"use strict";

const $ = (sel) => document.querySelector(sel);

const fmt = new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 });
const money = (v) => (v == null ? "—" : fmt.format(Number(v)));

async function api(path, options) {
  const res = await fetch(`/api/v1${path}`, options);
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail || `HTTP ${res.status}`);
  return body;
}

/* ---------------- health pill ---------------- */
async function refreshHealth() {
  const pill = $("#health-pill");
  try {
    const h = await api("/health");
    pill.classList.remove("bad");
    pill.classList.add("ok");
    const db = h.database && h.database.reachable ? "db" : "db:down";
    pill.lastChild.textContent = ` healthy · ${db} · v${h.version}`;
  } catch (e) {
    pill.classList.remove("ok");
    pill.classList.add("bad");
    pill.lastChild.textContent = " offline";
  }
}

/* ---------------- analyzer ---------------- */
async function runAnalysis(ticker, goal) {
  const result = $("#analyze-result");
  const errBox = $("#analyze-error");
  const btn = $("#analyze-btn");
  errBox.hidden = true;
  btn.disabled = true;
  btn.textContent = "Running…";
  try {
    const r = await api("/intelligence/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ticker, goal, max_iterations: 3 }),
    });
    $("#term-state").textContent = r.terminal_state || "UNKNOWN";
    $("#term-state").className = "terminal-state " + (r.terminal_state === "COMPLETE" ? "complete" : "blocked");
    $("#result-meta").textContent =
      `${r.evidence_count} evidence · ${r.resolved_count} resolved · ${r.iterations_used} iterations`;
    $("#summary-text").textContent = r.summary_text || "(no summary)";

    const factsEl = $("#facts");
    factsEl.innerHTML = "";
    (r.resolved_facts || []).forEach((f) => {
      const card = document.createElement("div");
      card.className = "fact-card";
      card.innerHTML = `
        <div class="metric">${escapeHtml(f.metric_name || f.metric || "metric")}</div>
        <div class="value">${escapeHtml(formatFactValue(f))}</div>
        <div class="meta">${escapeHtml(f.fiscal_period || f.reporting_period || "")} ·
          ${escapeHtml(f.currency_code || "")} · ${escapeHtml(f.unit || "")}</div>
        <span class="badge">tier ${f.source_tier ?? 1} · ${escapeHtml(f.source || "source")}</span>`;
      factsEl.appendChild(card);
    });
    result.hidden = false;
  } catch (e) {
    errBox.hidden = false;
    errBox.textContent = e.message;
    result.hidden = true;
  } finally {
    btn.disabled = false;
    btn.textContent = "Run analysis";
  }
}

function formatFactValue(f) {
  const v = f.value;
  if (v == null) return "—";
  if (f.scale) return `${fmt.format(Number(v))} ${escapeHtml(f.scale)}`;
  return fmt.format(Number(v));
}

/* ---------------- market snapshot ---------------- */
async function loadMarket(ticker) {
  const grid = $("#market-result");
  const errBox = $("#market-error");
  errBox.hidden = true;
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
      card("Price", price.price != null ? money(price.price) : "—", `high ${money(price.high_price)} · low ${money(price.low_price)}`),
      card("Volume", price.volume != null ? fmt.format(price.volume) : "—", price.trading_date || ""),
      card("Latest news", news[0] ? truncate(news[0].headline, 60) : "no articles", news[0] ? (news[0].source || "") : ""),
    ];
    grid.innerHTML = cards.join("");
    grid.hidden = false;

    if (!r.success && r.error) {
      errBox.hidden = false;
      errBox.textContent = r.error;
    }
  } catch (e) {
    errBox.hidden = false;
    errBox.textContent = e.message;
    grid.hidden = true;
  }
}

function card(k, v, sub) {
  const vEsc = escapeHtml(v);
  return `<div class="metric-card">
    <div class="k">${escapeHtml(k)}</div>
    <div class="v">${vEsc}</div>
    ${sub ? `<div class="sub">${escapeHtml(sub)}</div>` : ""}
  </div>`;
}

/* ---------------- system status ---------------- */
async function loadStatus() {
  const el = $("#status-result");
  try {
    const h = await api("/health");
    const db = h.database || {};
    const redis = h.redis || {};
    const prov = h.providers || {};

    const rows = [
      statusCard("API", "ok", `v${h.version} · stage ${h.stage}`, true),
      statusCard("PostgreSQL", db.reachable ? "ok" : db.configured ? "bad" : "no",
        db.reachable ? "SELECT 1 ok" : db.error || "not configured", db.reachable),
      statusCard("Redis", redis.configured ? "ok" : "no",
        redis.configured ? "configured" : "disabled (optional)", redis.configured),
    ];

    for (const [name, info] of Object.entries(prov.ai || {})) {
      rows.push(statusCard(`AI · ${name}`, info.key_configured ? "ok" : "no",
        info.key_configured ? "key set" : "no key", info.key_configured));
    }
    for (const [name, info] of Object.entries(prov.financial || {})) {
      rows.push(statusCard(`Data · ${name}`, info.key_configured ? "ok" : "no",
        info.key_configured ? "key set" : "no key", info.key_configured));
    }

    el.innerHTML = rows.join("");
  } catch (e) {
    el.innerHTML = `<div class="error-box">${escapeHtml(e.message)}</div>`;
  }
}

function statusCard(name, state, detail, ok) {
  const dotCls = state === "ok" ? "ok" : state === "bad" ? "bad" : "no";
  const label = state === "ok" ? "ok" : state === "bad" ? "down" : "off";
  return `<div class="status-card">
    <div class="name"><span class="dot ${dotCls}"></span>${escapeHtml(name)} <span style="margin-left:auto">${label}</span></div>
    <div class="detail">${escapeHtml(detail)}</div>
  </div>`;
}

/* ---------------- helpers ---------------- */
function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function truncate(s, n) {
  return s.length > n ? s.slice(0, n - 1) + "…" : s;
}

/* ---------------- wire up ---------------- */
document.addEventListener("DOMContentLoaded", () => {
  refreshHealth();
  loadStatus();

  $("#analyze-form").addEventListener("submit", (e) => {
    e.preventDefault();
    runAnalysis($("#ticker").value.trim(), $("#goal").value.trim());
  });

  $("#market-form").addEventListener("submit", (e) => {
    e.preventDefault();
    loadMarket($("#mkt-ticker").value.trim());
  });
  $("#refresh-market").addEventListener("click", () => loadMarket($("#mkt-ticker").value.trim()));

  $("#refresh-status").addEventListener("click", () => {
    refreshHealth();
    loadStatus();
  });

  setInterval(refreshHealth, 30000);
});
