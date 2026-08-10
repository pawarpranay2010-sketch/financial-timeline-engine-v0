"""
Financial Timeline Engine
Sprint 14 - FYJC Student End-to-End UI
backend/fyjc_student_ui.py

The student-facing Streamlit rendering layer for the FYJC Study /
Verify workflow. All reasoning is delegated to the Sprint 13 FYJC
capability modules through backend.maths.fyjc_student_flow (pure,
deterministic). This module ONLY prepares and renders the student
journey:

    📸 Photo / 📁 PDF / ✍️ Type
        -> What FT-E understood (editable)
        -> Maths | Book-Keeping flow (steps 1-6 / 1-8)
        -> C++ mathematical authority confirmation + audit
        -> Independent verification

Honesty rules implemented here
------------------------------
* No OCR engine is bundled in this deployment. A photo/image is shown to
  the student and clearly labelled as NOT machine-read; the student is
  guided to type/paste the question. FT-E never pretends it read a photo.
* BLOCKED / REVIEW_REQUIRED / UNSUPPORTED states are rendered with exact
  what / why / next-action copy and concrete actions (enter the missing
  value manually, review sources, etc.) - never a guessed answer.
* The expandable technical audit is optional and defaults to hidden.
"""

from __future__ import annotations

import html
from typing import Any, Dict, List, Optional

import streamlit as st

from backend.maths.fyjc_student_flow import (
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

# ---------------------------------------------------------------------------
# Session keys
# ---------------------------------------------------------------------------

K_MODE = "fte_fyjc_mode"
K_QUESTION = "fte_fyjc_question"
K_CORRECTED = "fte_fyjc_corrected"
K_DOC_TEXT = "fte_fyjc_doc_text"
K_DOC_NAME = "fte_fyjc_doc_name"
K_UPLOAD_KIND = "fte_fyjc_upload_kind"
K_FLOW = "fte_fyjc_flow"
K_EDIT = "fte_fyjc_edit"
K_MANUAL_FACTS = "fte_fyjc_manual_facts"
K_VERDICT = "fte_fyjc_verdict"
K_ACCT_VERIFY = "fte_fyjc_acct_verify"

_IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp")
_TEXT_EXT = (".pdf", ".docx", ".txt", ".csv", ".xlsx")

_FYJC_CSS = """
<style>
.fte-fyjc-title { font-size: 1.5rem; font-weight: 800; margin: .2rem 0 .1rem; }
.fte-fyjc-sub { color: var(--fte-muted, #8a94a6); margin-bottom: .6rem; }
.fte-fyjc-card { border: 1px solid var(--fte-border, #2b3550);
  border-radius: 12px; padding: .8rem 1rem; margin: .4rem 0; }
.fte-fyjc-step { border-left: 3px solid var(--fte-accent, #4f8cff);
  border-radius: 0 10px 10px 0; padding: .5rem .9rem; margin: .35rem 0;
  background: rgba(79,140,255,.05); }
.fte-fyjc-step b { color: var(--fte-text, #e6ecf5); }
.fte-fyjc-chip { display:inline-block; border-radius: 999px; padding:.1rem .6rem;
  font-size:.8rem; font-weight:700; margin-right:.3rem; }
.fte-fyjc-chip.green { background: rgba(46,204,113,.15); color:#2ecc71; }
.fte-fyjc-chip.amber { background: rgba(255,180,60,.15); color:#ffb43c; }
.fte-fyjc-chip.red   { background: rgba(255,99,99,.15); color:#ff6363; }
.fte-fyjc-chip.blue  { background: rgba(79,140,255,.15); color:#7fb0ff; }
.fte-fyjc-why { border:1px solid #ffb43c; border-radius:10px;
  padding:.6rem .9rem; background: rgba(255,180,60,.06); margin:.5rem 0; }
.fte-fyjc-blocked { border:1px solid #ff6363; border-radius:10px;
  padding:.6rem .9rem; background: rgba(255,99,99,.06); margin:.5rem 0; }
.fte-fyjc-unsupported { border:1px solid var(--fte-border,#2b3550);
  border-radius:10px; padding:.6rem .9rem; background: rgba(138,148,166,.08);
  margin:.5rem 0; }
</style>
"""


def _ensure_css() -> None:
    if not st.session_state.get("fte_fyjc_css_done"):
        st.markdown(_FYJC_CSS, unsafe_allow_html=True)
        st.session_state["fte_fyjc_css_done"] = True


def _chip(label: str, tone: str) -> str:
    return f'<span class="fte-fyjc-chip {tone}">{html.escape(str(label))}</span>'


def _esc(value: Any) -> str:
    return html.escape(str(value if value is not None else ""))


# ---------------------------------------------------------------------------
# Entry stage
# ---------------------------------------------------------------------------


def _reset_question() -> None:
    for key in (K_QUESTION, K_CORRECTED, K_DOC_TEXT, K_DOC_NAME,
                K_UPLOAD_KIND, K_FLOW, K_MANUAL_FACTS, K_VERDICT,
                K_ACCT_VERIFY):
        st.session_state.pop(key, None)


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


def _render_entry(demo: bool) -> None:
    st.markdown('<div class="fte-fyjc-title">🎓 FYJC Study / Verify</div>',
                unsafe_allow_html=True)
    st.markdown(
        '<div class="fte-fyjc-sub">For the 24 August FYJC Maths & '
        'Book-Keeping exam. Upload your question, take a photo, or type '
        'it — FT-E shows what it understood, applies the registered '
        'formula / golden rule, and lets you verify your own answer. '
        'C++ is the mathematical authority; FT-E never guesses.</div>',
        unsafe_allow_html=True,
    )

    st.radio(
        "What are you working on?",
        ["📸 Take Photo / Image", "📁 Upload Question (PDF / Document)",
         "✍️ Enter Question"],
        key=K_MODE,
        horizontal=True,
        label_visibility="collapsed",
    )
    mode = st.session_state[K_MODE]

    if mode.startswith("📸"):
        photo = st.file_uploader(
            "Take or choose a photo of the question",
            type=["png", "jpg", "jpeg", "webp"],
            key="fte_fyjc_photo",
            help="A clear photo of the textbook page or question paper.",
        )
        if photo is not None:
            st.session_state[K_UPLOAD_KIND] = "image"
            st.session_state[K_DOC_NAME] = getattr(photo, "name", "photo")
            st.image(photo, caption="Your uploaded question photo",
                     use_container_width=True)
            st.markdown(
                '<div class="fte-fyjc-why"><b>⚠️ Photo received — text '
                'not machine-read.</b> This deployment does not bundle an '
                'OCR engine, so FT-E will not pretend to read the photo '
                'and will never guess its text. Type or paste the question '
                'below (the photo stays visible as your source).</div>',
                unsafe_allow_html=True,
            )
        st.text_area(
            "Type the question from the photo",
            key=K_QUESTION,
            height=120,
            placeholder=(
                "e.g. Calculate the Current Ratio. Current Assets "
                "Rs.5,00,000 and Current Liabilities Rs.2,50,000."
            ),
        )
    elif mode.startswith("📁"):
        doc = st.file_uploader(
            "Upload the question (.pdf, .docx, .txt)",
            type=["pdf", "docx", "txt"],
            key="fte_fyjc_doc",
            help="Text-based PDFs and documents. Scanned photo-PDFs cannot "
                 "be read without OCR — FT-E will say so honestly.",
        )
        if doc is not None:
            name = getattr(doc, "name", "document")
            if st.session_state.get(K_DOC_NAME) != name or \
                    st.session_state.get(K_DOC_TEXT) is None:
                text = _extract_document_text(doc)
                st.session_state[K_DOC_NAME] = name
                st.session_state[K_DOC_TEXT] = text
                st.session_state[K_UPLOAD_KIND] = "document"
            text = st.session_state.get(K_DOC_TEXT) or ""
            if text:
                st.markdown(
                    f'<div class="fte-fyjc-card">📄 <b>Extracted from '
                    f'<i>{_esc(name)}</i></b> — {len(text)} characters of '
                    f'readable text found. Review it below, then analyse.'
                    f'</div>',
                    unsafe_allow_html=True,
                )
                with st.expander("Show extracted text"):
                    st.code(text[:4000], language=None)
            else:
                st.markdown(
                    '<div class="fte-fyjc-blocked"><b>🔴 BLOCKED: text '
                    'extraction</b> — this file yielded no readable text '
                    '(scanned image PDF, encrypted, or empty). FT-E will '
                    'not guess content. Type or paste the question below '
                    'instead.</div>',
                    unsafe_allow_html=True,
                )
        st.text_area(
            "…or paste / type the question",
            key=K_QUESTION,
            height=120,
            placeholder="e.g. Purchased goods from Rahul on credit for Rs.10,000.",
        )
    else:
        st.text_area(
            "Enter the question",
            key=K_QUESTION,
            height=140,
            placeholder=(
                "Maths: 'Calculate the Profit Margin. Profit Rs.200 and "
                "Revenue Rs.1,000.'\n"
                "Book-Keeping: 'Purchased goods from Rahul on credit for "
                "Rs.10,000.'\n"
                "Or paste 'Concept: value' lines (e.g. 'Net Profit: 200')."
            ),
        )

    col_go, col_reset = st.columns([2, 1])
    with col_go:
        st.button("Analyse question", key="fte_fyjc_go",
                  use_container_width=True, type="primary")
    with col_reset:
        if st.button("Start over", key="fte_fyjc_reset",
                     use_container_width=True):
            _reset_question()
            st.rerun()


def _question_text() -> str:
    """The effective question text: a student correction wins over the
    typed/typed-into-photo text, which wins over extracted document text.
    K_CORRECTED is a plain session key (never a widget), so the
    understanding stage can update it freely."""
    corrected = str(st.session_state.get(K_CORRECTED) or "").strip()
    if corrected:
        return corrected
    typed = str(st.session_state.get(K_QUESTION) or "").strip()
    if typed:
        return typed
    return str(st.session_state.get(K_DOC_TEXT) or "").strip()


# ---------------------------------------------------------------------------
# Understanding stage
# ---------------------------------------------------------------------------


def _render_understanding(flow: Dict[str, Any]) -> None:
    understanding = flow.get("understanding") or {}
    st.markdown('<div class="fte-fyjc-title">1 · Question detected</div>',
                unsafe_allow_html=True)
    q = _question_text()
    st.markdown(
        f'<div class="fte-fyjc-card">“{_esc(q[:600])}”</div>',
        unsafe_allow_html=True,
    )

    st.markdown('<div class="fte-fyjc-title">FT-E understood</div>',
                unsafe_allow_html=True)
    st.markdown(
        f'<div class="fte-fyjc-card">{_esc(understanding.get("interpretation"))}'
        f'</div>',
        unsafe_allow_html=True,
    )

    domain = understanding.get("domain")
    if domain == "maths":
        domain_label, tone = "📐 Maths", "blue"
    elif domain == "bookkeeping":
        domain_label, tone = "📒 Book-Keeping & Accountancy", "green"
    else:
        domain_label, tone = "❓ Unrecognised", "amber"
    st.markdown(
        f'<div class="fte-fyjc-card">{_chip(domain_label, tone)}'
        f'<span style="color:var(--fte-muted,#8a94a6)">'
        f'{_esc(understanding.get("reason"))}</span></div>',
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
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.caption("No 'Concept: value' facts were parsed from the text.")

    concerns = understanding.get("concerns") or []
    if concerns:
        st.markdown(
            '<div class="fte-fyjc-why"><b>🟠 REVIEW REQUIRED</b></div>',
            unsafe_allow_html=True,
        )
        for concern in concerns:
            st.warning(concern)

    c1, c2 = st.columns([1, 3])
    with c1:
        if st.button("✏️ Correct / Edit", key="fte_fyjc_edit_btn",
                     use_container_width=True):
            st.session_state[K_EDIT] = True
            st.rerun()
    if st.session_state.get(K_EDIT):
        corrected = st.text_area(
            "Correct the question (FT-E will re-interpret it)",
            key="fte_fyjc_question_edit",
            value=_question_text(),
            height=120,
        )
        if st.button("Re-analyse corrected question",
                     key="fte_fyjc_reanalyse", type="primary"):
            st.session_state[K_CORRECTED] = corrected.strip()
            st.session_state[K_EDIT] = False
            st.session_state[K_FLOW] = None
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
    with st.expander("🔍 Technical audit (optional detail)"):
        st.caption(
            "Internal detail for anyone who wants it — not needed to use "
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


def _render_maths_flow(flow: Dict[str, Any]) -> None:
    st.markdown('<div class="fte-fyjc-title">2 · Maths — working</div>',
                unsafe_allow_html=True)
    resolved = bool(flow.get("resolved"))
    status = flow.get("status")
    if resolved:
        st.markdown(
            f'<div class="fte-fyjc-card">{_chip("🟢 VERIFIED", "green")} '
            f'{_esc(flow.get("status_label"))}</div>',
            unsafe_allow_html=True,
        )
    elif status == "UNSUPPORTED":
        st.markdown(
            f'<div class="fte-fyjc-card">{_chip("🟡 NOT SUPPORTED YET", "amber")}</div>',
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
    _render_audit(flow.get("audit") or {})

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

    # Manual value entry for BLOCKED maths (missing inputs)
    if status == "BLOCKED":
        _render_blocked_manual_entry(flow)


def _render_blocked_manual_entry(flow: Dict[str, Any]) -> None:
    outcome = flow.get("outcome") or {}
    missing = outcome.get("missing") or []
    if not missing:
        return
    st.markdown(
        '<div class="fte-fyjc-blocked"><b>🔴 BLOCKED</b> — required '
        'evidence is missing. FT-E cannot calculate without it. You can '
        'upload the relevant page or enter the verified value manually '
        'below (it will be labelled as student-entered, never as document '
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
            use_container_width=True,
        )
    if submitted:
        cleaned = {
            concept: value for concept, value in manual.items()
            if str(value or "").strip()
        }
        if cleaned:
            st.session_state[K_MANUAL_FACTS] = cleaned
            metric = flow.get("metric")
            if metric:
                new_flow = run_fyjc_maths_flow(
                    metric, facts=cleaned,
                    text=_question_text(),
                    student_answer=None,
                )
                new_flow["understanding"] = flow.get("understanding")
                st.session_state[K_FLOW] = new_flow
            st.rerun()
        else:
            st.warning("Enter at least one value to continue.")


def _render_accounting_flow(flow: Dict[str, Any]) -> None:
    st.markdown(
        '<div class="fte-fyjc-title">2 · Book-Keeping — reasoning</div>',
        unsafe_allow_html=True,
    )
    status = flow.get("status")
    if status == "VERIFIED":
        st.markdown(
            f'<div class="fte-fyjc-card">{_chip("🟢 VERIFIED", "green")} '
            f'{_esc(flow.get("status_label"))}</div>',
            unsafe_allow_html=True,
        )
    elif status == "BLOCKED":
        st.markdown(
            f'<div class="fte-fyjc-card">{_chip("🔴 BLOCKED", "red")} '
            f'{_esc(flow.get("status_label"))}</div>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            f'<div class="fte-fyjc-card">{_chip("🟠 REVIEW REQUIRED", "amber")} '
            f'{_esc(flow.get("status_label"))}</div>',
            unsafe_allow_html=True,
        )

    _render_steps(flow.get("steps") or [])
    _render_audit(flow.get("audit") or {})

    why_not = flow.get("why_not")
    if why_not:
        st.markdown(
            f'<div class="fte-fyjc-why"><b>Why FT-E could not decide:</b> '
            f'{_esc(why_not)}<br/><b>What you can do:</b> '
            f'{_esc(flow.get("next_action"))}</div>',
            unsafe_allow_html=True,
        )


# ---------------------------------------------------------------------------
# Independent verification (Sprint 14 section 8)
# ---------------------------------------------------------------------------


def _render_verification(flow: Dict[str, Any]) -> None:
    st.markdown('<div class="fte-fyjc-title">3 · Verify yourself</div>',
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
        "to the C++-verified value."
    )
    answer = st.text_input("Your answer", key="fte_fyjc_verify_answer",
                           placeholder="e.g. 20")
    verdict = st.session_state.get(K_VERDICT) or {}
    if st.button("Verify", key="fte_fyjc_verify_btn", type="primary",
                 use_container_width=True):
        metric = flow.get("metric")
        if metric:
            v = run_fyjc_maths_flow(
                metric,
                facts=st.session_state.get(K_MANUAL_FACTS) or None,
                text=_question_text(),
                student_answer=answer.strip() or None,
            )
            st.session_state[K_VERDICT] = {
                "verdict": v.get("verdict"),
                "student_display": (v.get("audit") or {}).get("student_display"),
                "correct_answer": (v.get("audit") or {}).get("correct_answer"),
                "mismatch": (v.get("audit") or {}).get("mismatch"),
            }
            st.rerun()
    _render_verdict(verdict)


def _render_verdict(verdict: Dict[str, Any]) -> None:
    v = verdict.get("verdict")
    if not v:
        return
    if v == "CORRECT":
        st.success(
            f"✅ MATCH — {_esc(verdict.get('student_display'))} is the "
            "C++-verified value."
        )
    elif v == "INCORRECT":
        st.error(
            f"🔴 MISMATCH — your answer {_esc(verdict.get('student_display'))} "
            f"differs from the C++-verified value "
            f"{_esc(verdict.get('correct_answer'))}. "
            f"{_esc(verdict.get('mismatch'))}"
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


def _render_accounting_verify(flow: Dict[str, Any]) -> None:
    outcome = flow.get("outcome") or {}
    entries = _reference_entries(flow)
    q = _question_text()
    result = st.session_state.get(K_ACCT_VERIFY) or {}

    st.markdown("Choose the check you want to perform:")

    # --- Journal entry check ---------------------------------------------
    with st.expander("📒 Check a journal entry (Debit = Credit + direction)"):
        jc = st.columns(4)
        with jc[0]:
            d1a = st.text_input("Debit account 1", key="fte_fyjc_jd1a",
                                placeholder="Purchases")
            d1v = st.text_input("Debit amount 1", key="fte_fyjc_jd1v",
                                placeholder="10000")
        with jc[1]:
            c1a = st.text_input("Credit account 1", key="fte_fyjc_jc1a",
                                placeholder="Rahul")
            c1v = st.text_input("Credit amount 1", key="fte_fyjc_jc1v",
                                placeholder="10000")
        with jc[2]:
            d2a = st.text_input("Debit account 2", key="fte_fyjc_jd2a")
            d2v = st.text_input("Debit amount 2", key="fte_fyjc_jd2v")
        with jc[3]:
            c2a = st.text_input("Credit account 2", key="fte_fyjc_jc2a")
            c2v = st.text_input("Credit amount 2", key="fte_fyjc_jc2v")
        if st.button("Verify journal entry", key="fte_fyjc_jv_btn",
                     use_container_width=True):
            jv = verify_student_journal(
                q,
                [d1a, d2a], [d1v, d2v], [c1a, c2a], [c1v, c2v],
            )
            result["journal"] = jv
            st.rerun()
        jv = result.get("journal")
        if jv:
            _render_journal_verdict(jv)

    # --- Ledger balance check --------------------------------------------
    with st.expander("📗 Check a ledger balance"):
        lc = st.columns(3)
        with lc[0]:
            acc = st.text_input("Account", key="fte_fyjc_lacc",
                                placeholder="Cash")
        with lc[1]:
            bal = st.text_input("Your balance", key="fte_fyjc_lbal",
                                placeholder="50000")
        with lc[2]:
            side = st.selectbox("Side", ["Dr", "Cr"], key="fte_fyjc_lside")
        if st.button("Verify ledger balance", key="fte_fyjc_lv_btn",
                     use_container_width=True):
            lv = verify_student_ledger(acc, bal, side, entries)
            result["ledger"] = lv
            st.rerun()
        lv = result.get("ledger")
        if lv:
            _render_verdict_entry(lv)

    # --- Trial balance check ---------------------------------------------
    with st.expander("⚖️ Check a trial balance (one line per account)"):
        st.caption(
            "One account per line: `Account, Dr amount, Cr amount` — "
            "e.g. `Cash, 50000, 0` and `Capital, 0, 50000`."
        )
        tb_text = st.text_area(
            "Your trial balance lines", key="fte_fyjc_tb_lines", height=90,
            placeholder="Cash, 50000, 0\nCapital, 0, 50000",
        )
        if st.button("Verify trial balance", key="fte_fyjc_tbv_btn",
                     use_container_width=True):
            tv = verify_student_trial_balance(tb_text, entries)
            result["trial_balance"] = tv
            st.rerun()
        tv = result.get("trial_balance")
        if tv:
            _render_verdict_entry(tv)

    # --- Built-in consistency summary ------------------------------------
    if flow.get("verification"):
        v = flow["verification"]
        st.markdown("**Engine consistency check for this transaction:**")
        st.markdown(
            f"Debit {v.get('total_debit'):,.2f} = "
            f"Credit {v.get('total_credit'):,.2f} → {v.get('verdict')}"
        )
    if not (outcome.get("debit_lines") or outcome.get("credit_lines")):
        st.caption(
            "The transaction was not resolved, so reference checks are "
            "limited — fix the description above first."
        )


def _render_journal_verdict(jv: Dict[str, Any]) -> None:
    verdict = jv.get("verdict")
    if verdict == "CORRECT":
        st.success("✅ The journal entry is correct and follows the golden rule.")
        st.markdown(f"**Rule:** {_esc(jv.get('rule'))}")
    elif verdict == "INCORRECT":
        st.error(f"🔴 {_esc(jv.get('what'))} — {_esc(jv.get('why_not'))}")
    elif verdict == "BALANCED":
        st.info(f"⚖️ {_esc(jv.get('what'))} — {_esc(jv.get('why_not'))}")
    else:
        st.info(_esc(jv.get("why_not") or "The entry could not be verified."))
    td = jv.get("total_debit")
    tc = jv.get("total_credit")
    if td is not None and tc is not None:
        st.markdown(f"Total Debit {td:,.2f} vs Total Credit {tc:,.2f}")


def _render_verdict_entry(v: Dict[str, Any]) -> None:
    verdict = v.get("verdict")
    if verdict == "CORRECT":
        st.success(f"✅ {_esc(v.get('what'))}")
    elif verdict == "INCORRECT":
        st.error(f"🔴 {_esc(v.get('what'))} — {_esc(v.get('why_not'))}")
    else:
        st.info(_esc(v.get("why_not") or "Could not verify that check."))


# ---------------------------------------------------------------------------
# Study surface (supported topics)
# ---------------------------------------------------------------------------


def _render_study_topics() -> None:
    with st.expander("📚 What FT-E can verify (FYJC study list)", expanded=False):
        topics = fyjc_study_topics()
        st.markdown("**Maths (existing registered formulas only):**")
        st.markdown(" · ".join(f"`{m}`" for m in topics["maths"]))
        st.markdown("**Book-Keeping & Accountancy:**")
        for topic in topics["bookkeeping"]:
            st.markdown(f"- {topic}")
        st.caption(
            "Anything else is refused deterministically — FT-E never "
            "invents a formula or a value."
        )


# ---------------------------------------------------------------------------
# Page entry
# ---------------------------------------------------------------------------


def render_fyjc_student_ui(demo: bool = False) -> None:
    """The FYJC Study / Verify page (rendered inside the workspace)."""
    _ensure_css()

    if demo and not _question_text() and not st.session_state.get(K_FLOW):
        st.session_state[K_QUESTION] = (
            "Purchased goods from Rahul on credit for Rs.10,000."
        )

    _render_entry(demo)
    _render_study_topics()

    question = _question_text()
    if not question:
        st.caption("Enter or upload a question to begin.")
        return

    if st.session_state.get(K_FLOW) is None and st.session_state.get(K_EDIT):
        # waiting for the corrected question to be re-analysed
        st.stop()

    flow = st.session_state.get(K_FLOW)
    if flow is None:
        flow = run_fyjc_student_flow(question)
        st.session_state[K_FLOW] = flow

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


def _render_refusal(flow: Dict[str, Any]) -> None:
    status = flow.get("status")
    tone = "red" if status == "BLOCKED" else "amber"
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
