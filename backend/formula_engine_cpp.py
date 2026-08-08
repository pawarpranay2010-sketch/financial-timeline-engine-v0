"""
Financial Timeline Engine
Sprint 7 - Python <-> C++ Formula Engine bridge

The deterministic Formula Engine (formula_engine/) is implemented in C++.
Python sends already-verified facts + the requested metric over a minimal
JSON interface (one document on stdin, one document on stdout) and receives
structured results: status, value (full precision), display_value,
calculation_steps, lineage and block_reason.

This bridge NEVER decides whether an external source is trustworthy — that
stays in Sprint 6.5. It simply hands verified facts to the C++ engine.

If the compiled binary is unavailable (e.g. an environment without a C++
toolchain), the bridge reports unavailable and the Python engine's own
deterministic Decimal path is used as a fallback — FT-E keeps working and
the existing tests keep passing. Stdlib only (json, os, subprocess).
"""

from __future__ import annotations

import json
import os
import subprocess
from typing import Any, Dict, Optional

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DEFAULT_BIN = os.path.join(_REPO_ROOT, "formula_engine", "bin", "formula_engine")

_STATUS_MAP = {
    "REPORTED_VERIFIED": "reported",
    "DERIVED_VERIFIED": "derived",
    "EXTERNAL_DERIVED": "external_derived",
    "BLOCKED": "blocked",
    "UNANALYZED": "unanalyzed",
}


def binary_path() -> Optional[str]:
    """Resolve the compiled C++ engine binary. `FTE_FORMULA_ENGINE_BIN`
    overrides the default repo-relative location. None when unavailable."""
    env = os.environ.get("FTE_FORMULA_ENGINE_BIN")
    if env:
        if os.path.exists(env) and os.access(env, os.X_OK):
            return env
        return None
    if os.path.exists(_DEFAULT_BIN) and os.access(_DEFAULT_BIN, os.X_OK):
        return _DEFAULT_BIN
    return None


def cpp_available() -> bool:
    return binary_path() is not None


def _fact_json(fact: Dict[str, Any]) -> Dict[str, Any]:
    """Serialize one verified fact for the C++ engine. Only real fields are
    sent; nothing is fabricated. Missing metadata stays absent."""
    out: Dict[str, Any] = {}
    for key in ("metric", "unit", "scale", "reporting_period",
                "provenance_tier", "document_name", "page", "evidence",
                "provider", "source_ref"):
        v = fact.get(key) if isinstance(fact, dict) else None
        if v not in (None, ""):
            out[key] = str(v)
    v = fact.get("value") if isinstance(fact, dict) else None
    if v is not None:
        try:
            out["value"] = float(v)
        except (TypeError, ValueError):
            pass
    return out


def cpp_calculate(
    metric_key: str,
    resolved_facts: Dict[str, Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Invoke the C++ engine with `resolved_facts` (input key -> verified
    fact). Returns a normalized result dict, or None when the binary is
    unavailable or the call failed (the caller falls back to Python).

    Result dict (on success):
      status        'derived' | 'external_derived' | 'blocked' | 'unanalyzed'
      value         float (fraction for percent-kind) or None
      display_value str
      steps         list[str]
      lineage       str
      block_reason  str | None
    """
    bin_path = binary_path()
    if bin_path is None:
        return None
    payload = {
        "metric": metric_key,
        "inputs": {k: _fact_json(f) for k, f in (resolved_facts or {}).items()},
    }
    try:
        proc = subprocess.run(
            [bin_path],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    if proc.returncode != 0:
        return None
    try:
        out = json.loads(proc.stdout)
    except ValueError:
        return None
    if not isinstance(out, dict) or out.get("error"):
        return None
    status = _STATUS_MAP.get(str(out.get("status") or ""))
    if status is None:
        return None
    return {
        "status": status,
        "value": out.get("value"),
        "display_value": out.get("display_value") or "",
        "steps": out.get("calculation_steps") or [],
        "lineage": out.get("lineage") or "",
        "block_reason": out.get("block_reason"),
    }


def cpp_solve_metric(
    metric_key: str,
    solve_for: str,
    resolved_facts: Dict[str, Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Sprint 12A - invoke the C++ engine to REVERSE-SOLVE one variable of
    a registered op-driven formula (e.g. solve Expenses from Revenue +
    Profit). Returns the same normalized result shape as cpp_calculate
    (status, value, display_value, steps, lineage, block_reason), or None
    when the binary is unavailable / the call failed (the caller falls
    back to the Python deterministic path)."""
    bin_path = binary_path()
    if bin_path is None:
        return None
    payload = {
        "metric": metric_key,
        "solve_for": solve_for,
        "inputs": {k: _fact_json(f) for k, f in (resolved_facts or {}).items()},
    }
    try:
        proc = subprocess.run(
            [bin_path],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    if proc.returncode != 0:
        return None
    try:
        out = json.loads(proc.stdout)
    except ValueError:
        return None
    if not isinstance(out, dict) or out.get("error"):
        return None
    status = _STATUS_MAP.get(str(out.get("status") or ""))
    if status is None:
        return None
    return {
        "status": status,
        "value": out.get("value"),
        "display_value": out.get("display_value") or "",
        "steps": out.get("calculation_steps") or [],
        "lineage": out.get("lineage") or "",
        "block_reason": out.get("block_reason"),
    }
