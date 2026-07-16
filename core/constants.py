# core/constants.py
"""
Static, non-configurable constants shared across the engine.

These differ from `core.config.EngineSettings` in that they are not
deployment-tunable knobs -- they're fixed product/prompt-engineering
constants that describe *how the engine behaves*, not *what environment
it runs in*.
"""
from __future__ import annotations

# Shared grounding instruction appended to every summarization/merge/
# extraction system prompt so the model stays strictly anchored to the
# supplied source text instead of inventing or generalizing.
GROUNDING_RULE: str = (
    "Base your entire response strictly and exclusively on the source text "
    "provided below. Do not invent, estimate, or generalize beyond what is "
    "explicitly stated. Preserve exact figures, tables, dates, timeline "
    "events, and management commentary verbatim wherever they appear -- do "
    "not paraphrase numbers or round figures. If the source does not "
    "contain enough information for a requested section, explicitly state "
    "\"The uploaded documents do not provide sufficient information.\" for "
    "that section instead of producing generic filler."
)

# Markers the engine uses to detect that a "successful" string response
# from a provider is actually a rendered user-facing error message rather
# than real model output. Centralized here so every layer (validation,
# timeline extraction, UI) agrees on what "this is an error" means instead
# of re-implementing the same three-symbol check in different places.
ERROR_RESPONSE_MARKERS: tuple = ("❌", "🔴", "⚠️")

# Default shape of Streamlit's `st.session_state`. Frontend code seeds
# every key on first run so the rest of the app can assume these keys
# always exist rather than defensively checking for them everywhere.
DEFAULT_SESSION_STATE: dict = {
    "ai_connected": False,
    "ai_provider_used": None,
    "provider_log": [],
    "timeline_data": [],
    "intelligence_outputs": {},
    "key_metrics": {},
    "sector_analysis": {},
    "risk_analysis": [],
    "controversy_analysis": [],
    "summary_cache": {},
    "merge_cache": {},
    "memo_cache": {},
    "timeline_cache": {},
    "intelligence_cache": {},
}
