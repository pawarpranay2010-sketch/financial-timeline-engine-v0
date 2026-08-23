"""
Platrixa
Sprint 15I-C - FYJC Student UI session & refresh persistence
backend/fyjc_student_session.py

The single, pure (non-Streamlit) session-state layer for the FYJC Study /
Verify page. It owns:

* the session-state keys (widget keys are contractually fixed and shared
  with the AppTest / Sprint 14 gate - never rename them),
* the canonical state machine:

      FRESH VISIT        -> ENTRY
      INPUT PROVIDED     -> INPUT_READY
      ANALYSIS COMPLETE  -> RESULT
      VERIFY MODE        -> VERIFYING
      EDIT QUESTION      -> EDITING
      RESET              -> ENTRY

* the stable question fingerprint (sha256 of the normalised question)
  that binds every stored artifact (flow / verdict / accounting check /
  manual facts / analysis error) to the question it was computed for,
* reconcile(): run at the top of every rerun; validates the session and
  drops anything stale,
* reset_session(): the ONLY way to clear everything (Start Over).

Persistence rules enforced here
------------------------------
* RESULT.question_fingerprint == CURRENT.question_fingerprint. A mismatch
  (question typed, edited, corrected, or cleared) discards the old result
  and returns to INPUT_READY / ENTRY. A changed question never displays a
  previous question's result.
* A rerun / browser refresh preserves a valid session; Start Over clears
  it. They are different actions and are never conflated.
* Uploaded binaries do not survive a browser refresh (Streamlit does not
  restore them). Extracted text and typed questions do - they are plain
  session values. The UI asks for a re-upload when only the binary
  existed and never fabricates file persistence.
* Failed analysis never becomes a successful result; it stays in a
  recoverable INPUT_READY state with the question preserved.
* No URLs, no global/shared state, no localStorage: nothing sensitive
  leaves the browser-server session, and one student's session can never
  leak into another's.

This module is pure: it operates on any mutable mapping (a plain dict in
tests, st.session_state in the app) and never imports Streamlit.
"""

from __future__ import annotations

import hashlib
from typing import Any, Mapping, MutableMapping

# ---------------------------------------------------------------------------
# Session-state keys (widget keys are part of the public UI contract -
# the AppTest and Sprint 14 gate pin them by name; never rename these).
# ---------------------------------------------------------------------------

K_MODE = "fte_fyjc_mode"                 # st.radio  - input method
K_QUESTION = "fte_fyjc_question"         # st.text_area - typed/entered text
K_CORRECTED = "fte_fyjc_corrected"       # plain key - student correction
K_DOC_TEXT = "fte_fyjc_doc_text"         # plain key - extracted document text
K_DOC_NAME = "fte_fyjc_doc_name"         # plain key - uploaded file name
K_UPLOAD_KIND = "fte_fyjc_upload_kind"   # plain key - "image" | "document"
K_FLOW = "fte_fyjc_flow"                 # plain key - analysis result dict
K_EDIT = "fte_fyjc_edit"                 # plain key - editing overlay flag
K_MANUAL_FACTS = "fte_fyjc_manual_facts" # plain key - student-entered values
K_VERDICT = "fte_fyjc_verdict"           # plain key - maths verification dict
K_ACCT_VERIFY = "fte_fyjc_acct_verify"   # plain key - accounting checks dict

# Sprint 15I-C additions (all plain keys - no widget contract impact).
K_ANALYSIS_ERROR = "fte_fyjc_analysis_error"   # dict {message, fp} | None
K_FLOW_FP = "fte_fyjc_flow_fp"
K_VERDICT_FP = "fte_fyjc_verdict_fp"
K_ACCT_FP = "fte_fyjc_acct_fp"
K_MANUAL_FACTS_FP = "fte_fyjc_manual_fp"

# Sprint 15I-UI additions (all plain keys - no widget contract impact).
# K_PROJ holds the student-workspace projection of the production
# orchestrate() result; K_GATE_PENDING holds the backend-emitted
# Confidence Gate awaiting a student decision; K_GATE_DECISION holds the
# resolved decision provenance. All three are bound to the question
# fingerprint like every other artifact.
K_PROJ = "fte_fyjc_projection"            # plain key - UI contract dict
K_PROJ_FP = "fte_fyjc_projection_fp"
K_GATE_PENDING = "fte_fyjc_gate_pending"  # plain key - gate payload | None
K_GATE_PENDING_FP = "fte_fyjc_gate_pending_fp"
K_GATE_DECISION = "fte_fyjc_gate_decision"  # plain key - resolved decision
K_GATE_DECISION_FP = "fte_fyjc_gate_decision_fp"

# Sprint 17 additions: multi-transaction problem workflow state.
# K_PROBLEM_WORKFLOW holds the full problem engine result and the
# current workflow position.  It is keyed to the question fingerprint
# so switching questions resets the workflow cleanly.
K_PROBLEM_WORKFLOW = "fte_fyjc_problem_workflow"   # dict - full workflow state
K_PROBLEM_WORKFLOW_FP = "fte_fyjc_problem_workflow_fp"
K_PROBLEM_CURRENT_TX = "fte_fyjc_problem_current_tx"  # int - current transaction index
K_PROBLEM_DECISIONS = "fte_fyjc_problem_decisions"    # dict - student decisions per tx
K_PROBLEM_DECISIONS_FP = "fte_fyjc_problem_decisions_fp"

# Widget keys of the uploaders (reset must clear them too).
K_PHOTO_UPLOAD = "fte_fyjc_photo"
K_DOC_UPLOAD = "fte_fyjc_doc"

# ---------------------------------------------------------------------------
# Stages (the canonical state machine - there is no competing one).
# ---------------------------------------------------------------------------

STAGE_ENTRY = "ENTRY"
STAGE_INPUT_READY = "INPUT_READY"
STAGE_RESULT = "RESULT"
STAGE_VERIFYING = "VERIFYING"
STAGE_EDITING = "EDITING"


# ---------------------------------------------------------------------------
# Fingerprint
# ---------------------------------------------------------------------------


def question_fingerprint(text: str) -> str:
    """A stable, deterministic fingerprint for the effective question.

    Deliberately a hash of the question itself - never a timestamp or a
    random id - so two identical questions always agree and any edit to
    the wording changes the fingerprint.
    """
    normalised = " ".join(str(text or "").strip().lower().split())
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()[:16]


def effective_question(state: Mapping[str, Any]) -> str:
    """The question text that drives the current session.

    A student correction wins over the typed text, which wins over the
    extracted document text - mirroring the historical precedence.
    """
    corrected = str(state.get(K_CORRECTED) or "").strip()
    if corrected:
        return corrected
    typed = str(state.get(K_QUESTION) or "").strip()
    if typed:
        return typed
    return str(state.get(K_DOC_TEXT) or "").strip()


# ---------------------------------------------------------------------------
# Stage derivation (assumes a reconciled state)
# ---------------------------------------------------------------------------


def derive_stage(state: Mapping[str, Any]) -> str:
    """The single stage the session is in right now."""
    if state.get(K_EDIT):
        return STAGE_EDITING
    if state.get(K_VERDICT) or state.get(K_ACCT_VERIFY):
        return STAGE_VERIFYING
    if state.get(K_FLOW):
        return STAGE_RESULT
    if effective_question(state):
        return STAGE_INPUT_READY
    return STAGE_ENTRY


# ---------------------------------------------------------------------------
# Reconciliation - the heart of refresh persistence
# ---------------------------------------------------------------------------


def _drop_pair(state: MutableMapping[str, Any], key: str, fp_key: str) -> None:
    state.pop(key, None)
    state.pop(fp_key, None)


def reconcile(state: MutableMapping[str, Any]) -> str:
    """Validate the session and discard anything stale. Idempotent.

    Called at the top of every rerun (and again after the entry widgets
    render, because the user may have just changed the question). Returns
    the derived stage.

    Artifacts (flow / verdict / accounting checks / manual facts / a
    stored analysis error) are bound to the question they belong to via
    the question fingerprint. Any mismatch - question edited, corrected,
    or cleared - discards the artifact so a previous question's result is
    never shown under a different question.
    """
    question = effective_question(state)
    fp = question_fingerprint(question) if question else None

    error = state.get(K_ANALYSIS_ERROR)
    if error is not None and (
            not isinstance(error, Mapping) or error.get("fp") != fp):
        state.pop(K_ANALYSIS_ERROR, None)

    if state.get(K_FLOW) is not None and (fp is None
                                          or state.get(K_FLOW_FP) != fp):
        _drop_pair(state, K_FLOW, K_FLOW_FP)

    if state.get(K_PROJ) is not None and (fp is None
                                          or state.get(K_PROJ_FP) != fp):
        _drop_pair(state, K_PROJ, K_PROJ_FP)

    if state.get(K_GATE_PENDING) is not None and (
            fp is None or state.get(K_GATE_PENDING_FP) != fp):
        _drop_pair(state, K_GATE_PENDING, K_GATE_PENDING_FP)

    if state.get(K_GATE_DECISION) is not None and (
            fp is None or state.get(K_GATE_DECISION_FP) != fp):
        _drop_pair(state, K_GATE_DECISION, K_GATE_DECISION_FP)

    if state.get(K_VERDICT) is not None and (fp is None
                                             or state.get(K_VERDICT_FP) != fp):
        _drop_pair(state, K_VERDICT, K_VERDICT_FP)

    if state.get(K_ACCT_VERIFY) is not None and (fp is None
                                                 or state.get(K_ACCT_FP) != fp):
        _drop_pair(state, K_ACCT_VERIFY, K_ACCT_FP)

    if state.get(K_MANUAL_FACTS) is not None and (
            fp is None or state.get(K_MANUAL_FACTS_FP) != fp):
        _drop_pair(state, K_MANUAL_FACTS, K_MANUAL_FACTS_FP)

    return derive_stage(state)


# ---------------------------------------------------------------------------
# Reset - the ONLY way to return to a genuinely fresh ENTRY
# ---------------------------------------------------------------------------

_RESET_KEYS = (
    K_MODE, K_QUESTION, K_CORRECTED, K_DOC_TEXT, K_DOC_NAME, K_UPLOAD_KIND,
    K_FLOW, K_EDIT, K_MANUAL_FACTS, K_VERDICT, K_ACCT_VERIFY,
    K_ANALYSIS_ERROR, K_FLOW_FP, K_VERDICT_FP, K_ACCT_FP, K_MANUAL_FACTS_FP,
    K_PHOTO_UPLOAD, K_DOC_UPLOAD,
    K_PROJ, K_PROJ_FP, K_GATE_PENDING, K_GATE_PENDING_FP,
    K_GATE_DECISION, K_GATE_DECISION_FP,
    K_PROBLEM_WORKFLOW, K_PROBLEM_WORKFLOW_FP, K_PROBLEM_CURRENT_TX,
    K_PROBLEM_DECISIONS, K_PROBLEM_DECISIONS_FP,
)


def reset_session(state: MutableMapping[str, Any]) -> None:
    """Clear the entire student session: question, input mode, analysis,
    result, student answer, verification, editing state, uploads, and all
    temporary metadata. Returns the app to a fresh ENTRY."""
    for key in _RESET_KEYS:
        state.pop(key, None)


# ---------------------------------------------------------------------------
# Upload honesty - binaries do not survive a browser refresh
# ---------------------------------------------------------------------------


def upload_recovery_note(state: Mapping[str, Any], kind: str) -> str:
    """A calm note when a previously uploaded binary is not available in
    this run (page refresh). Returns '' when nothing needs saying.

    kind: "image" or "document" - the mode the student is currently in.
    """
    uploaded_kind = state.get(K_UPLOAD_KIND)
    if kind == "image":
        if uploaded_kind == "image":
            return (
                "The photo you uploaded earlier isn't available after a page "
                "refresh. Upload it again if you still need it - your typed "
                "question below is safe."
            )
    elif kind == "document":
        if uploaded_kind != "document":
            return ""
        if not str(state.get(K_DOC_TEXT) or "").strip():
            return (
                "The file you uploaded earlier isn't available after a page "
                "refresh. Upload it again or paste the question below."
            )
        return (
            "The extracted text below is preserved. The original file isn't "
            "restored after a refresh - upload it again only if you need to "
            "view it."
        )
    return ""
