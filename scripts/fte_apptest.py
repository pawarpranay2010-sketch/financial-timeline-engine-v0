import os
import sys

sys.path.insert(0, os.getcwd())

from streamlit.testing.v1 import AppTest

APP = "app (1) (9).py"


def check_exceptions(at, label):
    if at.exception:
        print(f"FAIL [{label}]:")
        for e in at.exception:
            print(e.stack_trace if hasattr(e, "stack_trace") else e)
        return False
    return True


def main():
    # --- Terminal view (default, index 0) ---
    at = AppTest.from_file(APP, default_timeout=120)
    at.run()
    if not check_exceptions(at, "terminal login gate"):
        return 1
    inputs = at.text_input
    if len(inputs) >= 2:
        inputs[0].set_value("admin")
        inputs[1].set_value("financial_terminal_2026")
        at.button[0].click().run()
        if not check_exceptions(at, "terminal login submit"):
            return 1
    print("tabs (terminal view):", [t.label for t in at.tabs])
    print("dataframes (terminal):", len(at.dataframe))
    print("TERMINAL VIEW OK")

    # --- Classic Dashboard view: seed radio BEFORE the first run so the
    # widget id is registered on the run that renders the dashboard ---
    at2 = AppTest.from_file(APP, default_timeout=120)
    at2.session_state["fte_view_radio"] = "Classic Dashboard"
    at2.run()
    if not check_exceptions(at2, "classic login gate (radio pre-seeded)"):
        return 1
    inputs2 = at2.text_input
    if len(inputs2) >= 2:
        inputs2[0].set_value("admin")
        inputs2[1].set_value("financial_terminal_2026")
        at2.button[0].click().run()
        if not check_exceptions(at2, "classic dashboard view"):
            return 1
        print("classic dashboard buttons:", [b.label for b in at2.button])
        print("classic dashboard subheaders:", len(at2.subheader))
        print("CLASSIC DASHBOARD VIEW OK")

    print("ALL CHECKS COMPLETE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
