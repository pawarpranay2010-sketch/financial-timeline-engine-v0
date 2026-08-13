"""
Financial Timeline Engine
Sprint 15I-I - Student Practice UI + Teacher Dashboard
backend/fyjc_practice_ui.py

Pure orchestration/presentation layer on top of the verified 15I-H
student-learning engines and the 15I-G verified Question Bank. This
module contains NO accounting rules, NO verification logic and NO LLM.
Every verdict, mistake record and mastery update is produced by the
15I-H engines; every question comes from the 15I-G bank's APPROVED set.

Authority chain preserved (never inverted):
    FT-E verified journal
        -> QuestionBank canonical expected_journal
        -> PracticeEngine (verdict)
        -> MistakeLedger + MasteryEngine
        -> this UI

Student identity: the FYJC layer has no auth table; the UI derives the
student id from the signed-in workspace email when present, else a
caller-editable ID is used. Persistence is the repo-conventional JSON
layers (QuestionBank file + PracticeStore file); no new database.

Path overrides (tests/deployments): FTE_FYJC_BANK_PATH and
FTE_FYJC_PRACTICE_STORE_PATH.
"""

from __future__ import annotations

import html
import os
from typing import Any, Dict, List, Optional

import streamlit as st

from backend.maths.fyjc_practice_engine import (
    MODE_NORMAL,
    MODE_WEAKNESS,
    MODE_CHAPTER,
    MODE_EXAM_MIX,
    MODE_MISTAKE_RETRY,
    MODE_REVISION,
    MODES,
    OUTCOME_CORRECT,
    OUTCOME_INCORRECT,
    OUTCOME_REVIEW_REQUIRED,
    OUTCOME_NOT_SUPPORTED,
    SESSION_ACTIVE,
    SESSION_COMPLETED,
    SESSION_ABANDONED,
    PracticeEngine,
)
from backend.maths.fyjc_mistake_ledger import (
    MISTAKE_OPEN,
    MISTAKE_IMPROVING,
    MISTAKE_RESOLVED,
    MISTAKE_CATEGORIES,
)
from backend.maths.fyjc_mastery_engine import (
    MASTERY_UNSEEN,
    MASTERY_LEARNING,
    MASTERY_DEVELOPING,
    MASTERY_MASTERED,
    MASTERY_REVIEW,
)
from backend.maths.fyjc_question_bank import (
    QuestionBank,
    FYJC_CONTENT_BANK_PATH,
    STATUS_APPROVED,
    STATUS_DRAFT,
    STATUS_COMPILED,
    STATUS_VALIDATING,
    STATUS_REJECTED,
    STATUS_REVIEW_REQUIRED,
)

# Deterministic UI selection seed: same bank + same history + same config
# -> same question order. The engine still persists every interaction.
UI_RNG_SEED = 20260813

BANK_PATH = os.environ.get("FTE_FYJC_BANK_PATH") or FYJC_CONTENT_BANK_PATH
STORE_PATH = (os.environ.get("FTE_FYJC_PRACTICE_STORE_PATH")
              or os.path.join("content_bank", "practice.json"))

# Student-facing explanation templates. Deterministic, derived ONLY from
# the verified structural difference categories produced by 15I-H - never
# an LLM, never an invented answer.
_EXPLAIN: Dict[str, str] = {
    "AMOUNT_ERROR": (
        "The accounts are right, but at least one amount does not match "
        "the verified entry. Compare each amount with the answer below."
    ),
    "DEBIT_CREDIT_DIRECTION": (
        "The accounts are right, but the debit and credit sides are "
        "reversed. Check which account receives value (Dr) and which "
        "gives value (Cr)."
    ),
    "PARTY_ROLE_ERROR": (
        "The direction of the party account is wrong - check who owes "
        "whom before choosing which side the person's account sits on."
    ),
    "ACCOUNT_SELECTION": (
        "One or more accounts are missing or different from the verified "
        "entry. Compare the account names with the answer below."
    ),
    "TRANSACTION_CLASSIFICATION": (
        "The accounts do not match at all - the transaction was probably "
        "classified differently than the verified answer."
    ),
    "MULTI_TRANSACTION_ERROR": (
        "This question contains more than one transaction. Handle each "
        "transaction separately in one journal entry before submitting."
    ),
    "LEDGER_BALANCING_ERROR": (
        "Your journal entry does not balance: total debits must equal "
        "total credits. Re-check the amounts on both sides."
    ),
    "GST_ERROR": (
        "The GST treatment does not match the verified entry - check "
        "which amount is recorded to the GST account."
    ),
    "TRADE_DISCOUNT_ERROR": (
        "The trade discount treatment does not match the verified entry "
        "- trade discount is not recorded in the journal."
    ),
    "CASH_DISCOUNT_ERROR": (
        "The cash discount treatment does not match the verified entry - "
        "cash discount received/allowed is recorded separately."
    ),
    "UNSUPPORTED_RESPONSE": (
        "This response is outside the verified syllabus range, so FT-E "
        "will not guess an answer for it."
    ),
    "AMBIGUOUS_RESPONSE": (
        "Your answer is ambiguous - FT-E cannot verify it. Rewrite it "
        "with clear accounts and amounts and try again."
    ),
}

_UI_CSS = """
<style>
.fte-pi-title { font-size: 1.3rem; font-weight: 800; letter-spacing: -.01em;
  margin: .05rem 0 .1rem; }
.fte-pi-sub { color: var(--fte-muted, #8a94a6); font-size: .92rem;
  margin-bottom: .3rem; }
.fte-pi-card { border: 1px solid var(--fte-border, #2b3550);
  border-radius: 10px; padding: .6rem .85rem; margin: .35rem 0; }
.fte-pi-question { border: 1px solid var(--fte-accent, #4f8cff);
  border-radius: 10px; padding: .7rem .95rem; margin: .4rem 0;
  background: rgba(79,140,255,.07); font-size: 1.05rem; line-height: 1.5; }
.fte-pi-chip { display:inline-block; border-radius: 999px; padding:.1rem .6rem;
  font-size:.78rem; font-weight:700; margin-right:.3rem; }
.fte-pi-chip.green { background: rgba(46,204,113,.15); color:#2ecc71; }
.fte-pi-chip.amber { background: rgba(255,180,60,.15); color:#ffb43c; }
.fte-pi-chip.red   { background: rgba(255,99,99,.15); color:#ff6363; }
.fte-pi-chip.blue  { background: rgba(79,140,255,.15); color:#7fb0ff; }
.fte-pi-chip.gray  { background: rgba(138,148,166,.15); color:#a9b2c2; }
.fte-pi-verdict { border-radius: 10px; padding: .65rem .9rem; margin: .45rem 0; }
.fte-pi-verdict.good { border:1px solid #2ecc71; background: rgba(46,204,113,.07); }
.fte-pi-verdict.bad  { border:1px solid #ff6363; background: rgba(255,99,99,.07); }
.fte-pi-verdict.warn { border:1px solid #ffb43c; background: rgba(255,180,60,.07); }
.fte-pi-verdict.neutral { border:1px solid var(--fte-border,#2b3550);
  background: rgba(138,148,166,.08); }
.fte-pi-journal { font-family: var(--fte-mono, ui-monospace, monospace);
  font-size: .92rem; line-height: 1.55; }
.fte-pi-note { color: var(--fte-muted, #8a94a6); font-size: .85rem; }
</style>
"""


def _ensure_css() -> None:
    st.markdown(_UI_CSS, unsafe_allow_html=True)


def _esc(value: Any) -> str:
    return html.escape(str(value if value is not None else ""))


def _chip(label: str, tone: str) -> str:
    return (f'<span class="fte-pi-chip {tone}">'
            f'{html.escape(str(label))}</span>')


def _fmt_amount(value: Any) -> str:
    try:
        return f"{float(value):,.2f}"
    except (TypeError, ValueError):
        return _esc(value)


_DIFF_LABELS = {1: "EASY", 2: "MEDIUM", 3: "HARD"}


def _difficulty_label(value: Any) -> str:
    return _DIFF_LABELS.get(value, str(value or "UNKNOWN"))


def _journal_lines(journal: Optional[Dict[str, Any]],
                   side: str) -> List[List[Any]]:
    if not journal:
        return []
    return [[acc, amt] for acc, amt in (journal.get(side) or [])]


def _journal_text(journal: Optional[Dict[str, Any]]) -> str:
    """Compact deterministic text form of a journal (presentation only)."""
    lines: List[str] = []
    for acc, amt in _journal_lines(journal, "debit"):
        lines.append(f"Dr  {acc:<22} {_fmt_amount(amt)}")
    for acc, amt in _journal_lines(journal, "credit"):
        lines.append(f"Cr  {acc:<22} {_fmt_amount(amt)}")
    return "\n".join(lines) if lines else "(no verified journal)"


# ---------------------------------------------------------------------------
# Bank / engine loading (deterministic; nothing is computed in the UI)
# ---------------------------------------------------------------------------


def _load_bank() -> QuestionBank:
    bank = QuestionBank(store_path=BANK_PATH)
    if not bank.list_questions():
        # Seed the verified bank from the 15H benchmark corpus through the
        # FULL 15I-G pipeline (compile -> validate -> approve). Only
        # VERIFIED oracles with debit/credit are admitted; everything is
        # deterministic and re-validated by the bank itself. The bank is a
        # caller-saves store, so persist the seed explicitly (it only runs
        # once, when the store is empty).
        try:
            from backend.maths import fyjc_bk_15h_benchmark as _h15
            result = bank.seed_from_benchmark(
                _h15, source_name="fyjc_bk_15h_benchmark")
            if (result or {}).get("approved"):
                bank.save()
        except Exception:
            # Never block the page: an empty bank shows a useful empty state.
            pass
    return bank


def _load_engine() -> PracticeEngine:
    return PracticeEngine(_load_bank(), STORE_PATH, rng_seed=UI_RNG_SEED)


def _default_student_id() -> str:
    email = st.session_state.get("fte_user_email")
    if email:
        local = str(email).split("@")[0].strip()
        if local:
            return local
    return "student-1"


def _student_id() -> str:
    value = st.session_state.get("fte_pi_student") or ""
    return str(value).strip() or "student-1"


def _active_session(engine: PracticeEngine,
                    student_id: str) -> Optional[Dict[str, Any]]:
    marker = st.session_state.get("fte_pi_session") or {}
    if marker.get("student_id") != student_id:
        return None
    sid = marker.get("session_id")
    if not sid:
        return None
    try:
        s = engine.get_session(sid)
    except KeyError:
        st.session_state.pop("fte_pi_session", None)
        return None
    if s["status"] not in (SESSION_ACTIVE, SESSION_COMPLETED):
        st.session_state.pop("fte_pi_session", None)
        return None
    return s


# ---------------------------------------------------------------------------
# Verdict rendering (student-facing; deterministic templates only)
# ---------------------------------------------------------------------------


def _render_verdict(engine: PracticeEngine, question: Dict[str, Any],
                    attempt: Dict[str, Any]) -> None:
    outcome = attempt.get("outcome")
    category = attempt.get("mistake_category")
    canonical = question.get("expected_journal") or {}

    if outcome == OUTCOME_CORRECT:
        st.markdown(
            '<div class="fte-pi-verdict good">'
            f'{_chip("CORRECT", "green")} '
            '<b>Your journal entry matches the verified answer.</b>'
            '</div>',
            unsafe_allow_html=True,
        )
        with st.expander("Verified journal"):
            st.markdown(
                f'<div class="fte-pi-journal">{_esc(_journal_text(canonical))}'
                '</div>',
                unsafe_allow_html=True,
            )
        return

    if outcome == OUTCOME_INCORRECT:
        tone = "red"
        title = "Not quite"
        explain = _EXPLAIN.get(category or "", (
            "Your answer does not match the verified journal entry."))
        st.markdown(
            '<div class="fte-pi-verdict bad">'
            f'{_chip("INCORRECT", tone)} <b>{_esc(title)}</b><br/>'
            f'<span class="fte-pi-note">Category: '
            f'{_esc(category or "UNKNOWN")}</span></div>',
            unsafe_allow_html=True,
        )
        st.markdown(
            f'<div class="fte-pi-card">{_esc(explain)}</div>',
            unsafe_allow_html=True,
        )
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**Your journal**")
            st.markdown(
                f'<div class="fte-pi-journal">'
                f'{_esc(_journal_text(attempt.get("verified_journal")))}'
                f'</div>',
                unsafe_allow_html=True,
            )
        with c2:
            st.markdown("**Verified answer**")
            st.markdown(
                f'<div class="fte-pi-journal">'
                f'{_esc(_journal_text(canonical))}</div>',
                unsafe_allow_html=True,
            )
        return

    if outcome == OUTCOME_REVIEW_REQUIRED:
        st.markdown(
            '<div class="fte-pi-verdict warn">'
            f'{_chip("REVIEW REQUIRED", "amber")} '
            "<b>This transaction is ambiguous - FT-E cannot verify it "
            "without more clarity.</b><br/>"
            "<span class=\"fte-pi-note\">Rewrite the question or your "
            "answer with clear accounts and amounts. This attempt does "
            "not count as correct or incorrect.</span></div>",
            unsafe_allow_html=True,
        )
        return

    # NOT_SUPPORTED
    st.markdown(
        '<div class="fte-pi-verdict neutral">'
        f'{_chip("NOT SUPPORTED", "gray")} '
        "<b>This transaction type is outside FT-E's current verified "
        "syllabus range.</b><br/>"
        "<span class=\"fte-pi-note\">FT-E never guesses an answer - "
        "this attempt does not count as a mistake.</span></div>",
        unsafe_allow_html=True,
    )


def _render_journal_input() -> None:
    st.markdown("**Enter your journal entry**")
    header = st.columns([1.35, 1, 1.35, 1])
    header[0].markdown("**Debit account**")
    header[1].markdown("**Amount**")
    header[2].markdown("**Credit account**")
    header[3].markdown("**Amount**")
    for i in range(1, 4):
        row = st.columns([1.35, 1, 1.35, 1])
        with row[0]:
            st.text_input("", label_visibility="collapsed",
                          key=f"fte_pi_d{i}a", placeholder="e.g. Cash")
        with row[1]:
            st.text_input("", label_visibility="collapsed",
                          key=f"fte_pi_d{i}v", placeholder="e.g. 12000")
        with row[2]:
            st.text_input("", label_visibility="collapsed",
                          key=f"fte_pi_c{i}a", placeholder="e.g. Sales")
        with row[3]:
            st.text_input("", label_visibility="collapsed",
                          key=f"fte_pi_c{i}v", placeholder="e.g. 12000")


def _collect_journal() -> tuple:
    d_accs, d_amts, c_accs, c_amts = [], [], [], []
    for i in range(1, 4):
        d_accs.append(st.session_state.get(f"fte_pi_d{i}a", "") or "")
        d_amts.append(st.session_state.get(f"fte_pi_d{i}v", "") or "")
        c_accs.append(st.session_state.get(f"fte_pi_c{i}a", "") or "")
        c_amts.append(st.session_state.get(f"fte_pi_c{i}v", "") or "")
    raw_lines: List[str] = []
    for acc, amt in zip(d_accs, d_amts):
        if str(acc).strip() and str(amt).strip():
            raw_lines.append(f"Debit {acc} {amt}")
    for acc, amt in zip(c_accs, c_amts):
        if str(acc).strip() and str(amt).strip():
            raw_lines.append(f"Credit {acc} {amt}")
    return d_accs, d_amts, c_accs, c_amts, "; ".join(raw_lines)


def _clear_journal_inputs() -> None:
    for i in range(1, 4):
        for side in ("d", "c"):
            st.session_state.pop(f"fte_pi_{side}{i}a", None)
            st.session_state.pop(f"fte_pi_{side}{i}v", None)


# ---------------------------------------------------------------------------
# Practice flow
# ---------------------------------------------------------------------------


def _scope_options(engine: PracticeEngine) -> Dict[str, List[Any]]:
    approved = engine.bank.list_approved()
    chapters = sorted({q.get("chapter") for q in approved
                       if q.get("chapter") and q["chapter"] != "UNKNOWN"})
    concepts = sorted({q.get("concept_key") for q in approved
                       if q.get("concept_key")
                       and q["concept_key"] != "UNKNOWN"})
    types = sorted({t for q in approved
                    for t in (q.get("transaction_types") or [])})
    difficulties = sorted({q.get("difficulty") for q in approved
                           if q.get("difficulty") != "UNKNOWN"})
    return {"chapters": chapters, "concepts": concepts,
            "types": types, "difficulties": difficulties}


def _render_new_session(engine: PracticeEngine, student_id: str) -> None:
    opts = _scope_options(engine)
    mode_labels = {
        MODE_NORMAL: "Normal practice",
        MODE_WEAKNESS: "Weakness practice",
        MODE_CHAPTER: "Chapter practice",
        MODE_EXAM_MIX: "Exam mix",
        MODE_MISTAKE_RETRY: "Mistake retry",
        MODE_REVISION: "Revision",
    }
    if not engine.bank.list_approved():
        st.info(
            "No approved questions are available yet. Add verified content "
            "through the Question Bank before starting practice."
        )
        return
    with st.form("fte_pi_new_session"):
        st.markdown("**Set up your practice session**")
        col1, col2 = st.columns(2)
        with col1:
            mode = st.selectbox(
                "Mode", list(MODES), key="fte_pi_mode",
                format_func=lambda m: mode_labels.get(m, m))
            chapter = st.selectbox(
                "Chapter", ["Any"] + opts["chapters"], key="fte_pi_chapter")
            concept = st.selectbox(
                "Concept", ["Any"] + opts["concepts"], key="fte_pi_concept")
        with col2:
            difficulty = st.selectbox(
                "Difficulty", ["Any"] + [
                    f"{_difficulty_label(d)} ({d})" for d in opts["difficulties"]],
                key="fte_pi_difficulty")
            ttype = st.selectbox(
                "Transaction type", ["Any"] + opts["types"], key="fte_pi_type")
            count = st.number_input(
                "Number of questions", min_value=1, max_value=100, value=10,
                key="fte_pi_count")
        started = st.form_submit_button(
            "Start practice session", type="primary")
    if not started:
        return
    diff_value = None
    if difficulty != "Any":
        try:
            diff_value = int(difficulty.split("(")[-1].rstrip(")"))
        except (IndexError, ValueError):
            diff_value = None
    sid = engine.create_session(
        student_id=student_id,
        mode=mode,
        chapter=None if chapter == "Any" else chapter,
        concept=None if concept == "Any" else concept,
        transaction_type=None if ttype == "Any" else ttype,
        difficulty=diff_value,
        question_count=int(count),
    )
    st.session_state["fte_pi_session"] = {
        "student_id": student_id, "session_id": sid}
    st.session_state["fte_pi_qid"] = None
    st.session_state["fte_pi_last"] = None
    st.session_state.pop("fte_pi_summary", None)
    st.rerun()


def _render_question_screen(engine: PracticeEngine, session: Dict[str, Any],
                            student_id: str) -> None:
    sid = session["session_id"]
    qid = st.session_state.get("fte_pi_qid")
    if qid is None:
        try:
            qid = engine.select_next(sid)
        except ValueError as exc:
            st.warning(f"Could not pick a question: {_esc(exc)}")
            st.session_state.pop("fte_pi_session", None)
            return
        st.session_state["fte_pi_qid"] = qid
        st.session_state["fte_pi_last"] = None
        st.rerun()
    question = engine.bank.get_question(qid)
    chips = [
        _chip(f"Q {session.get('current_index', 0)}",
              "blue"),
        _chip(f"{_difficulty_label(question.get('difficulty'))}",
              "gray"),
    ]
    if question.get("chapter") and question["chapter"] != "UNKNOWN":
        chips.append(_chip(str(question["chapter"]), "blue"))
    if question.get("concept_key") and question["concept_key"] != "UNKNOWN":
        chips.append(_chip(str(question["concept_key"]), "blue"))
    for t in (question.get("transaction_types") or []):
        chips.append(_chip(str(t), "gray"))
    st.markdown(
        f'<div class="fte-pi-card">{" ".join(chips)}</div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        f'<div class="fte-pi-question">{_esc(question.get("raw_text"))}</div>',
        unsafe_allow_html=True,
    )

    _render_journal_input()
    st.caption(
        "Enter the journal entry for the whole question. Rows you leave "
        "empty are ignored."
    )
    if st.button("Submit answer", key="fte_pi_submit", type="primary",
                 width="stretch"):
        d_accs, d_amts, c_accs, c_amts, raw = _collect_journal()
        try:
            attempt = engine.submit_answer(
                sid, qid, d_accs, d_amts, c_accs, c_amts, raw_response=raw)
        except ValueError as exc:
            st.error(f"Could not submit: {_esc(exc)}")
            return
        st.session_state["fte_pi_last"] = attempt
        st.rerun()

    last = st.session_state.get("fte_pi_last")
    if not last:
        return
    _render_verdict(engine, question, last)

    st.markdown("---")
    b1, b2, b3 = st.columns(3)
    with b1:
        if st.button("Retry", key="fte_pi_retry", width="stretch"):
            st.session_state["fte_pi_last"] = None
            st.rerun()
    with b2:
        if st.button("Next question", key="fte_pi_next", width="stretch"):
            st.session_state["fte_pi_last"] = None
            st.session_state["fte_pi_qid"] = None
            _clear_journal_inputs()
            st.rerun()
    with b3:
        if st.button("End session", key="fte_pi_end", width="stretch"):
            completed = engine.complete_session(sid)
            st.session_state["fte_pi_summary"] = {
                "session": completed,
                "dashboard": engine.student_dashboard(student_id),
            }
            st.session_state.pop("fte_pi_session", None)
            st.session_state["fte_pi_qid"] = None
            st.session_state["fte_pi_last"] = None
            st.rerun()


def _render_summary(engine: PracticeEngine, student_id: str) -> None:
    summary = st.session_state.get("fte_pi_summary") or {}
    session = summary.get("session") or {}
    dash = summary.get("dashboard") or {}
    st.markdown('<div class="fte-pi-title">Session complete</div>',
                unsafe_allow_html=True)
    if not session:
        st.caption("No finished session to show yet.")
        return
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Answered", session.get("completed_count", 0))
    m2.metric("Correct", session.get("correct_count", 0))
    m3.metric("Incorrect", session.get("incorrect_count", 0))
    m4.metric("Review needed", session.get("review_required_count", 0))
    scored = (session.get("correct_count", 0)
              + session.get("incorrect_count", 0))
    accuracy = (session.get("correct_count", 0) / scored
                if scored else 0.0)
    st.markdown(
        f"**Accuracy this session:** {accuracy:.0%} "
        f"({session.get('correct_count', 0)} correct of {scored} scored)"
    )
    st.markdown("**Recommended review areas**")
    review_areas: List[str] = []
    for rec in engine.mastery.records().values():
        if rec.get("student_id") != student_id:
            continue
        if rec.get("mastery_state") == MASTERY_REVIEW:
            review_areas.append(f"{rec.get('concept_key')} (review)")
        elif rec.get("mastery_state") == MASTERY_LEARNING:
            review_areas.append(f"{rec.get('concept_key')} (learning)")
    open_mistakes = [m for m in engine.ledger.records().values()
                     if m.get("student_id") == student_id
                     and m.get("status") == MISTAKE_OPEN]
    for m in open_mistakes:
        review_areas.append(
            f"{m.get('concept_key')} — {m.get('mistake_category')}")
    if not review_areas:
        st.caption("Nothing flagged — keep going!")
    else:
        for area in sorted(set(review_areas)):
            st.markdown(f"- {_esc(area)}")
    if st.button("Start a new session", key="fte_pi_new_session_btn",
                 type="primary"):
        st.session_state.pop("fte_pi_summary", None)
        st.rerun()


def render_practice_section(demo: bool = False) -> None:
    _ensure_css()
    st.markdown('<div class="fte-pi-title">FYJC Practice</div>',
                unsafe_allow_html=True)
    st.markdown(
        '<div class="fte-pi-sub">Practice verified questions, get '
        'FT-E-backed feedback, and watch your mastery grow.</div>',
        unsafe_allow_html=True,
    )
    student_id = st.text_input(
        "Student ID", key="fte_pi_student", value=_default_student_id())
    student_id = student_id.strip() or "student-1"
    engine = _load_engine()

    tab_practice, tab_progress = st.tabs(["Practice", "My progress"])
    with tab_practice:
        session = _active_session(engine, student_id)
        if session and session["status"] == SESSION_ACTIVE:
            _render_question_screen(engine, session, student_id)
        elif session and session["status"] == SESSION_COMPLETED:
            st.session_state["fte_pi_summary"] = {
                "session": session,
                "dashboard": engine.student_dashboard(student_id),
            }
            st.session_state.pop("fte_pi_session", None)
            _render_summary(engine, student_id)
        elif st.session_state.get("fte_pi_summary"):
            _render_summary(engine, student_id)
        else:
            _render_new_session(engine, student_id)
    with tab_progress:
        _render_progress(engine, student_id)


# ---------------------------------------------------------------------------
# Student progress view (15I-H data only, no alternative formulas)
# ---------------------------------------------------------------------------


def _render_progress(engine: PracticeEngine, student_id: str) -> None:
    dash = engine.student_dashboard(student_id)
    if dash["total_attempts"] == 0:
        st.info(
            "No practice data yet for this student. Start a practice "
            "session to build your progress here."
        )
        return
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Questions attempted", dash["total_attempts"])
    c2.metric("Accuracy (lifetime)", f"{dash['lifetime_accuracy']:.0%}")
    c3.metric("Accuracy (recent 10)", f"{dash['recent_accuracy']:.0%}")
    c4.metric("Current streak", dash["current_streak"])
    c1.metric("Open mistakes", dash["open_mistakes"])
    c2.metric("Resolved mistakes", dash["resolved_mistakes"])
    c3.metric("Review-required", dash["review_required"])
    c4.metric("Concepts seen", dash["mastery"].get("concepts_seen", 0))

    st.markdown("**Concept mastery**")
    mastery_rows = [r for r in engine.mastery.records().values()
                    if r.get("student_id") == student_id]
    if mastery_rows:
        for rec in sorted(mastery_rows, key=lambda r: r["concept_key"]):
            state = rec.get("mastery_state") or MASTERY_UNSEEN
            tone = {"MASTERED": "green", "DEVELOPING": "blue",
                    "LEARNING": "amber", "REVIEW": "red"}.get(state, "gray")
            st.markdown(
                f'<div class="fte-pi-card">{_chip(state, tone)} '
                f'<b>{_esc(rec.get("concept_key"))}</b> — '
                f'{rec.get("attempts", 0)} attempts, '
                f'accuracy {rec.get("accuracy", 0.0):.0%}, '
                f'recent {rec.get("recent_accuracy", 0.0):.0%}</div>',
                unsafe_allow_html=True,
            )
    else:
        st.caption("No mastery records yet.")

    mistakes = [m for m in engine.ledger.records().values()
                if m.get("student_id") == student_id]
    open_mistakes = [m for m in mistakes if m.get("status") == MISTAKE_OPEN]
    repeated = [m for m in mistakes
                if (m.get("occurrence_count") or 0) > 1]
    st.markdown("**Recent mistakes**")
    if not mistakes:
        st.caption("No mistakes recorded.")
    else:
        for m in sorted(mistakes, key=lambda x: str(x.get("created_at")),
                        reverse=True)[:8]:
            tone = {"OPEN": "red", "IMPROVING": "amber",
                    "RESOLVED": "green"}.get(m.get("status"), "gray")
            st.markdown(
                f'<div class="fte-pi-card">{_chip(str(m.get("status")), tone)} '
                f'<b>{_esc(m.get("concept_key"))}</b> — '
                f'{_esc(m.get("mistake_category"))} '
                f'(x{m.get("occurrence_count", 1)})</div>',
                unsafe_allow_html=True,
            )
    if repeated:
        st.markdown("**Repeated mistakes** "
                    f"({len(repeated)})")
        for m in repeated[:8]:
            st.markdown(
                f"- {_esc(m.get('concept_key'))} — "
                f"{_esc(m.get('mistake_category'))} "
                f"(x{m.get('occurrence_count', 1)})"
            )

    st.markdown("**Recent sessions**")
    sessions = [s for s in engine.store.sessions.values()
                if s.get("student_id") == student_id]
    if not sessions:
        st.caption("No sessions yet.")
    else:
        for s in sorted(sessions, key=lambda x: str(x.get("started_at")),
                        reverse=True)[:6]:
            st.markdown(
                f"- {_esc(s.get('session_id'))} — {_esc(s.get('mode'))} — "
                f"{s.get('completed_count', 0)} answered, "
                f"{s.get('correct_count', 0)} correct — "
                f"{_esc(s.get('status'))}"
            )
    weak = dash["mastery"].get("weakest") or []
    if weak:
        st.markdown("**Needs review**")
        for rec in weak[:5]:
            st.markdown(
                f"- {_esc(rec.get('concept_key'))} ({_esc(rec.get('mastery_state'))})"
            )


# ---------------------------------------------------------------------------
# Teacher dashboard (read-only analytics + controlled content operations)
# ---------------------------------------------------------------------------


def _render_bank_tab(engine: PracticeEngine) -> None:
    st.markdown("**Question Bank**")
    f1, f2, f3, f4 = st.columns(4)
    with f1:
        status = st.selectbox(
            "Status", ["All", STATUS_APPROVED, STATUS_DRAFT,
                       STATUS_COMPILED, STATUS_VALIDATING,
                       STATUS_REJECTED, STATUS_REVIEW_REQUIRED],
            key="fte_pi_tb_status")
    with f2:
        chapter = st.selectbox("Chapter", ["All"] + sorted({
            q.get("chapter") for q in engine.bank.list_questions()
            if q.get("chapter")}), key="fte_pi_tb_chapter")
    with f3:
        concept = st.selectbox("Concept", ["All"] + sorted({
            q.get("concept_key") for q in engine.bank.list_questions()
            if q.get("concept_key")}), key="fte_pi_tb_concept")
    with f4:
        difficulty = st.selectbox(
            "Difficulty", ["All", "1 (EASY)", "2 (MEDIUM)", "3 (HARD)"],
            key="fte_pi_tb_difficulty")
    questions = engine.bank.list_questions()
    if status != "All":
        questions = [q for q in questions if q.get("status") == status]
    if chapter != "All":
        questions = [q for q in questions if q.get("chapter") == chapter]
    if concept != "All":
        questions = [q for q in questions
                     if q.get("concept_key") == concept
                     or q.get("concept") == concept]
    if difficulty != "All":
        dval = int(difficulty.split(" ")[0])
        questions = [q for q in questions if q.get("difficulty") == dval]
    if not questions:
        st.caption("No questions match these filters.")
        return
    import pandas as pd
    rows = [{
        "question_id": q.get("question_id"),
        "status": q.get("status"),
        "chapter": q.get("chapter"),
        "concept": q.get("concept_key"),
        "difficulty": q.get("difficulty"),
        "types": ",".join(q.get("transaction_types") or []),
        "source": (q.get("source") or {}).get("source_name"),
    } for q in questions]
    st.dataframe(pd.DataFrame(rows), use_container_width=True, height=260)
    with st.expander(f"Inspect question ({len(questions)})"):
        for q in questions:
            st.markdown(f"**{q.get('question_id')}** — {_esc(q.get('raw_text'))}")
            st.caption(
                f"status={q.get('status')} • chapter={q.get('chapter')} • "
                f"concept={q.get('concept_key')} • difficulty="
                f"{q.get('difficulty')} • transactions="
                f"{q.get('transaction_count')}"
            )


def _render_students_tab(engine: PracticeEngine) -> None:
    st.markdown("**Student learning analytics**")
    students = sorted({a.get("student_id")
                       for a in engine.store.attempts.values()})
    if not students:
        st.caption("No student activity recorded yet.")
        return
    rows = []
    for sid in students:
        dash = engine.student_dashboard(sid)
        sessions = [s for s in engine.store.sessions.values()
                    if s.get("student_id") == sid]
        rows.append({
            "student_id": sid,
            "sessions": len(sessions),
            "attempts": dash["total_attempts"],
            "correct": dash["correct"],
            "incorrect": dash["incorrect"],
            "review_required": dash["review_required"],
            "open_mistakes": dash["open_mistakes"],
            "lifetime_accuracy": f"{dash['lifetime_accuracy']:.0%}",
        })
    import pandas as pd
    st.dataframe(pd.DataFrame(rows), use_container_width=True, height=220)


def _render_mistakes_tab(engine: PracticeEngine) -> None:
    st.markdown("**Mistake analysis**")
    records = list(engine.ledger.records().values())
    if not records:
        st.caption("No mistakes recorded yet.")
        return
    students = sorted({r.get("student_id") for r in records})
    concepts = sorted({r.get("concept_key") for r in records})
    categories = sorted({r.get("mistake_category") for r in records})
    f1, f2, f3 = st.columns(3)
    with f1:
        student = st.selectbox("Student", ["All"] + students,
                               key="fte_pi_tm_student")
    with f2:
        concept = st.selectbox("Concept", ["All"] + concepts,
                               key="fte_pi_tm_concept")
    with f3:
        category = st.selectbox("Category", ["All"] + categories,
                                key="fte_pi_tm_category")
    if student != "All":
        records = [r for r in records if r.get("student_id") == student]
    if concept != "All":
        records = [r for r in records if r.get("concept_key") == concept]
    if category != "All":
        records = [r for r in records
                   if r.get("mistake_category") == category]
    import pandas as pd
    rows = [{
        "mistake_id": r.get("mistake_id"),
        "student_id": r.get("student_id"),
        "question_id": r.get("question_id"),
        "concept": r.get("concept_key"),
        "category": r.get("mistake_category"),
        "occurrences": r.get("occurrence_count"),
        "status": r.get("status"),
        "created": r.get("created_at"),
    } for r in records]
    st.dataframe(pd.DataFrame(rows), use_container_width=True, height=260)


def _render_review_tab(engine: PracticeEngine) -> None:
    st.markdown("**Question review** (read-only inspection; "
                "canonical journals are never editable from the UI)")
    qid = st.text_input("Question ID", key="fte_pi_tr_qid",
                        placeholder="e.g. Q-...")
    if not qid:
        st.caption(
            "Enter a question ID to inspect provenance, validation state, "
            "the canonical journal and variants."
        )
        return
    try:
        q = engine.bank.get_question(qid)
    except KeyError:
        st.error(f"Unknown question: {_esc(qid)}")
        return
    tone = {"APPROVED": "green", "VALIDATING": "blue", "COMPILED": "blue",
            "DRAFT": "gray", "REJECTED": "red",
            "REVIEW_REQUIRED": "amber"}.get(q.get("status"), "gray")
    st.markdown(
        f'<div class="fte-pi-card">{_chip(str(q.get("status")), tone)} '
        f'<b>{_esc(qid)}</b></div>',
        unsafe_allow_html=True,
    )
    st.markdown(f"**Question:** {_esc(q.get('raw_text'))}")
    st.markdown(
        f"Chapter: {_esc(q.get('chapter'))} • Concept: "
        f"{_esc(q.get('concept_key'))} • Difficulty: "
        f"{_esc(q.get('difficulty'))} • Transactions: "
        f"{_esc(q.get('transaction_count'))}"
    )
    src = q.get("source") or {}
    with st.expander("Provenance"):
        for k, v in src.items():
            st.markdown(f"- **{_esc(k)}:** {_esc(v)}")
        mp = q.get("metadata_provenance") or {}
        if mp:
            st.markdown("**Metadata provenance**")
            for k, v in mp.items():
                st.markdown(f"- {_esc(k)}: {_esc(v)}")
    with st.expander("Canonical verified journal"):
        st.markdown(
            f'<div class="fte-pi-journal">'
            f'{_esc(_journal_text(q.get("expected_journal")))}</div>',
            unsafe_allow_html=True,
        )
    validation = q.get("validation_status")
    errors = q.get("validation_errors") or []
    warnings = q.get("validation_warnings") or []
    with st.expander("Validation state"):
        st.markdown(f"- validation_status: {_esc(validation)}")
        for e in errors:
            st.markdown(f"- error: {_esc(e)}")
        for w in warnings:
            st.markdown(f"- warning: {_esc(w)}")
    variants = q.get("variants") or []
    with st.expander(f"Variants ({len(variants)})"):
        if not variants:
            st.caption("No variants linked.")
        for v in variants:
            st.markdown(f"- {_esc(v)}")
    if q.get("canonical_id"):
        st.caption(f"This question is a variant of {_esc(q.get('canonical_id'))}.")
    # Controlled lifecycle operations only (existing bank APIs). Reject is
    # guarded by an explicit confirmation checkbox; canonical journals stay
    # untouched.
    c1, c2 = st.columns(2)
    with c1:
        if q.get("status") in (STATUS_APPROVED, STATUS_VALIDATING,
                               STATUS_COMPILED, STATUS_DRAFT):
            if st.button("Re-validate", key="fte_pi_tr_revalidate"):
                try:
                    engine.bank.validate_question(qid)
                    engine.bank.save()  # caller-saves store
                    st.success("Question re-validated through the "
                               "deterministic pipeline.")
                    st.rerun()
                except Exception as exc:  # deterministic pipeline errors
                    st.error(f"Re-validation failed: {_esc(exc)}")
    with c2:
        confirm = st.checkbox("Confirm rejection", key="fte_pi_tr_confirm")
        if st.button("Reject question", key="fte_pi_tr_reject",
                     disabled=not confirm):
            try:
                engine.bank.reject_question(qid)
                engine.bank.save()  # caller-saves store
                st.success("Question moved to REJECTED (never repaired).")
                st.rerun()
            except Exception as exc:
                st.error(f"Could not reject: {_esc(exc)}")


def render_teacher_section(demo: bool = False) -> None:
    _ensure_css()
    st.markdown('<div class="fte-pi-title">Teacher Dashboard</div>',
                unsafe_allow_html=True)
    st.markdown(
        '<div class="fte-pi-sub">Inspect approved content and student '
        'learning evidence. FT-E remains the sole accounting authority.</div>',
        unsafe_allow_html=True,
    )
    engine = _load_engine()
    tab_bank, tab_students, tab_mistakes, tab_review = st.tabs(
        ["Question Bank", "Students", "Mistakes", "Question Review"])
    with tab_bank:
        _render_bank_tab(engine)
    with tab_students:
        _render_students_tab(engine)
    with tab_mistakes:
        _render_mistakes_tab(engine)
    with tab_review:
        _render_review_tab(engine)
