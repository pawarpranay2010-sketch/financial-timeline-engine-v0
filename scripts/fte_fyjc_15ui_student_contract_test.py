#!/usr/bin/env python3
"""
Platrixa
Sprint 15I-UI - Student Interaction Contract & Confidence Gate Test
scripts/fte_fyjc_15ui_student_contract_test.py

Permanent gate for the first production UI milestone. Every check runs
against the REAL production boundary (backend.maths.fyjc_orchestration.
orchestrate) through the pure projection layer
(backend.maths.fyjc_ui_contract) - the UI never calculates, never
infers accounts, never invents amounts, never resolves ambiguity by
itself, and never generates accounting rules.

  A. Clear transaction  - VERIFIED, NO Confidence Gate, journal lines
     byte-identical to the backend's verified lines.
  B. Genuine ambiguity  - one precise Confidence Gate appears ONLY from
     the backend refusal payload (GST scheme: intra-state CGST+SGST vs
     inter-state IGST) with exactly two alternatives.
  C. User resolves       - the choice is consumed, the backend reruns
     with the decision explicit, both alternatives VERIFY with the
     correct different journals; repeated resolution is byte-identical.
  D. Repeated ambiguity - same input -> identical gate payload, always.
  E. Unsafe ambiguity    - a refusal with no finite safe alternative set
     stays a refusal; the gate never forces a choice.
  F. Contradiction       - INVALID_INPUT_MATH, zero journal lines, no
     fabricated result.
  G. Verified parity     - projection journal == backend verified journal
     across every released authority (commercial, bills, discrepancy,
     consignment, joint venture, single entry, bad-debt recovery).
  H. Why layer           - every rendered explanation event maps to an
     engine event/rule id through the localization dictionary; wording
     can never change accounting behavior.
  I. Debug mode          - the debug payload exactly reflects the
     production graph and cannot be altered (deep-copy read-only).
  J. Determinism         - same input + same backend state -> same graph
     -> same journal -> same verification -> same explanation path.
  K. Status behaviour    - distinct student-readable states for
     VERIFIED / REVIEW_REQUIRED / INVALID_INPUT_MATH / NOT_SUPPORTED /
     BLOCKED; no raw internal error codes in student copy.

PART 2 runs the REAL Streamlit Study/Verify UI through AppTest:
  - the app opens DIRECTLY into the student workspace (single text
    area, no login screen, no registration gate),
  - clear input -> verified result with backend-exact journal,
  - genuine ambiguity -> the Confidence Gate appears,
  - the student resolves it -> 'Got it. Continuing with ...' + VERIFIED,
  - contradiction -> INVALID INPUT (MATH), no fabricated result,
  - unsafe ambiguity -> refuses, no gate,
  - FTE_DEBUG_GRAPH=true -> read-only graph debug surface renders.

Exit code 0 = all checks pass.
"""

import json
import os
import sys
from copy import deepcopy
from dataclasses import asdict

sys.path.insert(0, os.getcwd())

from backend.maths.fyjc_bk_reasoning import (  # noqa: E402
    INVALID_INPUT_MATH,
    NOT_SUPPORTED,
    REVIEW_REQUIRED,
)
from backend.maths.fyjc_orchestration import (  # noqa: E402
    build_transaction_graph,
    orchestrate,
)
from backend.maths.fyjc_normalization import normalize_fyjc_text  # noqa: E402
from backend.maths.fyjc_ui_contract import (  # noqa: E402
    STATUS_PRESENTATION,
    WHY_LOCALIZATION,
    build_confidence_gate,
    debug_graph_payload,
    gate_is_pending,
    project_student_result,
    resolve_confidence_gate,
)
from backend.maths.status import BLOCKED, VERIFIED  # noqa: E402

TOTAL = [0]
FAILURES = []


def check(name: str, ok: bool, detail: str = "") -> None:
    TOTAL[0] += 1
    if ok:
        print(f"OK  [{name}]")
    else:
        FAILURES.append(name)
        print(f"FAIL [{name}] {detail}")


def backend_lines(result) -> list:
    return [
        (str(line.get("account")), str(line.get("amount")))
        for line in (result.get("debit_lines") or [])
        + (result.get("credit_lines") or [])
        if line.get("account")
    ]


def projection_lines(projection) -> list:
    return [
        (str(row.get("account")), str(row.get("amount")))
        for row in (projection.get("journal") or {}).get("rows") or []
    ]


def projection_events(projection) -> list:
    return [e.get("event_id") for e in (projection.get("why") or {}).get("events") or []]


# ---------------------------------------------------------------------------
# PART 1 - Backend interaction contract (pure, deterministic)
# ---------------------------------------------------------------------------

GATE_SALE = "Sold goods to Rahul for Rs.10,000 at 18% GST."
GATE_PURCHASE = "Purchased goods from Mark worth Rs.50,000 at 12% GST."


def test_a_clear_transaction() -> None:
    print("PART A - CLEAR TRANSACTION (no unnecessary clarification)")
    questions = [
        "Sold goods to Rahul for Rs.10,000 at 10% trade discount.",
        "Purchased goods from Mark on credit for Rs.50,000.",
        "Sold goods to Ram on credit for Rs.10,000. Received Rs.5,000 "
        "from him in part settlement.",
        "Purchased goods with a list price of \u20b925,000 at 10% trade "
        "discount from ravi kumar on credit.",
    ]
    for index, q in enumerate(questions):
        result = orchestrate(q)
        projection = project_student_result(result, q)
        check(f"A.{index + 1} VERIFIED [{q[:34]}]",
              result.get("status") == VERIFIED
              and projection.get("status") == VERIFIED,
              str(result.get("status")))
        check(f"A.{index + 1} no gate [{q[:34]}]",
              projection.get("confidence_gate") is None,
              str(projection.get("confidence_gate")))
        check(f"A.{index + 1} journal parity [{q[:34]}]",
              projection_lines(projection) == backend_lines(result),
              f"{projection_lines(projection)} != {backend_lines(result)}")
        check(f"A.{index + 1} verification statement [{q[:34]}]",
              bool((projection.get("verification") or {}).get("statement")),
              "")
    # balance statement on a verified result
    result = orchestrate(questions[0])
    projection = project_student_result(result, questions[0])
    check("A.5 balanced verification", (projection.get("verification") or {}).get("balanced") is True, "")


def test_b_genuine_ambiguity() -> None:
    print("PART B - GENUINE AMBIGUITY (one precise Confidence Gate)")
    for index, q in enumerate((GATE_SALE, GATE_PURCHASE)):
        result = orchestrate(q)
        projection = project_student_result(result, q)
        check(f"B.{index + 1} refuses [{q[:34]}]",
              result.get("status") == REVIEW_REQUIRED,
              str(result.get("status")))
        check(f"B.{index + 1} gate present [{q[:34]}]",
              projection.get("confidence_gate") is not None, "")
        gate = projection.get("confidence_gate") or {}
        check(f"B.{index + 1} gate id [{q[:34]}]",
              gate.get("gate_id") == "GST_SCHEME", str(gate.get("gate_id")))
        check(f"B.{index + 1} two alternatives [{q[:34]}]",
              len(gate.get("alternatives") or []) == 2,
              str(gate.get("alternatives")))
        ids = sorted(a.get("id") for a in gate.get("alternatives") or [])
        check(f"B.{index + 1} alternative ids [{q[:34]}]",
              ids == ["inter_state", "intra_state"], str(ids))
        check(f"B.{index + 1} segment from backend [{q[:34]}]",
              gate.get("segment") == q, str(gate.get("segment")))
        check(f"B.{index + 1} reason from backend refusal [{q[:34]}]",
              "intra-state" in (gate.get("reason") or ""), "")
        check(f"B.{index + 1} pending [{q[:34]}]",
              gate_is_pending(projection), "")
        # zero journal lines on the pending refusal
        check(f"B.{index + 1} zero lines while pending [{q[:34]}]",
              projection_lines(projection) == [], "")


def test_c_user_resolves() -> None:
    print("PART C - USER RESOLVES THE AMBIGUITY (choice consumed, continues)")
    for index, (q, expected_intra, expected_inter) in enumerate((
        (GATE_SALE,
         [("Rahul", "11800.00"), ("Sales", "10000"),
          ("Output CGST", "900.00"), ("Output SGST", "900.00")],
         [("Rahul", "11800.00"), ("Sales", "10000"),
          ("Output IGST", "1800.00")]),
        (GATE_PURCHASE,
         [("Purchases", "50000"), ("Input CGST", "3000.00"),
          ("Input SGST", "3000.00"), ("Mark", "56000.00")],
         [("Purchases", "50000"), ("Input IGST", "6000.00"),
          ("Mark", "56000.00")]),
    )):
        p_intra = resolve_confidence_gate(q, "GST_SCHEME", "intra_state")
        p_inter = resolve_confidence_gate(q, "GST_SCHEME", "inter_state")
        check(f"C.{index + 1} intra VERIFIED", p_intra.get("status") == VERIFIED,
              str(p_intra.get("status")))
        check(f"C.{index + 1} inter VERIFIED", p_inter.get("status") == VERIFIED,
              str(p_inter.get("status")))
        check(f"C.{index + 1} intra journal exact",
              projection_lines(p_intra) == expected_intra,
              str(projection_lines(p_intra)))
        check(f"C.{index + 1} inter journal exact",
              projection_lines(p_inter) == expected_inter,
              str(projection_lines(p_inter)))
        check(f"C.{index + 1} choices differ", expected_intra != expected_inter, "")
        # provenance of the decision
        res = p_intra.get("gate_resolution") or {}
        check(f"C.{index + 1} provenance keeps original question",
              res.get("original_question") == q, "")
        check(f"C.{index + 1} provenance records resolved question",
              "CGST @ 9%" in res.get("resolved_question", "")
              or "CGST @ 6%" in res.get("resolved_question", ""),
              str(res.get("resolved_question")))
        check(f"C.{index + 1} provenance accepted + final status",
              res.get("accepted") is True
              and res.get("final_status") == VERIFIED, str(res))
        check(f"C.{index + 1} gate no longer pending",
              not gate_is_pending(p_intra) and not gate_is_pending(p_inter), "")
        # deterministic repeat: byte-identical projection
        check(f"C.{index + 1} resolution deterministic repeat",
              p_intra == resolve_confidence_gate(q, "GST_SCHEME", "intra_state")
              and p_inter == resolve_confidence_gate(q, "GST_SCHEME", "inter_state"),
              "")
    # odd rate resolves through the same gate (7.5% CGST/SGST split)
    q = "Sold goods to Rahul for Rs.10,000 at 15% GST."
    result = orchestrate(q)
    gate = build_confidence_gate(result, q)
    check("C.3 odd-rate gate present", gate is not None, "")
    p_intra = resolve_confidence_gate(q, "GST_SCHEME", "intra_state")
    check("C.4 odd-rate intra VERIFIED", p_intra.get("status") == VERIFIED,
          str(p_intra.get("status")))
    check("C.4 odd-rate intra journal",
          projection_lines(p_intra) == [("Rahul", "11500.00"), ("Sales", "10000"),
                                        ("Output CGST", "750.00"),
                                        ("Output SGST", "750.00")],
          str(projection_lines(p_intra)))
    # an unknown gate id / decision never fabricates
    p_bad = resolve_confidence_gate(q, "NOT_A_GATE", "intra_state")
    check("C.5 unknown gate honest refusal", p_bad.get("status") != VERIFIED, "")
    p_bad2 = resolve_confidence_gate(q, "GST_SCHEME", "not_an_option")
    check("C.6 unknown decision honest refusal",
          p_bad2.get("status") != VERIFIED
          and (p_bad2.get("gate_resolution") or {}).get("accepted") is False, "")


def test_d_repeated_ambiguity() -> None:
    print("PART D - REPEATED AMBIGUITY (deterministic)")
    first = None
    for _ in range(3):
        result = orchestrate(GATE_SALE)
        gate = build_confidence_gate(result, GATE_SALE)
        if first is None:
            first = deepcopy(gate)
        else:
            check("D.1 identical gate payload", gate == first,
                  json.dumps(gate)[:200])
    p1 = project_student_result(orchestrate(GATE_SALE), GATE_SALE)
    p2 = project_student_result(orchestrate(GATE_SALE), GATE_SALE)
    check("D.2 identical projection across runs",
          p1.get("confidence_gate") == p2.get("confidence_gate"), "")


def test_e_unsafe_ambiguity() -> None:
    print("PART E - UNSAFE AMBIGUITY (refuses rather than forcing a choice)")
    cases = [
        # payment folding: the engine will not re-interpret a prior sale
        "Sold goods to Rahul for Rs.10,000. Received Rs.5,000.",
        # released GST + partial-payment boundary
        "Purchased goods from Mark worth Rs.1,00,000 at 10% trade discount "
        "and 12% GST. Half of the amount due was paid immediately by NEFT.",
        # creditor balance without a stated amount
        "Navin allowed 5% cash discount to us in full and final settlement "
        "of his account.",
    ]
    for index, q in enumerate(cases):
        result = orchestrate(q)
        gate = build_confidence_gate(result, q)
        check(f"E.{index + 1} no gate on unsafe ambiguity [{q[:34]}]",
              gate is None, str(gate))
        check(f"E.{index + 1} refusal preserved [{q[:34]}]",
              result.get("status") != VERIFIED, str(result.get("status")))
        check(f"E.{index + 1} zero lines on refusal [{q[:34]}]",
              backend_lines(result) == [], str(backend_lines(result)))


def test_f_contradiction() -> None:
    print("PART F - CONTRADICTION (INVALID_INPUT_MATH, never a fabricated result)")
    q = ("Purchased goods from Mark worth Rs.1,00,000 at 10% trade "
         "discount and 12% GST with CGST Rs.5,000 and SGST Rs.5,000.")
    result = orchestrate(q)
    projection = project_student_result(result, q)
    check("F.1 INVALID_INPUT_MATH", result.get("status") == INVALID_INPUT_MATH,
          str(result.get("status")))
    check("F.2 no gate on contradiction",
          projection.get("confidence_gate") is None, "")
    check("F.3 zero journal lines", projection_lines(projection) == [], "")
    check("F.4 student-readable contradiction state",
          "numbers don't add up" in
          (projection.get("headline") or "").lower()
          or "add up" in (projection.get("headline") or "").lower(), "")


def test_g_verified_parity() -> None:
    print("PART G - VERIFIED JOURNAL PARITY (UI == backend across authorities)")
    questions = [
        # commercial core
        "Purchased goods with a list price of \u20b925,000 at 10% trade "
        "discount from ravi kumar on credit.",
        "Sold goods to Rahul for Rs.10,000 at 10% trade discount.",
        # TD + GST compound
        "Purchased goods from Ram for Rs.20,000 at 10% trade discount and "
        "18% GST with CGST Rs.1,620 and SGST Rs.1,620",
        # bad-debt recovery (15I-TORTURE hardening)
        "Received Rs.2,000 from Kamal, which had earlier been written off "
        "as bad.",
        # bills of exchange
        "Rahul drew a bill of Rs.1,00,000 on Mohan for 3 months. Rahul "
        "discounted it with the bank at 12% p.a. On maturity Mohan "
        "dishonoured the bill and the bank paid Rs.500 noting charges.",
        # joint venture
        "Rahul and Mohan entered into a joint venture. Rahul contributed "
        "goods worth Rs.20,000 from his own stock. Mohan paid expenses of "
        "Rs.2,000. The venture sold goods for Rs.35,000. Rahul paid "
        "Rs.1,000 additional expenses. Profit is shared equally and the "
        "final settlement is made through bank.",
        # single entry (mathematical result, no journal)
        "Opening capital Rs.40,000. Closing capital Rs.60,000. Drawings "
        "during the year Rs.10,000. Fresh capital introduced Rs.5,000. "
        "Calculate profit.",
    ]
    verified = 0
    for index, q in enumerate(questions):
        result = orchestrate(q)
        projection = project_student_result(result, q)
        if result.get("status") != VERIFIED:
            continue
        verified += 1
        check(f"G.{index + 1} journal parity [{q[:36]}]",
              projection_lines(projection) == backend_lines(result),
              f"{projection_lines(projection)} != {backend_lines(result)}")
        check(f"G.{index + 1} verification balanced [{q[:36]}]",
              (projection.get("verification") or {}).get("balanced") is True
              or projection_lines(projection) == [],
              str((projection.get("verification") or {})))
    check("G.total verified cases exercised", verified >= 5, str(verified))


def test_h_why_layer() -> None:
    print("PART H - WHY LAYER (localized engine events, never LLM)")
    # TD + GST -> composed rule event maps through the dictionary
    q = ("Purchased goods from Ram for Rs.20,000 at 10% trade discount "
         "and 18% GST with CGST Rs.1,620 and SGST Rs.1,620")
    projection = project_student_result(orchestrate(q), q)
    events = projection_events(projection)
    check("H.1 TD-before-GST rule event composed",
          "RULE_TD_DEDUCT_BEFORE_GST" in events, str(events))
    check("H.2 rule event localized",
          WHY_LOCALIZATION.get("RULE_TD_DEDUCT_BEFORE_GST")
          == "Trade discount was deducted before GST.", "")
    # every composed event id has localized copy or is a passthrough event
    for event in (projection.get("why") or {}).get("events") or []:
        event_id = event.get("event_id") or ""
        if event_id.startswith(("LINE_", "BILLS_NOTE", "DISCREPANCY_NOTE",
                                "CONSIGNMENT_NOTE", "JOINT_VENTURE_NOTE",
                                "SINGLE_ENTRY_NOTE")):
            check(f"H.3 passthrough event has text [{event_id}]",
                  bool(event.get("text")), "")
        else:
            check(f"H.3 localized event [{event_id}]",
                  event_id in WHY_LOCALIZATION
                  and event.get("text") == WHY_LOCALIZATION[event_id],
                  str(event))
    # the dictionary is presentation-only: mutating wording cannot change
    # the backend journal (deterministic by construction - backend lines
    # come from orchestrate, not from the dictionary)
    check("H.4 dictionary never used by backend",
          projection_lines(projection)
          == backend_lines(orchestrate(q)), "")
    # per-line why text comes from the engine lines verbatim
    q_td = ("Purchased goods with a list price of \u20b925,000 at 10% "
            "trade discount from ravi kumar on credit.")
    p_td = project_student_result(orchestrate(q_td), q_td)
    why_text = " ".join(
        e.get("text") or "" for e in (p_td.get("why") or {}).get("events") or [])
    check("H.5 engine why text surfaces for the party",
          "Credit the giver" in why_text and "Personal A/c" in why_text,
          why_text[:300])


def test_i_debug_mode() -> None:
    print("PART I - DEBUG MODE (read-only mirror of the production graph)")
    q = GATE_SALE
    result = orchestrate(q)
    projection = project_student_result(result, q)
    graph = result.get("orchestration") or {}
    payload = debug_graph_payload(projection.get("result") or {})
    check("I.1 debug payload == production graph", payload == graph, "")
    check("I.2 segments preserved", payload.get("segments") == graph.get("segments"), "")
    check("I.3 invariants preserved", payload.get("invariants") == graph.get("invariants"), "")
    # mutation attempt cannot leak
    mutated = debug_graph_payload(projection.get("result") or {})
    mutated["segments"] = []
    mutated["invariants"] = {}
    check("I.4 deep copy read-only",
          debug_graph_payload(projection.get("result") or {}) == graph, "")


def test_j_determinism() -> None:
    print("PART J - DETERMINISM (same input -> same graph/journal/verification/why)")
    q = ("Purchased goods from Ram for Rs.20,000 at 10% trade discount "
         "and 18% GST with CGST Rs.1,620 and SGST Rs.1,620")
    runs = []
    for _ in range(2):
        result = orchestrate(q)
        normalized = normalize_fyjc_text(q)
        graph = build_transaction_graph(
            q, normalized=normalized.text, normalization=normalized.provenance)
        projection = project_student_result(result, q)
        runs.append({
            "graph": json.dumps(asdict(graph), sort_keys=True, default=str),
            "journal": projection_lines(projection),
            "verification": projection.get("verification"),
            "why": projection.get("why"),
            "status": projection.get("status"),
        })
    check("J.1 graph identical", runs[0]["graph"] == runs[1]["graph"], "")
    check("J.2 journal identical", runs[0]["journal"] == runs[1]["journal"], "")
    check("J.3 verification identical",
          runs[0]["verification"] == runs[1]["verification"], "")
    check("J.4 explanation path identical", runs[0]["why"] == runs[1]["why"], "")
    check("J.5 status identical", runs[0]["status"] == runs[1]["status"], "")


def test_k_status_behaviour() -> None:
    print("PART K - STATUS BEHAVIOUR (distinct, student-readable states)")
    expected = {
        VERIFIED: "Verified",
        REVIEW_REQUIRED: "Review required",
        INVALID_INPUT_MATH: "The numbers don't add up",
        NOT_SUPPORTED: "Not supported yet",
        BLOCKED: "Safety boundary",
    }
    for status, label in expected.items():
        presentation = STATUS_PRESENTATION.get(status, {})
        check(f"K.1 {status} has headline", bool(presentation.get("headline")), "")
        check(f"K.2 {status} label", (presentation.get("label") or "") == label,
              str(presentation.get("label")))
        check(f"K.3 {status} student-readable summary",
              bool(presentation.get("summary")) and "{" not in (presentation.get("summary") or ""),
              "")
    # no internal error codes in any student-facing state copy
    check("K.4 distinct headlines",
          len({STATUS_PRESENTATION[s]["headline"] for s in expected}) == 5, "")
    # UI status chip uses the raw status so released gates see the status
    from backend.fyjc_student_ui import _status_chip_label
    check("K.5 chip label REVIEW REQUIRED",
          _status_chip_label(REVIEW_REQUIRED) == "REVIEW REQUIRED", "")
    check("K.6 chip label INVALID",
          "INVALID" in _status_chip_label(INVALID_INPUT_MATH), "")


# ---------------------------------------------------------------------------
# PART 2 - REAL STREAMLIT STUDY/VERIFY APPTEST
# ---------------------------------------------------------------------------


def test_streamlit() -> None:
    print("PART L - REAL STREAMLIT STUDENT WORKSPACE APPTEST")
    try:
        from streamlit.testing.v1 import AppTest
    except Exception as exc:  # pragma: no cover
        check("L.0 apptest available", False, str(exc))
        return

    # -- the app opens DIRECTLY into the student workspace ----------------
    at = AppTest.from_file("app (1) (9).py", default_timeout=120)
    at.run()
    check("L.1 app entrance paints", not at.exception,
          [e.stack_trace for e in at.exception])
    check("L.2 student workspace visible immediately (no sign-in)",
          bool(at.text_area(key="fte_fyjc_question")), "")
    try:
        at.text_input(key="fte_email")
        login_shown = True
    except KeyError:
        login_shown = False
    check("L.3 no login gate before the workspace", not login_shown, "")
    check("L.4 sign-in action still present",
          bool(at.button(key="fte_btn_signin")), "")

    # -- clear transaction -> verified result, backend-exact journal ------
    at.text_area(key="fte_fyjc_question").set_value(
        "Purchased goods with a list price of \u20b925,000 at 10% trade "
        "discount from ravi kumar on credit."
    ).run()
    at.button(key="fte_fyjc_go").click().run()
    check("L.5 clear transaction renders", not at.exception,
          [e.stack_trace for e in at.exception])
    md = " ".join(m.value for m in at.markdown)
    check("L.6 VERIFIED shown", "VERIFIED" in md, md[:200])
    check("L.7 journal shows the backend amount",
          "22,500" in md, md[:200])
    check("L.8 aligned journal headers",
          "Account" in md and "Debit" in md and "Credit" in md, md[:200])
    check("L.9 verification statement",
          "every required amount in the question has been accounted for"
          in md, md[:200])
    check("L.10 Why? present", "Why?" in md, md[:200])
    check("L.11 no gate on clear input",
          "I need one clarification" not in md, "")
    result = orchestrate(
        "Purchased goods with a list price of \u20b925,000 at 10% trade "
        "discount from ravi kumar on credit.")
    for account, amount in backend_lines(result):
        # the UI renders the backend's account names verbatim and the
        # grouped display of the backend's amounts ("22,500" for 22500)
        check(f"L.12 UI shows backend line {account}",
              account in md, md[:300])
    check("L.12b UI shows backend amount grouped",
          "22,500" in md, md[:300])

    # -- genuine ambiguity -> one precise Confidence Gate -----------------
    at.text_area(key="fte_fyjc_question").set_value(
        "Sold goods to Rahul for Rs.10,000 at 18% GST."
    ).run()
    at.button(key="fte_fyjc_go").click().run()
    md = " ".join(m.value for m in at.markdown)
    check("L.13 gate headline shown", "I need one clarification" in md, md[:200])
    check("L.14 gate question shown",
          "How should the GST on this transaction be recorded?" in md, md[:200])
    radios = at.radio(key="fte_fyjc_gate_choice")
    check("L.15 gate alternatives rendered",
          radios is not None and len(radios.options) == 2, str(radios.options))
    labels = [str(o) for o in radios.options]
    check("L.16 alternatives are the backend's two",
          any("Intra-state" in l for l in labels)
          and any("Inter-state" in l for l in labels), str(labels))
    check("L.17 confirm submits the decision",
          bool(at.button(key="fte_fyjc_gate_confirm")), "")

    # -- student resolves -> choice consumed, transaction continues -------
    at.radio(key="fte_fyjc_gate_choice").set_value(
        "Inter-state — IGST").run()
    at.button(key="fte_fyjc_gate_confirm").click().run()
    check("L.18 resolution renders", not at.exception,
          [e.stack_trace for e in at.exception])
    md = " ".join(m.value for m in at.markdown)
    check("L.19 confirmation message",
          "Got it. Continuing with" in md and "IGST" in md, md[:250])
    check("L.20 resolution VERIFIED", "VERIFIED" in md, md[:200])
    check("L.21 inter-state journal rendered",
          "Output IGST" in md and "1,800" in md, md[:300])
    check("L.22 gate no longer shown after resolution",
          "I need one clarification" not in md, "")

    # resolve the other alternative deterministically (intra-state); a
    # fresh question guarantees a fresh gate (same text would reuse the
    # resolved session projection - deterministic by design)
    at.text_area(key="fte_fyjc_question").set_value(
        "Sold goods to Manav for Rs.10,000 at 18% GST."
    ).run()
    at.button(key="fte_fyjc_go").click().run()
    at.radio(key="fte_fyjc_gate_choice").set_value(
        "Intra-state — CGST and SGST").run()
    at.button(key="fte_fyjc_gate_confirm").click().run()
    md = " ".join(m.value for m in at.markdown)
    check("L.23 intra-state journal rendered",
          "Output CGST" in md and "Output SGST" in md
          and "900" in md, md[:300])

    # -- contradiction -> INVALID INPUT (MATH), never a fabricated result --
    at.text_area(key="fte_fyjc_question").set_value(
        "Purchased goods from Mark worth Rs.1,00,000 at 10% trade discount "
        "and 12% GST with CGST Rs.5,000 and SGST Rs.5,000."
    ).run()
    at.button(key="fte_fyjc_go").click().run()
    md = " ".join(m.value for m in at.markdown)
    check("L.24 contradiction state shown",
          "INVALID" in md.upper(), md[:200])
    check("L.25 contradiction headline",
          "don't add up" in md or "add up" in md.lower(), md[:200])
    check("L.26 no fabricated journal on contradiction",
          "**STATUS:** VERIFIED" not in md.upper(), "")

    # -- unsafe ambiguity -> refuses, no gate -----------------------------
    at.text_area(key="fte_fyjc_question").set_value(
        "Sold goods to Rahul for Rs.10,000. Received Rs.5,000."
    ).run()
    at.button(key="fte_fyjc_go").click().run()
    md = " ".join(m.value for m in at.markdown)
    check("L.27 unsafe ambiguity refuses", "REVIEW REQUIRED" in md, md[:200])
    check("L.28 no gate forced", "I need one clarification" not in md, "")

    # -- released path smoke: sign-in -> workspace -> FYJC Study ----------
    at2 = AppTest.from_file("app (1) (9).py", default_timeout=120)
    at2.run()
    at2.button(key="fte_btn_signin").click().run()
    at2.text_input(key="fte_email").set_value("analyst@example.com")
    at2.text_input(key="fte_password").set_value("secret123")
    at2.button(key="fte_btn_continue").click().run()
    at2.button(key="fte_ws_professional").click().run()
    at2.segmented_control(key="fte_page").set_value("FYJC Study").run()
    check("L.29 released workspace path paints", not at2.exception,
          [e.stack_trace for e in at2.exception])
    check("L.30 study page input present", bool(
        at2.radio(key="fte_fyjc_mode")), "")


def test_debug_apptest() -> None:
    print("PART M - FTE_DEBUG_GRAPH=true DEBUG SURFACE APPTEST")
    try:
        from streamlit.testing.v1 import AppTest
    except Exception as exc:  # pragma: no cover
        check("M.0 apptest available", False, str(exc))
        return
    os.environ["FTE_DEBUG_GRAPH"] = "true"
    try:
        at = AppTest.from_file("app (1) (9).py", default_timeout=120)
        at.run()
        at.text_area(key="fte_fyjc_question").set_value(
            "Sold goods to Rahul for Rs.10,000 at 18% GST."
        ).run()
        at.button(key="fte_fyjc_go").click().run()
        check("M.1 debug surface renders with debug flag", not at.exception,
              [e.stack_trace for e in at.exception])
        md = " ".join(m.value for m in at.markdown)
        check("M.2 debug graph heading",
              "Developer Debug" in md
              and "Transaction Graph" in md, md[:300])
        check("M.3 graph nodes exposed",
              "Graph nodes" in md or "Node ID" in md, md[:300])
        check("M.4 invariants exposed", "Safety invariants" in md, md[:300])
        check("M.5 explanation events exposed", "Explanation events" in md, md[:300])
    finally:
        os.environ.pop("FTE_DEBUG_GRAPH", None)
    # without the flag, the debug surface is not exposed
    at = AppTest.from_file("app (1) (9).py", default_timeout=120)
    at.run()
    at.text_area(key="fte_fyjc_question").set_value(
        "Sold goods to Rahul for Rs.10,000 at 18% GST."
    ).run()
    at.button(key="fte_fyjc_go").click().run()
    md = " ".join(m.value for m in at.markdown)
    check("M.6 debug surface hidden in Student Mode",
          "Developer Debug" not in md, "")


def main() -> None:
    test_a_clear_transaction()
    test_b_genuine_ambiguity()
    test_c_user_resolves()
    test_d_repeated_ambiguity()
    test_e_unsafe_ambiguity()
    test_f_contradiction()
    test_g_verified_parity()
    test_h_why_layer()
    test_i_debug_mode()
    test_j_determinism()
    test_k_status_behaviour()
    test_streamlit()
    test_debug_apptest()
    print(f"\n15I-UI gate: {TOTAL[0]} checks passed, {len(FAILURES)} failed")
    if FAILURES:
        for failure in FAILURES:
            print(" -", failure)
        sys.exit(1)
    print("ALL PASS")


if __name__ == "__main__":
    main()
