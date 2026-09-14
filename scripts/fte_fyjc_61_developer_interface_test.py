"""
Platrixa — Phase 12: developer-interface evidence suite
=======================================================

Proves the public interface contract end to end:

  A. public package imports successfully
  B. importing requires no live remote model (no torch/transformers, no network)
  C. public API has the documented shape
  D. public API calls Kernel.process EXACTLY ONCE per request
  E. public API does not bypass Kernel (dynamic + static proof)
  F. CLI uses the same public API
  G. JSON serialization is deterministic and JSON-safe
  H. RulePack works through the public interface
  I. RuleHooks work through the public interface
  J. hook attempts to request VERIFIED are sanitized
  K. PASS cannot upgrade a downgraded state
  L. UNAVAILABLE/ERROR cannot become success
  M. existing Kernel behavior remains unchanged (facade/direct parity)
  N. Phase 10 authority invariants remain enforced

Section 12 routing proof (D + E) is the critical architectural evidence:
the public layer depends on the Kernel, not on the Kernel's internal
orchestration components.

Run:  python3 scripts/fte_fyjc_61_developer_interface_test.py
"""

from __future__ import annotations

import inspect
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import platrixa  # noqa: E402
from platrixa import (  # noqa: E402
    BLOCKED,
    InputError,
    Platrixa,
    PlatrixaConfig,
    PlatrixaResult,
    PlatrixaError,
    ProviderError,
    REVIEW_REQUIRED,
    VERIFIED,
)
from backend.model_provider.base import (  # noqa: E402
    InterpretationResult,
    ProviderConfig,
    ProviderStatus,
)
from backend.kernel.kernel import Kernel  # noqa: E402
from backend.rules.contract import (  # noqa: E402
    OUTCOME_ERROR,
    OUTCOME_FAIL,
    OUTCOME_PASS,
    OUTCOME_UNAVAILABLE,
    RuleContext,
    RuleDecision,
    RuleHook,
)

_PASS = 0
_FAIL = 0


def check(name: str, ok: bool, detail: str = "") -> bool:
    global _PASS, _FAIL
    if ok:
        _PASS += 1
        print(f"  [PASS] {name}" + (f" — {detail}" if detail else ""))
    else:
        _FAIL += 1
        print(f"  [FAIL] {name} — {detail}")
    return ok


# ---------------------------------------------------------------------------
# Fixtures — stub provider mirrors the proven Phase 7R/9/60 kernel path
# ---------------------------------------------------------------------------

VALID_CANDIDATE: Dict[str, Any] = {
    "transaction_type": "PURCHASE",
    "parties": ["raj"],
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
}

VALID_INPUT = "purchased furniture from raj for rs.25000"


class StubProvider:
    """Counts interpret() invocations — the routing-proof instrument."""

    def __init__(self) -> None:
        self._config = ProviderConfig()
        self.interpret_calls = 0

    @property
    def config(self) -> ProviderConfig:
        return self._config

    def status(self) -> ProviderStatus:
        return ProviderStatus(
            available=True,
            model_id="stub-model",
            base_model_revision="stub-base",
            adapter_repo_id="stub-adapter",
            adapter_revision="stub-rev",
            reason="stub provider",
            loadable=True,
        )

    def interpret(self, raw_input: str) -> InterpretationResult:
        self.interpret_calls += 1
        return InterpretationResult(
            raw_input=raw_input,
            candidate=dict(VALID_CANDIDATE),
            model_id="stub-model",
            provider_revision="stub-rev",
            generated_profile={},
        )


class CountingKernel:
    """
    Wraps the client's OWN configured Kernel and counts process() calls.

    The wrapper deliberately preserves the client's kernel (including any
    rule pack/hooks from its config) — instrumentation must not change
    configuration.
    """

    def __init__(self, kernel: Kernel) -> None:
        self._kernel = kernel
        self.process_calls = 0

    def process(self, raw_input: str, *, request_id: Optional[str] = None):
        self.process_calls += 1
        return self._kernel.process(raw_input, request_id=request_id)

    def model_provider(self):
        return self._kernel.model_provider()

    def _get_rule_engine(self):
        return self._kernel._get_rule_engine()


def _client(provider: Optional[StubProvider] = None,
            config: Optional[PlatrixaConfig] = None):
    """
    Public client with the routing instruments wired in.

    The client is constructed normally (rule pack validated at construction,
    exactly as production); only the provider is swapped for the counting
    stub via the Kernel's own set_model_provider seam.
    """
    stub = provider or StubProvider()
    client = Platrixa(config=config)
    client._kernel.set_model_provider(stub)
    counting = CountingKernel(client._kernel)
    client._kernel = counting
    return client, counting, stub


# Hook fixtures -------------------------------------------------------------

class _PassHook(RuleHook):
    rule_id = "pass_hook"

    def validate(self, context: RuleContext) -> RuleDecision:
        return RuleDecision(rule_id=self.rule_id, outcome=OUTCOME_PASS)


class _FailHook(RuleHook):
    rule_id = "fail_hook"

    def validate(self, context: RuleContext) -> RuleDecision:
        return RuleDecision(rule_id=self.rule_id, outcome=OUTCOME_FAIL,
                            message="policy says review")


class _UnavailableHook(RuleHook):
    rule_id = "unavailable_hook"

    def validate(self, context: RuleContext) -> RuleDecision:
        return RuleDecision(rule_id=self.rule_id, outcome=OUTCOME_UNAVAILABLE,
                            message="external lookup unavailable")


class _ErrorHook(RuleHook):
    rule_id = "error_hook"

    def validate(self, context: RuleContext) -> RuleDecision:
        return RuleDecision(rule_id=self.rule_id, outcome=OUTCOME_ERROR,
                            message="hook crashed")


class _SmuggledHintHook(RuleHook):
    """Smuggles VERIFIED via metadata decision_hint — must be sanitized."""

    rule_id = "smuggled_hint_hook"

    def validate(self, context: RuleContext) -> RuleDecision:
        return RuleDecision(rule_id=self.rule_id, outcome=OUTCOME_PASS,
                            metadata={"decision_hint": "VERIFIED"},
                            message="demanding VERIFIED via metadata")


class _SmuggledKwargHook(RuleHook):
    """Smuggles VERIFIED via an invalid kwarg — contract rejects it (ERROR)."""

    rule_id = "smuggled_kwarg_hook"

    def validate(self, context: RuleContext) -> RuleDecision:
        return RuleDecision(rule_id=self.rule_id, outcome=OUTCOME_PASS,
                            decision_hint="VERIFIED")  # type: ignore[call-arg]


def _write_yaml(mapping: Any) -> str:
    import yaml

    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as fh:
        yaml.safe_dump(mapping, fh)
        return fh.name


# ---------------------------------------------------------------------------
print("\n=== A. Public package imports successfully ===")
check("A1 import platrixa works", platrixa.__version__ == "0.1.0",
      f"version={platrixa.__version__}")
check("A2 client class exported", isinstance(Platrixa, type))
check("A3 result class exported", isinstance(PlatrixaResult, type))
check("A4 config class exported", isinstance(PlatrixaConfig, type))
check("A5 error contract exported",
      issubclass(InputError, PlatrixaError) and issubclass(ProviderError, PlatrixaError))
check("A6 all documented exports present",
      all(hasattr(platrixa, n) for n in platrixa.__all__))

print("\n=== B. Import requires no live remote model ===")
code = (
    "import sys; sys.path.insert(0, r'%s'); import platrixa; "
    "print('TORCH' if 'torch' in sys.modules else 'CLEAN', "
    "'TRANSFORMERS' if 'transformers' in sys.modules else '', "
    "'OK' if callable(platrixa.Platrixa) else '')"
) % _REPO_ROOT
out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                     env={"PATH": "/usr/bin:/bin", "HOME": str(_REPO_ROOT)})
check("B1 fresh-process import succeeds with no env", out.returncode == 0,
      out.stderr.strip()[:80])
check("B2 no torch/transformers loaded at import",
      "CLEAN" in out.stdout and "OK" in out.stdout, out.stdout.strip()[:60])

print("\n=== C. Public API has the documented shape ===")
sig = inspect.signature(Platrixa.process)
check("C1 process(input) signature",
      list(sig.parameters) == ["self", "input", "request_id"],
      str(list(sig.parameters)))
check("C2 client constructor accepts config",
      "config" in inspect.signature(Platrixa.__init__).parameters)
for prop in ("status", "status_label", "success", "interpretation", "accounting",
             "issues", "grounding_issues", "rule_evidence", "request_id",
             "raw_input", "metadata", "next_action", "to_dict"):
    check(f"C3 result exposes {prop}", hasattr(PlatrixaResult, prop))
check("C4 config fields documented",
      all(f in PlatrixaConfig.__dataclass_fields__
          for f in ("provider", "endpoint_url", "endpoint_token", "timeout",
                    "transport", "provider_config", "rule_pack", "rule_hooks")))
check("C5 no global mutable config exposure", not hasattr(platrixa, "settings"))

print("\n=== D/E. KERNEL ROUTING PROOF (Section 12) ===")
client, counting, stub = _client()
result = client.process(VALID_INPUT)
check("D1 public call returns a result", result.status == VERIFIED, result.status)
check("D2 Kernel.process executed EXACTLY ONCE", counting.process_calls == 1,
      f"process_calls={counting.process_calls}")
check("D3 provider.interpret executed EXACTLY ONCE (via Kernel only)",
      stub.interpret_calls == 1, f"interpret_calls={stub.interpret_calls}")
client.process("second transaction through same client")
check("D4 second public call → second single Kernel.process",
      counting.process_calls == 2 and stub.interpret_calls == 2,
      f"process={counting.process_calls} interpret={stub.interpret_calls}")

facade_src = "\n".join(
    (Path("platrixa") / f).read_text(encoding="utf-8")
    for f in ("__init__.py", "config.py", "errors.py", "_facade.py", "__main__.py")
)
FORBIDDEN = {
    "accounting engine": ("hardened_bookkeeping_outcome", "fyjc_accounting"),
    "grounding gate": ("fyjc_grounding_gate", "ExpandedGroundingGate"),
    "schema validator": ("schema_verifier", "StructuredInterpretationValidator"),
    "rule engine direct use": ("RuleEngine(", "load_yaml_rule_pack("),
    "persistence": ("PostgresResultPersistence", "postgres", "persist("),
    "direct model invocation": (".interpret(",),
}
for label, needles in FORBIDDEN.items():
    hits = [n for n in needles if n in facade_src]
    check(f"E1 facade contains no {label}", not hits, f"hits={hits}" if hits else "clean")
provider_uses = [
    line.strip() for line in facade_src.splitlines()
    if "ModelProvider" in line and "import" not in line
]
check("E2 provider references are construction-only",
      not any("(" in u and "ModelProvider(" not in u for u in provider_uses),
      f"{provider_uses}")
check("E3 facade delegates to Kernel.process (single delegation point)",
      facade_src.count("self._kernel.process(") == 1)
client2, counting2, _ = _client()
inner = client2._kernel._kernel
check("E4 facade holds a real Kernel (no shadow runtime)", isinstance(inner, Kernel))

print("\n=== F. CLI uses the same public API ===")
import platrixa.__main__ as cli

class SpyClient(Platrixa):
    process_calls = 0

    def process(self, input, *, request_id=None):
        SpyClient.process_calls += 1
        return super().process(input, request_id=request_id)

# The CLI imports Platrixa from the package at call time (proving it uses the
# public API, not a private path) — so the spy patches the package attribute.
_orig = platrixa.Platrixa
platrixa.Platrixa = SpyClient
try:
    # Remote provider with no endpoint → fail-closed JSON; proves CLI→API→Kernel.
    rc = cli.main(["process", "--text", VALID_INPUT, "--provider", "remote"])
finally:
    platrixa.Platrixa = _orig
check("F1 CLI invoked the public API exactly once", SpyClient.process_calls == 1,
      f"calls={SpyClient.process_calls}")
check("F2 CLI exit code follows the deterministic state map", rc == 3,
      f"rc={rc} (MODEL_UNAVAILABLE without endpoint)")

print("\n=== G. JSON serialization is deterministic ===")
client, _, _ = _client()
r1 = client.process(VALID_INPUT)
r2 = client.process(VALID_INPUT)
s1 = json.dumps(r1.to_dict(), sort_keys=True, ensure_ascii=False)
s2 = json.dumps(r2.to_dict(), sort_keys=True, ensure_ascii=False)
check("G1 identical inputs → identical JSON", s1 == s2)
check("G2 serialization is JSON-safe (no Decimal/repr leaks)", isinstance(s1, str))
check("G3 no memory-address reprs in output", "0x" not in s1)
check("G4 no credentials in serialized output",
      "hf_" not in s1 and "TOKEN" not in s1.upper().replace("TOKEN_ENV", ""))

print("\n=== H. RulePack through the public interface ===")
pack_path = _REPO_ROOT / "examples" / "rules" / "platrixa_rules.yaml"
client, counting, _ = _client(config=PlatrixaConfig(rule_pack=str(pack_path)))
result = client.process(VALID_INPUT)
check("H1 example YAML pack accepted at construction", True)
check("H2 rule evidence produced through public result",
      len(result.rule_evidence) > 0, f"{len(result.rule_evidence)} records")
check("H3 evidence records carry rule_id/result/message",
      all(k in rec for rec in result.rule_evidence for k in ("rule_id", "result", "message")))

threshold_pack = _write_yaml({
    "rules": [{
        "id": "review_over_10k",
        "type": "threshold",
        "field": "amounts.0.value",
        "operator": "<",
        "value": 10000,
        "decision": "REVIEW_REQUIRED",
    }]
})
client, _, _ = _client(config=PlatrixaConfig(rule_pack=threshold_pack))
result = client.process(VALID_INPUT)
check("H4 threshold rule downgrades VERIFIED → REVIEW_REQUIRED",
      result.status == REVIEW_REQUIRED, result.status)
check("H5 downgrade evidence is on the public result",
      any(rec.get("rule_id") == "review_over_10k" and rec.get("result") == "FAIL"
          for rec in result.rule_evidence))

try:
    Platrixa(config=PlatrixaConfig(rule_pack=_write_yaml({"rules": [{"id": "bad", "type": "nope"}]})))
    check("H6 malformed pack fails closed at construction", False, "no error raised")
except Exception as exc:
    check("H6 malformed pack fails closed at construction", True, type(exc).__name__)

print("\n=== I. RuleHooks through the public interface ===")
client, _, _ = _client(config=PlatrixaConfig(rule_hooks=[_PassHook()]))
result = client.process(VALID_INPUT)
check("I1 pass hook preserves kernel VERIFIED", result.status == VERIFIED, result.status)
check("I2 pass hook evidence recorded",
      any(rec.get("rule_id") == "pass_hook" and rec.get("result") == "PASS"
          for rec in result.rule_evidence))

client, _, _ = _client(config=PlatrixaConfig(rule_hooks=[_FailHook()]))
result = client.process(VALID_INPUT)
check("I3 fail hook downgrades to REVIEW_REQUIRED", result.status == REVIEW_REQUIRED,
      result.status)

try:
    Platrixa(config=PlatrixaConfig(rule_hooks=[object()]))
    check("I4 non-hook object rejected at construction", False, "no error")
except Exception:
    check("I4 non-hook object rejected at construction", True)

print("\n=== J/K/L. State authority through the public interface ===")
# Metadata-based smuggle: sanitized hint, PASS outcome → no state change,
# kernel-decided VERIFIED preserved, rejection recorded in evidence.
client, _, _ = _client(config=PlatrixaConfig(rule_hooks=[_SmuggledHintHook()]))
result = client.process(VALID_INPUT)
check("J1 metadata smuggle cannot change the kernel-decided state",
      result.status == VERIFIED, result.status)
hint_rec = [rec for rec in result.rule_evidence if rec.get("rule_id") == "smuggled_hint_hook"]
check("J2 smuggled hint rejected in evidence, not honored",
      bool(hint_rec) and hint_rec[0].get("result") == "PASS"
      and hint_rec[0].get("metadata", {}).get("decision_hint_rejected") == "VERIFIED",
      json.dumps(hint_rec)[:100])
# Kwarg-based smuggle: RuleDecision contract rejects the unknown field →
# engine fail-closes to ERROR → downgrade (stricter than sanitization).
client, _, _ = _client(config=PlatrixaConfig(rule_hooks=[_SmuggledKwargHook()]))
result = client.process(VALID_INPUT)
kwarg_rec = [rec for rec in result.rule_evidence if rec.get("rule_id") == "smuggled_kwarg_hook"]
check("J3 kwarg smuggle fails closed (ERROR → downgrade)",
      result.status == REVIEW_REQUIRED and bool(kwarg_rec)
      and kwarg_rec[0].get("result") == "ERROR",
      f"status={result.status}")
client, _, _ = _client(config=PlatrixaConfig(
    rule_hooks=[_FailHook(), _SmuggledHintHook()]))
result = client.process(VALID_INPUT)
check("K1 PASS cannot upgrade a downgraded state",
      result.status == REVIEW_REQUIRED, result.status)
for hook, label in ((_UnavailableHook(), "UNAVAILABLE"), (_ErrorHook(), "ERROR")):
    client, _, _ = _client(config=PlatrixaConfig(rule_hooks=[hook]))
    result = client.process(VALID_INPUT)
    check(f"L1 {label} hook cannot become success",
          result.status == REVIEW_REQUIRED, result.status)

print("\n=== M. Existing Kernel behavior unchanged ===")
direct = Kernel(model_provider=StubProvider()).process(VALID_INPUT)
client, _, _ = _client()
via_public = client.process(VALID_INPUT)
check("M1 facade status equals direct Kernel status",
      direct.status == via_public.status, via_public.status)
check("M2 accounting result identical",
      json.dumps(direct.accounting_result, sort_keys=True, default=str)
      == json.dumps(via_public.accounting, sort_keys=True, default=str))
empty_kernel = Kernel(model_provider=StubProvider()).process("   ")
client, _, _ = _client()
try:
    client.process("   ")
    check("M3 empty-input contract preserved", False, "no InputError")
except InputError:
    check("M3 empty-input contract preserved", True,
          f"kernel direct={empty_kernel.status} (facade raises earlier, same fail-closed)")

print("\n=== N. Phase 10 authority invariants remain enforced ===")
from backend.rules.contract import RuleDecision as RD  # noqa: E402
try:
    RD(rule_id="x", outcome="VERIFIED")
    check("N1 RuleDecision cannot express VERIFIED", False, "constructed!")
except Exception:
    check("N1 RuleDecision cannot express VERIFIED", True)
from backend.rules.engine import RuleEngine  # noqa: E402
check("N2 RuleEngine exists and is kernel-side only",
      callable(RuleEngine.evaluate) and "RuleEngine(" not in facade_src)

# ---------------------------------------------------------------------------
print("\n" + "=" * 78)
print(f"RESULT: {'PASS' if _FAIL == 0 else 'FAIL'} — {_PASS}/{_PASS + _FAIL} checks passed")
print("=" * 78)
sys.exit(0 if _FAIL == 0 else 1)
