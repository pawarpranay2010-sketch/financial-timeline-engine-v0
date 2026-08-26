"""
Platrixa
Sprint 14 - FYJC Student End-to-End UI
backend/fyjc_student_ui.py

The student-facing Streamlit rendering layer for the FYJC Study /
Verify workflow. All reasoning is delegated to the Sprint 13 FYJC
capability modules through backend.maths.fyjc_student_flow (pure,
deterministic). This module ONLY prepares and renders the student
journey:

    📷 Photo / 📄 PDF / ✍️ Type
        -> What Platrixa understood (editable)
        -> Maths | Book-Keeping flow (steps 1-6 / 1-8)
        -> Final answer
        -> Independent verification ("verify your answer")
        -> Explanation / correction

Session & refresh persistence (Sprint 15I-C)
--------------------------------------------
All session state lives in backend.fyjc_student_session - the single,
pure session layer. It owns the keys, the canonical stage machine
(ENTRY / INPUT_READY / RESULT / VERIFYING / EDITING), and reconcile(),
which is run at the top of every rerun and again after the entry
widgets render. A stable sha256 fingerprint binds every stored artifact
(flow, verdict, accounting checks, manual facts, analysis error) to the
question it was computed for, so:

* a browser refresh / rerun preserves a valid session,
* changing the question discards the previous result (never stale),
* Start Over is the only action that fully clears the session,
* uploaded binaries are honestly reported as unavailable after a
  refresh (extracted text and typed questions are preserved),
* a failed analysis recovers to INPUT_READY instead of faking a result.

Honesty rules implemented here
------------------------------
* No OCR engine is bundled in this deployment. A photo/image is shown to
  the student and clearly labelled as NOT machine-read; the student is
  guided to type/paste the question. Platrixa never pretends it read a photo
  and never guesses text.
* BLOCKED / REVIEW_REQUIRED / UNSUPPORTED states are rendered with exact
  what / why / next-action copy and concrete actions (enter the missing
  value manually, review sources, etc.) - never a guessed answer.
* Technical evidence (formula ids, audit fields, engine internals) is
  hidden behind a collapsed "Verification details" expander so the
  student is never overwhelmed by internal detail by default.
"""

from __future__ import annotations

import html
import os
from typing import Any, Dict, List, Optional

import streamlit as st

from backend.fyjc_student_session import (
    K_MODE, K_QUESTION, K_CORRECTED, K_DOC_TEXT, K_DOC_NAME, K_UPLOAD_KIND,
    K_FLOW, K_EDIT, K_MANUAL_FACTS, K_VERDICT, K_ACCT_VERIFY,
    K_ANALYSIS_ERROR, K_FLOW_FP, K_VERDICT_FP, K_ACCT_FP, K_MANUAL_FACTS_FP,
    K_PROJ, K_PROJ_FP, K_GATE_PENDING, K_GATE_PENDING_FP,
    K_GATE_DECISION, K_GATE_DECISION_FP,
    STAGE_ENTRY,
    derive_stage,
    effective_question,
    question_fingerprint,
    reconcile,
    reset_session,
    upload_recovery_note,
)
from backend.maths.fyjc_ui_contract import (
    STATUS_PRESENTATION,
    debug_graph_payload,
    gate_is_pending,
    project_student_result,
    resolve_confidence_gate,
    validate_problem_integrity,
)
from backend.maths.fyjc_orchestration import orchestrate as fte_orchestrate
from backend.maths.fyjc_student_flow import (
    INVALID_INPUT_MATH,
    build_understanding,
    run_fyjc_student_flow,
    run_fyjc_maths_flow,
    run_fyjc_accounting_flow,
    parse_trial_balance_lines,
    verify_student_journal,
    verify_student_ledger,
    verify_student_trial_balance,
    fyjc_study_topics,
    fyjc_traditional_class,
)

_IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp")
_TEXT_EXT = (".pdf", ".docx", ".txt", ".csv", ".xlsx")

_FYJC_CSS = """
<style>
/* ---- Page identity: one accent, restrained typography, mobile-first ---- */
.fte-fyjc-page-title { font-size: 1.45rem; font-weight: 800;
  letter-spacing: -.01em; margin: .05rem 0 .1rem; }
.fte-fyjc-title { font-size: 1.1rem; font-weight: 700; margin: .55rem 0 .15rem; }
.fte-fyjc-sub { color: var(--fte-muted, #8a94a6); font-size: .95rem;
  margin-bottom: .35rem; }
.fte-fyjc-card { border: 1px solid var(--fte-border, #2b3550);
  border-radius: 10px; padding: .6rem .85rem; margin: .3rem 0; }
.fte-fyjc-step { border-left: 3px solid var(--fte-accent, #4f8cff);
  border-radius: 0 8px 8px 0; padding: .4rem .75rem; margin: .25rem 0;
  background: rgba(79,140,255,.05); }
.fte-fyjc-step b { color: var(--fte-text, #e6ecf5); }
.fte-fyjc-step div { margin: .08rem 0; line-height: 1.45; }
.fte-fyjc-chip { display:inline-block; border-radius: 999px; padding:.1rem .6rem;
  font-size:.78rem; font-weight:700; margin-right:.3rem; }
.fte-fyjc-chip.green { background: rgba(46,204,113,.15); color:#2ecc71; }
.fte-fyjc-chip.amber { background: rgba(255,180,60,.15); color:#ffb43c; }
.fte-fyjc-chip.red   { background: rgba(255,99,99,.15); color:#ff6363; }
.fte-fyjc-chip.blue  { background: rgba(79,140,255,.15); color:#7fb0ff; }
.fte-fyjc-answer { border: 1px solid var(--fte-accent, #4f8cff);
  border-radius: 10px; padding: .7rem .95rem; margin: .45rem 0;
  background: rgba(79,140,255,.08); }
.fte-fyjc-answer-label { color: var(--fte-accent-light, #7fb0ff);
  font-size: .78rem; font-weight: 700; letter-spacing: .05em;
  text-transform: uppercase; }
.fte-fyjc-answer-value { color: var(--fte-text, #e6ecf5);
  font-size: 1.3rem; font-weight: 800; margin-top: .12rem; }
.fte-fyjc-why { border:1px solid #ffb43c; border-radius:10px;
  padding:.55rem .85rem; background: rgba(255,180,60,.06); margin:.4rem 0; }
.fte-fyjc-blocked { border:1px solid #ff6363; border-radius:10px;
  padding:.55rem .85rem; background: rgba(255,99,99,.06); margin:.4rem 0; }
.fte-fyjc-unsupported { border:1px solid var(--fte-border,#2b3550);
  border-radius:10px; padding:.55rem .85rem; background: rgba(138,148,166,.08);
  margin:.4rem 0; }
.fte-fyjc-note { color: var(--fte-muted, #8a94a6); font-size: .85rem; }
.fte-fyjc-journal { width: 100%; border-collapse: collapse; margin: .2rem 0; }
.fte-fyjc-journal th { text-align: left; font-size: .72rem; text-transform: uppercase;
  letter-spacing: .04em; color: var(--fte-muted, #8a94a6); padding: .25rem .4rem;
  border-bottom: 1px solid var(--fte-border, #2b3550); }
.fte-fyjc-journal td { padding: .32rem .4rem;
  border-bottom: 1px solid rgba(43,53,80,.45); }
.fte-fyjc-journal td.amt { text-align: right; font-variant-numeric: tabular-nums; }
.fte-fyjc-whyline { margin: .32rem 0; }

/* ---- Input-mode selector: segmented pills with an obvious selected state */
div[data-testid="stRadio"] { margin: .15rem 0; }
div[data-testid="stRadio"] > div[role="radiogroup"] { gap: .45rem; }
div[data-testid="stRadio"] label {
  border: 1px solid var(--fte-border, #2b3550); border-radius: 10px;
  padding: .5rem .85rem; background: rgba(138,148,166,.05); cursor: pointer; }
div[data-testid="stRadio"] label:hover { background: rgba(138,148,166,.12); }
div[data-testid="stRadio"] label:has(input:checked) {
  border-color: var(--fte-accent, #4f8cff); background: rgba(79,140,255,.14); }
div[data-testid="stRadio"] label:has(input:checked) p {
  color: var(--fte-text, #e6ecf5); font-weight: 600; }

/* ---- Uploaders: compact but clearly tappable --------------------------- */
div[data-testid="stFileUploader"] { margin: .15rem 0; }
div[data-testid="stFileUploaderDropzone"] {
  min-height: 5.25rem !important; border-radius: 10px; }

/* ---- Expanders: calmer, tighter ----------------------------------------- */
div[data-testid="stExpander"] { border: 1px solid var(--fte-border, #2b3550);
  border-radius: 10px; margin: .3rem 0; }
div[data-testid="stExpander"] summary { font-weight: 600; }

/* ---- Tighter markdown rhythm on this page ------------------------------ */
[data-testid="stMarkdownContainer"] p { margin-bottom: .2rem; }

/* ---- Buttons: keep the primary action dominant -------------------------- */
.stButton > button[kind="primary"] { font-weight: 600; }
</style>
"""


def _ensure_css() -> None:
    # Always re-emitted: Streamlit drops elements that are not re-rendered on
    # a rerun, so a one-time injection would leave the page unstyled after the
    # first interaction (click, upload, page switch).
    st.markdown(_FYJC_CSS, unsafe_allow_html=True)


def _chip(label: str, tone: str) -> str:
    return f'<span class="fte-fyjc-chip {tone}">{html.escape(str(label))}</span>'


def _esc(value: Any) -> str:
    return html.escape(str(value if value is not None else ""))


# ---------------------------------------------------------------------------
# Entry stage
# ---------------------------------------------------------------------------


def _extract_document_text(uploaded) -> str:
    """Extract text from an uploaded PDF/DOCX/TXT via the existing
    ingestion pipeline (guarded). Returns '' when nothing readable."""
    try:
        from ingestion.extraction import extract_document
        result = extract_document(uploaded)
        parsed = (result or {}).get("parsed_document") or {}
        text = str(parsed.get("text") or "").strip()
        return text
    except Exception:
        return ""


def _file_size(uploaded) -> str:
    """Human-readable file size for the selected document."""
    try:
        size = int(getattr(uploaded, "size", 0) or 0)
    except (TypeError, ValueError):
        return ""
    if size <= 0:
        return ""
    if size >= 1_000_000:
        return f"{size / 1_000_000:.1f} MB"
    return f"{max(1, size // 1024)} KB"


def _render_how_it_works() -> None:
    """Collapsed, jargon-free explanation of what Platrixa does."""
    with st.expander("How Platrixa works"):
        st.markdown(
            "- Platrixa reads your question and shows what it understood.\n"
            "- It applies the registered formula (Maths) or the golden "
            "rule (Book-Keeping) step by step.\n"
            "- It shows the final answer and lets you check your own "
            "answer against it.\n"
            "- When a value is missing or the question is ambiguous, "
            "Platrixa asks instead of guessing."
        )


def _render_entry(demo: bool, stage: str) -> None:
    st.markdown('<div class="fte-fyjc-page-title">FYJC Study / Verify</div>',
                unsafe_allow_html=True)
    st.markdown(
        '<div class="fte-fyjc-sub">Solve, verify, and understand your '
        'FYJC Maths &amp; Book-Keeping questions.</div>',
        unsafe_allow_html=True,
    )
    _render_how_it_works()

    st.radio(
        "Input method",
        ["📷 Photo", "📄 PDF", "✍️ Enter Question"],
        key=K_MODE,
        horizontal=True,
        label_visibility="collapsed",
        help="Choose how you want to give Platrixa your question.",
    )
    mode = st.session_state[K_MODE]

    if mode.startswith("📷"):
        photo = st.file_uploader(
            "Upload a photo of your question",
            type=["png", "jpg", "jpeg", "webp"],
            key="fte_fyjc_photo",
            help="PNG, JPG, WEBP • Max 200 MB",
        )
        if photo is not None:
            st.session_state[K_UPLOAD_KIND] = "image"
            st.session_state[K_DOC_NAME] = getattr(photo, "name", "photo")
            st.image(photo, caption="Your question photo", width=300)
            st.markdown(
                '<div class="fte-fyjc-note">Photo received. This deployment '
                'does not bundle an OCR engine, so Platrixa will not pretend to '
                'read the photo and will never guess its text. Type the '
                'question below — the photo stays visible as your source.'
                '</div>',
                unsafe_allow_html=True,
            )
        else:
            note = upload_recovery_note(st.session_state, "image")
            if note:
                st.markdown(
                    f'<div class="fte-fyjc-note">{note}</div>',
                    unsafe_allow_html=True,
                )
        st.text_area(
            "Type the question",
            key=K_QUESTION,
            height=110,
            placeholder=(
                "e.g. Calculate the Current Ratio. Current Assets "
                "Rs.5,00,000 and Current Liabilities Rs.2,50,000."
            ),
        )
    elif mode.startswith("📄"):
        doc = st.file_uploader(
            "Upload a question document (PDF, DOCX, TXT)",
            type=["pdf", "docx", "txt"],
            key="fte_fyjc_doc",
            help="Text-based files. Scanned photo-PDFs cannot be read "
                 "without OCR — Platrixa will say so honestly.",
        )
        if doc is not None:
            name = getattr(doc, "name", "document")
            size = _file_size(doc)
            if st.session_state.get(K_DOC_NAME) != name or \
                    st.session_state.get(K_DOC_TEXT) is None:
                text = _extract_document_text(doc)
                st.session_state[K_DOC_NAME] = name
                st.session_state[K_DOC_TEXT] = text
                st.session_state[K_UPLOAD_KIND] = "document"
            text = st.session_state.get(K_DOC_TEXT) or ""
            if text:
                st.markdown(
                    f'<div class="fte-fyjc-card"><b>{_esc(name)}</b>'
                    f'{" · " + _esc(size) if size else ""} — '
                    f'{len(text)} characters of readable text found. '
                    f'<span class="fte-fyjc-note">Review it below, then '
                    f'analyse.</span></div>',
                    unsafe_allow_html=True,
                )
                with st.expander("Show extracted text"):
                    st.code(text[:4000], language=None)
            else:
                st.markdown(
                    '<div class="fte-fyjc-why"><b>No readable text found in '
                    'this file.</b> Platrixa does not bundle an OCR engine, so it '
                    'cannot read scanned or image-only documents — it will '
                    'never guess content.<br/><b>What you can do:</b> type or '
                    'paste the question below, or upload a text-based PDF, '
                    'DOCX, or TXT file.</div>',
                    unsafe_allow_html=True,
                )
        else:
            note = upload_recovery_note(st.session_state, "document")
            if note:
                st.markdown(
                    f'<div class="fte-fyjc-note">{note}</div>',
                    unsafe_allow_html=True,
                )
        st.text_area(
            "Or paste / type the question",
            key=K_QUESTION,
            height=110,
            placeholder="e.g. Purchased goods from Rahul on credit for Rs.10,000.",
        )
    else:
        st.text_area(
            "Enter your question",
            key=K_QUESTION,
            height=140,
            placeholder=(
                "Type or paste your FYJC question here…\n\n"
                "Maths: 'Calculate the Profit Margin. Profit Rs.200 and "
                "Revenue Rs.1,000.'\n"
                "Book-Keeping: 'Purchased goods from Rahul on credit for "
                "Rs.10,000.'"
            ),
        )

    has_input = bool(effective_question(st.session_state).strip())
    col_go, col_reset = st.columns([3, 1])
    with col_go:
        st.button(
            "Analyse question",
            key="fte_fyjc_go",
            width="stretch",
            type="primary",
            disabled=not has_input,
            help=None if has_input else "Type, upload, or paste a question first.",
        )
    with col_reset:
        if stage != STAGE_ENTRY:
            if st.button("Start over", key="fte_fyjc_reset",
                         width="stretch"):
                reset_session(st.session_state)
                st.rerun()


# ---------------------------------------------------------------------------
# Understanding stage
# ---------------------------------------------------------------------------


def _render_understanding(flow: Dict[str, Any]) -> None:
    understanding = flow.get("understanding") or {}
    st.markdown('<div class="fte-fyjc-title">1 · Question understood</div>',
                unsafe_allow_html=True)
    q = effective_question(st.session_state)
    st.markdown(
        f'<div class="fte-fyjc-card">“{_esc(q[:600])}”</div>',
        unsafe_allow_html=True,
    )

    st.markdown(
        '<div class="fte-fyjc-note">What Platrixa understood:</div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        f'<div class="fte-fyjc-card">{_esc(understanding.get("interpretation"))}'
        f'</div>',
        unsafe_allow_html=True,
    )

    domain = understanding.get("domain")
    if domain == "maths":
        domain_label, tone = "Maths", "blue"
    elif domain == "bookkeeping":
        domain_label, tone = "Book-Keeping & Accountancy", "green"
    else:
        domain_label, tone = "Unrecognised", "amber"
    st.markdown(
        f'<div class="fte-fyjc-card">{_chip(domain_label, tone)}'
        f'<span class="fte-fyjc-note">{_esc(understanding.get("reason"))}'
        f'</span></div>',
        unsafe_allow_html=True,
    )

    facts = understanding.get("facts") or []
    if facts:
        import pandas as pd
        st.dataframe(
            pd.DataFrame([
                {"Concept": f.get("concept"),
                 "Value": f.get("display_value"),
                 "Source": f.get("source")}
                for f in facts
            ]),
            width="stretch",
            hide_index=True,
        )
    else:
        st.caption("No 'Concept: value' facts were parsed from the text.")

    # Sprint 15I-R: informational notes are neutral hints, never a
    # warning - a VERIFIED result must not be framed as 'almost there'.
    info_notes = understanding.get("info_notes") or []
    for note in info_notes:
        st.markdown(
            f'<div class="fte-fyjc-note">{_esc(note)}</div>',
            unsafe_allow_html=True,
        )

    # Only BLOCKING concerns raise the 'Almost there' clarification
    # panel (an unregistered maths rate, an uncertain requested figure).
    concerns = understanding.get("concerns") or []
    if concerns:
        st.markdown(
            '<div class="fte-fyjc-why"><b>Almost there — Platrixa needs a little '
            'more clarity before it can solve this.</b></div>',
            unsafe_allow_html=True,
        )
        st.markdown("**What is unclear or missing:**")
        for concern in concerns:
            st.markdown(f"- {_esc(concern)}")
        st.markdown(
            "**What you can do:** correct the wording above with "
            "Correct / Edit, add the missing value, or rephrase the question."
        )

    c1, c2 = st.columns([1, 3])
    with c1:
        if st.button("Correct / Edit", key="fte_fyjc_edit_btn",
                     width="stretch"):
            st.session_state[K_EDIT] = True
            st.rerun()
    if st.session_state.get(K_EDIT):
        corrected = st.text_area(
            "Correct the question (Platrixa will re-interpret it)",
            key="fte_fyjc_question_edit",
            value=effective_question(st.session_state),
            height=120,
        )
        if st.button("Re-analyse corrected question",
                     key="fte_fyjc_reanalyse", type="primary"):
            st.session_state[K_CORRECTED] = corrected.strip()
            st.session_state[K_EDIT] = False
            st.session_state[K_FLOW] = None
            # The question is changing: every artifact from the old question
            # (result, verification, manual values, analysis error) is stale.
            for key in (K_FLOW_FP, K_VERDICT, K_VERDICT_FP, K_ACCT_VERIFY,
                        K_ACCT_FP, K_MANUAL_FACTS, K_MANUAL_FACTS_FP,
                        K_ANALYSIS_ERROR):
                st.session_state.pop(key, None)
            st.rerun()


# ---------------------------------------------------------------------------
# Flow rendering
# ---------------------------------------------------------------------------


def _render_steps(steps: List[Dict[str, Any]]) -> None:
    for step in steps:
        body = step.get("body") or []
        rows = "".join(
            f"<div>· {_esc(line)}</div>" for line in body
        )
        st.markdown(
            f'<div class="fte-fyjc-step"><b>{step.get("number")} — '
            f'{_esc(step.get("title"))}</b>{rows}</div>',
            unsafe_allow_html=True,
        )


def _render_audit(audit: Dict[str, Any]) -> None:
    with st.expander("Verification details"):
        st.caption(
            "Technical detail for anyone who wants it — not needed to use "
            "Platrixa."
        )
        rows = [
            ("Authority", audit.get("authority")),
            ("Formula ID", audit.get("formula_id")),
            ("Formula", audit.get("formula")),
            ("Status", audit.get("status")),
            ("Result", audit.get("result")),
        ]
        for label, value in rows:
            if value not in (None, "", "—"):
                st.markdown(f"**{label}:** {_esc(value)}")
        inputs = audit.get("inputs")
        if inputs:
            st.markdown("**Inputs used:**")
            for row in inputs:
                st.markdown(
                    f"- {_esc(row.get('concept'))} = "
                    f"{_esc(row.get('display_value'))} · "
                    f"{_esc(row.get('status'))} · "
                    f"{_esc(row.get('provenance_tier'))}"
                )


def _render_final_answer(flow: Dict[str, Any]) -> None:
    """A compact, prominent final-answer card for resolved maths."""
    if flow.get("flow") != "maths" or not flow.get("resolved"):
        return
    display = (flow.get("outcome") or {}).get("display_value")
    if display is None:
        return
    st.markdown(
        f'<div class="fte-fyjc-answer">'
        f'<div class="fte-fyjc-answer-label">Final answer</div>'
        f'<div class="fte-fyjc-answer-value">{_esc(display)}</div>'
        f'</div>',
        unsafe_allow_html=True,
    )


def _render_maths_flow(flow: Dict[str, Any]) -> None:
    st.markdown('<div class="fte-fyjc-title">2 · Maths — working</div>',
                unsafe_allow_html=True)
    resolved = bool(flow.get("resolved"))
    status = flow.get("status")
    if resolved:
        st.markdown(
            f'<div class="fte-fyjc-card">{_chip("VERIFIED", "green")} '
            f'{_esc(flow.get("status_label"))}</div>',
            unsafe_allow_html=True,
        )
    elif status == "UNSUPPORTED":
        st.markdown(
            f'<div class="fte-fyjc-card">{_chip("NOT SUPPORTED YET", "amber")}</div>',
            unsafe_allow_html=True,
        )
    else:
        tone = "red" if status == "BLOCKED" else "amber"
        st.markdown(
            f'<div class="fte-fyjc-card">{_chip(status or "", tone)} '
            f'{_esc(flow.get("status_label"))}</div>',
            unsafe_allow_html=True,
        )

    _render_steps(flow.get("steps") or [])

    # Refusal / next-step block
    why_not = flow.get("why_not")
    next_action = flow.get("next_action")
    if why_not:
        st.markdown(
            f'<div class="fte-fyjc-why"><b>Why Platrixa could not answer:</b> '
            f'{_esc(why_not)}<br/><b>What you can do:</b> '
            f'{_esc(next_action)}</div>',
            unsafe_allow_html=True,
        )

    _render_final_answer(flow)

    # Manual value entry for BLOCKED maths (missing inputs)
    if status == "BLOCKED":
        _render_blocked_manual_entry(flow)

    _render_audit(flow.get("audit") or {})


def _render_blocked_manual_entry(flow: Dict[str, Any]) -> None:
    outcome = flow.get("outcome") or {}
    missing = outcome.get("missing") or []
    if not missing:
        return
    st.markdown(
        '<div class="fte-fyjc-blocked"><b>One or two values are missing.</b> '
        'Platrixa cannot calculate without them, and it will not guess. You can '
        'upload the relevant page or enter the verified value manually '
        'below (it will be labelled as student-entered, never as a document '
        'fact).</div>',
        unsafe_allow_html=True,
    )
    manual = dict(st.session_state.get(K_MANUAL_FACTS) or {})
    with st.form("fte_fyjc_manual_form"):
        for concept in missing:
            manual[concept] = st.text_input(
                f"{concept} (enter verified value)", key=f"fte_fyjc_m_{concept}",
                value=str(manual.get(concept) or ""),
            )
        submitted = st.form_submit_button(
            "Re-run with the entered values", type="primary",
            width="stretch",
        )
    if submitted:
        cleaned = {
            concept: value for concept, value in manual.items()
            if str(value or "").strip()
        }
        if cleaned:
            st.session_state[K_MANUAL_FACTS] = cleaned
            st.session_state[K_MANUAL_FACTS_FP] = question_fingerprint(
                effective_question(st.session_state))
            metric = flow.get("metric")
            if metric:
                new_flow = run_fyjc_maths_flow(
                    metric, facts=cleaned,
                    text=effective_question(st.session_state),
                    student_answer=None,
                )
                new_flow["understanding"] = flow.get("understanding")
                st.session_state[K_FLOW] = new_flow
                st.session_state[K_FLOW_FP] = question_fingerprint(
                    effective_question(st.session_state))
            st.rerun()
        else:
            st.warning("Enter at least one value to continue.")


def _fmt_rupee(value: Any) -> str:
    """Format an engine amount as ₹ for the student view."""
    try:
        return f"\u20b9{float(value):,.2f}"
    except (TypeError, ValueError):
        return str(value)


def _render_journal_table(outcome: Dict[str, Any]) -> None:
    """A clean Debit | Amount | Credit | Amount table from the canonical
    journal lines (verbatim engine data - no accounting here)."""
    debits = outcome.get("debit_lines") or []
    credits = outcome.get("credit_lines") or []
    n = max(len(debits), len(credits))
    rows = []
    for i in range(n):
        d = debits[i] if i < len(debits) else {}
        c = credits[i] if i < len(credits) else {}
        d_acct = _esc(d.get("account") or "")
        d_amt = _fmt_rupee(d.get("amount")) if d.get("account") else ""
        c_acct = _esc(c.get("account") or "")
        c_amt = _fmt_rupee(c.get("amount")) if c.get("account") else ""
        rows.append(
            f"<tr><td>{d_acct}</td><td class='amt'>{d_amt}</td>"
            f"<td>{c_acct}</td><td class='amt'>{c_amt}</td></tr>"
        )
    st.markdown(
        '<div class="fte-fyjc-card"><table class="fte-fyjc-journal">'
        "<tr><th>Debit</th><th>Amount</th><th>Credit</th><th>Amount</th></tr>"
        + "".join(rows) + "</table></div>",
        unsafe_allow_html=True,
    )


def _render_amount_breakdown(outcome: Dict[str, Any]) -> None:
    """A 'Trade discount' breakdown straight from the engine's deterministic
    calculation records (BK_LIST_PRICE -> BK_TRADE_DISCOUNT_AMOUNT ->
    BK_NET_TRANSACTION_VALUE). Read-only; no arithmetic is done here."""
    records = {
        r.get("calculation_id"): r
        for r in (outcome.get("calculation_records") or [])
    }
    if "BK_TRADE_DISCOUNT_AMOUNT" not in records:
        return
    list_price = records.get("BK_LIST_PRICE", {}).get("result")
    td = records.get("BK_TRADE_DISCOUNT_AMOUNT", {})
    net = records.get("BK_NET_TRANSACTION_VALUE", {}).get("result")
    if list_price is None or net is None:
        return
    st.markdown("**Trade discount**")
    st.markdown(f"- List price: {_fmt_rupee(list_price)}")
    rate = (td.get("inputs") or {}).get("trade_discount_rate")
    if rate is not None:
        st.markdown(
            f"- Trade discount: {_esc(rate)}% = {_fmt_rupee(td.get('result'))}"
        )
    st.markdown(f"- Net amount: {_fmt_rupee(net)}")


def _render_accounting_answer(flow: Dict[str, Any]) -> None:
    """Sprint 15I-R: the ANSWER comes first - the canonical journal - so a
    verified book-keeping result is immediately readable."""
    st.markdown('<div class="fte-fyjc-title">2 · Answer</div>',
                unsafe_allow_html=True)
    st.markdown(
        f'<div class="fte-fyjc-card">{_chip("VERIFIED", "green")} '
        f'{_esc(flow.get("status_label"))}</div>',
        unsafe_allow_html=True,
    )
    st.markdown("**Journal Entry**")
    _render_journal_table(flow.get("outcome") or {})
    _render_discrepancy_notes(flow.get("outcome") or {})
    _render_bills_notes(flow.get("outcome") or {})
    _render_specialized_notes(flow.get("outcome") or {})


def _render_discrepancy_notes(outcome: Dict[str, Any]) -> None:
    """Sprint 15I-DISC: the Discrepancy Authority's reconciliation /
    correction explanation, rendered under a VERIFIED result. Read-only
    presentation data from the authority - no accounting here, and it is
    never shown as a warning or 'almost there' panel."""
    discrepancy = outcome.get("discrepancy") or {}
    if not discrepancy:
        return
    parts: List[str] = []
    for note in discrepancy.get("notes") or []:
        parts.append(f"<div>· {_esc(note)}</div>")
    for rec in discrepancy.get("reconciliation") or []:
        parts.append(
            f"<div>· {_esc(rec.get('book'))} balance: "
            f"{_esc(str(rec.get('direction'))).upper()} "
            f"{_fmt_rupee(rec.get('amount'))} — "
            f"{_esc(rec.get('effect'))}</div>"
        )
    model = discrepancy.get("correction_model") or {}
    if model.get("recorded") or model.get("should"):
        def _fmt_rows(rows: List[Dict[str, Any]]) -> str:
            return "; ".join(
                f"{_esc(row.get('side', '')).upper()} "
                f"{_esc(row.get('account'))} {_fmt_rupee(row.get('amount'))}"
                for row in rows
            ) or "—"
        parts.append(
            f"<div>· What was recorded: {_fmt_rows(model.get('recorded') or [])}</div>"
        )
        parts.append(
            f"<div>· What should have been recorded: "
            f"{_fmt_rows(model.get('should') or [])}</div>"
        )
        parts.append(
            f"<div>· Correction required: "
            f"{_fmt_rows(model.get('correction') or [])}</div>"
        )
        if model.get("suspense_used") is not None:
            parts.append(
                "<div>· Suspense Account used: "
                + ("YES (trial-balance difference established)"
                   if model.get("suspense_used")
                   else "NO (direct correction)")
                + "</div>"
            )
    if parts:
        st.markdown(
            '<div class="fte-fyjc-card"><b>Discrepancy / Reconciliation</b>'
            + "".join(parts) + "</div>",
            unsafe_allow_html=True,
        )


def _render_bills_notes(outcome: Dict[str, Any]) -> None:
    """Sprint 15I-BILLS: the Bills Authority's lifecycle / maturity /
    discount explanation, rendered under a VERIFIED result. Read-only
    presentation data from the authority - no accounting here, and it is
    never shown as a warning or 'almost there' panel."""
    bills = outcome.get("bills") or {}
    if not bills:
        return
    parts: List[str] = []
    for note in bills.get("notes") or []:
        parts.append(f"<div>· {_esc(note)}</div>")
    states = bills.get("states") or []
    if states:
        parts.append(
            "<div>· Lifecycle: "
            + " → ".join(_esc(s.get("state", ""))
                          + (" (implied)" if s.get("implicit") else "")
                          for s in states)
            + "</div>")
    maturity = bills.get("maturity") or {}
    if maturity:
        parts.append(
            f"<div>· Maturity: {_esc(maturity.get('period'))} + "
            f"{_esc(maturity.get('days_of_grace'))} days of grace"
            + (f" → due {_esc(maturity.get('due_date'))}"
               if maturity.get("due_date") else "")
            + "</div>")
    discount = bills.get("discount") or {}
    if discount:
        parts.append(
            f"<div>· Bank discount ({_esc(discount.get('basis'))}): "
            f"{_esc(discount.get('formula'))} = "
            f"Rs.{_esc(discount.get('discount'))} → proceeds "
            f"Rs.{_esc(discount.get('proceeds'))}</div>")
    roles = bills.get("roles") or {}
    named = {k: v for k, v in roles.items()
             if v and k in ("drawer", "drawee", "acceptor", "endorsee")}
    if named:
        parts.append(
            "<div>· Parties: "
            + ", ".join(f"{_esc(k)} {_esc(v)}"
                         for k, v in named.items())
            + "</div>")
    if parts:
        st.markdown(
            '<div class="fte-fyjc-card"><b>Bills of Exchange</b>'
            + "".join(parts) + "</div>",
            unsafe_allow_html=True,
        )


def _render_specialized_notes(outcome: Dict[str, Any]) -> None:
    """Sprint 15I-SPEC: the Consignment / Joint Venture / Single Entry
    authorities' valuation, sharing and net-worth explanations, rendered
    under a VERIFIED result. Read-only presentation data from the
    authority - no accounting here, and it is never shown as a warning
    or 'almost there' panel."""

    def _calc_rows(calcs: List[Dict[str, Any]]) -> List[str]:
        rows = []
        for calc in calcs:
            label = calc.get("label") or ""
            value = calc.get("value")
            formula = calc.get("formula")
            if label and value is not None:
                text = f"· {label}: {_fmt_rupee(value)}"
                if formula:
                    text += f" ({_esc(formula)})"
                rows.append(f"<div>{text}</div>")
        return rows

    consignment = outcome.get("consignment") or {}
    if consignment:
        parts: List[str] = []
        for note in consignment.get("notes") or []:
            parts.append(f"<div>· {_esc(note)}</div>")
        parts.extend(_calc_rows(consignment.get("calculations") or []))
        if consignment.get("consignee"):
            parts.append(
                f"<div>· Consignee: {_esc(consignment.get('consignee'))}</div>")
        if parts:
            st.markdown(
                '<div class="fte-fyjc-card"><b>Consignment</b>'
                + "".join(parts) + "</div>",
                unsafe_allow_html=True,
            )

    joint_venture = outcome.get("joint_venture") or {}
    if joint_venture:
        parts = []
        for note in joint_venture.get("notes") or []:
            parts.append(f"<div>· {_esc(note)}</div>")
        parts.extend(_calc_rows(joint_venture.get("calculations") or []))
        if joint_venture.get("venturer"):
            parts.append(f"<div>· Venturer (books): "
                         f"{_esc(joint_venture.get('venturer'))}</div>")
        if joint_venture.get("co_venturer"):
            parts.append(f"<div>· Co-venturer: "
                         f"{_esc(joint_venture.get('co_venturer'))}</div>")
        if parts:
            st.markdown(
                '<div class="fte-fyjc-card"><b>Joint Venture</b>'
                + "".join(parts) + "</div>",
                unsafe_allow_html=True,
            )

    single_entry = outcome.get("single_entry") or {}
    if single_entry:
        parts = []
        formula = single_entry.get("formula")
        result = single_entry.get("result")
        direction = single_entry.get("direction") or ""
        if formula:
            parts.append(f"<div>· {_esc(formula)}</div>")
        if result is not None:
            parts.append(
                f"<div>· Result: <b>{_esc(direction.upper())} "
                f"{_fmt_rupee(abs(result))}</b></div>")
        parts.extend(_calc_rows(single_entry.get("calculations") or []))
        parts.append(
            "<div>· Mathematical result - no journal entry required for "
            "the change-in-net-worth calculation.</div>")
        st.markdown(
            '<div class="fte-fyjc-card"><b>Incomplete Records / '
            'Single Entry</b>'
            + "".join(parts) + "</div>",
            unsafe_allow_html=True,
        )


def _render_accounting_why(flow: Dict[str, Any]) -> None:
    """Sprint 15I-R: a simple per-line explanation sourced from the engine's
    own WHY text (never duplicated in the UI), then the golden rule, then
    the trade-discount breakdown when the engine produced one."""
    outcome = flow.get("outcome") or {}
    st.markdown('<div class="fte-fyjc-title">Why?</div>',
                unsafe_allow_html=True)
    for line in (outcome.get("debit_lines") or []) + \
            (outcome.get("credit_lines") or []):
        account = line.get("account")
        if not account:
            continue
        side = "Debit" if line.get("side") == "debit" else "Credit"
        hint = line.get("side_hint") or line.get("rule") or ""
        st.markdown(
            f'<div class="fte-fyjc-whyline"><b>{side} {_esc(account)} '
            f'{_fmt_rupee(line.get("amount"))}</b> — {_esc(hint)}</div>',
            unsafe_allow_html=True,
        )
    rule = outcome.get("rule")
    if rule:
        st.markdown(f"**Golden rule applied:** {_esc(rule)}")
    _render_amount_breakdown(outcome)


def _render_accounting_flow(flow: Dict[str, Any]) -> None:
    status = flow.get("status")
    if status == "VERIFIED":
        # Sprint 15I-R: Answer -> Why? -> detailed reasoning (expander).
        _render_accounting_answer(flow)
        _render_accounting_why(flow)
        with st.expander("▸ Show detailed reasoning"):
            st.caption(
                "The full 8-step working — kept for anyone who wants the "
                "audit trail."
            )
            _render_steps(flow.get("steps") or [])
            _render_audit(flow.get("audit") or {})
        return

    st.markdown(
        '<div class="fte-fyjc-title">2 · Book-Keeping — reasoning</div>',
        unsafe_allow_html=True,
    )
    if status == "BLOCKED":
        st.markdown(
            f'<div class="fte-fyjc-card">{_chip("BLOCKED", "red")} '
            f'{_esc(flow.get("status_label"))}</div>',
            unsafe_allow_html=True,
        )
    elif status == INVALID_INPUT_MATH:
        # Sprint 15I-VY: a stated MATHEMATICAL contradiction is its own
        # refusal class - never framed as a vague "REVIEW REQUIRED".
        st.markdown(
            f'<div class="fte-fyjc-card">{_chip("INVALID INPUT (MATH)", "red")} '
            f'{_esc(flow.get("status_label"))}</div>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            f'<div class="fte-fyjc-card">{_chip("REVIEW REQUIRED", "amber")} '
            f'{_esc(flow.get("status_label"))}</div>',
            unsafe_allow_html=True,
        )

    _render_steps(flow.get("steps") or [])

    why_not = flow.get("why_not")
    if why_not:
        st.markdown(
            f'<div class="fte-fyjc-why"><b>Why Platrixa could not decide:</b> '
            f'{_esc(why_not)}<br/><b>What you can do:</b> '
            f'{_esc(flow.get("next_action"))}</div>',
            unsafe_allow_html=True,
        )

    _render_audit(flow.get("audit") or {})


# ---------------------------------------------------------------------------
# Independent verification (Sprint 14 section 8)
# ---------------------------------------------------------------------------


def _render_verification(flow: Dict[str, Any]) -> None:
    st.markdown('<div class="fte-fyjc-title">3 · Verify your answer</div>',
                unsafe_allow_html=True)
    if flow.get("flow") == "maths":
        _render_maths_verify(flow)
    elif flow.get("flow") == "accounting":
        _render_accounting_verify(flow)
    else:
        st.caption("Verification is available once a question is resolved.")


def _render_maths_verify(flow: Dict[str, Any]) -> None:
    if not flow.get("resolved"):
        st.caption(
            "This question was not resolved, so there is nothing to verify "
            "against yet — fix the inputs above first."
        )
        return
    st.markdown(
        "Enter your own answer (e.g. `20` or `20.00`) and Platrixa compares it "
        "to the verified value."
    )
    answer = st.text_input("Your answer", key="fte_fyjc_verify_answer",
                           placeholder="e.g. 20")
    verdict = st.session_state.get(K_VERDICT) or {}
    if st.button("Verify", key="fte_fyjc_verify_btn", type="primary",
                 width="stretch"):
        metric = flow.get("metric")
        if metric:
            v = run_fyjc_maths_flow(
                metric,
                facts=st.session_state.get(K_MANUAL_FACTS) or None,
                text=effective_question(st.session_state),
                student_answer=answer.strip() or None,
            )
            fp = question_fingerprint(effective_question(st.session_state))
            st.session_state[K_VERDICT] = {
                "verdict": v.get("verdict"),
                "student_display": (v.get("audit") or {}).get("student_display"),
                "correct_answer": (v.get("audit") or {}).get("correct_answer"),
                "mismatch": (v.get("audit") or {}).get("mismatch"),
            }
            st.session_state[K_VERDICT_FP] = fp
            st.rerun()
    _render_verdict(verdict)


def _render_verdict(verdict: Dict[str, Any]) -> None:
    v = verdict.get("verdict")
    if not v:
        return
    if v == "CORRECT":
        st.success("Your answer matches the verified value.")
        st.markdown(
            f"**Your answer:** {_esc(verdict.get('student_display'))}"
        )
        verified = verdict.get("correct_answer") or verdict.get(
            "student_display")
        st.markdown(f"**Platrixa verification:** {_esc(verified)}")
    elif v == "INCORRECT":
        st.error("Your answer does not match the verified value.")
        st.markdown(
            f"**Your answer:** {_esc(verdict.get('student_display'))}"
        )
        st.markdown(
            f"**Correct answer:** {_esc(verdict.get('correct_answer'))}"
        )
        if verdict.get("mismatch"):
            st.markdown(
                f"**First mistake:** {_esc(verdict.get('mismatch'))}"
            )
    else:
        st.info(_esc(verdict.get("mismatch") or "Could not verify that answer."))


def _reference_entries(flow: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The engine's own treatment as the reference journal entry."""
    outcome = flow.get("outcome") or {}
    entry = {
        "debits": [
            {"account": line.get("account"), "amount": line.get("amount")}
            for line in outcome.get("debit_lines") or []
            if line.get("account")
        ],
        "credits": [
            {"account": line.get("account"), "amount": line.get("amount")}
            for line in outcome.get("credit_lines") or []
            if line.get("account")
        ],
    }
    return [entry] if entry["debits"] and entry["credits"] else []


def _fmt_amount(value: Any) -> str:
    """Format a reference amount for display (guarded)."""
    try:
        return f"{float(value):,.2f}"
    except (TypeError, ValueError):
        return _esc(value)


def _save_acct_verify(result: Dict[str, Any]) -> None:
    """Persist accounting-verification results with their question binding.

    The result dict must be written back to session_state explicitly (a
    freshly-created local dict would be lost on the rerun triggered by the
    verify button), and bound to the current question via fingerprint so a
    changed question invalidates it.
    """
    st.session_state[K_ACCT_VERIFY] = result
    st.session_state[K_ACCT_FP] = question_fingerprint(
        effective_question(st.session_state))


def _render_accounting_verify(flow: Dict[str, Any]) -> None:
    outcome = flow.get("outcome") or {}
    entries = _reference_entries(flow)
    q = effective_question(st.session_state)
    result = st.session_state.get(K_ACCT_VERIFY) or {}

    # Sprint 15I-R: a verified engine answer leads with the Platrixa check.
    verification = flow.get("verification") or {}
    if flow.get("status") == "VERIFIED" and verification:
        st.markdown(
            f'<div class="fte-fyjc-card">{_chip("VERIFIED", "green")} '
            f'Platrixa verified this entry</div>',
            unsafe_allow_html=True,
        )
        td = verification.get("total_debit")
        tc = verification.get("total_credit")
        if td is not None and tc is not None:
            st.markdown(
                f"**Debit {td:,.2f} = Credit {tc:,.2f}** — the entry is "
                "balanced."
            )

    # Sprint 15I-R: the student self-checks move behind one expander. The
    # checks themselves (journal / ledger / trial balance) are unchanged.
    with st.expander("▸ Check my answer"):
        st.markdown("Choose the check you want to perform:")

        # --- Journal entry check -----------------------------------------
        with st.expander(
                "Check a journal entry (Debit = Credit + direction)"):
            row1 = st.columns(2)
            with row1[0]:
                d1a = st.text_input("Debit account 1", key="fte_fyjc_jd1a",
                                    placeholder="Purchases")
                d1v = st.text_input("Debit amount 1", key="fte_fyjc_jd1v",
                                    placeholder="10000")
            with row1[1]:
                c1a = st.text_input("Credit account 1",
                                    key="fte_fyjc_jc1a",
                                    placeholder="Rahul")
                c1v = st.text_input("Credit amount 1",
                                    key="fte_fyjc_jc1v",
                                    placeholder="10000")
            row2 = st.columns(2)
            with row2[0]:
                d2a = st.text_input("Debit account 2",
                                    key="fte_fyjc_jd2a")
                d2v = st.text_input("Debit amount 2",
                                    key="fte_fyjc_jd2v")
            with row2[1]:
                c2a = st.text_input("Credit account 2",
                                    key="fte_fyjc_jc2a")
                c2v = st.text_input("Credit amount 2",
                                    key="fte_fyjc_jc2v")
            if st.button("Verify journal entry", key="fte_fyjc_jv_btn",
                         width="stretch"):
                jv = verify_student_journal(
                    q,
                    [d1a, d2a], [d1v, d2v], [c1a, c2a], [c1v, c2v],
                )
                result = dict(st.session_state.get(K_ACCT_VERIFY) or {})
                result["journal"] = jv
                _save_acct_verify(result)
                st.rerun()
            jv = result.get("journal")
            if jv:
                _render_journal_verdict(jv, entries)

        # --- Ledger balance check ----------------------------------------
        with st.expander("Check a ledger balance"):
            lr = st.columns(2)
            with lr[0]:
                acc = st.text_input("Account", key="fte_fyjc_lacc",
                                    placeholder="Cash")
            with lr[1]:
                bal = st.text_input("Your balance", key="fte_fyjc_lbal",
                                    placeholder="50000")
            side = st.selectbox("Side", ["Dr", "Cr"],
                                key="fte_fyjc_lside")
            if st.button("Verify ledger balance", key="fte_fyjc_lv_btn",
                         width="stretch"):
                lv = verify_student_ledger(acc, bal, side, entries)
                result = dict(st.session_state.get(K_ACCT_VERIFY) or {})
                result["ledger"] = lv
                _save_acct_verify(result)
                st.rerun()
            lv = result.get("ledger")
            if lv:
                _render_verdict_entry(lv)

        # --- Trial balance check -----------------------------------------
        with st.expander(
                "Check a trial balance (one line per account)"):
            st.caption(
                "One account per line: `Account, Dr amount, Cr amount` — "
                "e.g. `Cash, 50000, 0` and `Capital, 0, 50000`."
            )
            tb_text = st.text_area(
                "Your trial balance lines", key="fte_fyjc_tb_lines",
                height=90,
                placeholder="Cash, 50000, 0\nCapital, 0, 50000",
            )
            if st.button("Verify trial balance", key="fte_fyjc_tbv_btn",
                         width="stretch"):
                tv = verify_student_trial_balance(tb_text, entries)
                result = dict(st.session_state.get(K_ACCT_VERIFY) or {})
                result["trial_balance"] = tv
                _save_acct_verify(result)
                st.rerun()
            tv = result.get("trial_balance")
            if tv:
                _render_verdict_entry(tv)

        # --- Built-in consistency summary --------------------------------
        if flow.get("verification"):
            v = flow["verification"]
            st.markdown(
                f"**Platrixa's own check:** Debit {v.get('total_debit'):,.2f} = "
                f"Credit {v.get('total_credit'):,.2f} — {v.get('verdict')}"
            )
        if not (outcome.get("debit_lines")
                or outcome.get("credit_lines")):
            st.caption(
                "The transaction was not resolved, so reference checks are "
                "limited — fix the description above first."
            )


def _render_journal_verdict(jv: Dict[str, Any],
                            entries: Optional[List[Dict[str, Any]]] = None) -> None:
    verdict = jv.get("verdict")
    if verdict == "CORRECT":
        st.success("The journal entry is correct and follows the golden rule.")
        st.markdown(f"**Rule:** {_esc(jv.get('rule'))}")
    elif verdict == "INCORRECT":
        st.error(f"{_esc(jv.get('what'))} — {_esc(jv.get('why_not'))}")
        ref = (entries or [{}])[0]
        ref_lines = [
            f"Dr {_esc(d.get('account'))} {_fmt_amount(d.get('amount'))}"
            for d in ref.get("debits") or []
        ] + [
            f"Cr {_esc(c.get('account'))} {_fmt_amount(c.get('amount'))}"
            for c in ref.get("credits") or []
        ]
        if ref_lines:
            st.markdown("**Platrixa's journal:** " + " · ".join(ref_lines))
    elif verdict == "BALANCED":
        st.info(f"{_esc(jv.get('what'))} — {_esc(jv.get('why_not'))}")
    else:
        st.info(_esc(jv.get("why_not") or "The entry could not be verified."))
    td = jv.get("total_debit")
    tc = jv.get("total_credit")
    if td is not None and tc is not None:
        st.markdown(f"Total Debit {td:,.2f} vs Total Credit {tc:,.2f}")


def _render_verdict_entry(v: Dict[str, Any]) -> None:
    verdict = v.get("verdict")
    if verdict == "CORRECT":
        st.success(f"{_esc(v.get('what'))}")
    elif verdict == "INCORRECT":
        st.error(f"{_esc(v.get('what'))} — {_esc(v.get('why_not'))}")
    else:
        st.info(_esc(v.get("why_not") or "Could not verify that check."))


# ---------------------------------------------------------------------------
# Study surface (supported topics) - collapsed, student-friendly categories
# ---------------------------------------------------------------------------


def _render_study_topics() -> None:
    with st.expander("What Platrixa can verify", expanded=False):
        topics = fyjc_study_topics()
        st.markdown("**Maths — financial calculations:**")
        st.markdown(" · ".join(f"`{m}`" for m in topics["maths"]))
        st.markdown("**Book-Keeping — journal, ledger & trial balance:**")
        for topic in topics["bookkeeping"]:
            st.markdown(f"- {topic}")
        st.markdown(
            "**Answer verification:** enter your own answer and Platrixa "
            "tells you whether it matches, with the first mistake if it "
            "does not."
        )
        st.caption(
            "Anything else is refused deterministically — Platrixa never "
            "invents a formula or a value."
        )


# ---------------------------------------------------------------------------
# Recoverable error state (refresh during/before analysis never fakes a result)
# ---------------------------------------------------------------------------


def _render_recoverable_error(error: Dict[str, Any]) -> None:
    st.markdown(
        '<div class="fte-fyjc-title">2 · Analysis</div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        f'<div class="fte-fyjc-why"><b>Platrixa couldn’t finish reading this '
        f'question.</b><br/>{_esc(error.get("message"))}</div>',
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Page entry
# ---------------------------------------------------------------------------


def render_fyjc_student_ui(demo: bool = False, landing: bool = False) -> None:
    """The FYJC page. Sprint 15I-UI: Study / Verify is now the Student
    Interaction Contract workspace - a projection of the production
    orchestrate() boundary with the backend-owned Confidence Gate.

    landing=True renders the pure student workspace (single text area,
    no input-mode radio) - used by the app entrance so the app opens
    directly into the workspace with no login/onboarding. landing=False
    keeps the released input-mode radio (Photo / PDF / typed) for the
    signed-in workspace page.

    Practice and Teacher Dashboard (Sprint 15I-I) are unchanged."""
    _ensure_css()

    if landing:
        _render_15i_student_workspace(demo, landing=True)
        return

    section = st.session_state.get("fte_fyjc_section")
    if section not in ("Study / Verify", "Practice", "Teacher Dashboard"):
        section = "Study / Verify"
    st.segmented_control(
        "FYJC section",
        options=["Study / Verify", "Practice", "Teacher Dashboard"],
        key="fte_fyjc_section",
        label_visibility="collapsed",
    )
    section = st.session_state["fte_fyjc_section"]
    if section == "Practice":
        from backend.fyjc_practice_ui import render_practice_section
        render_practice_section(demo=demo)
        return
    if section == "Teacher Dashboard":
        from backend.fyjc_practice_ui import render_teacher_section
        render_teacher_section(demo=demo)
        return

    _render_15i_student_workspace(demo, landing=False)


def _render_refusal(flow: Dict[str, Any]) -> None:
    status = flow.get("status")
    tone = ("red" if status in ("BLOCKED", INVALID_INPUT_MATH)
            else "amber")
    st.markdown(
        '<div class="fte-fyjc-title">Platrixa couldn’t solve this one</div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        f'<div class="fte-fyjc-card">{_chip(status or "REFUSED", tone)} '
        f'{_esc(flow.get("status_label"))}</div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        f'<div class="fte-fyjc-unsupported"><b>{_esc(flow.get("what"))}</b>'
        f'<br/>{_esc(flow.get("why_not"))}'
        f'<br/><b>What you can do:</b> {_esc(flow.get("next_action"))}</div>',
        unsafe_allow_html=True,
    )


# ===========================================================================
# Sprint 15I-UI - Student Interaction Contract workspace
#
# The student workspace is a PROJECTION of the production boundary
# (backend.maths.fyjc_orchestration.orchestrate) rendered through
# backend.maths.fyjc_ui_contract. The UI holds ZERO accounting authority:
# it never calculates a journal, never infers an account, never invents an
# amount, never resolves ambiguity by itself and never generates accounting
# rules. The only user decision the UI can submit is a Confidence Gate
# choice (backend-validated via resolve_confidence_gate).
# ===========================================================================

_15I_CSS = """
<style>
/* ---- Sprint 15I-UI: calm accounting-workspace typography ------------- */
.fte-15i-title { font-size: 1.02rem; font-weight: 750; margin: 1.1rem 0 .3rem;
  letter-spacing: -.01em; }
.fte-15i-title-lg { font-size: 1.6rem; font-weight: 800; margin: .4rem 0 .15rem;
  letter-spacing: -.02em; }
.fte-15i-sub { color: var(--fte-muted, #8a94a6); font-size: .95rem;
  margin-bottom: .9rem; line-height: 1.5; }
.fte-15i-label { color: var(--fte-muted, #8a94a6); font-size: .82rem;
  font-weight: 650; }
.fte-15i-row { padding: .28rem 0; border-bottom: 1px solid
  rgba(43,53,80,.35); }
.fte-15i-note { color: var(--fte-muted, #8a94a6); font-size: .88rem;
  margin: .3rem 0; line-height: 1.5; }
.fte-15i-ok { color: #2ecc71; font-size: 1.35rem; font-weight: 800;
  margin: .15rem 0 .1rem; }
.fte-15i-chip { display: inline-block; border-radius: 999px;
  padding: .12rem .65rem; font-size: .76rem; font-weight: 750;
  letter-spacing: .03em; margin-right: .4rem; }
.fte-15i-chip.green { background: rgba(46,204,113,.14); color: #2ecc71; }
.fte-15i-chip.amber { background: rgba(255,180,60,.14); color: #ffb43c; }
.fte-15i-chip.red { background: rgba(255,99,99,.14); color: #ff6363; }
.fte-15i-chip.neutral { background: rgba(138,148,166,.14);
  color: #aab4c6; }
.fte-15i-gate-head { font-size: 1.25rem; font-weight: 800; margin: .35rem 0;
  letter-spacing: -.01em; }
.fte-15i-whyline { margin: .3rem 0; line-height: 1.5; font-size: .93rem; }
.fte-15i-calcline { margin: .22rem 0; line-height: 1.45; font-size: .9rem;
  color: var(--fte-text, #e6ecf5); }
.fte-15i-calc-muted { color: var(--fte-muted, #8a94a6); font-size: .82rem; }
.fte-15i-state { margin: .4rem 0; line-height: 1.55; }
.fte-15i-hero { font-size: 1.05rem; font-weight: 800; letter-spacing: .02em;
  color: var(--fte-accent, #4f8cff); }

/* Keep the result area unboxed: hairline separators instead of cards. */
div[data-testid="stVerticalBlock"] > div:has(> .fte-15i-row) {
  border-left: 2px solid rgba(79,140,255,.25); padding-left: .6rem;
  margin: .2rem 0 .4rem; }
</style>
"""


def _ensure_15i_css() -> None:
    st.markdown(_15I_CSS, unsafe_allow_html=True)


def _compute_projection(question: str) -> Dict[str, Any]:
    """Run the production boundary once and project it into the student
    UI contract. Deterministic - the UI never reshapes accounting data.

    Sprint 16: detects multi-transaction problems (multiple sentences /
    semicolons) and routes them through the stateful problem engine
    instead of the single-transaction kernel."""
    import re as _re
    from backend.maths.fyjc_problem_engine import process_problem

    # Heuristic: a multi-transaction problem has 2+ sentence boundaries
    # (period+space followed by capital, or semicolons) or contains
    # opening-balance vocabulary.
    _multi_tx = bool(
        _re.search(r"[.;]\s+[A-Z]", question)
        or _re.search(r"\bbalances?\s+(?:as|on)\b", question, _re.I)
        or question.count(".") >= 2
        or question.count(";") >= 1
    )

    if _multi_tx:
        result = process_problem(question)
        # Flatten into a single-transaction-compatible projection for the
        # existing UI contract.  The first VERIFIED transaction is shown;
        # REVIEW_REQUIRED / informational transactions are summarized.
        verified = [t for t in result["transactions"]
                    if t["status"] == "VERIFIED"]
        if verified:
            # Use the last verified transaction as the primary result
            primary = verified[-1]
            _jnl = primary.get("journal") or {}
            single_result = {
                "status": primary["status"],
                "journal": _jnl,
                "why_not": None,
                "next_action": primary.get("next_action"),
                # Sprint 35: copy journal lines to top level so the UI
                # contract (_journal / _calculation) can read them.
                "debit_lines": _jnl.get("debit_lines", []),
                "credit_lines": _jnl.get("credit_lines", []),
                "calculation_records": _jnl.get("calculation_records", []),
            }
        else:
            # No verified transactions - surface first non-informational
            non_info = [t for t in result["transactions"]
                        if t.get("event_type") not in
                        ("INFORMATIONAL_EVENT", "OPENING_BALANCE")]
            primary = non_info[0] if non_info else result["transactions"][0]
            _jnl = primary.get("journal") or {}
            single_result = {
                "status": primary["status"],
                "journal": _jnl,
                "why_not": primary.get("why_not"),
                "next_action": primary.get("next_action"),
                "debit_lines": _jnl.get("debit_lines", []),
                "credit_lines": _jnl.get("credit_lines", []),
                "calculation_records": _jnl.get("calculation_records", []),
            }
        # Attach problem-level metadata for the UI to render
        single_result["problem_engine"] = {
            "problem_status": result["problem_status"],
            "transactions": result["transactions"],
            "ledger_snapshot": result["ledger_snapshot"],
            "deterministic": result["deterministic"],
        }
        return project_student_result(single_result, question)

    result = fte_orchestrate(question)
    return project_student_result(result, question)


def _store_projection(projection: Dict[str, Any],
                      question: str) -> None:
    fp = question_fingerprint(question)
    st.session_state[K_PROJ] = projection
    st.session_state[K_PROJ_FP] = fp
    if gate_is_pending(projection):
        st.session_state[K_GATE_PENDING] = projection.get("confidence_gate")
        st.session_state[K_GATE_PENDING_FP] = fp
    else:
        st.session_state.pop(K_GATE_PENDING, None)
        st.session_state.pop(K_GATE_PENDING_FP, None)


def _legacy_flow(question: str) -> Dict[str, Any]:
    """The Sprint 14 flow dict, computed on demand and cached. It powers
    the released 'Check my answer' verification widgets only - the
    primary rendering is the 15I-UI projection."""
    if st.session_state.get(K_FLOW) is not None:
        return st.session_state[K_FLOW]
    flow = run_fyjc_student_flow(question)
    st.session_state[K_FLOW] = flow
    st.session_state[K_FLOW_FP] = question_fingerprint(question)
    return flow


# ---------------------------------------------------------------------------
# UniversalInput
# ---------------------------------------------------------------------------


def _render_landing_input(demo: bool, stage: str) -> None:
    """The first interaction: a single text area. No login, no onboarding,
    no photo / PDF / OCR - just the question."""
    st.markdown('<div class="fte-15i-title-lg">What are you working on?</div>',
                unsafe_allow_html=True)
    st.markdown(
        '<div class="fte-15i-sub">Type or paste an accounting question. '
        'Platrixa verifies the result deterministically - and when the textbook '
        'is unclear, it asks exactly the one thing it needs.</div>',
        unsafe_allow_html=True,
    )
    st.text_area(
        "Enter your question",
        key=K_QUESTION,
        height=130,
        placeholder=(
            "e.g. Sold goods to Rahul for Rs.10,000 at 18% GST.\n\n"
            "Purchased goods from Mark on credit for Rs.50,000 at 10% "
            "trade discount."
        ),
    )
    has_input = bool(effective_question(st.session_state).strip())
    col_go, col_reset = st.columns([3, 1])
    with col_go:
        st.button(
            "Analyse question",
            key="fte_fyjc_go",
            width="stretch",
            type="primary",
            disabled=not has_input,
            help=None if has_input else "Type or paste a question first.",
        )
    with col_reset:
        if stage != STAGE_ENTRY:
            if st.button("Start over", key="fte_fyjc_reset",
                         width="stretch"):
                reset_session(st.session_state)
                st.rerun()


# ---------------------------------------------------------------------------
# UnderstandingView
# ---------------------------------------------------------------------------


def _render_understanding_view(projection: Dict[str, Any]) -> None:
    st.markdown('<div class="fte-15i-title">Understanding</div>',
                unsafe_allow_html=True)
    understanding = projection.get("understanding") or {}
    rows: List[tuple] = []
    if understanding.get("transaction_type"):
        rows.append(("Transaction type", understanding["transaction_type"]))
    if understanding.get("parties"):
        rows.append(("Parties", ", ".join(understanding["parties"])))
    if understanding.get("amounts"):
        rows.append(("Amounts", ", ".join(
            a.get("display") or _esc(a.get("original") or "")
            for a in understanding["amounts"])))
    if understanding.get("rates"):
        rows.append(("Rates", ", ".join(
            r.get("display") or "" for r in understanding["rates"])))
    if understanding.get("taxes"):
        rows.append(("Taxes", ", ".join(understanding["taxes"])))
    if understanding.get("fractions"):
        rows.append(("Payment fraction", ", ".join(
            f.get("display") or "" for f in understanding["fractions"])))
    if understanding.get("payment"):
        rows.append(("Payment method", ", ".join(
            understanding["payment"])))
    if understanding.get("historical"):
        rows.append(("Historical facts", ", ".join(
            understanding["historical"])))
    if understanding.get("accounts"):
        rows.append(("Accounts identified", ", ".join(
            understanding["accounts"])))
    if not rows:
        st.caption("Platrixa could not extract structured facts from this "
                   "question.")
        return
    for label, value in rows:
        c = st.columns([2, 5], gap="medium")
        with c[0]:
            st.markdown(
                f'<div class="fte-15i-row"><span class="fte-15i-label">'
                f'{_esc(label)}</span></div>',
                unsafe_allow_html=True,
            )
        with c[1]:
            st.markdown(
                f'<div class="fte-15i-row">{_esc(value)}</div>',
                unsafe_allow_html=True,
            )


# ---------------------------------------------------------------------------
# ConfidenceGate - the ONLY component that submits a user decision
# ---------------------------------------------------------------------------


def _render_confidence_gate(projection: Dict[str, Any],
                            question: str) -> None:
    gate = projection.get("confidence_gate") or {}
    alternatives = gate.get("alternatives") or []
    if not alternatives:
        return
    st.markdown('<div class="fte-15i-gate-head">I need one clarification</div>',
                unsafe_allow_html=True)
    if gate.get("segment"):
        st.markdown(
            f'<div class="fte-15i-note">“{_esc(gate["segment"])}”</div>',
            unsafe_allow_html=True,
        )
    st.markdown(
        f'<div class="fte-15i-state">{_esc(gate.get("question"))}</div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        f'<div class="fte-15i-note">{_esc(gate.get("dependency"))}</div>',
        unsafe_allow_html=True,
    )
    labels = [alt.get("label") or "" for alt in alternatives]
    st.radio(
        "Choose the accounting meaning",
        labels,
        key="fte_fyjc_gate_choice",
        label_visibility="collapsed",
    )
    selected = st.session_state.get("fte_fyjc_gate_choice")
    if selected:
        for alt in alternatives:
            if alt.get("label") == selected:
                st.markdown(
                    f'<div class="fte-15i-note">{_esc(alt.get("effect"))}</div>',
                    unsafe_allow_html=True,
                )
    if st.button("Confirm", key="fte_fyjc_gate_confirm", type="primary",
                 width="stretch"):
        choice = st.session_state.get("fte_fyjc_gate_choice")
        decision_id = next(
            (alt.get("id") for alt in alternatives
             if alt.get("label") == choice),
            None,
        )
        if decision_id:
            resolved = resolve_confidence_gate(
                question, gate.get("gate_id"), decision_id)
            _store_projection(resolved, question)
            st.session_state[K_GATE_DECISION] = resolved.get(
                "gate_resolution")
            st.session_state[K_GATE_DECISION_FP] = question_fingerprint(
                question)
            st.rerun()


# ---------------------------------------------------------------------------
# VerifiedResult / JournalEntryView / VerificationView / WhyView /
# CalculationView
# ---------------------------------------------------------------------------


def _render_journal_entry_view(projection: Dict[str, Any]) -> None:
    """Aligned Account | Debit | Credit columns from the backend's verified
    journal lines - the UI never creates or reorders accounting lines."""
    rows = (projection.get("journal") or {}).get("rows") or []
    if not rows:
        st.caption("No journal lines were produced.")
        return
    header = st.columns([4, 2, 2], gap="medium")
    header[0].markdown("**Account**")
    header[1].markdown("**Debit**")
    header[2].markdown("**Credit**")
    for row in rows:
        c = st.columns([4, 2, 2], gap="medium")
        c[0].markdown(row.get("account") or "")
        if row.get("side") == "debit":
            c[1].markdown(row.get("display") or "")
        else:
            c[2].markdown(row.get("display") or "")


def _render_verification_view(projection: Dict[str, Any]) -> None:
    st.markdown('<div class="fte-15i-title">Verification</div>',
                unsafe_allow_html=True)
    verification = projection.get("verification") or {}
    st.markdown('<div class="fte-15i-ok">✓ Verified</div>',
                unsafe_allow_html=True)
    st.markdown(
        f'<div class="fte-15i-state">{_esc(verification.get("statement"))}</div>',
        unsafe_allow_html=True,
    )


def _render_why_view(projection: Dict[str, Any]) -> None:
    st.markdown('<div class="fte-15i-title">Why?</div>',
                unsafe_allow_html=True)
    events = (projection.get("why") or {}).get("events") or []
    if not events:
        st.caption("No explanation events were recorded for this result.")
        return
    for event in events:
        text = event.get("text") or ""
        if text:
            st.markdown(
                f'<div class="fte-15i-whyline">· {_esc(text)}</div>',
                unsafe_allow_html=True,
            )


def _render_calculation_view(projection: Dict[str, Any]) -> None:
    records = (projection.get("calculation") or {}).get("records") or []
    if not records:
        st.caption("No calculation chain was recorded.")
        return
    for record in records:
        inputs = record.get("inputs") or {}
        input_text = " · ".join(
            f"{_esc(k)} = {_esc(v)}" for k, v in inputs.items())
        line = f"{_esc(record.get('label'))} = {_esc(record.get('result'))}"
        if record.get("formula"):
            line += (
                f' <span class="fte-15i-calc-muted">'
                f'({_esc(record["formula"])})</span>'
            )
        st.markdown(
            f'<div class="fte-15i-calcline">· {line}'
            + (f'<br/><span class="fte-15i-calc-muted">'
               f'{input_text}</span>' if input_text else "")
            + '</div>',
            unsafe_allow_html=True,
        )


def _render_verified_result(projection: Dict[str, Any]) -> None:
    resolution = projection.get("gate_resolution") or {}
    if resolution.get("accepted") and resolution.get("decision_label"):
        st.markdown(
            f'<div class="fte-15i-note">Got it. Continuing with '
            f'“{_esc(resolution["decision_label"])}”.</div>',
            unsafe_allow_html=True,
        )
    st.markdown('<div class="fte-15i-title">Result</div>',
                unsafe_allow_html=True)
    st.markdown(
        f'<div class="fte-15i-state">{_chip("VERIFIED", "green")} '
        f'{_esc(projection.get("status_label"))}</div>',
        unsafe_allow_html=True,
    )
    st.markdown('<div class="fte-15i-title">Journal Entry</div>',
                unsafe_allow_html=True)
    _render_journal_entry_view(projection)
    _render_verification_view(projection)
    st.markdown('<div class="fte-15i-title">Why?</div>',
                unsafe_allow_html=True)
    _render_why_view(projection)
    st.markdown('<div class="fte-15i-title">Show calculation</div>',
                unsafe_allow_html=True)
    _render_calculation_view(projection)


# ---------------------------------------------------------------------------
# Status behaviour (calm, distinct, never raw internals)
# ---------------------------------------------------------------------------


def _status_chip_label(status: str) -> str:
    return {
        "REVIEW_REQUIRED": "REVIEW REQUIRED",
        "NOT_SUPPORTED": "NOT SUPPORTED",
        INVALID_INPUT_MATH: "INVALID INPUT (MATH)",
        "BLOCKED": "BLOCKED",
    }.get(status, status or "REFUSED")


def _render_status_state(projection: Dict[str, Any]) -> None:
    status = projection.get("status")
    tone = projection.get("tone") or "neutral"
    st.markdown(
        f'<div class="fte-15i-state">{_chip(_status_chip_label(status), tone)}'
        f' {_esc(projection.get("headline"))}</div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        f'<div class="fte-15i-state">{_esc(projection.get("summary"))}</div>',
        unsafe_allow_html=True,
    )
    why_not = projection.get("why_not")
    if why_not:
        st.markdown(
            f'<div class="fte-15i-whyline"><b>Why:</b> {_esc(why_not)}</div>',
            unsafe_allow_html=True,
        )
    next_action = projection.get("next_action")
    if next_action:
        st.markdown(
            f'<div class="fte-15i-whyline"><b>What you can do:</b> '
            f'{_esc(next_action)}</div>',
            unsafe_allow_html=True,
        )


# ---------------------------------------------------------------------------
# Developer Debug Mode (FTE_DEBUG_GRAPH=true, read-only)
# ---------------------------------------------------------------------------


def _debug_enabled() -> bool:
    return os.environ.get("FTE_DEBUG_GRAPH", "").strip().lower() == "true"


# ---------------------------------------------------------------------------
# Sprint 17 -- Stateful Student Problem Workflow
# ---------------------------------------------------------------------------

from backend.fyjc_student_session import (
    K_PROBLEM_WORKFLOW, K_PROBLEM_WORKFLOW_FP, K_PROBLEM_CURRENT_TX,
    K_PROBLEM_DECISIONS, K_PROBLEM_DECISIONS_FP,
)

# Transaction status icons
_TX_ICON = {
    "VERIFIED": "\u2705",
    "REVIEW_REQUIRED": "\u26a0\ufe0f",
    "NOT_SUPPORTED": "\u274c",
    "INVALID_INPUT_MATH": "\u274c",
    "INFORMATIONAL_EVENT": "\u2139\ufe0f",
    "OPENING_BALANCE": "\u2139\ufe0f",
    "BLOCKED": "\u274c",
}


def _is_multi_tx_problem(projection):
    """Check if this projection contains a problem-engine result."""
    return bool(projection.get("problem_engine"))


def _init_problem_workflow(question, projection):
    """Initialize or refresh the problem workflow state from projection.

    Sprint 35: applies transaction-level VERIFIED+journal integrity
    validation so no posting transaction can claim VERIFIED with zero
    journal lines."""
    pe = projection.get("problem_engine")
    if not pe:
        return
    fp = question_fingerprint(question)
    current_fp = st.session_state.get(K_PROBLEM_WORKFLOW_FP)
    if current_fp == fp and K_PROBLEM_WORKFLOW in st.session_state:
        return  # already initialized for this question
    # Sprint 35: validate transaction integrity before storing
    integrity = validate_problem_integrity(pe.get("transactions", []))
    pe_validated = dict(pe)
    pe_validated["transactions"] = integrity["transactions"]
    pe_validated["_integrity_violations"] = integrity["integrity_violations"]
    pe_validated["_verified_count"] = integrity["verified_count"]
    pe_validated["_review_required_count"] = integrity["review_required_count"]
    st.session_state[K_PROBLEM_WORKFLOW] = pe_validated
    st.session_state[K_PROBLEM_WORKFLOW_FP] = fp
    st.session_state[K_PROBLEM_CURRENT_TX] = 0
    st.session_state[K_PROBLEM_DECISIONS] = {}
    st.session_state[K_PROBLEM_DECISIONS_FP] = fp


def _get_workflow_state():
    """Get the current problem workflow state."""
    return st.session_state.get(K_PROBLEM_WORKFLOW, {})


def _get_current_tx_index():
    """Get the current transaction index the student is viewing."""
    return st.session_state.get(K_PROBLEM_CURRENT_TX, 0)


def _advance_to_next_tx():
    """Advance the workflow to the next transaction."""
    current = _get_current_tx_index()
    wf = _get_workflow_state()
    txns = wf.get("transactions", [])
    if current < len(txns) - 1:
        st.session_state[K_PROBLEM_CURRENT_TX] = current + 1


def _tx_status_icon(status):
    """Get the icon for a transaction status."""
    return _TX_ICON.get(status, "\u25cb")


def _render_problem_timeline(wf, current_idx):
    """Sprint 35: Render a comprehensive whole-problem transaction timeline.

    Shows every transaction with its status, a summary of what was understood,
    and the current viewing position. The student sees the complete accounting
    story at a glance."""
    txns = wf.get("transactions", [])
    total = len(txns)
    if total == 0:
        return

    verified = wf.get("_verified_count", 0)
    review = wf.get("_review_required_count", 0)
    not_supp = sum(1 for t in txns if t.get("status") in ("NOT_SUPPORTED", "INVALID_INPUT_MATH"))

    st.markdown('<div class="fte-15i-title">Accounting Problem</div>',
                unsafe_allow_html=True)

    # Summary line
    summary_parts = []
    if verified:
        summary_parts.append("\u2705 {} verified".format(verified))
    if review:
        summary_parts.append("\u26a0\ufe0f {} needs review".format(review))
    if not_supp:
        summary_parts.append("\u274c {} unsupported".format(not_supp))
    st.markdown("  ".join(summary_parts))

    for i, tx in enumerate(txns):
        idx = tx["index"]
        status = tx["status"]
        text = tx["text"]
        icon = _tx_status_icon(status)
        ev = tx.get("event_type", "ACCOUNTING_TRANSACTION")

        # Status label
        if status == "VERIFIED":
            status_label = "Verified"
        elif status == "REVIEW_REQUIRED":
            status_label = "Needs review"
        elif status in ("NOT_SUPPORTED", "INVALID_INPUT_MATH"):
            status_label = status.replace("_", " ").title()
        elif ev in ("INFORMATIONAL_EVENT", "OPENING_BALANCE"):
            status_label = ev.replace("_", " ").title()
        else:
            status_label = status

        # Transaction text preview
        preview = text[:65] + ("..." if len(text) > 65 else "")

        if current_idx >= 0 and i == current_idx:
            st.markdown(
                '<div style="background:#f0f7ff; border-left:3px solid #1a73e8; '
                'padding:6px 10px; margin:3px 0; border-radius:4px;">'
                '<b>\u25b6 T{idx}</b> {preview}<br/>'
                '<span style="color:#666; font-size:0.85em;">{icon} {status_label}</span>'
                '</div>'.format(idx=idx, preview=preview, icon=icon,
                                status_label=status_label),
                unsafe_allow_html=True)
        else:
            st.markdown(
                '<div style="padding:3px 10px; margin:2px 0;">'
                '{icon} <b>T{idx}</b> {preview} '
                '<span style="color:#888; font-size:0.85em;">\u2014 {status_label}</span>'
                '</div>'.format(icon=icon, idx=idx, preview=preview,
                                status_label=status_label),
                unsafe_allow_html=True)


def _relevant_calc_records(calc_records, debit_lines, credit_lines):
    """Sprint 37: Filter calculation records to only show those relevant
    to the specific transaction type.

    The engine generates generic calculation records (BK_LIST_PRICE,
    BK_NET_TRANSACTION_VALUE, BK_PAID_CREDIT_SPLIT) for every transaction.
    For transactions like drawings, expenses, or opening entries, these
    generic records are meaningless and misleading. This function
    suppresses them based on the transaction's journal accounts.
    """
    if not calc_records:
        return calc_records

    accounts = set()
    for line in (debit_lines or []) + (credit_lines or []):
        acct = (line.get("account") or "").lower().strip()
        if acct:
            accounts.add(acct)

    # Non-goods transactions where BK_LIST_PRICE/BK_NET_TRANSACTION_VALUE
    # are meaningless
    _NON_GOODS_ACCOUNTS = {
        "drawings", "office expenses", "general expenses",
        "rent", "salaries", "wages", "insurance", "electricity",
        "advertisement", "postage", "stationery", "repairs",
        "interest paid", "commission paid", "carriage inward",
        "carriage outward", "income tax", "fuel", "telephone expenses",
        "conveyance", "printing", "capital", "loan", "bank loan",
        "interest on drawings", "interest on capital",
    }
    is_non_goods = bool(accounts & _NON_GOODS_ACCOUNTS)

    filtered = []
    for rec in calc_records:
        calc_id = rec.get("calculation_id", "")

        # Suppress list price and net value for non-goods transactions
        if is_non_goods and calc_id in (
            "BK_LIST_PRICE", "BK_NET_TRANSACTION_VALUE",
        ):
            continue

        # Suppress paid/credit split when there is no credit component
        if calc_id == "BK_PAID_CREDIT_SPLIT":
            result = rec.get("result") or {}
            credit_amt = result.get("credit") if isinstance(result, dict) else None
            if credit_amt is not None and credit_amt == 0:
                # Full cash payment — the split is trivial and misleading
                continue

        filtered.append(rec)
    return filtered


def _render_tx_detail(tx, wf):
    """Sprint 35/37: Render a comprehensive transaction card.

    Design hierarchy:


    Design hierarchy:
    1. What happened? (original statement)
    2. What did Platrixa understand? (normalized interpretation)
    3. Is it verified? (status)
    4. What journal did it create? (aligned table)
    5. What calculation produced the amounts? (transaction-specific)
    6. Why did it choose this treatment? (optional expanders)
    7. What changed in the ledger? (state delta)
    """
    idx = tx["index"]
    status = tx["status"]
    text = tx["text"]
    ev = tx.get("event_type", "ACCOUNTING_TRANSACTION")
    jnl = tx.get("journal") or {}
    debit_lines = jnl.get("debit_lines", [])
    credit_lines = jnl.get("credit_lines", [])
    calc_records = _relevant_calc_records(
        jnl.get("calculation_records", []), debit_lines, credit_lines)

    # --- Transaction Header ---
    st.markdown('<div class="fte-15i-title">Transaction {}</div>'.format(idx),
                unsafe_allow_html=True)

    # --- Status Badge ---
    if status == "VERIFIED":
        st.success("\u2705 Verified")
    elif status == "REVIEW_REQUIRED":
        st.warning("\u26a0\ufe0f Review required")
    elif status in ("NOT_SUPPORTED", "INVALID_INPUT_MATH"):
        st.error("\u274c {}".format(status.replace("_", " ").title()))
    elif ev in ("INFORMATIONAL_EVENT", "OPENING_BALANCE"):
        st.info("\u2139\ufe0f {} \u2014 no accounting entry".format(
            ev.replace("_", " ").title()))
    elif status == "BLOCKED":
        st.error("\u274c Blocked")

    # --- Original Statement ---
    st.markdown('<div style="background:#f8f9fa; padding:8px 12px; '
                'border-radius:4px; margin:4px 0; font-style:italic;">'
                '"{}"</div>'.format(text), unsafe_allow_html=True)

    # --- Historical References ---
    if tx.get("historical_references"):
        with st.expander("\U0001f50d Historical dependencies", expanded=False):
            for ref in tx["historical_references"]:
                st.markdown(
                    "\u2192 Referenced T{} ({}): Rs.{}".format(
                        ref["transaction_index"], ref["event_type"], ref["amount"]
                    )
                )

    # --- Journal Entry (aligned table) ---
    if debit_lines or credit_lines:
        st.markdown("**Journal Entry**")
        # Header row
        cols = st.columns([4, 2, 2], gap="small")
        cols[0].markdown("**Account**")
        cols[1].markdown("**Debit**")
        cols[2].markdown("**Credit**")
        # Debit lines
        for line in debit_lines:
            cols = st.columns([4, 2, 2], gap="small")
            cols[0].markdown(line.get("account", ""))
            cols[1].markdown("\u20b9{:,}".format(
                int(float(line.get("amount", 0))) if line.get("amount") else 0))
            cols[2].markdown("\u2014")
        # Credit lines
        for line in credit_lines:
            cols = st.columns([4, 2, 2], gap="small")
            cols[0].markdown(line.get("account", ""))
            cols[1].markdown("\u2014")
            cols[2].markdown("\u20b9{:,}".format(
                int(float(line.get("amount", 0))) if line.get("amount") else 0))
        # Totals
        total_d = jnl.get("total_debit", 0)
        total_c = jnl.get("total_credit", 0)
        cols = st.columns([4, 2, 2], gap="small")
        cols[0].markdown("**Total**")
        cols[1].markdown("**\u20b9{:,}**".format(
            int(float(total_d)) if total_d else 0))
        cols[2].markdown("**\u20b9{:,}**".format(
            int(float(total_c)) if total_c else 0))
        if jnl.get("balanced"):
            st.caption("\u2705 Balanced")
    elif status == "VERIFIED" and ev == "ACCOUNTING_TRANSACTION":
        # Sprint 35 invariant: VERIFIED posting with no journal should not
        # reach here (validate_transaction_integrity downgrades it), but
        # display a safety message just in case.
        st.error("This transaction claims Verified but has no journal entry. "
                 "This should not happen.")

    # --- State Delta (accounting effect) ---
    if tx.get("state_delta"):
        sd = tx["state_delta"]
        with st.expander("\U0001f4ca Accounting effect", expanded=False):
            for d in sd.get("deltas", []):
                arrow = "\u2191" if d["direction"] == "debit" else "\u2193"
                st.markdown("{} **{}**: \u20b9{}".format(
                    arrow, d["account"],
                    "\u20b9{:,}".format(
                        int(float(d["amount"])) if d.get("amount") else 0)))

    # --- Optional: Why? ---
    if status == "VERIFIED" and (tx.get("why_not") or jnl.get("narration")):
        with st.expander("\U0001f4a1 Why this treatment?", expanded=False):
            if jnl.get("narration"):
                st.markdown(jnl["narration"])
            if jnl.get("why_not"):
                st.markdown(jnl["why_not"])

    # --- Optional: Calculation (transaction-specific) ---
    if calc_records:
        with st.expander("\U0001f4d0 Calculation", expanded=False):
            for rec in calc_records:
                label = rec.get("label", "")
                result_val = rec.get("result", "")
                formula = rec.get("formula", "")
                inputs = rec.get("inputs", {})
                input_text = " \u00b7 ".join(
                    "{} = {}".format(k, v) for k, v in inputs.items())
                line = "**{}**: {}".format(label, result_val)
                if formula:
                    line += " *({})*".format(formula)
                st.markdown(line)
                if input_text:
                    st.caption(input_text)

    # --- Next action ---
    if tx.get("next_action"):
        st.info("\U0001f446 **Next:** {}".format(tx["next_action"]))


def _render_problem_result(wf):
    """Render the final cumulative problem result."""
    problem_status = wf.get("problem_status", "UNKNOWN")
    txns = wf.get("transactions", [])
    ledger = wf.get("ledger_snapshot", {})

    st.markdown("---")
    st.markdown("## \U0001f389 Problem Complete")

    if problem_status == "PROBLEM_VERIFIED":
        st.success("**All transactions verified successfully.**")
    elif problem_status == "PROBLEM_REVIEW_REQUIRED":
        st.warning("**A transaction requires clarification.**")
    elif problem_status == "PROBLEM_INVALID_INPUT_MATH":
        st.error("**The problem contains a mathematical contradiction.**")
    elif problem_status == "PROBLEM_NOT_SUPPORTED":
        st.error("**The problem exceeds the supported accounting boundary.**")

    st.markdown("### Transaction Summary")
    for tx in txns:
        icon = _tx_status_icon(tx["status"])
        text = tx["text"][:50] + ("..." if len(tx["text"]) > 50 else "")
        note = ""
        if tx["status"] == "REVIEW_REQUIRED":
            note = " (needs clarification)"
        elif tx.get("event_type") in ("INFORMATIONAL_EVENT", "OPENING_BALANCE"):
            note = " (informational)"
        st.markdown("  {} T{} {}{}".format(icon, tx["index"], text, note))

    balances = ledger.get("balances", {})
    if balances:
        st.markdown("### Final Ledger")
        for acc, bal in sorted(balances.items()):
            sign = "+" if not bal.startswith("-") else ""
            st.markdown("  **{}** Rs.{}{}".format(acc, sign, bal))

    violations = wf.get("safety_violations", [])
    if violations:
        st.error("Safety violations: {}".format(violations))
    else:
        st.caption("Safety invariants: all zero")


def _render_problem_workflow(question, projection):
    """Sprint 35/36: Render the whole-problem workflow with ALL transactions
    visible simultaneously.

    The student sees the complete accounting story at once:
    - Timeline overview at the top
    - Every transaction as an expandable card with its own journal,
      calculation, explanation, and details
    - Final problem result and ledger at the bottom

    This is NOT a step-by-step wizard. Every transaction is visible and
    individually explorable.
    """
    pe = projection.get("problem_engine")
    if not pe:
        return

    _init_problem_workflow(question, projection)
    wf = _get_workflow_state()
    txns = wf.get("transactions", [])

    if not txns:
        st.info("No transactions detected in this problem.")
        return

    # --- Timeline overview (always visible) ---
    _render_problem_timeline(wf, -1)  # -1 = no highlight (all visible)
    st.markdown("---")

    # --- All transaction cards (each in its own expander) ---
    for i, tx in enumerate(txns):
        status = tx["status"]
        ev = tx.get("event_type", "ACCOUNTING_TRANSACTION")
        icon = _tx_status_icon(status)
        text = tx["text"]
        preview = text[:60] + ("..." if len(text) > 60 else "")

        # Status label for the expander title
        if status == "VERIFIED":
            status_label = "\u2705 Verified"
        elif status == "REVIEW_REQUIRED":
            status_label = "\u26a0\ufe0f Needs review"
        elif status in ("NOT_SUPPORTED", "INVALID_INPUT_MATH"):
            status_label = "\u274c " + status.replace("_", " ").title()
        elif ev in ("INFORMATIONAL_EVENT", "OPENING_BALANCE"):
            status_label = "\u2139\ufe0f " + ev.replace("_", " ").title()
        elif status == "BLOCKED":
            status_label = "\u274c Blocked"
        else:
            status_label = status

        # Auto-expand REVIEW_REQUIRED so the student sees what needs attention
        is_attention = status in ("REVIEW_REQUIRED", "BLOCKED",
                                  "NOT_SUPPORTED", "INVALID_INPUT_MATH")

        with st.expander(
            "T{} {} \u2014 {}".format(tx["index"], icon, preview),
            expanded=is_attention,
        ):
            _render_tx_detail(tx, wf)

    # --- Final problem result ---
    _render_problem_result(wf)


def _render_debug_graph(projection: Dict[str, Any]) -> None:
    """Read-only developer surface over the production graph. Never
    exposed in normal Student Mode and never mutates the graph."""
    if not _debug_enabled():
        return
    import pandas as pd
    payload = debug_graph_payload(projection.get("result") or {})
    st.markdown("---")
    st.markdown(
        '<div class="fte-15i-title">Developer Debug — Transaction Graph '
        '(read-only)</div>',
        unsafe_allow_html=True,
    )
    segments = payload.get("segments") or []
    if segments:
        st.markdown("**Graph nodes**")
        st.dataframe(
            pd.DataFrame([{
                "Node ID": s.get("index"),
                "Segment": s.get("text"),
                "Classification": s.get("classification"),
                "Authority": s.get("base_authority"),
                "Facts": "; ".join(
                    f"{f.get('kind')}={f.get('value')}"
                    for f in s.get("facts") or []),
                "Unresolved": s.get("unresolved"),
            } for s in segments]),
            hide_index=True,
            width="stretch",
        )
    if payload.get("dependencies"):
        st.markdown("**Graph edges / dependencies**")
        st.json(payload["dependencies"])
    if payload.get("ownership"):
        st.markdown("**Amount ownership**")
        st.dataframe(pd.DataFrame(payload["ownership"]), hide_index=True,
                     width="stretch")
    contradictions = payload.get("contradictions") or []
    violations = payload.get("violations") or []
    if contradictions or violations:
        st.markdown("**Contradiction state / violations**")
        st.json({"contradictions": contradictions,
                 "violations": violations})
    if payload.get("invariants"):
        st.markdown("**Safety invariants**")
        st.json(payload["invariants"])
    if projection.get("gate_resolution"):
        st.markdown("**Confidence Gate decision**")
        st.json(projection["gate_resolution"])
    why_events = list((projection.get("why") or {}).get("events") or [])
    # Every state exposes the engine's explanation events. Where the engine
    # refused (no journal, no events yet), the events are the backend's own
    # reason payload and the Confidence Gate's unresolved dependency -
    # never anything the UI invented.
    if not why_events and projection.get("why_not"):
        why_events.append({
            "event_id": "ENGINE_REVIEW_REQUIRED",
            "text": str(projection["why_not"]),
        })
    gate = projection.get("confidence_gate")
    if gate and not projection.get("gate_resolution"):
        why_events.append({
            "event_id": "CONFIDENCE_GATE_PENDING",
            "text": "{} options: {}".format(
                gate.get("question") or "",
                " / ".join(str(o) for o in (gate.get("options") or [])),
            ),
        })
    st.markdown("**Explanation events**")
    st.json(why_events)
    st.markdown("**Raw graph payload**")
    st.json(payload)


# ---------------------------------------------------------------------------
# Legacy verification surface (released gates drive the check widgets)
# ---------------------------------------------------------------------------


def _render_legacy_verify(question: str) -> None:
    flow = _legacy_flow(question)
    if flow.get("flow") != "accounting":
        return
    with st.expander("▸ Show detailed reasoning"):
        st.caption(
            "The full working — kept for anyone who wants the audit trail."
        )
        _render_steps(flow.get("steps") or [])
        _render_audit(flow.get("audit") or {})
    _render_accounting_verify(flow)


# ---------------------------------------------------------------------------
# The 15I-UI student workspace (shared by the landing and the FYJC Study
# page)
# ---------------------------------------------------------------------------


def _render_15i_student_workspace(demo: bool, landing: bool) -> None:
    """UniversalInput -> backend projection -> [Confidence Gate] ->
    verified result / status state -> (debug surface when enabled)."""
    _ensure_15i_css()
    stage = reconcile(st.session_state)
    if demo and stage == STAGE_ENTRY:
        st.session_state[K_QUESTION] = (
            "Purchased goods from Rahul on credit for Rs.10,000."
        )
        stage = reconcile(st.session_state)
    if landing:
        _render_landing_input(demo, stage)
    else:
        _render_entry(demo, stage)
    reconcile(st.session_state)
    question = effective_question(st.session_state)
    if not question:
        st.caption("Enter or upload a question to begin.")
        _render_study_topics()
        return
    if st.session_state.get(K_PROJ) is None and st.session_state.get(K_EDIT):
        st.stop()
    if st.session_state.get(K_PROJ) is None:
        try:
            projection = _compute_projection(question)
            _store_projection(projection, question)
        except Exception:  # defensive: recoverable, never fake
            st.session_state[K_ANALYSIS_ERROR] = {
                "message": (
                    "Platrixa hit an unexpected problem while reading that "
                    "question. Your question is still saved below - press "
                    "Analyse question to try again."
                ),
                "fp": question_fingerprint(question),
            }
    if st.session_state.get(K_ANALYSIS_ERROR):
        _render_recoverable_error(st.session_state[K_ANALYSIS_ERROR])
        _render_study_topics()
        return
    projection = st.session_state[K_PROJ]

    st.markdown("---")
    if gate_is_pending(projection):
        _render_confidence_gate(projection, question)
        _render_debug_graph(projection)
        return

    # Sprint 17: multi-transaction problem workflow
    if _is_multi_tx_problem(projection):
        _render_problem_workflow(question, projection)
        _render_debug_graph(projection)
        return

    if projection.get("status") == "VERIFIED":
        _render_understanding_view(projection)
        _render_verified_result(projection)
        _render_legacy_verify(question)
    else:
        _render_status_state(projection)
    _render_debug_graph(projection)
