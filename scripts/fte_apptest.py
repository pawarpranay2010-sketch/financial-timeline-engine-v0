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
    # --- FT-E entrance (first paint, no session state) ---
    at = AppTest.from_file(APP, default_timeout=120)
    at.run()
    if not check_exceptions(at, "entrance page"):
        return 1
    print("ENTRANCE OK — buttons:", [b.label for b in at.button])

    # --- Sign-in path: entrance -> sign in -> workspace selection ---
    at.button(key="fte_btn_signin").click().run()
    if not check_exceptions(at, "sign-in page"):
        return 1
    at.text_input(key="fte_email").set_value("analyst@example.com")
    at.text_input(key="fte_password").set_value("secret123")
    at.button(key="fte_btn_continue").click().run()
    if not check_exceptions(at, "sign-in submit -> workspace selection"):
        return 1
    print("SIGN-IN + WORKSPACE SELECTION OK")

    # --- Workspace shell (Financial Grid page is the default page) ---
    at.button(key="fte_ws_professional").click().run()
    if not check_exceptions(at, "workspace shell (Financial Grid)"):
        return 1
    print("WORKSPACE SHELL OK — pages:", [s.label for s in at.segmented_control])

    # --- Intelligence page (Ask Co-Pilot / Selected Metric Analysis / Memo) ---
    at.segmented_control(key="fte_page").set_value("Intelligence").run()
    if not check_exceptions(at, "Intelligence page"):
        return 1
    print("INTELLIGENCE PAGE OK — chat inputs:", len(at.chat_input))

    # --- System page (provider / engine health) ---
    at.segmented_control(key="fte_page").set_value("System").run()
    if not check_exceptions(at, "System page"):
        return 1
    print("SYSTEM PAGE OK — dataframes:", len(at.dataframe))

    # --- Back to Financial Grid: selected-metric state preserved ---
    at.segmented_control(key="fte_page").set_value("Financial Grid").run()
    if not check_exceptions(at, "back to Financial Grid"):
        return 1
    print("GRID RETURN OK")

    # --- Classic Dashboard remains reachable via the sidebar switch ---
    at.sidebar.radio(key="fte_view_radio").set_value("Classic Dashboard").run()
    if not check_exceptions(at, "Classic Dashboard view"):
        return 1
    print("classic dashboard buttons:", [b.label for b in at.button])
    print("classic dashboard subheaders:", len(at.subheader))
    print("CLASSIC DASHBOARD VIEW OK")

    # --- Create-account path: entrance -> create account -> selection ---
    at2 = AppTest.from_file(APP, default_timeout=120)
    at2.run()
    at2.button(key="fte_btn_create").click().run()
    if not check_exceptions(at2, "create-account page"):
        return 1
    at2.text_input(key="fte_name").set_value("Pranay Pawar")
    at2.text_input(key="fte_email_signup").set_value("pranay@example.com")
    at2.text_input(key="fte_password_signup").set_value("secret123")
    at2.button(key="fte_btn_signup_submit").click().run()
    if not check_exceptions(at2, "create-account submit -> workspace selection"):
        return 1
    at2.button(key="fte_ws_ca").click().run()
    if not check_exceptions(at2, "workspace via create account"):
        return 1
    print("CREATE ACCOUNT PATH OK")

    print("ALL CHECKS COMPLETE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
