# App.py
"""
Financial Timeline Engine -- Institutional Financial Intelligence Platform

Section map (Phase 12 -- modular organization within a single file):
  1. Config & Session State
  2. Parsing            (file ingestion: PDF/DOCX/XLSX/CSV/TXT)
  3. AI Providers        (Google AI Studio -> Groq -> OpenRouter fallback chain)
  4. Document Processing  (chunking, summarization, hierarchical merge, caching)
  5. Timeline             (event extraction)
  6. Analytics            (Phase 3/5 institutional intelligence modules)
  7. Export               (DOCX / PDF / JSON / CSV / Excel / Markdown)
  8. Dashboard             (visualization)
  9. Future Modules        (Phase 11 architecture scaffold)
 10. Live Market Intelligence (Phase 6 architecture scaffold)
 11. Main App / UI
 12. Auth
"""
import streamlit as st
import requests
import io
import json
from datetime import datetime, timezone
import pandas as pd
from docx import Document
from pypdf import PdfReader
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from xml.sax.saxutils import escape as xml_escape
from google import genai
from google.genai import types

from core import (
    SETTINGS,
    FUTURE_MODULES,
    GROUNDING_RULE,
    DEFAULT_SESSION_STATE,
    get_secret,
    get_provider_health,
    provider_logger,
    hash_text,
    CacheManager,
    retry,
    extract_json,
    contains_error_marker,
)
from ingestion.extraction import extract_document, extract_multiple, merge_document_text
from ingestion.statistics import document_statistics, print_statistics

# =============================================================================
# SECTION 1: Config & Session State
# =============================================================================
# All tunable constants (model IDs, timeouts, retry policy, chunking sizes,
# the future-module roadmap) now live in core/config.py as `SETTINGS`, a
# single typed EngineSettings instance -- see that module's docstring for
# why. The names below are kept as module-level aliases so every existing
# reference further down in this file (PRIMARY_MODEL, CHUNK_SIZE, etc.)
# continues to work completely unchanged.
st.set_page_config(page_title="Financial Timeline Engine", layout="centered")

PRIMARY_MODEL = SETTINGS.primary_model
FALLBACK_MODEL = SETTINGS.fallback_model
OPENROUTER_MODELS = list(SETTINGS.openrouter_models)
GROQ_MODELS = list(SETTINGS.groq_models)

GROQ_TIMEOUT_SECONDS = SETTINGS.groq_timeout_seconds
OPENROUTER_TIMEOUT_SECONDS = SETTINGS.openrouter_timeout_seconds
PROVIDER_RETRY_ATTEMPTS = SETTINGS.provider_retry_attempts
PROVIDER_RETRY_DELAY_SECONDS = SETTINGS.provider_retry_delay_seconds

CHUNK_SIZE = SETTINGS.chunk_size
CHUNK_OVERLAP = SETTINGS.chunk_overlap
MERGE_BATCH_SIZE = SETTINGS.merge_batch_size

LIVE_INTELLIGENCE_API_KEY_NAME = SETTINGS.live_intelligence_api_key_name

# --- Session state init ---
# Default shape now lives in core/constants.py (DEFAULT_SESSION_STATE) so
# it can be reused by future non-Streamlit entry points (tests, backend
# warm-up) without importing this UI module.
for _key, _default in DEFAULT_SESSION_STATE.items():
    if _key not in st.session_state:
        st.session_state[_key] = _default


def _hash_text(text):
    """Thin backward-compatible alias for core.utilities.hash_text, kept
    so every call site below (`_hash_text(...)`) needs no further edits."""
    return hash_text(text)


def _cached_call(cache_name, cache_key, compute_fn):
    """Thin backward-compatible alias over core.utilities.CacheManager,
    preserving the exact (cache_name, cache_key, compute_fn) call
    signature used throughout this file. Builds a fresh CacheManager over
    the relevant st.session_state[cache_name] dict on each call -- cheap,
    since CacheManager itself holds no state beyond the mapping reference."""
    cache_store = st.session_state.setdefault(cache_name, {})
    return CacheManager(cache_store).get_or_compute(cache_key, compute_fn)


def _log_provider_event(stage, provider, status, detail=""):
    """Thin backward-compatible alias over the shared
    core.logging.provider_logger instance."""
    return provider_logger.log(stage, provider, status, detail)


def _retry(fn, attempts=PROVIDER_RETRY_ATTEMPTS, delay=PROVIDER_RETRY_DELAY_SECONDS):
    """Thin backward-compatible alias over core.utilities.retry."""
    return retry(fn, attempts=attempts, delay=delay)


def _debug_stage(label, text):
    """Pipeline debug instrumentation (added while diagnosing the
    "always insufficient information" + export traceback bugs): records
    the length, a first-1000-character preview, and whether an error
    marker is present for one intermediate pipeline output. Entries
    accumulate in st.session_state['pipeline_debug_log'] for the current
    "Generate Timeline Report" run (reset at the top of that button
    handler) and are rendered in a "Pipeline Debug Trace" expander so the
    exact stage where real content is lost -- or silently replaced by an
    error/placeholder -- is visible directly in the UI, without needing
    server log access."""
    text = text or ""
    st.session_state.setdefault("pipeline_debug_log", []).append({
        "stage": label,
        "length": len(text),
        "error_marker_detected": contains_error_marker(text),
        "preview": text[:1000],
    })

# =============================================================================
# SECTION 2: Parsing (file ingestion)
# =============================================================================
@st.cache_data(show_spinner=False)
 def extract_document_data_legacy(uploaded_file):
    """Reads text lines from uploaded files safely."""
    if uploaded_file is None:
        return ""
    try:
        filename = uploaded_file.name.lower()
        if filename.endswith(".txt") or filename.endswith(".csv"):
            return uploaded_file.read().decode("utf-8", errors="ignore")
        elif filename.endswith(".xlsx"):
            df_sheets = pd.read_excel(uploaded_file, sheet_name=None)
            excel_text = ""
            for sheet, df in df_sheets.items():
                excel_text += f"\n--- Excel Sheet: {sheet} ---\n" + df.to_string() + "\n"
            return excel_text
        elif filename.endswith(".pdf"):
            pdf_reader = PdfReader(uploaded_file)
            pdf_text = f"\n--- PDF Document: {filename} ---\n"
            for page_num, page in enumerate(pdf_reader.pages, start=1):
                page_text = page.extract_text()
                if page_text and page_text.strip():
                    pdf_text += f"\n[Page {page_num}]\n" + page_text.strip() + "\n"
            return pdf_text
        elif filename.endswith(".docx"):
            word_doc = Document(uploaded_file)
            word_text = f"\n--- Word Document: {filename} ---\n"
            for para in word_doc.paragraphs:
                if para.text.strip():
                    word_text += para.text + "\n"
            return word_text
        else:
            return uploaded_file.read().decode("utf-8", errors="ignore")
    except Exception as e:
        return f"Error reading file text content: {str(e)}"


# =============================================================================
# SECTION 3: AI Providers (Google AI Studio -> Groq -> OpenRouter)
# =============================================================================
def call_google_ai_studio(prompt_text, system_prompt=None, temperature=None):
    """Calls Google AI Studio (Gemini) via the official google-genai SDK.
    PRIMARY provider in the fallback chain."""
    api_key = st.secrets.get("GOOGLE_API_KEY", "")
    if not api_key:
        raise ValueError("Missing Google Key")
    client = genai.Client(api_key=api_key)
    config = types.GenerateContentConfig(
        system_instruction=system_prompt if system_prompt else None,
        temperature=temperature if temperature is not None else None,
    )
    res = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=str(prompt_text),
        config=config,
    )
    if res.text:
        return res.text
    raise RuntimeError("Empty response")


def call_groq_engine(prompt_text, system_prompt=None, temperature=None):
    """Calls the Groq API directly. SECONDARY provider in the fallback
    chain. Tries each model in GROQ_MODELS in order; if one is
    decommissioned, rate-limited (429), or errors out, automatically
    retries with the next model. Only raises once every model has failed."""
    api_key = st.secrets.get("GROQ_API_KEY", "")
    if not api_key:
        raise ValueError("Missing Groq Key")

    endpoint = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": str(prompt_text)})

    last_error = None
    for model_id in GROQ_MODELS:
        try:
            payload = {"model": model_id, "messages": messages}
            if temperature is not None:
                payload["temperature"] = temperature
            res = requests.post(endpoint, headers=headers, json=payload, timeout=GROQ_TIMEOUT_SECONDS)
            if res.status_code == 200:
                return res.json()["choices"][0]["message"]["content"]
            elif res.status_code == 429:
                last_error = RuntimeError(f"Groq model '{model_id}' rate-limited (HTTP 429); trying next model.")
            else:
                last_error = RuntimeError(f"Groq model '{model_id}' failed with status: {res.status_code}")
        except Exception as e:
            last_error = e

    raise last_error


def _openrouter_request(prompt_text, model_id, system_prompt=None, temperature=None):
    """Shared helper for a single OpenRouter chat-completion call.
    Returns (success: bool, content_or_error: str)."""
    api_key = st.secrets.get("OPENROUTER_API_KEY", "")
    if not api_key:
        return False, "❌ OpenRouter API Key missing inside Streamlit Secrets panel."

    endpoint = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://streamlit.app",
        "X-Title": "Financial Timeline Engine"
    }

    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": str(prompt_text)})

    payload = {"model": model_id, "messages": messages}
    if temperature is not None:
        payload["temperature"] = temperature

    try:
        res = requests.post(endpoint, headers=headers, json=payload, timeout=OPENROUTER_TIMEOUT_SECONDS)
    except requests.exceptions.Timeout:
        return False, "TIMEOUT"
    except Exception:
        return False, "🔴 AI server busy or experiencing high latency volume right now. Please tap regenerate to claim a fresh server slot link."

    if res.status_code == 200:
        try:
            data = res.json()
            if "choices" in data and len(data["choices"]) > 0:
                st.session_state["ai_connected"] = True
                return True, data["choices"][0]["message"]["content"]
            return False, "⚠️ OpenRouter returned an empty choices payload. Please try clicking the button again."
        except Exception:
            return False, "⚠️ OpenRouter server returned a malformed response. The free pool is heavily congested right now. Please try again in 10 seconds!"
    else:
        return False, f"❌ OpenRouter Connection Failed. Server status code: {res.status_code}. Please retry."


def call_openrouter_engine(prompt_text, system_prompt=None, temperature=None):
    """Sends requests to OpenRouter, with a retry against a fallback
    OpenRouter model. FINAL fallback in the three-provider chain."""
    if system_prompt is None:
        system_prompt = (
            "You are an elite Wall Street financial research analyst. Generate "
            "structured multi-section corporate reports with key dates, events, "
            "and milestones."
        )

    success, result = _openrouter_request(prompt_text, PRIMARY_MODEL, system_prompt=system_prompt, temperature=temperature)
    if success:
        return result

    success, result = _openrouter_request(prompt_text, FALLBACK_MODEL, system_prompt=system_prompt, temperature=temperature)
    if success:
        return result

    if result == "TIMEOUT":
        return "🔴 AI server busy or experiencing high latency volume right now. Please tap regenerate to claim a fresh server slot link."
    return result


def call_ai_with_fallback(prompt_text, system_prompt=None, temperature=None):
    """Real multi-provider fallback chain: Google AI Studio -> Groq ->
    OpenRouter, each with a retry-with-backoff before moving to the next
    provider. Structured logging (see _log_provider_event) replaces the
    old ad hoc print()/st.warning() debug pattern -- failures are recorded
    to st.session_state['provider_log'] instead of being shown inline as
    raw warnings on every call."""
    # 1) PRIMARY: Google AI Studio
    try:
        result = _retry(lambda: call_google_ai_studio(prompt_text, system_prompt=system_prompt, temperature=temperature))
        st.session_state["ai_connected"] = True
        st.session_state["ai_provider_used"] = "Google AI Studio"
        _log_provider_event("call_ai_with_fallback", "Google AI Studio", "success")
        return result
    except Exception as e:
        _log_provider_event("call_ai_with_fallback", "Google AI Studio", "failed", f"{type(e).__name__}: {e}")

    # 2) SECONDARY: Groq
    try:
        result = _retry(lambda: call_groq_engine(prompt_text, system_prompt=system_prompt, temperature=temperature))
        st.session_state["ai_connected"] = True
        st.session_state["ai_provider_used"] = "Groq"
        _log_provider_event("call_ai_with_fallback", "Groq", "success")
        return result
    except Exception as e:
        _log_provider_event("call_ai_with_fallback", "Groq", "failed", f"{type(e).__name__}: {e}")

    # 3) FINAL FALLBACK: OpenRouter (already has its own internal 2-model retry)
    result = call_openrouter_engine(prompt_text, system_prompt=system_prompt, temperature=temperature)
    if not (result.startswith("❌") or result.startswith("🔴") or result.startswith("⚠️")):
        st.session_state["ai_connected"] = True
        st.session_state["ai_provider_used"] = "OpenRouter"
        _log_provider_event("call_ai_with_fallback", "OpenRouter", "success")
    else:
        _log_provider_event("call_ai_with_fallback", "OpenRouter", "failed", result)
    return result
  
# =============================================================================
# SECTION 4: Document Processing (chunking, summarization, hierarchical merge)
# =============================================================================
# GROUNDING_RULE now lives in core/constants.py and is imported above; kept
# as a plain reference here (no reassignment) so every use further down in
# this file is unchanged.


def chunk_text(text, chunk_size=CHUNK_SIZE, overlap=CHUNK_OVERLAP):
    """Splits text into fixed-size character chunks with overlap, so
    context (numbers, dates, table rows) isn't cut mid-thought at a
    chunk boundary."""
    if not text:
        return []
    chunks = []
    start = 0
    text_length = len(text)
    while start < text_length:
        end = start + chunk_size
        chunks.append(text[start:end])
        if end >= text_length:
            break
        start = end - overlap
    return chunks


def summarize_single_document(document_text, file_name):
    """Summarizes one document/chunk into a concise institutional
    financial summary (500-1000 words), explicitly instructed to
    preserve numbers, tables, dates, timeline events, management
    commentary, and financial-statement figures rather than smoothing
    them into vague prose (Phase 2)."""
    if not document_text or not document_text.strip():
        return f"⚠️ No extractable text found in '{file_name}'. Nothing to summarize."

    system_prompt = (
        "You are an elite institutional financial analyst. Produce concise, "
        "precise, and professional document summaries suitable for a "
        "buy-side investment research desk. Focus on material facts: exact "
        "figures, tables (rendered as clear text), dates, timeline events, "
        "management commentary, and financial statement line items. Avoid "
        "filler language and avoid restating the obvious. " + GROUNDING_RULE
    )

    summarization_prompt = f"""Summarize the following document into a single, coherent institutional financial summary.

Requirements:
- Length: 500 to 1000 words.
- Tone: professional, analytical, institutional-grade.
- Preserve exact figures, tables, dates, timeline events, management commentary, and financial statement data verbatim -- do not round or paraphrase numbers.
- Do not use markdown headers or bullet lists; write in clear prose paragraphs (tables may be rendered as simple "Label: Value" lines within the prose).

Source Document Name: {file_name}

Source Document Text:
{document_text}

Return only the summary text, with no preamble or meta-commentary."""

    return call_ai_with_fallback(summarization_prompt, system_prompt=system_prompt, temperature=0.3)


def _merge_summary_batch(document_summaries):
    """Merges a single batch (<= MERGE_BATCH_SIZE) of summaries into one
    coherent master summary. Internal helper for merge_document_summaries;
    see that function for the hierarchical/recursive batching logic.

    ROOT-CAUSE FIX for "every intelligence module says insufficient
    information": an individual chunk/document summarization call can
    fail and return one of this app's own rendered error strings (e.g.
    "❌ OpenRouter Connection Failed...", see call_ai_with_fallback /
    summarize_single_document) instead of raising. Previously that error
    string was indistinguishable from a real summary by the time it
    reached here, so it was fed straight into the merge prompt as if it
    were genuine document content. When enough (or all) inputs to a merge
    were error strings, the merge model had nothing real to work with and,
    per its own grounding instructions, correctly reported that no
    information was available -- and *that* message became the
    master_summary, so every downstream intelligence field also
    (correctly, given that garbage input) reported insufficient
    information. This was the exact stage where real extracted text was
    being replaced by a fallback message.

    Fix: error-marked inputs are filtered out before building the merge
    prompt (the prompt text itself is completely unchanged), and if
    literally everything in this batch failed, a clear error is returned
    immediately instead of asking the AI to "merge" garbage.
    """
    valid_summaries = [d for d in document_summaries if not contains_error_marker(d.get("summary", ""))]
    failed_summaries = [d for d in document_summaries if contains_error_marker(d.get("summary", ""))]

    for doc in failed_summaries:
        _log_provider_event(
            "_merge_summary_batch", "pipeline", "excluded",
            f"Excluded '{doc.get('file_name')}' from merge -- its summary was an "
            f"error message, not real content: {str(doc.get('summary', ''))[:200]}"
        )

    if not valid_summaries:
        error_result = (
            "❌ Unable to generate a summary: every source document/chunk in this "
            "batch failed AI summarization. Check the Provider Health & Activity "
            "Log above for the underlying provider errors, then try again."
        )
        _debug_stage("merge_batch_result (all inputs failed)", error_result)
        return error_result

    system_prompt = (
        "You are an elite institutional financial analyst. Merge multiple "
        "per-document summaries into a single, coherent master summary "
        "suitable for a buy-side investment research desk. Reconcile "
        "overlapping information rather than repeating it, but preserve "
        "every distinct financial metric, table, date, timeline event, "
        "management commentary, and strategic insight found across the "
        "source summaries. Do not drop material information for brevity. "
        + GROUNDING_RULE
    )

    combined_summaries_text = ""
    for doc in valid_summaries:
        file_name = doc.get("file_name", "Unknown Document")
        summary = doc.get("summary", "")
        combined_summaries_text += f"\n--- Summary of: {file_name} ---\n{summary}\n"

    merge_prompt = f"""Below are individual summaries of separate financial documents (or document sections). Merge them into one coherent institutional financial master summary.

Requirements:
- Reconcile and consolidate overlapping information; do not repeat the same fact twice.
- Preserve all distinct financial metrics, tables, dates/timelines, management commentary, risk factors, controversies, and strategic implications mentioned across the summaries.
- Organize the merged summary in clear prose paragraphs.
- Attribute conflicting figures or claims across documents where relevant, rather than silently picking one.
- Do not fabricate information not present in the source summaries.

Individual Summaries:
{combined_summaries_text}

Return only the merged master summary text, with no preamble or meta-commentary."""

    result = call_ai_with_fallback(merge_prompt, system_prompt=system_prompt, temperature=0.3)
    _debug_stage("merge_batch_result", result)
    return result


def merge_document_summaries(document_summaries):
    """Merges per-document (or per-chunk) summaries into one coherent
    institutional financial master summary.

    Phase 2/7 -- hierarchical/recursive merge: if there are more than
    MERGE_BATCH_SIZE summaries, they are merged in batches first, and the
    batch-level merged summaries are then recursively merged again. This
    lets the pipeline handle an arbitrarily large number of documents or
    chunks without ever sending an oversized single merge request to any
    provider (which is what caused Groq's earlier HTTP 413s)."""
    if not document_summaries or len(document_summaries) == 0:
        return "⚠️ No document summaries available to merge."

    if len(document_summaries) <= MERGE_BATCH_SIZE:
        return _merge_summary_batch(document_summaries)

    batch_merged = []
    for i in range(0, len(document_summaries), MERGE_BATCH_SIZE):
        batch = document_summaries[i:i + MERGE_BATCH_SIZE]
        batch_summary = _merge_summary_batch(batch)
        batch_merged.append({"file_name": f"Batch {i // MERGE_BATCH_SIZE + 1}", "summary": batch_summary})

    return merge_document_summaries(batch_merged)


def summarize_document_with_chunking(document_text, file_name):
    """For large documents, splits text into chunks before summarizing
    (avoiding oversized AI requests, e.g. Groq HTTP 413), then merges the
    chunk summaries into one document-level summary via the existing
    hierarchical merge_document_summaries(). Small documents skip
    chunking entirely and behave exactly as summarize_single_document.

    Instrumented with _debug_stage() at every intermediate output
    (extracted text length, each chunk summary, the merged result) so the
    exact stage where real content is lost is visible in the Pipeline
    Debug Trace expander."""
    _debug_stage(f"extracted_text :: {file_name}", document_text)

    if not document_text or len(document_text) <= CHUNK_SIZE:
        summary = summarize_single_document(document_text, file_name)
        _debug_stage(f"single_document_summary :: {file_name}", summary)
        return summary

    chunks = chunk_text(document_text)
    chunk_summaries = []
    for idx, chunk in enumerate(chunks, start=1):
        chunk_label = f"{file_name} (Part {idx}/{len(chunks)})"
        chunk_summary = summarize_single_document(chunk, chunk_label)
        _debug_stage(f"chunk_summary :: {chunk_label}", chunk_summary)
        chunk_summaries.append({"file_name": chunk_label, "summary": chunk_summary})

    merged = merge_document_summaries(chunk_summaries)
    _debug_stage(f"document_merged_summary :: {file_name}", merged)
    return merged


# =============================================================================
# SECTION 5: Timeline extraction
# =============================================================================
def _extract_json_from_ai_response(result, expected_type=dict):
    """Thin backward-compatible alias over core.validation.extract_json,
    which now owns the markdown-fence stripping, error-marker detection,
    and bracket-scan fallback logic (unchanged behavior, single
    implementation shared with any future caller outside this file)."""
    return extract_json(result, expected_type=expected_type)


def extract_timeline_events(ai_narrative):
    """Parses AI narrative to extract structured timeline events."""
    try:
        structuring_prompt = f"""Extract timeline events from this narrative and return as JSON array with objects containing: date (YYYY-MM-DD or YYYY-MM or YYYY), event (string), category (string), impact (string).

Narrative:
{ai_narrative}

Return ONLY valid JSON array, no markdown, no extra text."""

        result = call_ai_with_fallback(structuring_prompt, temperature=0.3)
        return _extract_json_from_ai_response(result, expected_type=list)
    except Exception:
        return []


# =============================================================================
# SECTION 6: Analytics -- Phase 3/5 Institutional Intelligence Modules
# =============================================================================
# All 17 sections below are produced by ONE consolidated AI call
# (run_universal_intelligence_extraction), not 17 separate calls -- this
# directly serves Phase 8 (reduce AI cost / avoid duplicate calls) while
# still covering the full requested breadth.
INTELLIGENCE_MODULES = {
    "executive_summary": {
        "title": "📝 Executive Summary",
        "instruction": "A concise institutional executive summary (250-400 words) covering the business, recent performance, and overall investment context.",
        "expected_type": str,
    },
    "key_metrics": {
        "title": "📌 Key Financial Metrics",
        "instruction": "All available key financial metrics (Revenue, EBITDA, PAT, EPS, Debt, Cash, Margins, Growth, Capex, Free Cash Flow, ROE, ROCE, Market Cap, and other material KPIs), mapping each metric name to its reported value including units/period.",
        "expected_type": dict,
    },
    "ratio_analysis": {
        "title": "📐 Ratio Analysis",
        "instruction": "Compute whichever of these ratios are derivable purely from figures explicitly present in the source: Revenue Growth, PAT Growth, EBITDA Growth, ROE, ROCE, Debt/Equity, Current Ratio, Operating Margin, Net Margin, EPS, Free Cash Flow indicators. For each, either show the computed value with its calculation, or the exact string 'Not computable from provided documents'.",
        "expected_type": dict,
    },
    "financial_performance": {
        "title": "📈 Financial Performance",
        "instruction": "A structured narrative (300-500 words) describing revenue, profitability, and cash flow trends explicitly stated in the source.",
        "expected_type": str,
    },
    "segment_analysis": {
        "title": "🧩 Segment Analysis",
        "instruction": "Each distinct business segment/division mentioned, as an array of objects with 'segment_name', 'performance_summary', 'contribution'.",
        "expected_type": list,
    },
    "sector_analysis": {
        "title": "🏭 Sector Analysis",
        "instruction": "An object with 'sector', 'industry', 'business_model', 'competitors' (list), 'market_position', 'industry_trends', 'peer_context'.",
        "expected_type": dict,
    },
    "competitor_analysis": {
        "title": "⚔️ Competitor Analysis",
        "instruction": "Named competitors/peers mentioned, as an array of objects with 'competitor_name', 'context'.",
        "expected_type": list,
    },
    "swot": {
        "title": "🧭 SWOT Analysis",
        "instruction": "An object with keys 'strengths', 'weaknesses', 'opportunities', 'threats', each a list of short strings grounded in the source.",
        "expected_type": dict,
    },
    "risk_analysis": {
        "title": "⚠️ Risk Analysis",
        "instruction": "A structured risk assessment as an array of objects with 'category' (Business, Financial, Operational, Governance, Regulatory, Macroeconomic, or Investment), 'risk', 'severity' (Low/Medium/High), 'probability' (Low/Medium/High), 'mitigation'.",
        "expected_type": list,
    },
    "controversy_analysis": {
        "title": "🚨 Controversy Analysis",
        "instruction": "Any controversies (litigation, fraud, governance issues, management changes, accounting issues, environmental, political, regulatory actions, negative news) as an array of objects with 'date', 'type', 'description', 'severity', 'source'.",
        "expected_type": list,
    },
    "governance_analysis": {
        "title": "🏛️ Governance Analysis",
        "instruction": "A narrative (150-300 words) on corporate governance details explicitly mentioned (board structure, management changes, related-party matters, audit/compliance notes).",
        "expected_type": str,
    },
    "esg_summary": {
        "title": "🌱 ESG Summary",
        "instruction": "A narrative (150-300 words) summarizing any Environmental, Social, and Governance information explicitly mentioned.",
        "expected_type": str,
    },
    "investment_thesis": {
        "title": "💡 Investment Thesis",
        "instruction": "A concise investment thesis (150-300 words) grounded strictly in the source.",
        "expected_type": str,
    },
    "bull_case": {
        "title": "🐂 Bull Case",
        "instruction": "The strongest bullish arguments supported by the source, as an array of short strings.",
        "expected_type": list,
    },
    "bear_case": {
        "title": "🐻 Bear Case",
        "instruction": "The strongest bearish/risk arguments supported by the source, as an array of short strings.",
        "expected_type": list,
    },
    "catalysts": {
        "title": "⚡ Catalysts",
        "instruction": "Specific upcoming events/dates/triggers mentioned that could move the outlook, as an array of objects with 'catalyst', 'expected_timing'.",
        "expected_type": list,
    },
    "action_points": {
        "title": "✅ Action Points",
        "instruction": "Concrete, specific action points/recommendations an analyst would take next, as an array of short strings.",
        "expected_type": list,
    },
}


def _type_hint(expected_type):
    if expected_type is dict:
        return "a JSON object"
    if expected_type is list:
        return "a JSON array"
    return "a plain text string (no markdown)"


def run_universal_intelligence_extraction(master_summary):
    """Phase 3/5/8: single consolidated AI call that extracts all 17
    institutional intelligence sections defined in INTELLIGENCE_MODULES
    at once. Returns a dict keyed by module key; any module missing from
    the AI's response falls back to an empty value of the expected type
    so downstream rendering never breaks on a missing key.

    BUG FIXES:
    - Now also short-circuits (returning empty_result) when master_summary
      itself is an error string (contains_error_marker(...)), not just
      when it's empty. Previously an error string that slipped through
      from an upstream summarization/merge failure was treated as real
      content and sent to the AI anyway -- with nothing genuine to work
      from, the AI would (correctly, per its own grounding instructions)
      report "insufficient information" for every single field. Combined
      with the _merge_summary_batch fix above, master_summary should now
      only ever be an error string if literally every source failed.
    - Each field's value is now checked against its declared
      `expected_type` before being kept, and a `list` value containing a
      mix of dict and non-dict items (which the model occasionally
      returns) is normalized down to just its dict items. An unfiltered
      mixed list is what caused `pd.DataFrame(...)` to raise
      "ValueError: dictionary update sequence element #0 has length 1; 2
      is required" downstream in rendering/export -- the traceback that
      appeared right after generation.
    """
    empty_result = {key: config["expected_type"]() for key, config in INTELLIGENCE_MODULES.items()}

    _debug_stage("intelligence_extraction_input", master_summary)

    if not master_summary or not master_summary.strip() or contains_error_marker(master_summary):
        return empty_result

    system_prompt = (
        "You are an elite institutional financial research analyst producing "
        "a complete, structured research package. " + GROUNDING_RULE
    )

    field_instructions = "\n".join(
        f'- "{key}" ({_type_hint(config["expected_type"])}): {config["instruction"]}'
        for key, config in INTELLIGENCE_MODULES.items()
    )

    extraction_prompt = f"""Analyze the Source Summary below and produce a complete institutional research package.

Return ONLY a single valid JSON object with exactly these top-level keys:

{field_instructions}

Source Summary:
{master_summary}

Return ONLY the JSON object. No markdown, no extra text, no preamble."""

    result = call_ai_with_fallback(extraction_prompt, system_prompt=system_prompt, temperature=0.2)
    parsed = _extract_json_from_ai_response(result, expected_type=dict)
    if not parsed:
        return empty_result

    final = dict(empty_result)
    for key, config in INTELLIGENCE_MODULES.items():
        if key not in parsed or not parsed[key]:
            continue
        value = parsed[key]
        expected_type = config["expected_type"]

        if expected_type is list and isinstance(value, list):
            dict_items = [item for item in value if isinstance(item, dict)]
            if dict_items and len(dict_items) != len(value):
                # Mixed list (some dict items, some not) -- keep only the
                # structured items so downstream pd.DataFrame(...) calls
                # in rendering/export never see an inconsistent shape.
                value = dict_items
            final[key] = value
        elif isinstance(value, expected_type):
            final[key] = value
        # else: the AI returned a type that doesn't match this field's
        # expected_type at all (e.g. a plain string where a list was
        # expected) -- leave this field at its empty default rather than
        # passing an unexpected shape downstream.

    return final


def render_intelligence_output(key, value):
    """Renders one intelligence module's output using the same visual
    language already used elsewhere in the app (st.subheader, st.dataframe,
    st.markdown) -- no new UI patterns introduced."""
    config = INTELLIGENCE_MODULES[key]
    if not value:
        return
    st.subheader(config["title"])

    if isinstance(value, dict):
        if key == "swot":
            swot_col1, swot_col2 = st.columns(2)
            with swot_col1:
                st.markdown("**Strengths**")
                for item in value.get("strengths") or []:
                    st.markdown(f"- {item}")
                st.markdown("**Opportunities**")
                for item in value.get("opportunities") or []:
                    st.markdown(f"- {item}")
            with swot_col2:
                st.markdown("**Weaknesses**")
                for item in value.get("weaknesses") or []:
                    st.markdown(f"- {item}")
                st.markdown("**Threats**")
                for item in value.get("threats") or []:
                    st.markdown(f"- {item}")
        elif key == "sector_analysis":
            for field_name, field_value in value.items():
                if field_value:
                    label = field_name.replace("_", " ").title()
                    if isinstance(field_value, list):
                        field_value = ", ".join(str(v) for v in field_value)
                    st.markdown(f"**{label}:** {field_value}")
        else:
            st.dataframe(
                pd.DataFrame(list(value.items()), columns=["Field", "Value"]),
                use_container_width=True,
                hide_index=True
            )
    elif isinstance(value, list):
        if value and all(isinstance(item, dict) for item in value):
            st.dataframe(pd.DataFrame(value), use_container_width=True, hide_index=True)
        else:
            for item in value:
                st.markdown(f"- {item}")
    else:
        st.markdown(str(value))
# =============================================================================
# SECTION 7: Export System (DOCX / PDF / JSON / CSV / Excel / Markdown)
# =============================================================================
def _flatten_intelligence_for_text(intelligence_outputs):
    """Shared helper: converts the intelligence_outputs dict into a flat
    list of (title, rendered_text_or_rows) for reuse across DOCX/PDF/MD
    export, avoiding duplicating this formatting logic three times."""
    sections = []
    for key, value in (intelligence_outputs or {}).items():
        if not value:
            continue
        title = INTELLIGENCE_MODULES.get(key, {}).get("title", key)
        sections.append((title, value))
    return sections


def generate_docx_download(text_content, timeline_data=None, intelligence_outputs=None):
    """Compiles the generated AI analysis report into a Word document
    download stream. intelligence_outputs is optional (default None) so
    any existing call site without it still behaves exactly as before."""
    doc = Document()

    doc.add_heading("Institutional Investment Research Memo", level=1)
    doc.add_paragraph("-" * 40)
    doc.add_heading("Executive Summary & Analysis", level=2)

    if text_content:
        clean_text_string = str(text_content)
        for line in clean_text_string.split('\n'):
            if line.strip():
                sanitized_line = "".join(c for c in line if c.isprintable() or c in ['\t', '\n'])
                sanitized_line = sanitized_line.replace('**', '').replace('__', '').replace('```', '')
                if sanitized_line.strip():
                    doc.add_paragraph(sanitized_line.strip())
    else:
        doc.add_paragraph("No report content generated.")

    if timeline_data and len(timeline_data) > 0:
        doc.add_heading("Extracted Timeline Events", level=2)
        for event in timeline_data:
            date_str = "".join(c for c in str(event.get("date", "N/A")) if c.isprintable())
            event_name = "".join(c for c in str(event.get("event", "N/A")) if c.isprintable())
            category = "".join(c for c in str(event.get("category", "N/A")) if c.isprintable())
            impact = "".join(c for c in str(event.get("impact", "N/A")) if c.isprintable())
            doc.add_paragraph(f"📅 {date_str}: {event_name}", style="List Bullet")
            doc.add_paragraph(f"Category: {category} | Impact: {impact}", style="List Bullet 2")

    for title, value in _flatten_intelligence_for_text(intelligence_outputs):
        doc.add_heading(title, level=2)
        if isinstance(value, str):
            doc.add_paragraph(value)
        elif isinstance(value, dict):
            for field_name, field_value in value.items():
                if isinstance(field_value, list):
                    field_value = ", ".join(str(v) for v in field_value)
                doc.add_paragraph(f"{field_name.replace('_', ' ').title()}: {field_value}", style="List Bullet")
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    line = " | ".join(f"{k}: {v}" for k, v in item.items())
                else:
                    line = str(item)
                doc.add_paragraph(line, style="List Bullet")

    bio = io.BytesIO()
    doc.save(bio)
    bio.seek(0)
    return bio


def generate_pdf_download(text_content, timeline_data=None, intelligence_outputs=None):
    """Compiles the report into an institutional-styled PDF using
    reportlab. intelligence_outputs is optional (default None) for
    backward compatibility. All text is XML-escaped since reportlab's
    Paragraph uses an internal XML markup dialect, and emoji are avoided
    in this output (reportlab's built-in fonts lack emoji glyphs, unlike
    python-docx which substitutes fonts automatically)."""
    bio = io.BytesIO()
    doc = SimpleDocTemplate(bio, pagesize=letter, topMargin=0.75 * 72, bottomMargin=0.75 * 72)
    base_styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        "InstitutionalTitle", parent=base_styles["Title"],
        textColor=colors.HexColor("#1a2b4c"), fontSize=20, spaceAfter=4
    )
    meta_style = ParagraphStyle(
        "InstitutionalMeta", parent=base_styles["Normal"],
        textColor=colors.grey, fontSize=8, spaceAfter=14
    )
    heading_style = ParagraphStyle(
        "InstitutionalHeading", parent=base_styles["Heading2"],
        textColor=colors.HexColor("#1a2b4c"), spaceBefore=14, spaceAfter=6
    )
    normal_style = base_styles["Normal"]

    story = [
        Paragraph("Institutional Investment Research Memo", title_style),
        Paragraph(f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}", meta_style),
        Paragraph("Executive Summary &amp; Analysis", heading_style),
        Spacer(1, 6),
    ]

    if text_content:
        for line in str(text_content).split('\n'):
            if line.strip():
                sanitized_line = "".join(c for c in line if c.isprintable() or c in ['\t', '\n'])
                sanitized_line = sanitized_line.replace('**', '').replace('__', '').replace('```', '')
                if sanitized_line.strip():
                    story.append(Paragraph(xml_escape(sanitized_line.strip()), normal_style))
                    story.append(Spacer(1, 6))
    else:
        story.append(Paragraph("No report content generated.", normal_style))

    if timeline_data and len(timeline_data) > 0:
        story.append(Paragraph("Extracted Timeline Events", heading_style))
        for event in timeline_data:
            date_str = "".join(c for c in str(event.get("date", "N/A")) if c.isprintable())
            event_name = "".join(c for c in str(event.get("event", "N/A")) if c.isprintable())
            category = "".join(c for c in str(event.get("category", "N/A")) if c.isprintable())
            impact = "".join(c for c in str(event.get("impact", "N/A")) if c.isprintable())
            story.append(Paragraph(xml_escape(f"Date: {date_str} -- {event_name}"), normal_style))
            story.append(Paragraph(xml_escape(f"Category: {category} | Impact: {impact}"), normal_style))
            story.append(Spacer(1, 6))

    for title, value in _flatten_intelligence_for_text(intelligence_outputs):
        story.append(Paragraph(xml_escape(title), heading_style))
        if isinstance(value, str):
            story.append(Paragraph(xml_escape(value), normal_style))
        elif isinstance(value, dict):
            for field_name, field_value in value.items():
                if isinstance(field_value, list):
                    field_value = ", ".join(str(v) for v in field_value)
                line = f"{field_name.replace('_', ' ').title()}: {field_value}"
                story.append(Paragraph(xml_escape(line), normal_style))
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    line = " | ".join(f"{k}: {v}" for k, v in item.items())
                else:
                    line = str(item)
                story.append(Paragraph(xml_escape(line), normal_style))
        story.append(Spacer(1, 6))

    doc.build(story)
    bio.seek(0)
    return bio


def generate_json_export(ai_narrative_result, timeline_events, intelligence_outputs):
    """Phase 4: JSON export -- full structured payload."""
    payload = {
        "investment_memo": ai_narrative_result,
        "timeline_events": timeline_events or [],
        "intelligence": intelligence_outputs or {},
    }
    return json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8")


def generate_markdown_export(ai_narrative_result, timeline_events, intelligence_outputs):
    """Phase 4: Markdown export."""
    md_lines = ["# Institutional Investment Research Memo", "", "## Investment Memo", "", ai_narrative_result or "", ""]
    if timeline_events:
        md_lines += ["## Timeline Events", ""]
        for event in timeline_events:
            md_lines.append(
                f"- **{event.get('date', 'N/A')}** -- {event.get('event', 'N/A')} "
                f"(_{event.get('category', 'N/A')}_, impact: {event.get('impact', 'N/A')})"
            )
        md_lines.append("")
    for title, value in _flatten_intelligence_for_text(intelligence_outputs):
        md_lines += [f"## {title}", ""]
        if isinstance(value, str):
            md_lines.append(value)
        elif isinstance(value, dict):
            for k, v in value.items():
                if isinstance(v, list):
                    v = ", ".join(str(x) for x in v)
                md_lines.append(f"- **{k}**: {v}")
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    md_lines.append("- " + " | ".join(f"**{k}**: {v}" for k, v in item.items()))
                else:
                    md_lines.append(f"- {item}")
        md_lines.append("")
    return "\n".join(md_lines).encode("utf-8")


def generate_csv_export(timeline_events, intelligence_outputs):
    """Phase 4: CSV export -- stacks the timeline table and any
    list-of-dict intelligence outputs (e.g. risk_analysis) as separate
    labeled sections within one CSV file."""
    buffer = io.StringIO()
    if timeline_events:
        buffer.write("Timeline Events\n")
        pd.DataFrame(timeline_events).to_csv(buffer, index=False)
        buffer.write("\n")
    for key, value in (intelligence_outputs or {}).items():
        if isinstance(value, list) and value and all(isinstance(item, dict) for item in value):
            title = INTELLIGENCE_MODULES.get(key, {}).get("title", key)
            buffer.write(f"{title}\n")
            pd.DataFrame(value).to_csv(buffer, index=False)
            buffer.write("\n")
    return buffer.getvalue().encode("utf-8")


def generate_excel_export(timeline_events, intelligence_outputs, key_metrics=None):
    """Phase 4: Excel export -- one sheet per structured section.

    Bug fix: openpyxl raises `IndexError: At least one sheet must be
    visible` if the workbook ends up with zero worksheets written (e.g.
    when timeline_events, intelligence_outputs, and key_metrics are all
    empty). This guarantees at least one sheet always exists before the
    writer saves: if nothing else was written, a "Summary" sheet is added
    instead of leaving the workbook empty.
    """
    bio = io.BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl") as writer:
        wrote_any = False
        if timeline_events:
            pd.DataFrame(timeline_events).to_excel(writer, sheet_name="Timeline", index=False)
            wrote_any = True
        if key_metrics:
            pd.DataFrame(list(key_metrics.items()), columns=["Metric", "Value"]).to_excel(
                writer, sheet_name="Key Metrics", index=False
            )
            wrote_any = True
        for key, value in (intelligence_outputs or {}).items():
            if isinstance(value, list) and value and all(isinstance(item, dict) for item in value):
                raw_name = INTELLIGENCE_MODULES.get(key, {}).get("title", key)
                sheet_name = "".join(c for c in raw_name if c not in ['\\', '/', '*', '[', ']', ':', '?']).strip()
                sheet_name = (sheet_name or key)[:31]
                pd.DataFrame(value).to_excel(writer, sheet_name=sheet_name, index=False)
                wrote_any = True
        if not wrote_any:
            pd.DataFrame(["Financial Timeline Engine", "No exportable data available."]).to_excel(
                writer, sheet_name="Summary", index=False, header=False
            )
    bio.seek(0)
    return bio.getvalue()


# =============================================================================
# SECTION 8: Dashboard / Visualization
# =============================================================================
def render_timeline_visualization(timeline_data):
    """Renders a simplified timeline visualization."""
    if not timeline_data or len(timeline_data) == 0:
        st.info("No timeline events extracted yet.")
        return
    st.subheader("📊 Timeline Events")
    df_timeline = pd.DataFrame(timeline_data)
    st.dataframe(df_timeline, use_container_width=True, hide_index=True)


# =============================================================================
# SECTION 9: Future Modules (Phase 11 -- architecture only, not implemented)
# =============================================================================
def get_future_module_status():
    """Returns the roadmap of planned-but-not-yet-implemented modules
    (Valuation, DCF, Comparable Analysis, Forecasting, Earnings Model,
    Financial Modeling, Portfolio Analysis, Watchlist, Company Comparison,
    Stock Scoring, Screening Engine) for display in the UI. This is
    intentionally scaffolding only, per Phase 11's explicit scope."""
    return {key: {"name": name, "status": "planned"} for key, name in FUTURE_MODULES.items()}


# =============================================================================
# SECTION 10: Live Market Intelligence (Phase 6 -- architecture only)
# =============================================================================
def is_live_market_intelligence_enabled():
    return bool(get_secret(LIVE_INTELLIGENCE_API_KEY_NAME, ""))


def fetch_live_market_intelligence(query):
    """Phase 6 architecture stub: a real, working function that gracefully
    degrades when no live-data API key is configured, rather than
    crashing or fabricating results. No specific news/filings/market-data
    vendor is wired in yet (that requires choosing and paying for a
    provider) -- once one is chosen, its client call replaces the body of
    the `if enabled` branch below; the calling code in main() already
    handles both the enabled and disabled cases cleanly."""
    if not is_live_market_intelligence_enabled():
        return {
            "enabled": False,
            "data": None,
            "message": "Live market intelligence is not configured for this deployment.",
        }
    return {
        "enabled": True,
        "data": None,
        "message": "Live market intelligence key detected, but no data provider integration has been selected yet.",
    }
# =============================================================================
# SECTION 11: Main App / UI
# =============================================================================
def main():
    st.title("📈 Financial Timeline Engine")

    # --- AI status (Phase 1: provider health detection) ---
    health = get_provider_health()
    has_any_key = any(health.values())
    if not has_any_key:
        st.error("🔴 AI Status: Offline (No AI Provider Keys Found in Streamlit Secrets)")
    elif st.session_state["ai_connected"]:
        provider = st.session_state.get("ai_provider_used", "AI Provider")
        st.success(f"🟢 AI Status: Connected & Verified Live ({provider})")
    else:
        st.info("🟡 AI Status: API Key(s) Loaded (Awaiting First Live Document Generation Connection)")

    with st.expander("🩺 Provider Health & Activity Log", expanded=False):
        health_df = pd.DataFrame([{"Provider": k, "Configured": "✅" if v else "❌"} for k, v in health.items()])
        st.dataframe(health_df, use_container_width=True, hide_index=True)
        if st.session_state["provider_log"]:
            st.dataframe(pd.DataFrame(st.session_state["provider_log"][-20:]), use_container_width=True, hide_index=True)
        else:
            st.caption("No AI provider activity yet.")

    with st.expander("🔮 Roadmap (Planned Modules)", expanded=False):
        roadmap = get_future_module_status()
        st.dataframe(
            pd.DataFrame([{"Module": v["name"], "Status": v["status"].title()} for v in roadmap.values()]),
            use_container_width=True, hide_index=True
        )

    if is_live_market_intelligence_enabled():
        st.caption("🌐 Live Market Intelligence: key detected (provider integration pending).")

    # --- Sidebar Document Ingestion ---
    st.sidebar.header("📁 Document Ingestion")
    uploaded_files = st.sidebar.file_uploader(
        "Upload Financial Documents (.txt, .csv, .xlsx, .docx, .pdf)",
        type=["txt", "csv", "xlsx", "docx", "pdf"],
        accept_multiple_files=True
    )


    combined_raw_text = ""
document_summaries = []

if uploaded_files:
    extraction_result = extract_multiple(uploaded_files)

    combined_raw_text = extraction_result["merged_text"]

    for doc in extraction_result["documents"]:
        cache_key = f"{doc['filename']}:{_hash_text(doc['text'])}"

        summary_text, _ = _cached_call(
            "summary_cache",
            cache_key,
            lambda text=doc["text"], name=doc["filename"]:
                summarize_document_with_chunking(text, name)
        )

        document_summaries.append({
            "file_name": doc["filename"],
            "summary": summary_text
        })

    st.subheader("📊 Ingested Data Summary")
    col1, col2 = st.columns(2)
    col1.metric(label="📄 Files Processed", value=len(uploaded_files) if uploaded_files else 0)
    col2.metric(label="📊 Extracted Characters", value=len(combined_raw_text))

    st.markdown("---")
    st.subheader("🔬 AI Analysis Engine")

    if st.button("🚀 Generate Timeline Report"):
        if not uploaded_files:
            st.warning("Please upload at least one financial document before generating a report.")
        else:
            # Reset the pipeline debug trace for this run so it only shows
            # entries from the current click, not accumulated across every
            # click in the session.
            st.session_state["pipeline_debug_log"] = []

            with st.spinner("Merging document summaries..."):
                merge_cache_key = _hash_text(
                    "||".join(f"{d['file_name']}:{d['summary']}" for d in document_summaries)
                )
                master_summary, _ = _cached_call(
                    "merge_cache", merge_cache_key,
                    lambda: merge_document_summaries(document_summaries)
                )
                _debug_stage("master_summary (final, pre-memo)", master_summary)

            # Bug fix: if summarization/merging failed for every uploaded
            # file/chunk, master_summary itself is an error string. Detect
            # that HERE (in addition to the existing ai_narrative_result
            # check below) so the wasted downstream memo/timeline/
            # intelligence AI calls are skipped and the user gets a clear,
            # specific reason instead of 17 sections each independently
            # reporting "insufficient information".
            master_summary_failed = (
                not master_summary or not master_summary.strip() or contains_error_marker(master_summary)
            )
            if master_summary_failed:
                st.error(
                    "❌ Document summarization failed for every uploaded file/chunk -- "
                    "there is no real content to analyze. Check the Provider Health & "
                    "Activity Log above for the underlying error, then try again."
                )

            with st.spinner("Generating investment memo..."):
                memo_system_prompt = (
                    "You are an elite institutional investment research analyst. "
                    "Write the investment memo strictly and exclusively from the "
                    "facts, figures, dates, and events contained in the Document "
                    "Summary provided below. Do not produce generic, templated, or "
                    "boilerplate analysis -- every claim must be traceable to a "
                    "specific fact stated in the Document Summary. If the Document "
                    "Summary lacks information for a requested section, explicitly "
                    "state that the source documents did not provide it rather than "
                    "inventing generic filler."
                )
                prompt = f"""Analyze the Document Summary below carefully. Extract key event milestones, timelines, and potential controversy flags SPECIFIC to this Document Summary. Write a comprehensive multi-paragraph investment memo that identifies, using only facts from the Document Summary:
1. Key financial events and dates
2. Market movements and impacts
3. Risk factors and opportunities
4. Strategic implications

Document Summary:
{master_summary}

Generate a professional investment memo grounded strictly in the Document Summary above. Do not generate generic industry commentary that is not tied to a specific fact in the Document Summary."""

                memo_cache_key = _hash_text(master_summary)
                ai_narrative_result, _ = _cached_call(
                    "memo_cache", memo_cache_key,
                    lambda: call_ai_with_fallback(prompt, system_prompt=memo_system_prompt, temperature=0.3)
                )

            st.markdown("### 📝 Generated Investment Memo")
            st.write(ai_narrative_result)

            is_error = (
                master_summary_failed
                or ("❌" in ai_narrative_result) or ("🔴" in ai_narrative_result) or ("⚠️" in ai_narrative_result)
            )

            timeline_events = []
            intelligence_outputs = {}
            if not is_error:
                with st.spinner("Extracting timeline events..."):
                    timeline_cache_key = _hash_text(ai_narrative_result)
                    timeline_events, _ = _cached_call(
                        "timeline_cache", timeline_cache_key,
                        lambda: extract_timeline_events(ai_narrative_result)
                    )
                    st.session_state["timeline_data"] = timeline_events

                if timeline_events:
                    render_timeline_visualization(timeline_events)

                with st.spinner("Running institutional intelligence extraction..."):
                    intelligence_cache_key = _hash_text(master_summary)
                    intelligence_outputs, _ = _cached_call(
                        "intelligence_cache", intelligence_cache_key,
                        lambda: run_universal_intelligence_extraction(master_summary)
                    )
                    st.session_state["intelligence_outputs"] = intelligence_outputs
                    st.session_state["key_metrics"] = intelligence_outputs.get("key_metrics", {})
                    st.session_state["sector_analysis"] = intelligence_outputs.get("sector_analysis", {})
                    st.session_state["risk_analysis"] = intelligence_outputs.get("risk_analysis", [])
                    st.session_state["controversy_analysis"] = intelligence_outputs.get("controversy_analysis", [])

                for module_key in INTELLIGENCE_MODULES:
                    render_intelligence_output(module_key, intelligence_outputs.get(module_key))

                docx_file_stream = generate_docx_download(ai_narrative_result, timeline_events, intelligence_outputs)
                pdf_file_stream = generate_pdf_download(ai_narrative_result, timeline_events, intelligence_outputs)

                export_col1, export_col2 = st.columns(2)
                with export_col1:
                    st.download_button(
                        label="📥 Download as Word Document",
                        data=docx_file_stream,
                        file_name="Financial_Timeline_Investment_Memo.docx",
                        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        use_container_width=True
                    )
                with export_col2:
                    st.download_button(
                        label="📄 Download PDF",
                        data=pdf_file_stream,
                        file_name="Financial_Timeline_Investment_Memo.pdf",
                        mime="application/pdf",
                        use_container_width=True
                    )

                with st.expander("📤 More Export Formats (JSON / CSV / Excel / Markdown)", expanded=False):
                    json_bytes = generate_json_export(ai_narrative_result, timeline_events, intelligence_outputs)
                    md_bytes = generate_markdown_export(ai_narrative_result, timeline_events, intelligence_outputs)
                    csv_bytes = generate_csv_export(timeline_events, intelligence_outputs)
                    excel_bytes = generate_excel_export(
                        timeline_events, intelligence_outputs, intelligence_outputs.get("key_metrics", {})
                    )

                    more_col1, more_col2 = st.columns(2)
                    with more_col1:
                        st.download_button(
                            "🧾 Download JSON", data=json_bytes,
                            file_name="Financial_Timeline_Report.json", mime="application/json",
                            use_container_width=True
                        )
                        st.download_button(
                            "📊 Download CSV", data=csv_bytes,
                            file_name="Financial_Timeline_Report.csv", mime="text/csv",
                            use_container_width=True
                        )
                    with more_col2:
                        st.download_button(
                            "📗 Download Excel", data=excel_bytes,
                            file_name="Financial_Timeline_Report.xlsx",
                            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            use_container_width=True
                        )
                        st.download_button(
                            "📃 Download Markdown", data=md_bytes,
                            file_name="Financial_Timeline_Report.md", mime="text/markdown",
                            use_container_width=True
                        )
            else:
                st.warning("AI generation encountered an error. Please review the message above and try again.")

            with st.expander("🔍 Pipeline Debug Trace", expanded=False):
                debug_entries = st.session_state.get("pipeline_debug_log", [])
                if debug_entries:
                    st.dataframe(
                        pd.DataFrame([
                            {
                                "Stage": e["stage"],
                                "Length": e["length"],
                                "Error Marker?": "⚠️" if e["error_marker_detected"] else "",
                                "Preview (first 1000 chars)": e["preview"],
                            }
                            for e in debug_entries
                        ]),
                        use_container_width=True, hide_index=True
                    )
                else:
                    st.caption("No pipeline stages recorded for this run.")


# =============================================================================
# SECTION 12: Auth
# =============================================================================
def check_login():
    if "authenticated" not in st.session_state:
        st.session_state["authenticated"] = False
    if not st.session_state["authenticated"]:
        st.markdown("🔐 Institutional Terminal Access")
        col_l1, col_l2, col_l3 = st.columns([1, 2, 1])
        with col_l2:
            input_user = st.text_input("Username")
            input_pass = st.text_input("Password", type="password")
            if st.button("🚀 Log In", use_container_width=True):
                if input_user == "admin" and input_pass == "financial_terminal_2026":
                    st.session_state["authenticated"] = True
                    st.rerun()
                else:
                    st.error("❌ Invalid Credentials")
        return False
    return True


if __name__ == "__main__":
    if check_login():
        main()
