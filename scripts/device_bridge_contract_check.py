#!/usr/bin/env python3
"""Read-only contract check for the device-side local_llama_bridge.py.

WHY THIS EXISTS
---------------
`local_llama_bridge.py` lives on the Android/Termux device, not in this
repository. Its current implementation is believed to call a chat-completion
endpoint, while `docs/LOCAL_MODEL_TRANSPORT.md` and
`scripts/build_local_gguf_artifact.py` both record that the adapter was
fine-tuned on Alpaca *completions* and must therefore be driven through the
raw `/v1/completions` route with the canonical prompt.

That file cannot be fetched from here, so it cannot be auto-rewritten either:
applying regex edits to unseen source would be a guess, and a silent
mis-patch on the validation transport is worse than no patch at all. This
script does the half that CAN be done safely and reproducibly - it reads the
device file and reports, per required property, whether the file satisfies
the documented adapter contract, with the offending line numbers. The edit
itself is then made on the device, against the reported lines.

THIS SCRIPT NEVER WRITES. It has no backup to make because it modifies
nothing. The device-side procedure (cp to a private backup first) is printed
at the end of every run.

USAGE (on the device, from the repo root)
-----------------------------------------
    python3 scripts/device_bridge_contract_check.py
    python3 scripts/device_bridge_contract_check.py ~/local_llama_bridge.py

EXIT CODES
----------
    0  every required property is present (file is compliant)
    1  at least one required property is missing (file needs the edit)
    2  the file could not be read / parsed

Exit 2 is deliberately distinct from exit 1: "I could not check" must never
be reported as "the contract holds" or as "the contract is broken".
"""

from __future__ import annotations

import ast
import pathlib
import sys
from typing import Callable, List, NamedTuple, Optional, Tuple

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

DEFAULT_CANDIDATES = (
    "local_llama_bridge.py",
    "device/local_llama_bridge.py",
    "android/local_llama_bridge.py",
)


class Finding(NamedTuple):
    ident: str
    label: str
    ok: bool
    detail: str


def locate(argv: List[str]) -> Optional[pathlib.Path]:
    if len(argv) > 1:
        candidate = pathlib.Path(argv[1])
        if not candidate.is_absolute():
            candidate = REPO_ROOT / candidate
        return candidate
    for rel in DEFAULT_CANDIDATES:
        candidate = REPO_ROOT / rel
        if candidate.is_file():
            return candidate
    return None


# ----------------------------------------------------------------------
# Property checks. Each returns (ok, detail). `detail` is printed whether
# the check passed or failed so a passing run is still auditable.
# ----------------------------------------------------------------------

def check_imports(src: str, tree: ast.Module) -> Tuple[bool, str]:
    """Canonical prompt builder + guard must be imported, not retyped."""
    wanted = {"build_prompt", "is_alpaca_prompt"}
    found: set = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                found.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                found.add((alias.asname or alias.name).split(".")[0])
    missing = sorted(wanted - found)
    if missing:
        return False, f"missing import(s): {', '.join(missing)}"
    return True, "build_prompt and is_alpaca_prompt are imported"


def check_no_retyped_prompt(src: str, tree: ast.Module) -> Tuple[bool, str]:
    """The Alpaca scaffold must not be re-constructed inline in the bridge."""
    # "### Instruction:" appearing as a literal in the bridge means the
    # canonical template was copied by hand instead of called.
    offenders = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and ("### Instruction:" in node.value or "### Response:" in node.value)
    ]
    if offenders:
        return False, (
            "inlined Alpaca scaffold at line(s) "
            f"{offenders}; call build_prompt() instead of retyping the template"
        )
    return True, "no inlined Alpaca scaffold"


def check_prompt_guard(src: str, tree: ast.Module) -> Tuple[bool, str]:
    """is_alpaca_prompt() must be called BEFORE the request is sent."""
    call_lines = sorted(
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "is_alpaca_prompt"
    )
    if not call_lines:
        return False, "is_alpaca_prompt() is never called (no chat-substitution guard)"
    # FastAPI route decorators are also `.post` attributes but are not
    # outbound HTTP calls; excluding them keeps the ordering comparison
    # about the real request.
    decorator_lines = {
        dec.lineno
        for node in ast.walk(tree)
        for dec in getattr(node, "decorator_list", []) or []
    }
    post_lines = sorted(
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"post", "urlopen", "request"}
        and node.lineno not in decorator_lines
    )
    if not post_lines:
        return False, "no outbound HTTP call found to compare the guard against"
    if call_lines[0] < post_lines[0]:
        return True, (
            f"guard called at line {call_lines[0]}, first HTTP call at line "
            f"{post_lines[0]} (guard precedes the request)"
        )
    return False, (
        f"is_alpaca_prompt() is called at line {call_lines[0]} but the first "
        f"HTTP call is at line {post_lines[0]} - the guard runs too late"
    )


def check_raw_completion_endpoint(src: str, tree: ast.Module) -> Tuple[bool, str]:
    """Raw /v1/completions only. Chat routes are explicitly rejected."""
    chat = sorted({line for value, line in _literal_lines(tree)
                   if "chat/completions" in value})
    raw = sorted({line for value, line in _literal_lines(tree)
                  if "/v1/completions" in value or value == "/completion"})
    if chat:
        return False, (
            f"chat endpoint referenced at line(s) {chat}; the adapter was "
            "fine-tuned on completions and must use /v1/completions"
        )
    if not raw:
        return False, "no raw completion endpoint (/v1/completions or /completion) found"
    return True, f"raw completion endpoint at line(s) {raw}"


def check_reads_choices_text(src: str, tree: ast.Module) -> Tuple[bool, str]:
    """Completion text must come from choices[0].text, not message.content."""
    chat_field = sorted({line for value, line in _literal_lines(tree)
                         if value in {"content", "message.content"}})
    if chat_field:
        return False, (
            f"reads the chat field at line(s) {chat_field}; a raw completion "
            "returns choices[0].text, not choices[0].message.content"
        )
    text_reads = sorted({line for value, line in _literal_lines(tree)
                         if value in {"text", "choices"}})
    if not text_reads:
        return False, "never reads choices/text from the completion response"
    return True, f"reads completion text at line(s) {text_reads}"


def check_finish_reason_handled(src: str, tree: ast.Module) -> Tuple[bool, str]:
    """Truncation must be rejected, not silently accepted."""
    hits = sorted({line for value, line in _literal_lines(tree)
                   if "length" in value})
    if not hits:
        return False, (
            'no finish_reason handling found; a truncated completion '
            '(finish_reason == "length") must be rejected'
        )
    return True, f"finish_reason handling at line(s) {hits}"


def check_envelope_preserved(src: str, tree: ast.Module) -> Tuple[bool, str]:
    """The provider contract envelope must stay {"text"} -> {"interpretation_text"}."""
    req = '"text"' in src or "'text'" in src
    resp = "interpretation_text" in src
    if req and resp:
        return True, 'request {"text": ...} and response {"interpretation_text": ...} present'
    missing = []
    if not req:
        missing.append('request field "text"')
    if not resp:
        missing.append('response field "interpretation_text"')
    return False, "envelope field(s) missing: " + ", ".join(missing)


def check_generation_constants(src: str, tree: ast.Module) -> Tuple[bool, str]:
    """Must send the canonical 512 / 0.0 / 1.0 decode settings.

    Either the literal values may be written into the request body, or the
    three constants may be imported from the canonical prompt module - both
    are the documented contract. Anything else (a different sampler, or a
    temperature that does not match training) confounds quantization with
    sampling and fails.
    """
    literals = set(_literal_values(tree))
    imported = {alias.name for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom) for alias in node.names}
    problems = []
    for name, value in (
        ("MAX_NEW_TOKENS", "512"),
        ("TEMPERATURE", "0.0"),
        ("TOP_P", "1.0"),
    ):
        if value in literals or name in imported:
            continue
        problems.append(
            f"{name} neither imported nor set literally to {value}"
        )
    if problems:
        return False, "; ".join(problems)
    return True, "512 / 0.0 / 1.0 present (literal or imported constant)"


def _literal_values(tree: ast.Module):
    """Every string constant in the file, in source order."""
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]


def _literal_lines(tree: ast.Module):
    """(value, lineno) for every string constant, in source order."""
    return [
        (node.value, node.lineno)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]


CHECKS: Tuple[Callable[[str, ast.Module], Tuple[bool, str]], ...] = (
    check_imports,
    check_no_retyped_prompt,
    check_prompt_guard,
    check_raw_completion_endpoint,
    check_reads_choices_text,
    check_finish_reason_handled,
    check_envelope_preserved,
    check_generation_constants,
)


PROCEDURE = """\
DEVICE-SIDE PROCEDURE (run in Termux; nothing below was applied here)
--------------------------------------------------------------------
 1. cp -p local_llama_bridge.py local_llama_bridge.py.bak
    (private backup; verify with `ls -l local_llama_bridge.py.bak`)
 2. Apply ONLY the changes this checker reported as FAIL, then re-run:
        python3 scripts/device_bridge_contract_check.py
    until it exits 0.
 3. Put the canonical prompt module next to the bridge so the bridge can
    import it (it is dependency-free, so it copies cleanly):
        cp backend/model_provider/platrixa_prompt.py .
 4. Static contract verification against the real bridge source:
        PLATRIXA_DEVICE_BRIDGE="$PWD/local_llama_bridge.py" \\
          python3 scripts/local_bridge_prompt_contract_test.py
 5. Live acceptance (only with llama-server running on 127.0.0.1:8081):
        python3 scripts/local_bridge_acceptance.py --out /tmp/acc.json
 6. Artifact provenance:
        sha256sum local_llama_bridge.py
        sha256sum "$PLATRIXA_LOCAL_MODEL_PATH"
        cat "$(dirname "$PLATRIXA_LOCAL_MODEL_PATH")/artifact-manifest.json" 2>/dev/null
    If no manifest exists next to the GGUF, the answer is
    PROVENANCE_UNKNOWN - a self-computed hash proves only that the file did
    not change, never that it was trained from the documented adapter.
 7. To revert: mv local_llama_bridge.py.bak local_llama_bridge.py
"""


def main(argv: List[str]) -> int:
    path = locate(argv)
    if path is None or not path.is_file():
        print("device bridge not found; checked:")
        for rel in DEFAULT_CANDIDATES:
            print(f"  {REPO_ROOT / rel}")
        print("pass an explicit path as argv[1] to check a file on the device.")
        return 2
    try:
        src = path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"cannot read {path}: {exc}")
        return 2
    try:
        tree = ast.parse(src, filename=str(path))
    except SyntaxError as exc:
        print(f"cannot parse {path}: {exc}")
        return 2

    findings: List[Finding] = []
    for idx, check in enumerate(CHECKS, start=1):
        try:
            ok, detail = check(src, tree)
        except Exception as exc:  # a checker bug must not read as "compliant"
            ok, detail = False, f"check raised {type(exc).__name__}: {exc}"
        findings.append(Finding(f"D{idx}", check.__name__, ok, detail))

    print("=" * 72)
    print(f"device bridge: {path}  ({len(src.splitlines())} lines)")
    print("=" * 72)
    for f in findings:
        print(f"{'PASS' if f.ok else 'FAIL'} {f.ident}  {f.label}")
        print(f"       {f.detail}")
    failed = [f for f in findings if not f.ok]
    print("-" * 72)
    print(f"RESULT: {len(findings) - len(failed)} PASS, {len(failed)} FAIL")
    print()
    print(PROCEDURE)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))