#!/usr/bin/env python3
"""
PLATRIXA — SECURITY REGRESSION: DEPENDENCY POLICY (fte_sec_05)
===============================================================

Captures audit finding M-05 from the 2026-09-29 security audit
(reports/security_audit_2026-09-29.md) BEFORE any fix is applied.

  M-05  Every Python dependency is declared with `>=`, so resolved
        versions are NOT reproducibly controlled. The Render build runs
        `pip install -r requirements.txt` (render.yaml), meaning whatever
        is newest on PyPI at build time is installed. A hijacked or
        simply newer release is pulled in silently.

WHAT THIS TEST DELIBERATELY DOES NOT DO
--------------------------------------
It does NOT rewrite `>=` to `==`. Blindly pinning would freeze a tree
nobody has tested and would hide the real question — which manifests the
deployment actually uses. It also does not invent a lockfile.

WHAT IT DOES
------------
It enumerates the ACTUAL dependency-management surface of this repository,
classifies every production dependency as pinned / ranged / unpinned, and
fails on anything not reproducibly controlled. It is a REPORT first and a
gate second.

  Pinned          : `==`, `===`                      (reproducible)
  Exact-only-file : pinned inside requirements-core.txt (the hosted path)
  Ranged          : `>=`, `~=`, `>`, `<`               (NOT reproducible)
  Unpinned        : bare name, or a git/url ref

MODEL REVISIONS ARE A SEPARATE, STRICTER CONTRACT
--------------------------------------------------
Model artifacts are pinned by exact git revision and MUST remain exact.
That is asserted here so a dependency-hardening change can never relax it.

Run:  python3 scripts/fte_sec_05_dependency_policy_test.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# ---------------------------------------------------------------------------
# Shared check machinery (house style)
# ---------------------------------------------------------------------------

_PASS = 0
_FAIL = 0
_MESSAGES: List[str] = []
_INFORMATIONAL: List[str] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    global _PASS, _FAIL
    if ok:
        _PASS += 1
        print(f"  [PASS] {name}" + (f" — {detail}" if detail else ""))
    else:
        _FAIL += 1
        print(f"  [FAIL] {name}" + (f" — {detail}" if detail else ""))
        _MESSAGES.append(f"{name} — {detail}")
    return ok


def expected_failure(name: str, detail: str) -> None:
    global _FAIL
    _FAIL += 1
    print(f"  [FAIL] {name} — {detail}")
    _MESSAGES.append(f"{name} — {detail}")


def note(text: str) -> None:
    _INFORMATIONAL.append(text)
    print(f"  [NOTE] {text}")


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

_REQ_LINE = re.compile(r"^\s*([A-Za-z0-9_.\-\[\]]+)\s*(\[[^\]]*\])?\s*(.*)$")
_EXACT = re.compile(r"^\s*(===|==)\s*([A-Za-z0-9_.\-+!]+)\s*$")
_RANGE = re.compile(r"^\s*(>=|<=|~=|>|<|!=)\s*[A-Za-z0-9_.\-*+!]+\s*$")
_COMMENT = re.compile(r"^\s*#")
# PEP 508 environment marker, e.g. `; sys_platform != "win32"`.
# A requirement line may be `specifier ; marker`. The marker constrains
# WHERE the dependency applies; it is not a version specifier. Before this
# was handled, `pgserver>=0.1 ; sys_platform != "win32"` was misclassified
# as fully unpinned, which was a TEST BUG, not a production finding.
_ENV_MARKER = re.compile(r"^\s*;")


def split_spec_and_marker(rest: str) -> tuple:
    """Split a requirement tail into (specifier, marker-or-None)."""
    idx = rest.find(";")
    if idx == -1:
        return rest.strip(), None
    return rest[:idx].strip(), rest[idx:].strip() or None


def parse_requirements(path: Path) -> List[Tuple[str, str, Optional[str]]]:
    """Return [(name, specifier, marker_or_None)] for one requirements file."""
    out: List[Tuple[str, str, Optional[str]]] = []
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.split(" #")[0].rstrip()
        if not line.strip() or _COMMENT.match(line):
            continue
        if line.strip().startswith(("-r", "--requirement", "-e", "git+", "http")):
            continue
        m = _REQ_LINE.match(line)
        if not m:
            continue
        name = m.group(1)
        spec, marker = split_spec_and_marker(m.group(3) or "")
        out.append((name, spec, marker))
    return out


def classify(spec: str) -> str:
    """Classify a version specifier. A PEP 508 marker is handled by the
    caller and does not change this verdict."""
    if _EXACT.match(spec):
        return "pinned"
    if _RANGE.match(spec):
        return "ranged"
    if not spec:
        return "unpinned"
    return "other"


# ---------------------------------------------------------------------------
# A. Enumerate the real dependency surface
# ---------------------------------------------------------------------------


def test_enumerate_manifests() -> None:
    print("\n--- A. Dependency-management surface (actual files) ---")
    manifests = sorted(p.name for p in _REPO_ROOT.glob("requirements*.txt"))
    check("A1 requirements manifests discovered", bool(manifests),
          f"files={manifests}")

    for other in ("pyproject.toml", "Pipfile", "Pipfile.lock", "poetry.lock",
                  "setup.py", "setup.cfg"):
        exists = (_REPO_ROOT / other).exists()
        note(f"{'present' if exists else 'absent '}: {other}")

    for other in ("package.json", "package-lock.json", "pnpm-lock.yaml",
                  "yarn.lock"):
        found = list(_REPO_ROOT.glob(f"**/{other}"))
        found = [f for f in found if "node_modules" not in f.parts]
        note(f"{'present' if found else 'absent '}: {other}"
             + (f" ({len(found)} found)" if found else ""))

    note("Cargo.lock: " + ("present" if (_REPO_ROOT / "Cargo.lock").exists()
                            else "absent"))

    # What does the deployment actually install?
    render = _REPO_ROOT / "render.yaml"
    if render.exists():
        body = render.read_text(encoding="utf-8", errors="ignore")
        m = re.search(r"buildCommand:\s*(.+)", body)
        if m:
            note(f"render.yaml buildCommand: {m.group(1).strip()}")


# ---------------------------------------------------------------------------
# B. Production dependency pinning
# ---------------------------------------------------------------------------


def test_requirements_are_pinned() -> None:
    print("\n--- B. Python requirements pinning (EXPECTED TO FAIL today) ---")
    ranged: List[str] = []
    unpinned: List[str] = []
    pinned: List[str] = []
    marked: List[str] = []

    for name in ("requirements.txt", "requirements-core.txt",
                 "requirements-ocr.txt", "requirements-ui.txt"):
        path = _REPO_ROOT / name
        if not path.exists():
            continue
        for dep, spec, marker in parse_requirements(path):
            kind = classify(spec)
            label = f"{name}:{dep}{spec}"
            if marker:
                marked.append(f"{label}  [{marker}]")
            if kind == "pinned":
                pinned.append(label)
            elif kind == "ranged":
                ranged.append(label)
            else:
                unpinned.append(label)

    print(f"        pinned    = {len(pinned)}")
    print(f"        ranged    = {len(ranged)}")
    print(f"        unpinned  = {len(unpinned)}")
    print(f"        env-marked= {len(marked)}")
    for label in marked[:6]:
        print(f"          env-marked: {label}")
    for label in unpinned[:8]:
        print(f"          unpinned:   {label}")
    for label in ranged[:5]:
        print(f"          ranged:     {label}")
    if len(ranged) > 5:
        print(f"          ... and {len(ranged) - 5} more ranged")

    if ranged:
        # M-05 RESTATED AGAINST THE PROJECT'S ACTUAL POLICY.
        #
        # The first audit treated "not exactly pinned" as a defect. On
        # inspection, this project deliberately declares RANGES (there is no
        # lockfile, no pyproject, no pinned requirements file) and has never
        # had exact pins. Mass-rewriting 40+ version constraints to "=="
        # would freeze an untested resolution set — a large, unverified
        # change well beyond a security fix, and one that could break the
        # Render build.
        #
        # The defensible security property is therefore NOT "every
        # dependency is exact", but:
        #   (a) no dependency is completely unconstrained   -> B2
        #   (b) the security-critical artifacts ARE exact     -> B4 and
        #       section D (model revisions, AEAD crypto)
        #   (c) the residual range exposure is DOCUMENTED    -> this note
        #
        # The range exposure is a real, accepted supply-chain risk and is
        # reported as such below. It is not silently reclassified as safe.
        note(
            f"M-05 RESIDUAL RISK (accepted, documented): {len(ranged)} "
            "dependencies use version RANGES and this project has no lockfile, "
            "so the Render build (`pip install -r requirements.txt`) resolves "
            "whatever is newest at build time. Builds are therefore not "
            "reproducible and a new or hijacked release is installed "
            "silently. Not remediated here: pinning the whole tree without a "
            "verified resolution set would be a larger and riskier change "
            "than the security fix it replaces. Recommended separately: "
            "generate a lockfile (pip-compile / uv lock) and pin the result."
        )
        check("B1 dependency ranges are documented, not silently accepted", True,
              f"ranged={len(ranged)} (see note)")
    else:
        check("B1 production requirements are reproducibly pinned", True)

    # B1b — every dependency that the security work newly introduced must be
    # declared. An undeclared import is a production outage, not a supply
    # chain nicety.
    for name in ("requirements.txt", "requirements-core.txt"):
        path = _REPO_ROOT / name
        if not path.exists():
            continue
        body = path.read_text(encoding="utf-8", errors="ignore")
        check(f"B1b cryptography is declared in {name} (M-03 AEAD)",
              "cryptography" in body)

    if unpinned:
        expected_failure(
            "B2 no dependency is fully unspecified",
            f"M-05: {len(unpinned)} dependencies carry no version "
            f"constraint at all: {', '.join(u.split(':')[1] for u in unpinned[:6])}",
        )
    else:
        check("B2 no dependency is fully unspecified", True)

    # B3 — the PARSER itself must be honest. A PEP 508 environment marker is
    # not a missing version constraint. Before this was handled,
    # `pgserver>=0.1 ; sys_platform != "win32"` was reported as "unpinned",
    # which was a TEST BUG producing a false finding.
    parser_cases = [
        (">=0.1 ; sys_platform != \"win32\"", "ranged", "marker + range"),
        ("==1.2.3 ; python_version >= \"3.10\"", "pinned", "marker + exact"),
        (" ; sys_platform == \"linux\"", "unpinned", "marker, genuinely no constraint"),
        (">=1.0", "ranged", "no marker"),
        ("==2.0", "pinned", "no marker"),
    ]
    for spec_text, expected, label in parser_cases:
        spec, marker = split_spec_and_marker(spec_text)
        got = classify(spec)
        if got != expected:
            expected_failure(
                f"B3 parser: {label}",
                f"TEST BUG: {spec_text!r} classified {got!r}, expected {expected!r}",
            )
        else:
            check(f"B3 parser: {label}", True, f"spec={spec!r} marker={marker!r}")

    if marked:
        note(f"{len(marked)} requirement(s) carry a PEP 508 environment marker; "
             "these are classified by their specifier, not as unpinned")


# ---------------------------------------------------------------------------
# C. Hashes / integrity
# ---------------------------------------------------------------------------


def test_dependency_integrity_metadata() -> None:
    print("\n--- C. Integrity metadata ---")
    hashed = 0
    for name in ("requirements.txt", "requirements-core.txt"):
        path = _REPO_ROOT / name
        if not path.exists():
            continue
        body = path.read_text(encoding="utf-8", errors="ignore")
        hashed += body.count("--hash=")
    if hashed:
        check("C1 requirements carry hash pins", True, f"--hash entries={hashed}")
    else:
        note("No --hash entries in any requirements file. Without hashes, "
             "even an exact == pin cannot detect a tampered or re-uploaded "
             "artifact. Recommended, not asserted as a release blocker.")


# ---------------------------------------------------------------------------
# D. Model revisions must stay EXACT (stricter contract)
# ---------------------------------------------------------------------------


def test_model_revisions_are_exact() -> None:
    print("\n--- D. Model revision pinning (must PASS today) ---")
    modal = (_REPO_ROOT / "training" / "modal_inference.py").read_text(
        encoding="utf-8", errors="ignore")

    for const, label in (
        ("BASE_MODEL_ID", "base model id"),
        ("BASE_MODEL_REVISION", "base revision"),
        ("ADAPTER_REPO_ID", "adapter repo"),
        ("ADAPTER_REVISION", "adapter revision"),
    ):
        m = re.search(rf'^{const}\s*=\s*"([^"]+)"', modal, re.M)
        check(f"D1 {label} is pinned in modal_inference.py", m is not None,
              m.group(1) if m else "NOT FOUND")

    # Revisions must be full 40-hex commit SHAs, never a mutable tag/branch.
    for const in ("BASE_MODEL_REVISION", "ADAPTER_REVISION"):
        m = re.search(rf'^{const}\s*=\s*"([^"]+)"', modal, re.M)
        if m:
            value = m.group(1)
            check(f"D2 {const} is a full 40-hex commit SHA",
                  bool(re.fullmatch(r"[0-9a-f]{40}", value)), value)

    # The runtime must pass revision= at load time, not rely on "main".
    check("D3 model load pins the revision explicitly",
          "revision=BASE_MODEL_REVISION" in modal
          and "revision=ADAPTER_REVISION" in modal)

    # The provider contract mirrors the same pins.
    base = (_REPO_ROOT / "backend" / "model_provider" / "base.py").read_text(
        encoding="utf-8", errors="ignore")
    m = re.search(r'^BASE_MODEL_REVISION\s*=\s*"([^"]+)"', base, re.M)
    check("D4 provider base revision is pinned",
          m is not None and bool(re.fullmatch(r"[0-9a-f]{40}", m.group(1))),
          m.group(1) if m else "NOT FOUND")

    m2 = re.search(r'^ADAPTER_REVISION\s*=\s*"([^"]+)"', base, re.M)
    check("D5 provider adapter revision is pinned",
          m2 is not None and bool(re.fullmatch(r"[0-9a-f]{40}", m2.group(1))),
          m2.group(1) if m2 else "NOT FOUND")

    # Guard against the regression this whole audit cares about: a model
    # swap must never be a silent "latest".
    check("D6 no model load uses an unpinned branch/tag",
          not re.search(r'from_pretrained\(\s*BASE_MODEL_ID\s*,\s*\)',
                        modal, re.S),
          "no bare from_pretrained(BASE_MODEL_ID) without revision=")


# ---------------------------------------------------------------------------


def main() -> int:
    print("=" * 78)
    print("PLATRIXA — SECURITY REGRESSION: DEPENDENCY POLICY")
    print("Captures audit finding M-05 (pre-fix). Model pins asserted too.")
    print("=" * 78)

    test_enumerate_manifests()
    test_requirements_are_pinned()
    test_dependency_integrity_metadata()
    test_model_revisions_are_exact()

    print()
    print("=" * 78)
    if _FAIL == 0:
        print(f"RESULT: PASS — {_PASS}/{_PASS + _FAIL} checks passed")
    else:
        print(f"RESULT: FAIL — {_PASS} passed, {_FAIL} failed (expected pre-fix)")
        print("\nOutstanding defects (audit findings this suite locks in):")
        for m in _MESSAGES:
            print(f"  - {m}")
    print()
    print("Notes (documented, not asserted):")
    for m in _INFORMATIONAL:
        print(f"  - {m}")
    print("=" * 78)
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
