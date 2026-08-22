#!/usr/bin/env python3
"""
Platrixa
Sprint 14 - FYJC Student UI smoke test (Streamlit AppTest)
scripts/fte_fyjc_student_ui_apptest.py

Renders the real Streamlit app through AppTest and walks the FYJC
Study / Verify page end to end: entrance -> sign in -> workspace ->
FYJC Study -> typed Maths question -> verify-yourself -> reset ->
typed Book-Keeping question -> Correct / Edit -> re-analyse.

The point is UI integration: the Sprint 14 acceptance gate
(fte_fyjc_student_ui_test.py) proves the deterministic journey logic;
this proves it RENDERS without exceptions inside the real app shell.

Exit code 0 = all UI paths render cleanly.
"""

import os
import sys

sys.path.insert(0, os.getcwd())

from streamlit.testing.v1 import AppTest  # noqa: E402

APP = "app (1) (9).py"

FAILURES = []


def check(name, ok, detail=""):
    if not ok:
        FAILURES.append(f"{name}: {detail}")
        print(f"FAIL [{name}] {detail}")
    else:
        print(f"OK [{name}]")


def main():
    at = AppTest.from_file(APP, default_timeout=120)
    at.run()
    check("entrance", not at.exception,
          [e.stack_trace for e in at.exception])
    at.button(key="fte_btn_signin").click().run()
    at.text_input(key="fte_email").set_value("analyst@example.com")
    at.text_input(key="fte_password").set_value("secret123")
    at.button(key="fte_btn_continue").click().run()
    at.button(key="fte_ws_professional").click().run()
    check("workspace shell", not at.exception,
          [e.stack_trace for e in at.exception])

    # --- FYJC Study page: first paint --------------------------------
    at.segmented_control(key="fte_page").set_value("FYJC Study").run()
    check("FYJC Study page paints", not at.exception,
          [e.stack_trace for e in at.exception])
    labels = [b.label for b in at.button]
    check("FYJC entry controls present",
          any("Analyse" in l for l in labels), str(labels))

    # --- Maths flow ----------------------------------------------------
    at.radio(key="fte_fyjc_mode").set_value("✍️ Enter Question").run()
    at.text_area(key="fte_fyjc_question").set_value(
        "Calculate the Current Ratio.\nCurrent Assets: Rs.5,00,000\n"
        "Current Liabilities: Rs.2,50,000"
    ).run()
    at.button(key="fte_fyjc_go").click().run()
    check("maths flow renders", not at.exception,
          [e.stack_trace for e in at.exception])

    # --- independent verification --------------------------------------
    at.text_input(key="fte_fyjc_verify_answer").set_value("2")
    at.button(key="fte_fyjc_verify_btn").click().run()
    check("verify-yourself renders", not at.exception,
          [e.stack_trace for e in at.exception])

    # --- accounting flow + correction ----------------------------------
    at.button(key="fte_fyjc_reset").click().run()
    at.radio(key="fte_fyjc_mode").set_value("✍️ Enter Question").run()
    at.text_area(key="fte_fyjc_question").set_value(
        "Purchased goods from Rahul on credit for Rs.10,000."
    ).run()
    at.button(key="fte_fyjc_go").click().run()
    check("accounting flow renders", not at.exception,
          [e.stack_trace for e in at.exception])
    at.button(key="fte_fyjc_edit_btn").click().run()
    at.text_area(key="fte_fyjc_question_edit").set_value(
        "Purchased goods for cash Rs.10,000."
    ).run()
    at.button(key="fte_fyjc_reanalyse").click().run()
    check("correct/edit path renders", not at.exception,
          [e.stack_trace for e in at.exception])

    if FAILURES:
        print("=" * 72)
        print("FYJC UI SMOKE TEST FAIL")
        return 1
    print("=" * 72)
    print("FYJC UI SMOKE TEST: ALL CHECKS COMPLETE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
