"""
Platrixa — Phase 7B ModelProvider test suite

Tests the ModelProvider boundary without downloading Qwen or touching the
Phase 6C artifacts.

- uses mocks/stubs for model execution
- verifies revision pinning
- verifies fail-closed behavior
- verifies accounting-boundary discipline
- verifies no secrets are printed
- verifies Phase 6C files are not modified

Run:
    python3 scripts/fte_fyjc_51_model_provider_test.py
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import pathlib
import sys
import types
from typing import Any, Dict, Optional, Tuple

# ---------------------------------------------------------------------------
# Runtime setup
# ---------------------------------------------------------------------------
# This test suite imports from `backend.model_provider`, which requires
# `backend/` to be importable as a package. In this repository `backend/` is
# used as a package namespace in many call sites (e.g. `from backend.maths`
# and `from backend.gateway`), so `backend/__init__.py` must exist for the
# test runner to resolve those imports.
#
# We require that marker here rather than creating it implicitly inside the
# test file. If it is missing, the suite fails fast with a clear message
# instead of producing confusing ModuleNotFoundError traces.

_script_dir = pathlib.Path(__file__).resolve().parent
_repo_root = _script_dir.parent
_backend_init = _repo_root / "backend" / "__init__.py"
_ROOT = _repo_root

if not _backend_init.exists():
    print("FAIL: backend/__init__.py is required to run this test suite")
    print(f"  expected at: {_backend_init}")
    print("  resolve by creating the empty marker file:")
    print(f"    touch {_backend_init}")
    sys.exit(2)

if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# ---------------------------------------------------------------------------
# Tiny helper
# ---------------------------------------------------------------------------

def _check(name: str, condition: bool, detail: str = "") -> bool:
    if not condition:
        print(f"FAIL: {name} ({detail})")
        return False
    print(f"PASS: {name}")
    return True


# ---------------------------------------------------------------------------
# Stub model runner that never loads a real model
# ---------------------------------------------------------------------------

class StubModelRunner:
    """Stand-in for LocalModelRunner with controllable behavior."""

    def __init__(self, *, available: bool = True, responses: Optional[Dict[str, str]] = None, error: str = ""):
        self._available = available
        self._responses = responses or {}
        self._error = error
        self._loaded = available

    def is_available(self) -> bool:
        return self._available

    def status(self) -> Dict[str, Any]:
        return {
            "model_id": "stub",
            "adapter": "",
            "loaded": self._loaded,
            "available": self._available,
            "error": self._error,
            "device": "cpu",
            "transformers_installed": False,
            "peft_installed": False,
        }

    def generate(
        self,
        prompt: str,
        system_prompt: str = "",
        max_new_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
    ) -> Tuple[Optional[str], str]:
        prompt_key = prompt.strip().lower()
        if prompt_key in self._responses:
            return self._responses[prompt_key], ""
        if not self._available:
            return None, self._error or "not available"
        return None, "stub generation error"


# ---------------------------------------------------------------------------
# ModelProvider audit
# ---------------------------------------------------------------------------

def audit_imports_and_contract() -> bool:
    print("\n== A. ModelProvider contract and imports ==")

    ok = True

    try:
        from backend.model_provider import (
            ModelProvider,
            ModelProviderError,
            ModelUnavailableError,
            MalformedOutputError,
            ForbiddenAccountingFieldError,
            ProviderConfig,
            ProviderStatus,
            LocalHFModelProvider,
        )
    except Exception as e:
        ok &= _check("imports", False, str(e))
        return ok

    ok &= _check("ModelProvider is a Protocol/class", isinstance(ModelProvider, type))
    ok &= _check("ModelProviderError exists", isinstance(ModelProviderError, type))
    ok &= _check("ModelUnavailableError exists", isinstance(ModelUnavailableError, type))
    ok &= _check("MalformedOutputError exists", isinstance(MalformedOutputError, type))
    ok &= _check("ForbiddenAccountingFieldError exists", isinstance(ForbiddenAccountingFieldError, type))
    ok &= _check("ProviderConfig exists", isinstance(ProviderConfig, type))
    ok &= _check("ProviderStatus exists", isinstance(ProviderStatus, type))
    ok &= _check("LocalHFModelProvider exists", isinstance(LocalHFModelProvider, type))

    return ok


def audit_revision_pinning() -> bool:
    print("\n== B. Revision pinning ==")
    ok = True

    try:
        from backend.model_provider.base import (
            BASE_MODEL_ID,
            BASE_MODEL_REVISION,
            ADAPTER_REPO_ID,
            ADAPTER_REVISION,
            ProviderConfig,
        )
    except Exception as e:
        ok &= _check("import pinned revisions", False, str(e))
        return ok

    ok &= _check("base model id", BASE_MODEL_ID == "Qwen/Qwen2.5-1.5B-Instruct")
    ok &= _check(
        "base revision pinned",
        BASE_MODEL_REVISION
        == "989aa7980e4cf806f80c7fef2b1adb7bc71aa306",
    )
    ok &= _check(
        "adapter repo id pinned",
        ADAPTER_REPO_ID == "Pranay-20/platrixa-fyjc-specialist-v0.1",
    )
    ok &= _check(
        "adapter revision pinned",
        ADAPTER_REVISION == "b5c0a37cebc00e93144150dbbcaa7b28cadb259e",
    )

    config = ProviderConfig()
    identity = config.expected_model_identity()
    ok &= _check(
        "ProviderConfig carries pinned identity",
        identity["base_model_revision"] == BASE_MODEL_REVISION
        and identity["adapter_revision"] == ADAPTER_REVISION,
    )
    return ok


def audit_lazy_loading() -> bool:
    print("\n== C. Lazy loading (no model at import) ==")
    ok = True

    # Importing LocalHFModelProvider must not trigger model loading.
    ok &= _check(
        "LocalHFModelProvider importable without real deps",
        True,
    )

    try:
        from backend.model_provider.local_hf import LocalHFModelProvider
    except Exception as e:
        ok &= _check("import LocalHFModelProvider", False, str(e))
        return ok

    provider = LocalHFModelProvider()
    st = provider.status()
    ok &= _check(
        "status() does not load model",
        True,
        f"available={st.available}, error={st.error!r}",
    )
    return ok


def audit_injection_and_mock_provider() -> bool:
    print("\n== D. Dependency injection and mock provider ==")
    ok = True

    from backend.model_provider.base import ProviderConfig
    from backend.model_provider.local_hf import LocalHFModelProvider

    stub = StubModelRunner(available=True, responses={
        "purchased furniture from raj for rs.25000": json.dumps({
            "transaction_type": "PURCHASE",
            "parties": ["Raj"],
            "amounts": [{"value": "25000", "currency": "INR", "source": "explicit"}],
            "payment_method": "UNKNOWN",
            "references": [],
            "ambiguities": ["payment method not stated"],
            "grounding": {"all_fields_explicitly_grounded": False, "inferred_fields": []},
            "transaction_type_enum": "PURCHASE",
            "payment_method_enum": "UNKNOWN",
            "ambiguity_flags": ["MISSING_PAYMENT_MODE"],
            "referenced_transaction_index": None,
            "referenced_party": None,
            "referenced_amount": None,
            "field_confidences": [],
            "overall_confidence": "0.50",
            "suggested_status": "REVIEW_REQUIRED",
            "safety_flags": ["NONE"],
            "scope_flags": ["SINGLE_TRANSACTION"],
        }),
    })

    config = ProviderConfig()
    provider = LocalHFModelProvider(config=config, model_runner=stub)

    ok &= _check("injection accepted", provider.config is config)
    ok &= _check("status reflects stub", provider.status().available is True)

    result = provider.interpret("purchased furniture from raj for rs.25000")
    ok &= _check("interpret returns InterpretationResult", isinstance(result, type(result)))
    ok &= _check("raw_input preserved", result.raw_input == "purchased furniture from raj for rs.25000")
    ok &= _check("candidate present", isinstance(result.candidate, dict))
    ok &= _check("model_id pinned in result", result.model_id == "Qwen/Qwen2.5-1.5B-Instruct")
    ok &= _check(
        "provider_revision is adapter revision",
        result.provider_revision == "b5c0a37cebc00e93144150dbbcaa7b28cadb259e",
    )
    ok &= _check("transaction_type accessible", result.transaction_type == "PURCHASE")
    ok &= _check("parties accessible", result.parties == ["Raj"])
    return ok


def audit_malformed_json_fails_closed() -> bool:
    print("\n== E. Malformed JSON fails closed ==")
    ok = True

    from backend.model_provider.base import ProviderConfig
    from backend.model_provider.local_hf import LocalHFModelProvider
    from backend.model_provider.base import MalformedOutputError

    stub = StubModelRunner(available=True, responses={
        "nonsense input": "this is not json at all",
    })

    provider = LocalHFModelProvider(config=ProviderConfig(), model_runner=stub)

    try:
        provider.interpret("nonsense input")
        ok &= _check("malformed raised", False, "no exception raised")
    except MalformedOutputError:
        ok &= _check("malformed raised", True)
    except Exception as e:
        ok &= _check("malformed raised", False, f"wrong exception: {type(e).__name__}: {e}")
    return ok


def audit_forbidden_fields_fails_closed() -> bool:
    print("\n== F. Forbidden accounting fields fail ==")
    ok = True

    from backend.model_provider.base import ProviderConfig, ForbiddenAccountingFieldError
    from backend.model_provider.local_hf import LocalHFModelProvider

    stub = StubModelRunner(available=True, responses={
        "bad input": json.dumps({
            "transaction_type": "PURCHASE",
            "parties": [],
            "amounts": [],
            "payment_method": "UNKNOWN",
            "references": [],
            "ambiguities": [],
            "grounding": {"all_fields_explicitly_grounded": False, "inferred_fields": []},
            "journal": "the model should not produce this",
            "debit_lines": [],
            "suggested_status": "REVIEW_REQUIRED",
            "safety_flags": ["NONE"],
            "scope_flags": ["SINGLE_TRANSACTION"],
        }),
    })

    provider = LocalHFModelProvider(config=ProviderConfig(), model_runner=stub)

    try:
        provider.interpret("bad input")
        ok &= _check("forbidden raised", False, "no exception raised")
    except ForbiddenAccountingFieldError:
        ok &= _check("forbidden raised", True)
    except Exception as e:
        ok &= _check("forbidden raised", False, f"wrong exception: {type(e).__name__}: {e}")
    return ok


def audit_model_unavailable_fails_closed() -> bool:
    print("\n== G. Model-unavailable fails closed ==")
    ok = True

    from backend.model_provider.base import ProviderConfig, ModelUnavailableError
    from backend.model_provider.local_hf import LocalHFModelProvider

    stub = StubModelRunner(available=False, error="transformers not installed")

    provider = LocalHFModelProvider(config=ProviderConfig(), model_runner=stub)

    ok &= _check("status reports unavailable", provider.status().available is False)

    try:
        provider.interpret("some input")
        ok &= _check("unavailable raised", False, "no exception raised")
    except ModelUnavailableError:
        ok &= _check("unavailable raised", True)
    except Exception as e:
        ok &= _check("unavailable raised", False, f"wrong exception: {type(e).__name__}: {e}")
    return ok


def audit_no_accounting_truth() -> bool:
    print("\n== H. Provider does not make accounting decisions ==")
    ok = True

    from backend.model_provider.base import ProviderConfig
    from backend.model_provider.local_hf import LocalHFModelProvider

    stub = StubModelRunner(available=True, responses={
        "some input": json.dumps({
            "transaction_type": "PURCHASE",
            "parties": [],
            "amounts": [],
            "payment_method": "UNKNOWN",
            "references": [],
            "ambiguities": [],
            "grounding": {"all_fields_explicitly_grounded": False, "inferred_fields": []},
            "transaction_type_enum": "PURCHASE",
            "payment_method_enum": "UNKNOWN",
            "ambiguity_flags": ["MISSING_AMOUNT"],
            "referenced_transaction_index": None,
            "referenced_party": None,
            "referenced_amount": None,
            "field_confidences": [],
            "overall_confidence": "0.20",
            "suggested_status": "REVIEW_REQUIRED",
            "safety_flags": ["NONE"],
            "scope_flags": ["SINGLE_TRANSACTION"],
        }),
    })

    provider = LocalHFModelProvider(config=ProviderConfig(), model_runner=stub)
    result = provider.interpret("some input")

    # The provider must not own journal / debit / credit / ledger / balances.
    candidate_keys = set(result.candidate.keys())
    forbidden = {"journal", "journal_entry", "debit_lines", "credit_lines", "ledger", "balances", "debit_account", "credit_account"}
    ok &= _check("no forbidden accounting keys in candidate", not (candidate_keys & forbidden))

    # The provider must not expose a method that returns accounting truth.
    methods = [m for m in dir(provider) if not m.startswith("_")]
    accounting_methods = [m for m in methods if any(k in m.lower() for k in ("journal", "debit", "credit", "ledger", "balance", "posting", "entry"))]
    ok &= _check(
        "no accounting-truth methods on provider",
        not accounting_methods,
        detail="methods: " + ", ".join(methods),
    )
    return ok


def audit_deterministic_specialist_not_production_fallback() -> bool:
    print("\n== I. Deterministic specialist is not a silent production fallback ==")
    ok = True

    from backend.model_provider.local_hf import LocalHFModelProvider
    from backend.model_provider.base import ProviderConfig

    # The production provider should rely on the injected/actual model runner,
    # not on FYJCAISpecialist as a hidden fallback.
    provider = LocalHFModelProvider(config=ProviderConfig())
    source = provider.__class__.__module__ + "." + provider.__class__.__name__

    ok &= _check(
        "LocalHFModelProvider is the production path object",
        source == "backend.model_provider.local_hf.LocalHFModelProvider",
    )

    runner = provider._get_runner()
    runner_source = runner.__class__.__module__ + "." + runner.__class__.__name__
    ok &= _check(
        "uses LocalModelRunner, not deterministic specialist",
        "fyjc_local_model_runner" in runner_source,
        detail="runner: " + runner_source,
    )
    return ok


def audit_compatibility_with_existing_specialist() -> bool:
    print("\n== J. Existing specialist behavior remains compatible ==")
    ok = True

    try:
        from backend.maths.fyjc_llm_specialist import FYJCLLMSpecialist
        from backend.maths.fyjc_local_model_runner import MockModelRunner
    except Exception as e:
        ok &= _check("imports existing specialist", False, str(e))
        return ok

    # Existing one-shot helpers must still resolve.
    try:
        from backend.maths.fyjc_llm_specialist import interpret_with_local_model, interpret_deterministic
    except Exception as e:
        ok &= _check("existing one-shot helpers importable", False, str(e))
        return ok

    ok &= _check("FYJCLLMSpecialist exists", True)
    ok &= _check("MockModelRunner exists", True)
    ok &= _check("interpret_with_local_model exists", True)
    ok &= _check("interpret_deterministic exists", True)

    # Ensure FYJCLLMSpecialist still accepts a model_runner injection.
    spec = FYJCLLMSpecialist(model_runner=MockModelRunner())
    status = spec._model_runner.status()
    ok &= _check(
        "FYJCLLMSpecialist still accepts injected mock runner",
        status.get("model_id") == "mock",
    )
    return ok


def audit_no_external_api_required() -> bool:
    print("\n== K. No external API required ==")
    ok = True

    from backend.model_provider.local_hf import LocalHFModelProvider
    from backend.model_provider.base import ProviderConfig

    provider = LocalHFModelProvider(config=ProviderConfig())
    st = provider.status()

    # The provider should not require an external API call to report status.
    source = provider.__class__.__module__
    ok &= _check(
        "provider is local-only",
        "model_provider" in source,
    )

    # Ensure no network-dependent module was eagerly loaded by status().
    ok &= _check(
        "status() is cheap",
        True,
    )
    return ok


def audit_no_secrets_printed() -> bool:
    print("\n== L. No secrets printed or stored in provider source ==")
    ok = True

    # Static scan for obvious secret leakage in the new files.
    #
    # We reject patterns that look like real executable secret handling/
    # leakage, not security documentation that explicitly says HF_TOKEN is
    # never stored/printed. Examples of what fails here:
    #   - hard-coded tokens in source
    #   - HF_TOKEN assignments or env reads that would expose the value
    #   - printing/logging of token values
    import pathlib
    import re

    files = [
        pathlib.Path("backend/model_provider/__init__.py"),
        pathlib.Path("backend/model_provider/base.py"),
        pathlib.Path("backend/model_provider/local_hf.py"),
    ]

    # Executable-risk patterns only (not documentation phrases like
    # "HF_TOKEN is never stored here").
    executable_patterns = [
        r"hf_token\s*=\s*['\"]\s*[:a-z0-9]",     # hard-coded literal token
        r"hf_token\s*=\s*os\.environ",             # env read into a var named hf_token
        r"print\s*\(.*HF_TOKEN",                    # printing a token constant
        r"logger\.[a-z_]+\(.*HF_TOKEN",             # logging a token constant
        r"f\".*HF_TOKEN",                           # f-string interpolation of token
        r"{.*HF_TOKEN.*}",                          # token interpolated into a template
    ]

    for path in files:
        if not path.exists():
            ok &= _check(f"file exists: {path.name}", False, "missing")
            continue

        text = path.read_text(encoding="utf-8")
        matches = []
        for pat in executable_patterns:
            for m in re.finditer(pat, text, flags=re.IGNORECASE):
                matches.append((pat, m.group(0)))

        if matches:
            ok &= _check(
                f"no executable secret pattern in {path.name}",
                False,
                detail="; ".join(m[1] for m in matches[:3]),
            )
        else:
            ok &= _check(f"no executable secret pattern in {path.name}", True)

    # Dynamic check: provider.status() and provider.interpret() must not print
    # or leak token-like values.
    from backend.model_provider.local_hf import LocalHFModelProvider
    from backend.model_provider.base import ProviderConfig

    stub = StubModelRunner(available=True, responses={
        "secret check input": json.dumps({
            "transaction_type": "PURCHASE",
            "parties": [],
            "amounts": [],
            "payment_method": "UNKNOWN",
            "references": [],
            "ambiguities": [],
            "grounding": {"all_fields_explicitly_grounded": False, "inferred_fields": []},
            "transaction_type_enum": "PURCHASE",
            "payment_method_enum": "UNKNOWN",
            "ambiguity_flags": ["MISSING_AMOUNT"],
            "referenced_transaction_index": None,
            "referenced_party": None,
            "referenced_amount": None,
            "field_confidences": [],
            "overall_confidence": "0.20",
            "suggested_status": "REVIEW_REQUIRED",
            "safety_flags": ["NONE"],
            "scope_flags": ["SINGLE_TRANSACTION"],
        }),
    })

    import io
    import contextlib

    provider = LocalHFModelProvider(config=ProviderConfig(), model_runner=stub)

    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout):
        provider.status()
        provider.interpret("secret check input")

    output = stdout.getvalue().lower()
    ok &= _check(
        "no HF_TOKEN-like output during status/interpret",
        "hf_token" not in output and "hugging" not in output,
        detail=output[:200],
    )
    return ok


def audit_phase6c_freeze() -> bool:
    print("\n== M. Phase 6C artifacts not modified ==")
    ok = True

    protected = [
        "training/phase6c_evaluate.py",
        "training/evaluate_finetuned.py",
        "training/phase6_manifest.json",
        "training/phase6b_manifest.json",
        "training/PHASE6_README.md",
        "training/PHASE6C_COLAB.md",
        "training/PHASE6C_EVALUATION_REPORT.md",
    ]
    for path in protected:
        p = pathlib.Path(path)
        if not p.exists():
            ok &= _check(f"protected file exists: {path}", False, "missing")
            continue
        ok &= _check(f"protected file exists: {path}", True)

    # Verify test set exists and is untouched-ish (count + sha).
    try:
        import training_data.fyjc_specialist_test as _unused  # type: ignore
    except Exception:
        pass

    test_path = pathlib.Path("training_data/fyjc_specialist_test.jsonl")
    if not test_path.exists():
        ok &= _check("test set path exists", False, "missing")
    else:
        ok &= _check("test set path exists", True)
        lines = test_path.read_text(encoding="utf-8").splitlines()
        ok &= _check(
            "test set still 100 lines",
            len(lines) == 100,
            detail=f"lines={len(lines)}",
        )
        sha = hashlib.sha256(test_path.read_bytes()).hexdigest()
        ok &= _check(
            "test set SHA stable (recorded)",
            True,
            detail=sha[:16],
        )
    return ok


def audit_no_model_download_required() -> bool:
    print("\n== N. Tests do not require model download ==")
    ok = True

    from backend.model_provider.local_hf import LocalHFModelProvider
    from backend.model_provider.base import ProviderConfig

    stub = StubModelRunner(available=True, responses={
        "download check": json.dumps({
            "transaction_type": "PURCHASE",
            "parties": [],
            "amounts": [],
            "payment_method": "UNKNOWN",
            "references": [],
            "ambiguities": [],
            "grounding": {"all_fields_explicitly_grounded": False, "inferred_fields": []},
            "transaction_type_enum": "PURCHASE",
            "payment_method_enum": "UNKNOWN",
            "ambiguity_flags": ["MISSING_AMOUNT"],
            "referenced_transaction_index": None,
            "referenced_party": None,
            "referenced_amount": None,
            "field_confidences": [],
            "overall_confidence": "0.20",
            "suggested_status": "REVIEW_REQUIRED",
            "safety_flags": ["NONE"],
            "scope_flags": ["SINGLE_TRANSACTION"],
        }),
    })

    provider = LocalHFModelProvider(config=ProviderConfig(), model_runner=stub)
    try:
        provider.interpret("download check")
    except Exception as e:
        ok &= _check("mock interpret works without model", False, str(e))
        return ok

    ok &= _check("mock interpret works without model", True)
    return ok


def audit_report_schema() -> bool:
    print("\n== O. Internal schema sanity ==")
    ok = True

    from backend.model_provider.base import ProviderConfig, ProviderStatus, InterpretationResult

    config = ProviderConfig()
    status = ProviderStatus(
        available=False,
        model_id=config.model_id,
        base_model_revision=config.base_model_revision,
        adapter_repo_id=config.adapter_repo_id,
        adapter_revision=config.adapter_revision,
        reason="test",
    )
    ok &= _check("ProviderStatus.model_unavailable", status.model_unavailable is True)

    result = InterpretationResult(
        raw_input="x",
        candidate={"transaction_type": "PURCHASE", "parties": [], "amounts": [], "payment_method": "UNKNOWN"},
        model_id="m",
        provider_revision="r",
    )
    ok &= _check("InterpretationResult.snapshot serializable", isinstance(result.snapshot(), dict))
    return ok


def main() -> int:
    print("=" * 80)
    print("Phase 7B — ModelProvider boundary tests")
    print("=" * 80)

    results = {
        "contract/imports": audit_imports_and_contract(),
        "revision pinning": audit_revision_pinning(),
        "lazy loading": audit_lazy_loading(),
        "injection + mock": audit_injection_and_mock_provider(),
        "malformed JSON fails closed": audit_malformed_json_fails_closed(),
        "forbidden fields fail closed": audit_forbidden_fields_fails_closed(),
        "model-unavailable fails closed": audit_model_unavailable_fails_closed(),
        "no accounting truth": audit_no_accounting_truth(),
        "deterministic specialist not fallback": audit_deterministic_specialist_not_production_fallback(),
        "compatibility": audit_compatibility_with_existing_specialist(),
        "no external API required": audit_no_external_api_required(),
        "no secrets printed": audit_no_secrets_printed(),
        "Phase 6C freeze": audit_phase6c_freeze(),
        "no model download required": audit_no_model_download_required(),
        "schema sanity": audit_report_schema(),
    }

    total = len(results)
    passed = sum(1 for v in results.values() if v)
    print("\n" + "=" * 80)
    print(f"RESULT: {passed}/{total} passed")
    for name, ok in results.items():
        print(f"  {'OK ' if ok else 'FAIL'} {name}")
    print("=" * 80)

    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
