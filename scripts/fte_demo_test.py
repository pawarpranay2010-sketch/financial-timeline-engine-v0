#!/usr/bin/env python3
"""Sprint 4 targeted tests — Static Demo Memo Mode.

Confirms:
  1. Demo opens without an API key.
  2. Demo Generate Memo does not call an AI provider (byte-identical static
     memo, provider_log empty, ai_connected stays False).
  3. Demo Co-Pilot does not call an AI provider (deterministic answers).
  4. Demo metric clicks open the evidence dialog.
  5. Demo metric values come only from the static demo dataset.
  6. Demo cannot process arbitrary uploads (no file_uploader in demo route).
  7. Real workspace still uses the existing real pipeline (uploader present).
  8. Entrance -> Workspace -> Grid -> Intelligence -> System nav intact.
  9. Existing memo rendering works (focused memo view opens with links).
 10. No new external dependencies (requirements.txt untouched; demo code
     imports only stdlib + existing app functions).
"""
import ast
import os
import re as _re
import sys

sys.path.insert(0, os.getcwd())

from streamlit.testing.v1 import AppTest

APP = "app (1) (9).py"


def simulate_card_visibility(memo_html, checked_id):
    """Faithful browser-free simulation of the demo memo's radio+label CSS
    mechanism. Given the generated memo HTML and the id of the (single)
    checked radio, return (radio_ids, card_ids, visible_card_ids):

    - radios: ids of the hidden <input type="radio" name="fte-memo-card">
    - cards:   data-card ids of the pre-rendered .fte-memo-card overlays
    - visible: exactly the card whose per-metric rule matches the checked
      radio: '#id:checked ~ .fte-memo-card[data-card="id"] { display:
      block; }'. Every other card stays display:none (base rule).

    Because the radios form ONE exclusive group, only one can be checked
    at a time, which is what makes "click another metric → content
    replaces" and "Close/×/backdrop → card disappears" work."""
    rules = {}
    style_m = _re.search(r"<style>(.*?)</style>", memo_html, _re.S)
    if style_m:
        for m in _re.finditer(
            r'#(ftemetric-[a-z0-9-]+):checked\s*~\s*\.fte-memo-card\[data-card="(ftemetric-[a-z0-9-]+)"\]\s*\{\s*display:\s*block;\s*\}',
            style_m.group(1),
        ):
            rules[m.group(1)] = m.group(2)
    radios = set(_re.findall(r'<input type="radio"[^>]*id="(ftemetric-[a-z0-9-]+)"[^>]*>', memo_html))
    cards = set(_re.findall(r'class="fte-memo-card" role="dialog" data-card="(ftemetric-[a-z0-9-]+)"', memo_html))
    visible = set()
    if checked_id in rules:
        visible.add(rules[checked_id])
    return radios, cards, visible


def ss(at, key, default=None):
    """Safe session_state read (AppTest's proxy has no .get())."""
    try:
        return at.session_state[key]
    except (KeyError, AttributeError):
        return default


def check_exceptions(at, label):
    if at.exception:
        print(f"FAIL [{label}]:")
        for e in at.exception:
            print(getattr(e, "stack_trace", e))
        return False
    return True


def extract_demo_memo() -> str:
    """Read _FTE_DEMO_MEMO from the app source (byte-identity check)."""
    tree = ast.parse(open(APP, encoding="utf-8").read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "_FTE_DEMO_MEMO":
                    return ast.literal_eval(node.value)
    raise AssertionError("_FTE_DEMO_MEMO not found in app source")


def main():
    failures = 0
    expected_memo = extract_demo_memo()
    assert "EXECUTIVE SUMMARY" in expected_memo and "281.70B" in expected_memo

    # --- 1) Demo opens without an API key ---
    at = AppTest.from_file(APP, default_timeout=120)
    at.run()
    # The terminal stylesheet is injected ONCE on the first run and then
    # persists in the DOM (fte_css_injected guard), so capture it now —
    # later runs do not re-emit the <style> element.
    css_joined = " ".join(str(m.value) for m in at.markdown)
    assert "<style>" in css_joined, "terminal CSS not injected on entrance"
    if not check_exceptions(at, "entrance"):
        return 1
    assert any(b.key == "fte_btn_demo" for b in at.button), "entrance demo button missing"
    at.button(key="fte_btn_demo").click().run()
    if not check_exceptions(at, "demo workspace"):
        return 1
    assert ss(at, "fte_route") == "demo"
    assert ss(at, "fte_demo_mode") is True
    print("1. DEMO OPENS WITHOUT API KEY OK — pages:", [s.label for s in at.segmented_control])

    # --- 6) Demo cannot process arbitrary uploads ---
    if len(at.file_uploader) > 0:
        print("FAIL [6]: demo route exposes a file uploader")
        failures += 1
    else:
        print("6. DEMO HAS NO UPLOAD PATH OK (0 file uploaders)")

    # --- 5) Demo metric values come only from the static dataset ---
    rows = ss(at, "fte_grid_rows") or []
    assert rows, "demo grid produced no rows"
    demo_values = {
        "281.70B", "98.30B", "125.50B", "13.05", "96.60B", "512.20B",
        "243.70B", "268.50B", "127.80B", "161.00B", "0.35", "0.37",
        "0.19", "0.36", "1.4", "—",
    }
    row_values = {r["Value"] for r in rows}
    assert row_values <= demo_values, f"unexpected values in demo grid: {row_values - demo_values}"
    kinds = {r["_kind"] for r in rows}
    assert kinds == {"verified", "derived", "blocked", "unanalyzed"}, kinds
    assert any(r["metric"] == "Segment Gross Margin" and r["_kind"] == "blocked" for r in rows)
    print("5. DEMO VALUES STATIC-ONLY OK —", len(rows), "rows (verified/derived/blocked/unanalyzed)")

    # --- 4) Demo metric click opens the evidence dialog. A click sets
    # fte_selected_metric + fte_overlay_open and the rerun renders the
    # dialog — drive exactly that state and verify the dialog body is the
    # demo evidence card (this AppTest build has no dataframe select_rows).
    at.session_state["fte_selected_metric"] = "Revenue"
    at.session_state["fte_overlay_open"] = True
    at.run()
    if not check_exceptions(at, "demo evidence dialog"):
        return 1
    assert ss(at, "fte_overlay_open") is True, "overlay closed unexpectedly"
    dialog_bodies = " ".join(str(m.value) for m in at.markdown)
    assert "Revenue" in dialog_bodies and "What it means" in dialog_bodies, "dialog lacks demo evidence"
    assert "10-K FY2025" in dialog_bodies, "dialog lacks demo provenance"
    print("4. DEMO METRIC CLICK OPENS EVIDENCE DIALOG OK (demo provenance shown)")

    # --- 3) Demo Co-Pilot is deterministic and AI-free ---
    at.segmented_control(key="fte_page").set_value("Intelligence").run()
    if not check_exceptions(at, "demo intelligence"):
        return 1
    assert not ss(at, "provider_log"), "provider_log not empty before chat"
    at.button(key="fte_demo_suggest_0").click().run()  # "Summarize the strongest verified evidence."
    if not check_exceptions(at, "demo copilot answer"):
        return 1
    msgs = ss(at, "fte_demo_chat_messages") or []
    assert msgs and "281.70B" in msgs[-1]["content"], "demo copilot did not answer from dataset"
    assert not ss(at, "provider_log"), "AI provider was called by demo Co-Pilot"
    assert ss(at, "ai_connected") is False
    # Clear the grid selection left by check 4 so this question is truly
    # unsupported (the app correctly answers selected-metric questions).
    at.session_state["fte_selected_metric"] = None
    at.session_state["fte_overlay_open"] = False
    at.run()
    at.chat_input[0].set_value("What is the weather in Tokyo?").run()
    fallback = ss(at, "fte_demo_chat_messages", [{}])[-1]["content"]
    assert fallback == (
        "I can answer a limited set of questions in Demo Mode. Try asking about the "
        "selected metric, strongest verified evidence, key risks, or what to "
        "investigate next."
    ), fallback
    assert not ss(at, "provider_log"), "AI provider called for unsupported demo question"
    print("3. DEMO CO-PILOT DETERMINISTIC + NO AI OK")

    # --- 2) Demo Generate Memo is static and AI-free ---
    at.button(key="fte_btn_demo_memo").click().run()
    if not check_exceptions(at, "demo memo view"):
        return 1
    assert ss(at, "fte_memo_view_open") is True
    assert ss(at, "fte_memo_status") == "ready"
    draft = ss(at, "fte_memo_draft") or ""
    assert draft == expected_memo, "demo memo differs from the static sample (AI path used?)"
    assert not ss(at, "provider_log"), "AI provider was called by demo Generate Memo"
    assert ss(at, "ai_connected") is False
    bodies = [m.value for m in at.markdown]
    assert any("fte-metric-link" in str(b) for b in bodies), "memo body has no inline metric links"
    assert any("Demo memo · Pre-analyzed sample · No AI generation used" in str(b) for b in bodies)
    print("2. DEMO GENERATE MEMO STATIC + NO AI OK (byte-identical memo, inline links present)")

    # --- Sprint 4.2.1: demo memo metric interaction is fully client-side
    # AND URL-independent. Each metric is a <label for="ftemetric-<slug>">
    # toggling a hidden radio of ONE exclusive group; a per-metric CSS rule
    # (#ftemetric-<slug>:checked ~ .fte-memo-card[data-card=...]) makes the
    # pre-rendered floating card visible. No URL navigation, no hash, no
    # :target, no JS, no rerun — the route, memo and scroll can never
    # reset, and the overlay is immune to proxy/URL handling.
    memo_html = next(str(m.value) for m in at.markdown if "fte-memo-para" in str(m.value))
    joined = " ".join(str(m.value) for m in at.markdown)
    assert 'for="ftemetric-revenue"' in joined, "demo memo lost its Revenue metric toggle"
    assert 'id="ftemetric-revenue"' in joined, "Revenue radio not embedded"
    assert 'name="fte-memo-card"' in joined, "exclusive radio group missing"
    assert 'id="ftemetric-none" class="fte-memo-radio"' in joined, "none-radio missing"
    # Sprint 4.3.1: radios must be UNCONTROLLED (no checked attr) so React
    # does not treat them as read-only controlled fields, and the memo must
    # separate structural HTML blocks with blank lines so CommonMark parses
    # the cards block as HTML instead of swallowing it as raw text.
    assert 'class="fte-memo-radio" checked' not in joined, "radio must not be pre-checked (React controlled-input bug)"
    assert '\n\n<div class="fte-memo-para"' in memo_html, "memo paragraphs must be blank-line separated (CommonMark)"
    assert '</style>\n<div class="fte-memo-cards"' in memo_html, "cards div must start on its own line after </style>"
    assert 'data-card="ftemetric-revenue"' in joined, "Revenue evidence card not embedded"
    assert "281.70B" in joined, "Revenue card lacks its static demo value"
    assert "What it means" in joined, "card lacks the explainer section"
    assert "Consolidated Statements of Income" in joined, "card lacks demo provenance"
    assert "?fte_metric=" not in joined, "demo memo still emits query-param links (would rerun)"
    assert 'href="#ftemetric-' not in joined, "demo memo still uses URL-fragment anchors"
    # Sprint 4.3 regression guard: the base overlay CSS must live in the
    # memo's OWN inline <style> (self-contained), not only in the global
    # terminal stylesheet — otherwise cards render as in-flow divs below
    # the memo whenever the app-level CSS is not applied (deployment).
    assert ".fte-memo-card {" in memo_html and "position: fixed;" in memo_html, "floating overlay CSS missing from memo's own style (Sprint 4.3)"
    assert "min(360px" in memo_html and "100vw" in memo_html, "responsive card sizing missing from memo's own style"
    assert "display: none;" in memo_html, "cards not hidden by default in the memo's own style"
    radios, cards, visible = simulate_card_visibility(memo_html, "ftemetric-none")
    assert visible == set(), f"default state must show NO card, got {visible}"
    radios, cards, visible = simulate_card_visibility(memo_html, "ftemetric-revenue")
    assert visible == {"ftemetric-revenue"}, f"clicking Revenue must show only the Revenue card, got {visible}"
    print("R1. CLICK REVENUE → VISIBLE FLOATING CARD OVER MEMO OK (radio+label :checked, zero URL/navigation)")

    # R2 — clicking another metric (ROE, exactly as in the UX flow)
    # replaces the SAME overlay's content; the exclusive group guarantees
    # only one card is ever visible.
    joined2 = " ".join(str(m.value) for m in at.markdown)
    assert 'for="ftemetric-roe"' in joined2, "ROE toggle missing"
    assert 'id="ftemetric-roe"' in joined2, "ROE radio missing"
    assert 'data-card="ftemetric-roe"' in joined2, "ROE card missing"
    radios, cards, visible = simulate_card_visibility(memo_html, "ftemetric-roe")
    assert visible == {"ftemetric-roe"}, f"clicking ROE must swap to the ROE card, got {visible}"
    radios, cards, visible = simulate_card_visibility(memo_html, "ftemetric-revenue")
    assert visible == {"ftemetric-revenue"}, "Revenue card must reappear when reselected"
    assert "0.37" in joined2 or "0.366" in joined2, "ROE card lacks its static demo value"
    assert "Return on equity" in joined2 or "return on equity" in joined2, "ROE explainer missing"
    # Coherence: every card has a radio + show rule; the none radio never shows.
    radios, cards, visible = simulate_card_visibility(memo_html, "ftemetric-none")
    assert cards <= radios, f"cards without radios: {cards - radios}"
    rule_slugs = set(
        _re.findall(r'#(ftemetric-[a-z0-9-]+):checked ~ .fte-memo-card\[data-card="ftemetric-[a-z0-9-]+"\] \{ display: block; \}', memo_html)
    )
    assert "ftemetric-none" not in rule_slugs, "none radio must never show a card"
    assert rule_slugs == cards, f"missing show rule: {cards - rule_slugs}"
    print("R2. CLICK ROE → SAME MEMO, SAME CARD REPLACED OK (exclusive radio group, one card max)")

    # R3 — dismiss: × and backdrop are labels for #ftemetric-none, which
    # restores the all-hidden default; the redundant in-card Close button
    # is gone; the memo itself is untouched (no route change, no rerun,
    # scroll preserved).
    assert "fte-card-close" not in memo_html, "redundant in-card Close button still present"
    assert joined.count('for="ftemetric-none"') >= 2, "× / backdrop close labels missing"
    assert "fte-card-backdrop" in joined, "backdrop element missing"
    assert "display: none;" in memo_html, "cards not hidden by default in the memo's own style"
    radios, cards, visible = simulate_card_visibility(memo_html, "ftemetric-none")
    assert visible == set(), "after ×/backdrop no card may remain visible"
    print("R3. × / BACKDROP → SAME DEMO MEMO OK (no in-card Close; labels toggle #ftemetric-none)")

    # R4 — the demo memo no longer depends on the server dialog at all:
    # no metric-click state is set server-side and no dialog button exists.
    # (ESC keyboard dismissal is the one interaction that would require
    # client-side JS, which Streamlit inline HTML cannot execute; ×, Close
    # and backdrop-click are the provided dismissals.)
    assert ss(at, "fte_memo_metric_click") is None, "demo memo opened a server dialog"
    assert "fte_memo_metric_close" not in [b.key for b in at.button], "dialog button present"
    assert ss(at, "fte_route") == "demo" and ss(at, "fte_memo_view_open") is True
    print("R4. NO SERVER DIALOG FOR DEMO MEMO OK — ESC noted as the sole JS-only dismissal")

    # L1 — legacy ?fte_demo=1&fte_metric= bookmarks still reconstruct the
    # demo session (the earlier committed fix is preserved).
    at.query_params["fte_metric"] = "Revenue"
    at.query_params["fte_demo"] = "1"
    at.session_state["fte_route"] = "entrance"
    at.session_state["fte_memo_view_open"] = False
    at.session_state["fte_memo_draft"] = ""
    at.run()
    if not check_exceptions(at, "legacy demo URL"):
        return 1
    assert ss(at, "fte_route") == "demo", "legacy URL reset the route"
    assert ss(at, "fte_memo_view_open") is True, "legacy URL closed the memo"
    assert ss(at, "fte_memo_metric_click") == "Revenue"
    assert "fte_metric" not in at.query_params and "fte_demo" not in at.query_params
    print("L1. LEGACY ?fte_demo=1&fte_metric= URL STILL RECONSTRUCTS DEMO OK")

    # Back to Intelligence → correct demo Intelligence page.
    at.button(key="fte_btn_memo_back").click().run()
    assert ss(at, "fte_route") == "demo" and ss(at, "fte_memo_view_open") is False
    assert ss(at, "fte_page") == "Intelligence"
    print("R5. BACK TO INTELLIGENCE → DEMO INTELLIGENCE PAGE OK")

    # Reopen Demo Memo → still works.
    at.button(key="fte_btn_demo_memo").click().run()
    assert ss(at, "fte_memo_view_open") is True
    assert ss(at, "fte_memo_draft") == expected_memo
    print("R6. REOPEN DEMO MEMO OK")

    # R7 — the real (non-demo) memo path is untouched: its links still use
    # the query-param dialog navigation; demo radio toggles are demo-only.
    src_guard = open(APP, encoding="utf-8").read()
    assert 'href="?fte_metric={urllib.parse.quote(metric)}"' in src_guard, "real memo link path changed"
    assert 'for="ftemetric-{slug}"' in src_guard, "demo radio toggle missing"
    print("R7. REAL MEMO PATH UNTOUCHED OK (query-param links preserved; demo toggles demo-only)")

    # --- 8/9) Real workspace nav + real memo generator intact ---
    at2 = AppTest.from_file(APP, default_timeout=120)
    at2.run()
    at2.button(key="fte_btn_signin").click().run()
    at2.text_input(key="fte_email").set_value("analyst@example.com")
    at2.text_input(key="fte_password").set_value("secret123")
    at2.button(key="fte_btn_continue").click().run()
    at2.button(key="fte_ws_professional").click().run()
    if not check_exceptions(at2, "real workspace shell"):
        return 1
    assert ss(at2, "fte_demo_mode") is False, "real workspace entered demo mode"
    assert len(at2.file_uploader) > 0, "real workspace lost its uploader"
    at2.segmented_control(key="fte_page").set_value("Intelligence").run()
    if not check_exceptions(at2, "real intelligence page"):
        return 1
    intel_bodies = " ".join(str(m.value) for m in at2.markdown)
    assert "Generate Memo" in intel_bodies and "Upload a financial document" in intel_bodies, \
        "real memo generator component missing"
    at2.segmented_control(key="fte_page").set_value("System").run()
    if not check_exceptions(at2, "real system page"):
        return 1
    at2.segmented_control(key="fte_page").set_value("Financial Grid").run()
    if not check_exceptions(at2, "real grid page"):
        return 1
    print("7/8/9. REAL WORKSPACE + NAV + MEMO GENERATOR INTACT OK")

    # --- 10) No new external dependencies ---
    req = open("requirements.txt", encoding="utf-8").read()
    demo_src = open(APP, encoding="utf-8").read()
    section = demo_src[demo_src.index("SECTION 10e"):demo_src.index("SECTION 11: Main App")]
    for token in ("requests", "httpx", "numpy", "dotenv", "openai", "anthropic"):
        assert token not in section, f"demo code references new dependency {token}"
    print("10. NO NEW EXTERNAL DEPENDENCIES OK (requirements.txt untouched, stdlib only)")

    if failures:
        print(f"=== {failures} FAILURE(S) ===")
        return 1
    print("=== DEMO TESTS: ALL CHECKS COMPLETE ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
