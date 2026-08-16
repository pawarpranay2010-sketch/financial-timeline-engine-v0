"""
Financial Timeline Engine
Sprint 14 - FYJC Student End-to-End UI
backend/fyjc_student_ui.py

The student-facing Streamlit rendering layer for the FYJC Study /
Verify workflow. All reasoning is delegated to the Sprint 13 FYJC
capability modules through backend.maths.fyjc_student_flow (pure,
deterministic). This module ONLY prepares and renders the student
journey:

    📷 Photo / 📄 PDF / ✍️ Type
        -> What FT-E understood (editable)
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
  guided to type/paste the question. FT-E never pretends it read a photo
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
from typing import Any, Dict, List, Optional

import streamlit as st

from backend.fyjc_student_session import (
    K_MODE, K_QUESTION, K_CORRECTED, K_DOC_TEXT, K_DOC_NAME, K_UPLOAD_KIND,
    K_FLOW, K_EDIT, K_MANUAL_FACTS, K_VERDICT, K_ACCT_VERIFY,
    K_ANALYSIS_ERROR, K_FLOW_FP, K_VERDICT_FP, K_ACCT_FP, K_MANUAL_FACTS_FP,
    STAGE_ENTRY,
    derive_stage,
    effective_question,
    question_fingerprint,
    reconcile,
    reset_session,
    upload_recovery_note,
)
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
    """Collapsed, jargon-free explanation of what FT-E does."""
    with st.expander("How FT-E works"):
        st.markdown(
            "- FT-E reads your question and shows what it understood.\n"
            "- It applies the registered formula (Maths) or the golden "
            "rule (Book-Keeping) step by step.\n"
            "- It shows the final answer and lets you check your own "
            "answer against it.\n"
            "- When a value is missing or the question is ambiguous, "
            "FT-E asks instead of guessing."
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
        help="Choose how you want to give FT-E your question.",
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
                'does not bundle an OCR engine, so FT-E will not pretend to '
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
                 "without OCR — FT-E will say so honestly.",
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
                    'this file.</b> FT-E does not bundle an OCR engine, so it '
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
        '<div class="fte-fyjc-note">What FT-E understood:</div>',
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
            '<div class="fte-fyjc-why"><b>Almost there — FT-E needs a little '
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
            "Correct the question (FT-E will re-interpret it)",
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
            "FT-E."
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
            f'<div class="fte-fyjc-why"><b>Why FT-E could not answer:</b> '
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
        'FT-E cannot calculate without them, and it will not guess. You can '
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
            f'<div class="fte-fyjc-why"><b>Why FT-E could not decide:</b> '
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
        "Enter your own answer (e.g. `20` or `20.00`) and FT-E compares it "
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
        st.markdown(f"**FT-E verification:** {_esc(verified)}")
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

    # Sprint 15I-R: a verified engine answer leads with the FT-E check.
    verification = flow.get("verification") or {}
    if flow.get("status") == "VERIFIED" and verification:
        st.markdown(
            f'<div class="fte-fyjc-card">{_chip("VERIFIED", "green")} '
            f'FT-E verified this entry</div>',
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
                f"**FT-E's own check:** Debit {v.get('total_debit'):,.2f} = "
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
            st.markdown("**FT-E's journal:** " + " · ".join(ref_lines))
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
    with st.expander("What FT-E can verify", expanded=False):
        topics = fyjc_study_topics()
        st.markdown("**Maths — financial calculations:**")
        st.markdown(" · ".join(f"`{m}`" for m in topics["maths"]))
        st.markdown("**Book-Keeping — journal, ledger & trial balance:**")
        for topic in topics["bookkeeping"]:
            st.markdown(f"- {topic}")
        st.markdown(
            "**Answer verification:** enter your own answer and FT-E "
            "tells you whether it matches, with the first mistake if it "
            "does not."
        )
        st.caption(
            "Anything else is refused deterministically — FT-E never "
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
        f'<div class="fte-fyjc-why"><b>FT-E couldn’t finish reading this '
        f'question.</b><br/>{_esc(error.get("message"))}</div>',
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Page entry
# ---------------------------------------------------------------------------


def render_fyjc_student_ui(demo: bool = False) -> None:
    """The FYJC page (rendered inside the workspace). Sprint 15I-I adds a
    top-level section switcher: Study / Verify (Sprint 14 flow), Practice
    and Teacher Dashboard (Sprint 15I-I, rendered by
    backend.fyjc_practice_ui). The study/verify flow below is unchanged."""
    _ensure_css()

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

    # 1. Pre-render reconcile: validate the session left by the previous run
    #    (a refresh reruns the whole script; anything stale is dropped here).
    stage = reconcile(st.session_state)

    if demo and stage == STAGE_ENTRY:
        st.session_state[K_QUESTION] = (
            "Purchased goods from Rahul on credit for Rs.10,000."
        )
        stage = reconcile(st.session_state)

    _render_entry(demo, stage)

    # 2. Post-render reconcile: the entry widgets may have just changed the
    #    question (typed, cleared, switched modes) - a changed question must
    #    never display the previous question's result.
    reconcile(st.session_state)
    question = effective_question(st.session_state)
    if not question:
        st.caption("Enter or upload a question to begin.")
        _render_study_topics()
        return

    if st.session_state.get(K_FLOW) is None and st.session_state.get(K_EDIT):
        # waiting for the corrected question to be re-analysed
        st.stop()

    flow = st.session_state.get(K_FLOW)
    if flow is None:
        try:
            flow = run_fyjc_student_flow(question)
            st.session_state[K_FLOW] = flow
            st.session_state[K_FLOW_FP] = question_fingerprint(question)
        except Exception:  # defensive: a failure is recoverable, never fake
            st.session_state[K_ANALYSIS_ERROR] = {
                "message": (
                    "FT-E hit an unexpected problem while reading that "
                    "question. Your question is still saved below - press "
                    "Analyse question to try again."
                ),
                "fp": question_fingerprint(question),
            }
    if st.session_state.get(K_ANALYSIS_ERROR):
        _render_recoverable_error(st.session_state[K_ANALYSIS_ERROR])
        _render_study_topics()
        return
    flow = st.session_state[K_FLOW]

    st.markdown("---")
    _render_understanding(flow)

    if flow.get("flow") == "maths":
        _render_maths_flow(flow)
    elif flow.get("flow") == "accounting":
        _render_accounting_flow(flow)
    else:
        _render_refusal(flow)

    st.markdown("---")
    _render_verification(flow)

    st.markdown("---")
    _render_study_topics()


def _render_refusal(flow: Dict[str, Any]) -> None:
    status = flow.get("status")
    tone = ("red" if status in ("BLOCKED", INVALID_INPUT_MATH)
            else "amber")
    st.markdown(
        '<div class="fte-fyjc-title">FT-E couldn’t solve this one</div>',
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
