"""
Financial Timeline Engine
AI Financial Assistant — conversational layer

This module is a *conversational UI/orchestration layer* on top of the
existing frozen intelligence stack. It deliberately does NOT reimplement
any intelligence: it composes the existing components:

    * FinancialCalculator.safe_calculate_financial_ratios() /
      safe_calculate_cagr_ratios()   -> gated metric calculations
    * CalculationSafetyGate           -> verification / currency / scale /
                                         period gating (never bypassed)
    * AgenticRAGOrchestrator (via api.services.run_analysis)
                                      -> company / ticker Q&A
    * call_ai_with_fallback           -> general conversational answers
    * ingestion.extraction facts      -> document-backed Q&A

Behavioral guarantees
---------------------
* NEVER fabricates.  Missing evidence produces an explicit BLOCKED /
  not-verified answer, never a guess or a model-knowledge substitution.
* NEVER bypasses CalculationSafetyGate for calculations.
* Provenance (source, period, scale, unit, tier) is attached to every
  fact-based answer.
* Conversation context is bounded (recent turns + a compact topic), so
  prompts never grow without limit.
* Provider keys never appear in any answer, metadata, or log.

This module imports no Streamlit, so it is unit-testable outside the UI.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

# ---------------------------------------------------------------------------
# Frozen component imports (safe: no Streamlit dependency)
# ---------------------------------------------------------------------------
from backend.financial_calculator import (
    safe_calculate_financial_ratios,
    safe_calculate_cagr_ratios,
)
from backend.intelligence.calculation_safety_gate import CalculationSafetyGate
from core.constants import GROUNDING_RULE

# ---------------------------------------------------------------------------
# Intent vocabulary
# ---------------------------------------------------------------------------

# Question keyword -> extractor metric_id (canonical fact name).
METRIC_KEYWORDS: Dict[str, List[str]] = {
    "Revenue": ["revenue", "sales", "net sales", "turnover"],
    "NetIncome": ["net income", "net profit", "net earnings", "profit after tax", "profit"],
    "EPS": ["earnings per share", "eps"],
    "EBITDA": ["ebitda"],
    "OperatingIncome": ["operating income", "operating profit"],
    "GrossProfit": ["gross profit"],
    "TotalAssets": ["total assets", "assets"],
    "TotalLiabilities": ["total liabilities", "liabilities"],
    "ShareholdersEquity": ["equity", "shareholders equity", "shareholder equity", "book value"],
    "TotalDebt": ["total debt", "debt"],
    "CurrentAssets": ["current assets"],
    "CurrentLiabilities": ["current liabilities"],
    "OperatingCashFlow": ["operating cash flow", "cash flow from operations"],
    "FreeCashFlow": ["free cash flow"],
    "CashAndEquivalents": ["cash and cash equivalents", "cash"],
}

# Calculation questions -> human label.  These are routed through the
# gated calculator (never hand-computed).
CALC_KEYWORDS: Dict[str, List[str]] = {
    "ROE": ["roe", "return on equity"],
    "ROA": ["roa", "return on assets"],
    "Current Ratio": ["current ratio"],
    "Debt to Equity": ["debt to equity", "debt/equity", "d/e"],
    "Profit Margin": ["profit margin", "net margin", "net profit margin", "margin"],
    "CAGR": ["cagr", "compound annual growth"],
    "change": ["change", "change in", "growth", "grew", "grow", "increase",
               "decrease", "compare", "comparison", "difference", "vs", "versus"],
}

# Extractor metric_id -> calculator display key (FinancialCalculator schema).
CALCULATOR_KEY_MAP: Dict[str, str] = {
    "Revenue": "Revenue",
    "NetIncome": "Net Profit",
    "TotalAssets": "Assets",
    "TotalLiabilities": "Liabilities",
    "ShareholdersEquity": "Equity",
    "TotalDebt": "Debt",
    "CurrentAssets": "Current Assets",
    "CurrentLiabilities": "Current Liabilities",
}

# Ratio calculation -> the calculator input keys it needs.
RATIO_INPUTS: Dict[str, List[str]] = {
    "ROE": ["Net Profit", "Equity"],
    "ROA": ["Net Profit", "Assets"],
    "Current Ratio": ["Current Assets", "Current Liabilities"],
    "Debt to Equity": ["Debt", "Equity"],
    "Profit Margin": ["Revenue", "Net Profit"],
}

# Company name -> ticker (small, well-known map; RAG is ticker-driven).
KNOWN_COMPANIES: Dict[str, str] = {
    "apple": "AAPL", "microsoft": "MSFT", "nvidia": "NVDA", "tesla": "TSLA",
    "amazon": "AMZN", "meta": "META", "google": "GOOGL", "alphabet": "GOOGL",
    "netflix": "NFLX", "jpmorgan": "JPM", "coca-cola": "KO", "nike": "NKE",
    "infosys": "INFY", "hdfc": "HDFCBANK", "reliance": "RELIANCE",
    "tata": "TATAMOTORS", "larsen": "LT", "sun pharma": "SUNPHARMA",
}

# Uppercase tokens that are never tickers.
_STOP_TICKERS = {
    "AI", "FY", "USD", "INR", "EUR", "GBP", "EPS", "ROE", "ROA", "EBITDA",
    "GDP", "API", "PDF", "SEC", "US", "UK", "CEO", "CFO", "Q1", "Q2", "Q3",
    "Q4", "I", "A", "R&D",
}

_TICKER_RE = re.compile(r"\b[A-Z]{2,5}\b")

# Pronoun / reference markers that trigger follow-up resolution.
_FOLLOW_UP_MARKERS = (
    "it", "that", "this", "the company", "the firm", "previous year",
    "last year", "prior year", "compared with", "versus", " vs ", "grow",
    "growth", "change", "why", "increase", "decrease", "this metric",
)

_ERROR_MARKERS = ("❌", "🔴", "⚠️")

DEFAULT_MAX_MESSAGES = 10
DEFAULT_MAX_CONTEXT_CHARS = 4000


# ---------------------------------------------------------------------------
# Conversation context (bounded)
# ---------------------------------------------------------------------------
@dataclass
class ChatTopic:
    """Compact, stale-safe summary of the last answered financial topic,
    used to resolve follow-up references ('it', 'that company', 'the
    previous year') without replaying the full conversation."""

    metric: str = ""
    ticker: str = ""
    periods: List[str] = field(default_factory=list)
    currency: str = ""
    answer: str = ""


class ChatContext:
    """Bounded conversation history.

    Keeps the last `max_messages` turns plus a compact ChatTopic.  The
    prompt sent to an LLM is capped at `max_context_chars`, so context
    can never grow without limit.
    """

    def __init__(
        self,
        max_messages: int = DEFAULT_MAX_MESSAGES,
        max_context_chars: int = DEFAULT_MAX_CONTEXT_CHARS,
    ) -> None:
        self.messages: List[Dict[str, Any]] = []
        self.topic: Optional[ChatTopic] = None
        self.max_messages = max_messages
        self.max_context_chars = max_context_chars

    # -- mutation ----------------------------------------------------------
    def add_user(self, content: str) -> None:
        self.messages.append({"role": "user", "content": content})
        self._trim()

    def add_assistant(self, content: str, metadata: Optional[Dict[str, Any]] = None) -> None:
        self.messages.append({
            "role": "assistant",
            "content": content,
            "metadata": metadata or {},
        })
        self._refresh_topic(metadata or {})
        self._trim()

    def _trim(self) -> None:
        if len(self.messages) > self.max_messages:
            excess = len(self.messages) - self.max_messages
            self.messages = self.messages[excess:]

    def _refresh_topic(self, metadata: Dict[str, Any]) -> None:
        topic = metadata.get("topic")
        if isinstance(topic, dict) and topic:
            self.topic = ChatTopic(
                metric=topic.get("metric", ""),
                ticker=topic.get("ticker", ""),
                periods=list(topic.get("periods", []) or []),
                currency=topic.get("currency", ""),
                answer=metadata.get("content", ""),
            )

    # -- read ---------------------------------------------------------------
    def to_state(self) -> Dict[str, Any]:
        return {
            "messages": self.messages,
            "topic": self.topic.__dict__ if self.topic else None,
        }

    @classmethod
    def from_state(cls, state: Optional[Dict[str, Any]], **kwargs: Any) -> "ChatContext":
        ctx = cls(**kwargs)
        if not state:
            return ctx
        for msg in state.get("messages", []) or []:
            ctx.messages.append(dict(msg))
        ctx._trim()
        t = state.get("topic")
        if t:
            ctx.topic = ChatTopic(**{k: v for k, v in t.items() if k in ChatTopic.__dataclass_fields__})
        return ctx

    def recent_turns(self, n: int = 4) -> List[Dict[str, Any]]:
        return self.messages[-n:]

    def to_prompt(self, n: int = 4) -> str:
        """Bounded, compact rendering of the recent conversation for an LLM."""
        lines: List[str] = []
        for msg in self.recent_turns(n):
            role = "User" if msg["role"] == "user" else "Assistant"
            content = str(msg.get("content", ""))
            lines.append(f"{role}: {content}")
        text = "\n".join(lines)
        if len(text) > self.max_context_chars:
            text = text[-self.max_context_chars:]
        return text

    def resolve_follow_up(self, question: str) -> str:
        """If the question refers to the previous topic ('it', 'that
        company', 'previous year'), prepend a compact resolved subject."""
        q_lower = question.lower()
        has_marker = any(m in q_lower for m in _FOLLOW_UP_MARKERS)
        if not has_marker or not self.topic:
            return question
        if self._has_own_subject(question):
            return question
        subject = self.topic.metric or "that metric"
        if self.topic.ticker:
            subject = f"{self.topic.ticker} {subject}".strip()
        periods = " / ".join(self.topic.periods[-2:]) if self.topic.periods else ""
        hint = f" ({periods})" if periods else ""
        return f"Regarding {subject}{hint}: {question}"

    @staticmethod
    def _has_own_subject(question: str) -> bool:
        """True when the question names its own subject (an explicit
        metric like 'revenue'/'net income', or a ticker/company).

        Deliberately does NOT treat calculation verbs ("change", "grow",
        "vs", "increase"...) as an own subject: a follow-up like "how much
        did it grow vs last year?" carries no subject of its own and must
        be resolved against the previous topic."""
        q = question.lower()
        for keywords in METRIC_KEYWORDS.values():
            for kw in keywords:
                if kw in q:
                    return True
        return bool(_TICKER_RE.search(question))


# ---------------------------------------------------------------------------
# Intent detection helpers
# ---------------------------------------------------------------------------
def detect_ticker(question: str) -> Optional[str]:
    """Best-effort ticker detection: known company names, then uppercase
    tokens (excluding stopwords).  Returns None when ambiguous."""
    q = question.strip()
    if not q:
        return None
    q_lower = q.lower()
    # Longest known-company name match first (e.g. "sun pharma" before "sun").
    for name in sorted(KNOWN_COMPANIES, key=len, reverse=True):
        if name in q_lower:
            return KNOWN_COMPANIES[name]
    tokens = _TICKER_RE.findall(q)
    for tok in tokens:
        if tok in _STOP_TICKERS:
            continue
        # Require 2+ repeated letters or a typical ticker shape to reduce
        # false positives on ordinary words like "NET", "APP".
        if len(tok) >= 3 and tok not in {"NET", "APP", "FY", "USD", "EPS"}:
            return tok
    return None


def detect_metric(question: str) -> Optional[str]:
    """Returns the extractor metric_id the question is about, or None."""
    q = question.lower()
    for metric_id, keywords in METRIC_KEYWORDS.items():
        for kw in keywords:
            if kw in q:
                return metric_id
    return None


def detect_calculation(question: str) -> Optional[str]:
    """Returns the calculation label (ROE, Current Ratio, CAGR, change…)
    the question asks for, or None."""
    q = question.lower()
    for label, keywords in CALC_KEYWORDS.items():
        for kw in keywords:
            if kw in q:
                return label
    return None


def _is_greeting(question: str) -> bool:
    q = question.strip().lower()
    return any(q.startswith(g) for g in ("hi", "hello", "hey", "help", "what can you do"))


# ---------------------------------------------------------------------------
# Fact shaping (extractor facts -> gate-compatible financial_data)
# ---------------------------------------------------------------------------
def _shape_fact(fact: Dict[str, Any], document_name: str = "") -> Dict[str, Any]:
    """Convert an extractor fact dict into the CalculationSafetyGate /
    FinancialCalculator financial_data entry shape.

    A fact is considered VERIFIED only when it carries a numeric value
    AND provenance (source + period).  Everything else stays PENDING so
    the gate blocks it — the assistant never computes from unverified
    data.
    """
    value = fact.get("metric_value")
    if value is None:
        value = fact.get("value")
    source = fact.get("source") or ""
    period = fact.get("fiscal_period") or fact.get("reporting_period") or ""
    has_provenance = bool(source) and bool(period)
    return {
        # CRITICAL: the extractor's canonical metric id must be carried
        # onto the shaped fact — every metric lookup / calculation routes
        # through `f.get("metric")`. Without this key the assistant would
        # silently find zero facts for any metric question.
        "metric": fact.get("metric_name") or fact.get("metric_id") or "",
        "value": value,
        "verification_status": "VERIFIED" if (value is not None and has_provenance) else "PENDING",
        "currency_code": fact.get("currency_code", ""),
        "currency_role": fact.get("currency_role", "REPORTING"),
        "reporting_period": period,
        "scale": fact.get("scale") or "",
        "original_value": fact.get("raw_value") if fact.get("raw_value") is not None else value,
        "normalized_value": fact.get("normalized_value"),
        "unit": fact.get("unit", ""),
        "source": source,
        "source_type": fact.get("source_type", ""),
        "source_tier": fact.get("source_tier", 1),
        "document": document_name or fact.get("document", ""),
    }


def _collect_facts(documents: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """Flatten document financial_facts into tagged, gate-shaped facts."""
    out: List[Dict[str, Any]] = []
    for doc in documents or []:
        name = doc.get("file_name", doc.get("document", ""))
        for fact in doc.get("financial_facts") or []:
            out.append(_shape_fact(fact, document_name=name))
    return out


def _facts_for_metric(facts: List[Dict[str, Any]], metric_id: str) -> List[Dict[str, Any]]:
    return [f for f in facts if f.get("metric") == metric_id]


def _format_number(value: Any) -> str:
    if value is None:
        return "n/a"
    try:
        num = float(value)
    except (TypeError, ValueError):
        return str(value)
    if num == int(num):
        return f"{int(num):,}"
    return f"{num:,.2f}".rstrip("0").rstrip(".")


def _display_value(fact: Dict[str, Any]) -> str:
    """Human-readable rendering of a fact's value in its original scale.

    The extractor stores the full normalized magnitude in `value` (e.g.
    Apple FY2024 revenue = 391,035,000,000) alongside the presentation
    scale metadata ("10^6" = the document presented it in millions). For
    readability we re-apply that scale so the answer reads like the
    source document ("391,035 (millions)") instead of dumping the raw
    full-precision number with a contradictory suffix.
    """
    unit = fact.get("unit") or ""
    scale = fact.get("scale") or ""
    parts = [f"{_format_number(fact.get('value'))}"]
    if scale:
        if scale == "10^6":
            try:
                parts = [f"{_format_number(float(fact.get('value')) / 1e6)} (millions)"]
            except (TypeError, ValueError):
                parts = [f"{_format_number(fact.get('value'))}"]
        elif scale == "10^9":
            try:
                parts = [f"{_format_number(float(fact.get('value')) / 1e9)} (billions)"]
            except (TypeError, ValueError):
                parts = [f"{_format_number(fact.get('value'))}"]
        elif scale == "per-share":
            parts.append("per share")
        elif scale == "percentage":
            parts.append("%")
        else:
            parts.append(f"(×{scale})")
    if unit and unit not in ("USD", "shares"):
        parts.append(unit)
    return " ".join(parts)


# ---------------------------------------------------------------------------
# The assistant
# ---------------------------------------------------------------------------
class FinancialChatAssistant:
    """Orchestrates the existing intelligence layer for conversational Q&A.

    Injection points (defaults are lazy so the module stays importable in
    non-Streamlit contexts and tests can stub them):
        llm_call   — callable(prompt, system_prompt=None) -> str
        rag_runner — callable(ticker, goal, max_iterations=3) -> dict
    """

    def __init__(
        self,
        llm_call: Optional[Callable[..., str]] = None,
        rag_runner: Optional[Callable[..., Dict[str, Any]]] = None,
    ) -> None:
        self._llm_call = llm_call or self._default_llm_call
        self._rag_runner = rag_runner or self._default_rag_runner

    # -- lazy defaults -----------------------------------------------------
    @staticmethod
    def _default_llm_call(prompt: str, system_prompt: Optional[str] = None) -> str:
        from app import call_ai_with_fallback  # lazy: avoids circular import
        return call_ai_with_fallback(prompt, system_prompt=system_prompt, temperature=0.3)

    @staticmethod
    def _default_rag_runner(ticker: str, goal: str, max_iterations: int = 3) -> Dict[str, Any]:
        import api.services as svc  # lazy: keeps startup light
        return svc.run_analysis(ticker=ticker, goal=goal, max_iterations=max_iterations)

    # -- public entry point --------------------------------------------------
    def answer(
        self,
        question: str,
        context: Optional[ChatContext] = None,
        documents: Optional[List[Dict[str, Any]]] = None,
        provider_health: Optional[Dict[str, bool]] = None,
    ) -> Dict[str, Any]:
        """Produce a conversational answer for `question`.

        Returns {"content": str, "metadata": {...}} where metadata carries
        intent, evidence (provenance), calculation, and the compact topic
        used for follow-up resolution.
        """
        ctx = context or ChatContext()
        question = ctx.resolve_follow_up((question or "").strip())
        if not question:
            return self._response(
                "Please ask a question about a company, a financial metric, or your uploaded documents.",
                intent="empty", ctx=ctx,
            )
        if _is_greeting(question):
            return self._response(
                "I'm your financial research assistant. I can answer questions about "
                "your uploaded documents, verified financial metrics (revenue, ROE, "
                "current ratio, CAGR…), and companies via the evidence engine. Try: "
                "*\"What was the revenue?\"* or *\"Analyze AAPL's FY2024 net income\"*.",
                intent="greeting", ctx=ctx,
            )

        facts = _collect_facts(documents)
        calc = detect_calculation(question)
        metric = detect_metric(question)
        ticker = detect_ticker(question)

        # --- 1. Calculation questions (always through the gate) ------------
        if calc:
            result = self._answer_calculation(question, calc, metric, facts, ctx)
            if result:
                return result

        # --- 2. Single-metric lookups against documents ---------------------
        if metric:
            result = self._answer_metric(question, metric, facts, ticker, ctx)
            if result:
                return result

        # --- 3. Company / ticker questions via Agentic RAG ------------------
        if ticker:
            result = self._answer_company(question, ticker, ctx, provider_health)
            if result:
                return result

        # --- 4. Document question with no recognized metric ------------------
        if documents:
            return self._response(
                "I couldn't verify that from the available documents. "
                "Ask about a specific financial metric (revenue, net income, EPS, "
                "current ratio…) or a company ticker.",
                intent="document_miss", evidence=self._evidence(facts, limit=5), ctx=ctx,
            )

        # --- 5. General conversational question via provider chain ----------
        return self._answer_general(question, ctx, provider_health)

    # -- routing --------------------------------------------------------------
    def _answer_calculation(
        self,
        question: str,
        calc: str,
        metric: Optional[str],
        facts: List[Dict[str, Any]],
        ctx: ChatContext,
    ) -> Optional[Dict[str, Any]]:
        if calc == "change":
            return self._answer_change(question, metric, facts, ctx)
        if calc == "CAGR":
            return self._answer_cagr(question, metric, facts, ctx)

        inputs = RATIO_INPUTS.get(calc)
        if not inputs:
            return None
        financial_data = self._build_financial_data(facts, inputs)
        if not financial_data:
            # Spec: missing inputs must produce BLOCKED(MISSING) with
            # calculation=None -- never a computed or estimated figure.
            return self._blocked_response(
                calc, {"status": "BLOCKED", "reason": "MISSING"}, ctx,
            )
        result = safe_calculate_financial_ratios(financial_data, required_metrics=inputs)
        if result.get("status") != "ALLOWED":
            return self._blocked_response(calc, result, ctx)
        ratio_value = (result.get("calculation") or {}).get(calc)
        if ratio_value is None:
            return None
        answer = (
            f"The **{calc}** is **{_format_number(ratio_value.get('value'))}** "
            f"(calculated from verified evidence)."
        )
        return self._response(
            answer,
            intent="calculation",
            evidence=self._evidence(facts, inputs=inputs),
            calculation={
                "name": calc,
                "formula": self._formula(calc),
                "value": ratio_value.get("value"),
                "inputs": {k: v.get("value") for k, v in financial_data.items() if v.get("value") is not None},
                "periods": sorted({str(f.get("reporting_period")) for f in financial_data.values() if f.get("reporting_period")}),
            },
            topic=self._topic(metric=calc, facts=financial_data.values()),
            ctx=ctx,
        )

    def _answer_cagr(
        self,
        question: str,
        metric: Optional[str],
        facts: List[Dict[str, Any]],
        ctx: ChatContext,
    ) -> Optional[Dict[str, Any]]:
        metric = metric or "Revenue"
        calc_key = CALCULATOR_KEY_MAP.get(metric)
        if not calc_key:
            return self._blocked_response("CAGR", {"status": "BLOCKED", "reason": "MISSING"}, ctx)
        candidates = [f for f in facts if f.get("metric") == metric and f.get("value") is not None]
        if len(candidates) < 2:
            return self._blocked_response(
                "CAGR", {"status": "BLOCKED", "reason": "MISSING"},
                ctx, detail=f"need two fiscal periods of verified {metric} to compute CAGR.",
            )
        candidates.sort(key=lambda f: str(f.get("reporting_period") or ""))
        begin, end = candidates[0], candidates[-1]
        financial_data = {
            "CAGR Beginning Value": begin,
            "CAGR Ending Value": end,
        }
        result = safe_calculate_cagr_ratios(financial_data)
        if result.get("status") != "ALLOWED":
            return self._blocked_response("CAGR", result, ctx)
        cagr = (result.get("calculation") or {}).get("CAGR", {})
        if not cagr:
            return None
        answer = (
            f"The **CAGR** for {metric} between **{cagr.get('beginning_period', '')}** "
            f"and **{cagr.get('ending_period', '')}** is **{(cagr.get('value') or 0) * 100:.2f}%** "
            f"(over {cagr.get('years', '?')} year(s), from verified evidence)."
        )
        return self._response(
            answer,
            intent="cagr",
            evidence=self._evidence([begin, end]),
            calculation={
                "name": "CAGR",
                "formula": "(Ending / Beginning)^(1/n) − 1",
                "value": cagr.get("value"),
                "years": cagr.get("years"),
                "beginning": cagr.get("beginning_period"),
                "ending": cagr.get("ending_period"),
            },
            topic=self._topic(metric=f"{metric} CAGR", facts=[begin, end]),
            ctx=ctx,
        )

    def _answer_change(
        self,
        question: str,
        metric: Optional[str],
        facts: List[Dict[str, Any]],
        ctx: ChatContext,
    ) -> Optional[Dict[str, Any]]:
        metric = metric or (ctx.topic.metric if ctx.topic else None) or "Revenue"
        calc_key = CALCULATOR_KEY_MAP.get(metric)
        candidates = [f for f in facts if f.get("metric") == metric and f.get("value") is not None]
        if not candidates:
            return self._blocked_response(
                "change", {"status": "BLOCKED", "reason": "MISSING"},
                ctx, detail=f"no verified {metric} evidence found.",
            )
        if len(candidates) < 2:
            return self._blocked_response(
                "change", {"status": "BLOCKED", "reason": "MISSING"},
                ctx, detail=f"need two periods of {metric} to compute the change.",
            )
        # Run BOTH inputs through the gate before any arithmetic.
        gate = CalculationSafetyGate()
        candidates.sort(key=lambda f: str(f.get("reporting_period") or ""))
        begin, end = candidates[0], candidates[-1]
        for fact in (begin, end):
            verdict = gate.check({metric: fact}, [metric])
            if verdict.get("status") != "ALLOWED":
                return self._blocked_response("change", verdict, ctx)
        delta = end["value"] - begin["value"]
        pct = (delta / begin["value"] * 100) if begin["value"] else None
        answer = (
            f"**{metric}** moved from **{_display_value(begin)}** ({begin.get('reporting_period')}) "
            f"to **{_display_value(end)}** ({end.get('reporting_period')}). "
            f"Change: **{_display_value({'value': delta})}**"
            + (f" (**{pct:.2f}%**)." if pct is not None else ".")
        )
        return self._response(
            answer,
            intent="change",
            evidence=self._evidence([begin, end]),
            calculation={
                "name": f"{metric} change",
                "formula": "Ending − Beginning",
                "value": delta,
                "percent": pct,
                "beginning": begin.get("reporting_period"),
                "ending": end.get("reporting_period"),
            },
            topic=self._topic(metric=metric, facts=[begin, end]),
            ctx=ctx,
        )

    def _answer_metric(
        self,
        question: str,
        metric: str,
        facts: List[Dict[str, Any]],
        ticker: Optional[str],
        ctx: ChatContext,
    ) -> Optional[Dict[str, Any]]:
        matches = [f for f in facts if f.get("metric") == metric and f.get("value") is not None]
        if not matches:
            # Fall through to company/RAG path if a ticker is present.
            return None
        matches.sort(key=lambda f: str(f.get("reporting_period") or ""))
        best = matches[-1]
        label = self._metric_label(metric)
        answer = (
            f"**{label}** for {best.get('reporting_period') or 'the latest period'} "
            f"was **{_display_value(best)}**."
        )
        return self._response(
            answer,
            intent="metric",
            evidence=self._evidence(matches),
            topic=self._topic(metric=metric, facts=matches, ticker=ticker),
            ctx=ctx,
        )

    def _answer_company(
        self,
        question: str,
        ticker: str,
        ctx: ChatContext,
        provider_health: Optional[Dict[str, bool]],
    ) -> Optional[Dict[str, Any]]:
        try:
            result = self._rag_runner(ticker=ticker, goal=question, max_iterations=3)
        except Exception as exc:  # DB down, provider down, etc.
            return self._response(
                f"I couldn't retrieve live evidence for {ticker} right now "
                f"(the evidence/database layer is unavailable). Please try again later.",
                intent="company_error", ctx=ctx,
            )
        if not result or result.get("terminal_state") != "COMPLETE":
            reason = (result or {}).get("terminal_reason") or "not enough verified evidence"
            return self._response(
                f"I couldn't verify that for {ticker}. The evidence engine reported: {reason}.",
                intent="company_blocked", ctx=ctx,
            )
        summary = (result.get("summary_text") or "").strip()
        resolved = result.get("resolved_facts") or []
        if not summary and not resolved:
            return self._response(
                f"I couldn't verify that for {ticker} from available sources.",
                intent="company_empty", ctx=ctx,
            )
        metric = detect_metric(question) or (ctx.topic.metric if ctx.topic else "")
        return self._response(
            summary or f"Here is the verified evidence for {ticker}.",
            intent="company",
            evidence=self._evidence_from_resolved(resolved),
            topic=self._topic(metric=metric or "analysis", facts=resolved, ticker=ticker),
            ctx=ctx,
        )

    def _answer_general(
        self,
        question: str,
        ctx: ChatContext,
        provider_health: Optional[Dict[str, bool]],
    ) -> Dict[str, Any]:
        if provider_health is not None and not any(provider_health.values()):
            return self._response(
                "AI Assistant is currently unavailable. Configure an AI provider to continue.",
                intent="no_provider", ctx=ctx,
            )
        try:
            history = ctx.to_prompt()
            system = (
                "You are a professional financial research assistant. "
                "Answer conversational follow-ups using the conversation context. "
                "Never invent financial figures — if a specific metric/figure is "
                "requested, say it must be verified from uploaded documents or "
                "company evidence. " + GROUNDING_RULE
            )
            prompt = history + "\n\nQuestion: " + question if history else question
            content = self._llm_call(prompt, system_prompt=system)
        except Exception as exc:
            return self._response(
                "The AI provider is temporarily unavailable. Please try again shortly.",
                intent="provider_error", ctx=ctx,
            )
        if content.startswith(_ERROR_MARKERS) or any(m in content for m in ("❌", "🔴")):
            return self._response(
                "The AI provider returned an error. Please try again in a few seconds.",
                intent="provider_error", ctx=ctx,
            )
        return self._response(content, intent="general", ctx=ctx)

    # -- helpers --------------------------------------------------------------
    def _build_financial_data(
        self, facts: List[Dict[str, Any]], inputs: List[str]
    ) -> Dict[str, Any]:
        """Build gate-shaped financial_data for the calculator display keys."""
        financial_data: Dict[str, Any] = {}
        for calc_key in inputs:
            metric_ids = [m for m, k in CALCULATOR_KEY_MAP.items() if k == calc_key]
            matches = [f for f in facts if f.get("metric") in metric_ids and f.get("value") is not None]
            if not matches:
                return {}
            best = max(matches, key=lambda f: self._fact_rank(f))
            entry = dict(best)
            entry["verification_status"] = best.get("verification_status", "PENDING")
            financial_data[calc_key] = entry
        return financial_data

    @staticmethod
    def _fact_rank(fact: Dict[str, Any]) -> int:
        verified = 1 if fact.get("verification_status") == "VERIFIED" else 0
        return (verified * 1000) + int(fact.get("source_tier") or 0)

    @staticmethod
    def _metric_label(metric_id: str) -> str:
        return CALCULATOR_KEY_MAP.get(metric_id, metric_id.replace("_", " "))

    @staticmethod
    def _formula(calc: str) -> str:
        return {
            "ROE": "Net Profit / Equity",
            "ROA": "Net Profit / Assets",
            "Current Ratio": "Current Assets / Current Liabilities",
            "Debt to Equity": "Debt / Equity",
            "Profit Margin": "Net Profit / Revenue",
        }.get(calc, "")

    @staticmethod
    def _topic(metric: str = "", facts: Any = None, ticker: str = "") -> Dict[str, Any]:
        periods: List[str] = []
        currency = ""
        for f in facts or []:
            p = str(f.get("reporting_period") or f.get("fiscal_period") or "")
            if p and p not in periods:
                periods.append(p)
            c = f.get("currency_code") or ""
            if c and not currency:
                currency = c
        return {
            "metric": metric,
            "ticker": ticker,
            "periods": periods[-4:],
            "currency": currency,
        }

    @staticmethod
    def _evidence(facts: List[Dict[str, Any]], inputs: Optional[List[str]] = None, limit: int = 6) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for f in facts[:limit]:
            out.append({
                "metric": f.get("metric", ""),
                "value": _format_number(f.get("value")),
                "display": _display_value(f),
                "period": f.get("reporting_period", ""),
                "currency": f.get("currency_code", ""),
                "scale": f.get("scale", ""),
                "unit": f.get("unit", ""),
                "source": f.get("source", ""),
                "source_type": f.get("source_type", ""),
                "source_tier": f.get("source_tier", 1),
                "document": f.get("document", ""),
            })
        return out

    @staticmethod
    def _evidence_from_resolved(resolved: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for r in resolved[:6]:
            out.append({
                "metric": r.get("metric_name") or r.get("metric") or "",
                "value": _format_number(r.get("value")),
                "display": _display_value(r),
                "period": r.get("fiscal_period") or r.get("reporting_period") or "",
                "currency": r.get("currency_code", ""),
                "scale": r.get("scale", ""),
                "unit": r.get("unit", ""),
                "source": r.get("source", ""),
                "source_type": r.get("source_type", ""),
                "source_tier": r.get("source_tier", 1),
                "document": r.get("document_id", r.get("document", "")),
            })
        return out

    def _blocked_response(
        self,
        calc: str,
        verdict: Dict[str, Any],
        ctx: ChatContext,
        detail: str = "",
    ) -> Dict[str, Any]:
        reason = (verdict.get("reason") or "").replace("_", " ").capitalize() or "Not verified"
        if detail:
            reason = f"{reason} — {detail}"
        return self._response(
            f"I couldn't verify the **{calc}** from the available evidence "
            f"(status: {reason}). I never estimate or invent figures — "
            f"please upload the relevant financial document or provide verified data.",
            intent="blocked",
            metadata_extra={"blocked_reason": reason},
            ctx=ctx,
        )

    def _response(
        self,
        content: str,
        intent: str,
        ctx: ChatContext,
        evidence: Optional[List[Dict[str, Any]]] = None,
        calculation: Optional[Dict[str, Any]] = None,
        topic: Optional[Dict[str, Any]] = None,
        metadata_extra: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        metadata: Dict[str, Any] = {
            "intent": intent,
            "evidence": evidence or [],
            "calculation": calculation,
            "topic": topic,
        }
        if metadata_extra:
            metadata.update(metadata_extra)
        # Never allow secret values to leak into metadata by construction.
        metadata = self._sanitize_metadata(metadata)
        return {"content": content, "metadata": metadata}

    @staticmethod
    def _sanitize_metadata(metadata: Dict[str, Any]) -> Dict[str, Any]:
        """Defensive: strip anything that looks like a credential from
        metadata before it reaches session state / the UI."""
        import re as _re
        patterns = (r"sk-[A-Za-z0-9]{8,}", r"gsk_[A-Za-z0-9]{8,}",
                    r"AIza[A-Za-z0-9_-]{20,}", r"Bearer\s+\S+", r"key\s*=\s*\S+")
        safe: Dict[str, Any] = {}
        for k, v in metadata.items():
            if isinstance(v, str):
                for pat in patterns:
                    v = _re.sub(pat, "[REDACTED]", v)
                safe[k] = v
            elif isinstance(v, list):
                safe[k] = []
                for item in v:
                    if isinstance(item, dict):
                        cleaned_item = {}
                        for kk, vv in item.items():
                            if isinstance(vv, str):
                                for pat in patterns:
                                    vv = _re.sub(pat, "[REDACTED]", vv)
                            cleaned_item[kk] = vv
                        safe[k].append(cleaned_item)
                    elif isinstance(item, str):
                        for pat in patterns:
                            item = _re.sub(pat, "[REDACTED]", item)
                        safe[k].append(item)
                    else:
                        safe[k].append(item)
            else:
                safe[k] = v
        return safe


def build_chat_assistant() -> FinancialChatAssistant:
    """Factory used by the UI (kept separate so tests can inject stubs)."""
    return FinancialChatAssistant()
