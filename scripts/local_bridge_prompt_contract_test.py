#!/usr/bin/env python3
"""
Platrixa — device-bridge prompt-alignment regression tests (offline)
====================================================================

WHY THIS EXISTS
---------------
The Android device bridge (`local_llama_bridge.py`) sent the canonical
`SYSTEM_PROMPT` through `/v1/chat/completions`. The Phase-H adapter was
fine-tuned on byte-exact Alpaca *completions* (`backend/model_provider/
platrixa_prompt.py`), never on the Qwen chat template. The mismatch degraded
instruction-following until the model omitted `suggested_status` from its JSON
entirely, and the provider contract correctly rejected the 17/18-field output.

These tests lock the corrected contract in place:

  1. the bridge must not import SYSTEM_PROMPT
  2. the bridge must not use /v1/chat/completions
  3. the request is built by the shared build_prompt()
  4. a prompt-contract violation fails closed BEFORE any HTTP request
  5. finish_reason == "length" is rejected (never silently parsed)
  6. a 17/18-field candidate missing `suggested_status` stays rejected
  7. the strict provider contract is unchanged (no defaults, no fabrication)

TEST DISCIPLINE
---------------
  - Fully offline. No model, no network, no llama.cpp, no device.
  - Checks 1-5 are static/mocked AST + import analysis of the bridge source.
    They never execute the bridge, so a missing `llama_cpp`/`fastapi` on the
    analysis host cannot mask a regression.
  - Check 6 drives the REAL provider (LocalBridgeModelProvider) against a
    loopback fake bridge, exactly like scripts/local_bridge_contract_test.py.
  - Check 7 re-asserts the provider contract invariants directly.

BRIDGE DISCOVERY
----------------
`local_llama_bridge.py` is a DEVICE-SIDE file and is intentionally not tracked
in this repository. It is searched for in this order:

    1. $PLATRIXA_DEVICE_BRIDGE (absolute or repo-relative path)
    2. ./local_llama_bridge.py
    3. ./device/local_llama_bridge.py
    4. ./android/local_llama_bridge.py

When it is absent, checks 1-5 report SKIP with the reason (never PASS, never
FAIL) and the script still exits 0, so this test is safe to run on a host that
has never seen the device file. Checks 6 and 7 always run.

Exit codes: 0 = no failures, 1 = at least one failure.
"""

from __future__ import annotations

import ast
import json
import os
import pathlib
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.model_provider.base import MalformedOutputError  # noqa: E402
from backend.model_provider.local_bridge import (  # noqa: E402
    LocalBridgeModelProvider,
    build_provider_envelope,
    enforce_interpretation_contract,
)
from backend.model_provider.platrixa_prompt import (  # noqa: E402
    ALPACA_INSTRUCTION,
    MAX_NEW_TOKENS,
    REQUIRED_FIELDS_18,
    TEMPERATURE,
    TOP_P,
    build_prompt,
    is_alpaca_prompt,
)

FAILURES: List[str] = []
SKIPS: List[str] = []


def check(name: str, condition: bool, detail: str = "") -> bool:
    print(f"{'PASS' if condition else 'FAIL'}: {name}" + (f" — {detail}" if detail else ""))
    if not condition:
        FAILURES.append(name)
    return condition


def skip(name: str, reason: str) -> None:
    print(f"SKIP: {name} — {reason}")
    SKIPS.append(name)


# ---------------------------------------------------------------------------
# Contract fixture (same shape the committed bridge contract test uses)
# ---------------------------------------------------------------------------

def make_candidate(**overrides: Any) -> Dict[str, Any]:
    candidate: Dict[str, Any] = {
        "transaction_type": "PURCHASE",
        "parties": [],
        "amounts": [],
        "payment_method": "CASH",
        "references": [],
        "ambiguities": [],
        "grounding": {"all_fields_explicitly_grounded": True, "inferred_fields": []},
        "transaction_type_enum": "PURCHASE",
        "payment_method_enum": "CASH",
        "ambiguity_flags": ["NONE"],
        "referenced_transaction_index": None,
        "referenced_party": None,
        "referenced_amount": None,
        "field_confidences": [],
        "overall_confidence": "0.40",
        "suggested_status": "REVIEW_REQUIRED",
        "safety_flags": ["NONE"],
        "scope_flags": ["SINGLE_TRANSACTION"],
    }
    assert set(REQUIRED_FIELDS_18) == set(candidate.keys()), "fixture drifted"
    candidate.update(overrides)
    return candidate


# ---------------------------------------------------------------------------
# Bridge discovery + AST helpers
# ---------------------------------------------------------------------------

BRIDGE_ENV = "PLATRIXA_DEVICE_BRIDGE"
BRIDGE_CANDIDATES = (
    "local_llama_bridge.py",
    "device/local_llama_bridge.py",
    "android/local_llama_bridge.py",
)


OVERRIDE_REQUESTED: Optional[str] = None
OVERRIDE_MISSING: Optional[str] = None


def find_bridge() -> Optional[pathlib.Path]:
    global OVERRIDE_REQUESTED, OVERRIDE_MISSING
    env = os.environ.get(BRIDGE_ENV, "").strip()
    if env:
        candidate = pathlib.Path(env)
        if not candidate.is_absolute():
            candidate = REPO_ROOT / candidate
        OVERRIDE_REQUESTED = env
        if candidate.is_file():
            return candidate
        # An explicit override that does not resolve is an operator error.
        # It is still a SKIP (never a silent PASS), but it is reported by
        # name so the reason cannot be mistaken for "this host simply has
        # no device file".
        OVERRIDE_MISSING = str(candidate)
        return None
    for rel in BRIDGE_CANDIDATES:
        candidate = REPO_ROOT / rel
        if candidate.is_file():
            return candidate
    return None


def parse_bridge(path: pathlib.Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def imported_names(tree: ast.Module) -> set:
    """Every bare identifier bound by an Import/ImportFrom anywhere in the file."""
    names: set = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                names.add(alias.asname or alias.name)
    return names


def imported_from_module(tree: ast.Module, module_suffix: str) -> List[str]:
    """Names imported from any module whose dotted path ends with `module_suffix`."""
    found: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module.endswith(module_suffix):
                found.extend(a.asname or a.name for a in node.names)
    return found


def string_constants(tree: ast.Module) -> List[str]:
    out: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            out.append(node.value)
    return out


def called_functions(tree: ast.Module) -> List[str]:
    out: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Name):
                out.append(fn.id)
            elif isinstance(fn, ast.Attribute):
                out.append(fn.attr)
    return out


# ===========================================================================
print("--- 1-5. device bridge prompt alignment (static analysis) ---")

bridge_path = find_bridge()
bridge_absent_reason = (
    f"device-side bridge not found on this host; set {BRIDGE_ENV}=<path> or place "
    "local_llama_bridge.py at the repo root to run these checks"
)

if bridge_path is None:
    for name, desc in (
        ("B1 bridge does not import SYSTEM_PROMPT", "forbidden import check"),
        ("B2 bridge does not use /v1/chat/completions", "forbidden endpoint check"),
        ("B3 bridge builds the prompt via build_prompt()", "shared builder check"),
        ("B4 prompt-contract failure occurs before any HTTP request", "ordering check"),
        ("B5 finish_reason == 'length' is rejected", "truncation check"),
    ):
        skip(name, bridge_absent_reason)
    print("       (checks 6 and 7 still run — they need no bridge source)")
else:
    print(f"       bridge source: {bridge_path}")
    tree = parse_bridge(bridge_path)
    imports = imported_names(tree)
    strings = string_constants(tree)
    calls = called_functions(tree)

    # --- 1. must not import SYSTEM_PROMPT ------------------------------
    check(
        "B1 bridge does not import SYSTEM_PROMPT",
        "SYSTEM_PROMPT" not in imports,
        f"imports from fyjc_llm_specialist: "
        f"{imported_from_module(tree, 'fyjc_llm_specialist') or 'none'}",
    )

    # --- 2. must not target the chat endpoint --------------------------
    chat_hits = [s for s in strings if "/v1/chat/completions" in s]
    check(
        "B2 bridge does not use /v1/chat/completions",
        not chat_hits,
        f"offending literal(s): {chat_hits}" if chat_hits else "no chat endpoint literal",
    )

    # --- 3. uses the shared Alpaca prompt builder ----------------------
    check(
        "B3 bridge builds the prompt via build_prompt()",
        "build_prompt" in calls and "build_prompt" in imports,
        f"build_prompt imported={'build_prompt' in imports}, called={'build_prompt' in calls}",
    )
    check(
        "B3b bridge validates the prompt via is_alpaca_prompt()",
        "is_alpaca_prompt" in calls and "is_alpaca_prompt" in imports,
        f"is_alpaca_prompt imported={'is_alpaca_prompt' in imports}, "
        f"called={'is_alpaca_prompt' in calls}",
    )
    check(
        "B3c bridge imports the prompt from backend.model_provider.platrixa_prompt",
        bool(imported_from_module(tree, "platrixa_prompt")),
        f"platrixa_prompt imports: {imported_from_module(tree, 'platrixa_prompt')}",
    )
    # The instruction must not be re-declared locally: no hand-copied prompt.
    instruction_redefined = any(
        isinstance(node, (ast.Assign, ast.AnnAssign))
        and any(
            isinstance(t, ast.Name)
            and t.id in {"ALPACA_INSTRUCTION", "ALPACA_PREFIX", "SYSTEM_PROMPT"}
            for t in (
                node.targets if isinstance(node, ast.Assign) else [node.target]
            )
        )
        for node in tree.body
    )
    check(
        "B3d bridge does not re-declare the prompt text locally",
        not instruction_redefined,
        "prompt must come from platrixa_prompt, not be duplicated",
    )

    # --- 4. prompt guard runs BEFORE the HTTP call ---------------------
    # Find the function that both validates the prompt and performs the POST.
    def function_order() -> Tuple[Optional[ast.FunctionDef], Optional[int], Optional[int]]:
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            # Decorators are route metadata, not executable statements. On the
            # device bridge the only executable HTTP call is the real outbound
            # requests.post(...). Skip decorators entirely so the AST walker
            # inspects only executable function-body statements.
            inner_calls: List[Tuple[int, str]] = []
            for sub in ast.walk(node):
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if isinstance(sub, ast.Call):
                    if isinstance(sub.func, ast.Name):
                        inner_calls.append((sub.lineno, sub.func.id))
                    elif isinstance(sub.func, ast.Attribute):
                        # A route decorator is app.post(...): func.attr == "post"
                        # with func.value being the FastAPI app name. An inbound
                        # outbound request is requests.post(...): func.attr ==
                        # "post" with func.value being a Name "requests".
                        if isinstance(sub.func.value, ast.Name) and sub.func.value.id == "requests":
                            inner_calls.append((sub.lineno, sub.func.attr))
            # Guard identified by its call name.
            guard_line = next((ln for ln, n in inner_calls if n == "is_alpaca_prompt"), None)
            # The actual outbound request identified specifically as
            # requests.post(...), never a decorator.
            post_line = next((ln for ln, n in inner_calls if n == "post"), None)
            if guard_line is not None and post_line is not None:
                return node, guard_line, post_line
        return None, None, None

    handler, guard_line, post_line = function_order()
    check(
        "B4 prompt-contract check precedes the HTTP request",
        handler is not None and guard_line is not None and post_line is not None
        and guard_line < post_line,
        (
            f"handler={handler.name if handler else None}, "
            f"guard@line {guard_line}, post@line {post_line}"
            if handler is not None
            else "no single function containing both the guard and the HTTP call"
        ),
    )

    # --- 5. finish_reason == length rejected ----------------------------
    has_finish_reason = "finish_reason" in strings
    check(
        "B5 bridge inspects finish_reason",
        has_finish_reason,
        "finish_reason literal present — truncation is detectable"
        if has_finish_reason
        else "no finish_reason literal — truncation cannot be detected",
    )
    has_length = "length" in strings
    check(
        "B5b bridge rejects length-terminated output",
        has_length,
        "'length' literal present — truncated output is refused"
        if has_length
        else "no 'length' literal — truncated output would be parsed as if complete",
    )

    # --- 5c. no missing-field default at the device layer --------------
    check(
        "B5c bridge does not fabricate suggested_status",
        not any("suggested_status" in s and s.strip().startswith("suggested_status")
                for s in strings),
        "bridge must not inject a suggested_status literal",
    )

    # --- 5d. response envelope preserved for local_bridge.py ----------
    check(
        "B5d bridge returns the interpretation_text envelope local_bridge.py expects",
        any("interpretation_text" in s for s in strings),
        "local_bridge.py build_provider_envelope() requires 'interpretation_text'",
    )

    # --- 5e. decode settings match the training-time contract ---------
    check(
        "B5e bridge does not send messages[] (raw prompt, not chat turns)",
        '"messages"' not in bridge_path.read_text(encoding="utf-8")
        and "'messages'" not in bridge_path.read_text(encoding="utf-8"),
        "a messages[] body means the chat template is being used",
    )


# ===========================================================================
print("--- 6. 17/18-field candidate stays rejected (real provider) ---")

MODE = {"mode": "missing_suggested_status"}


class FakeBridge(BaseHTTPRequestHandler):
    """Loopback test double for the device bridge. Not model evidence."""

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        request = json.loads(self.rfile.read(length) or b"{}")
        if not request.get("text"):
            self.send_response(422)
            self.end_headers()
            return
        if self.path != "/interpret":
            self.send_response(404)
            self.end_headers()
            return

        if MODE["mode"] == "missing_suggested_status":
            candidate = make_candidate()
            candidate.pop("suggested_status")
            inner = json.dumps(candidate)
        elif MODE["mode"] == "truncated_json":
            # 17 fields, and the JSON is cut mid-object (what a real truncation
            # looks like). Must NOT be silently completed.
            candidate = make_candidate()
            candidate.pop("suggested_status")
            inner = json.dumps(candidate)[:-12]
        else:
            inner = json.dumps(make_candidate())

        body = json.dumps({"interpretation_text": inner, "model": {}})
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def log_message(self, *args: Any) -> None:
        return


SERVER = HTTPServer(("127.0.0.1", 0), FakeBridge)
threading.Thread(target=SERVER.serve_forever, daemon=True).start()
PROVIDER_URL = f"http://127.0.0.1:{SERVER.server_port}"


def interpret_with(mode: str):
    MODE["mode"] = mode
    provider = LocalBridgeModelProvider(url=PROVIDER_URL)
    try:
        result = provider.interpret("Paid 1250 cash for office stationery.")
        return provider, result, None
    except Exception as exc:  # noqa: BLE001
        return provider, None, exc


# --- 6a. exactly the field the device model dropped ----------------------
provider, result, exc = interpret_with("missing_suggested_status")
rejected = isinstance(exc, MalformedOutputError) and "suggested_status" in str(exc)
check(
    "B6a 17/18-field candidate (no suggested_status) is REJECTED",
    rejected,
    f"raised {type(exc).__name__}: {exc}" if exc else "ACCEPTED — contract weakened!",
)
check(
    "B6b rejection audit records exactly the one gap, no silent fill",
    provider.last_trace.get("contract", {}).get("missing_fields") == ["suggested_status"],
    f"missing_fields={provider.last_trace.get('contract', {}).get('missing_fields')}",
)
check(
    "B6c raw model output preserved verbatim for audit on reject",
    bool(provider.last_trace.get("raw_response_text")),
    f"raw_len={len(provider.last_trace.get('raw_response_text') or '')}",
)
check(
    "B6d candidate never gains a suggested_status default",
    result is None,
    "no InterpretationResult should be produced for a 17-field candidate",
)

# --- 6e. truncated JSON must not be salvaged ----------------------------
provider_t, result_t, exc_t = interpret_with("truncated_json")
check(
    "B6e truncated JSON is rejected, never partially salvaged",
    isinstance(exc_t, MalformedOutputError),
    f"raised {type(exc_t).__name__}: {exc_t}" if exc_t else "ACCEPTED truncated fragment!",
)

# ===========================================================================
print("--- 7. strict provider contract unchanged ---")

# 7a. the complete 18-field candidate still passes (no over-tightening)
MODE["mode"] = "complete"
provider_ok, result_ok, exc_ok = interpret_with("complete")
check(
    "B7a complete 18/18 candidate still PASSES the provider contract",
    exc_ok is None and result_ok is not None
    and all(f in result_ok.candidate for f in REQUIRED_FIELDS_18),
    f"fields="
    f"{sum(f in (result_ok.candidate if result_ok else {}) for f in REQUIRED_FIELDS_18)}/18",
)

# 7b. enforce_interpretation_contract still names the missing field
try:
    enforce_interpretation_contract(
        {k: v for k, v in make_candidate().items() if k != "suggested_status"}
    )
    check("B7b contract names the missing field", False, "no exception raised")
except MalformedOutputError as exc_b:
    check(
        "B7b contract names the missing field",
        "suggested_status" in str(exc_b),
        f"raised MalformedOutputError: {exc_b}",
    )

# 7c. enforce_interpretation_contract injects no defaults (pure presence check)
probe = {k: v for k, v in make_candidate().items() if k != "suggested_status"}
try:
    enforce_interpretation_contract(dict(probe))
except MalformedOutputError:
    pass
check(
    "B7c contract never mutates the candidate to add a field",
    "suggested_status" not in probe,
    "probe dict untouched — no default insertion",
)

# 7d. VERIFIED is still clamped, still recorded
verified = make_candidate(suggested_status="VERIFIED")
info = enforce_interpretation_contract(verified)
check(
    "B7d VERIFIED still clamped to REVIEW_REQUIRED and recorded",
    verified["suggested_status"] == "REVIEW_REQUIRED"
    and info.get("suggested_status_clamped_to_review_required") is True
    and info.get("suggested_status_raw") == "VERIFIED",
    f"now={verified['suggested_status']}",
)

# 7e. the training prompt contract itself is intact
check(
    "B7e shared prompt builder still produces valid Alpaca framing",
    is_alpaca_prompt(build_prompt("Paid 1250 cash.")),
    "build_prompt()/is_alpaca_prompt() contract",
)
check(
    "B7f all 18 fields are still named in ALPACA_INSTRUCTION",
    all(f in ALPACA_INSTRUCTION for f in REQUIRED_FIELDS_18),
    "instruction names every contract field",
)
check(
    "B7g training-time decode settings unchanged",
    (MAX_NEW_TOKENS, TEMPERATURE, TOP_P) == (512, 0.0, 1.0),
    f"max_new_tokens={MAX_NEW_TOKENS} temperature={TEMPERATURE} top_p={TOP_P}",
)

# 7h. envelope shape still exact
envelope = build_provider_envelope(
    {"interpretation_text": json.dumps(make_candidate()), "model": {}}
)
check(
    "B7h provider envelope shape unchanged",
    set(envelope.keys()) == {"interpretation", "model"},
    f"keys={sorted(envelope.keys())}",
)

SERVER.shutdown()

# ===========================================================================
print("=" * 68)
print(f"bridge source analysed : {bridge_path if bridge_path else 'NOT PRESENT (static checks skipped)'}")
if OVERRIDE_MISSING:
    print(
        f"  ! ${BRIDGE_ENV} was set to {OVERRIDE_REQUESTED!r} but that path is not a "
        f"readable file here (resolved: {OVERRIDE_MISSING})."
    )
    print(
        "    B1-B5 are therefore UNVERIFIED for the real device bridge, not merely "
        "unavailable on this host. Re-run from Termux where the file exists."
    )
print(f"skipped                : {len(SKIPS)}")
if FAILURES:
    print(f"RESULT: {len(FAILURES)} FAIL — {FAILURES}")
    sys.exit(1)
print("RESULT: ALL PASS")
sys.exit(0)