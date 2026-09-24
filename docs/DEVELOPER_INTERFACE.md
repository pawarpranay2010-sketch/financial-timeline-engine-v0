# Platrixa Developer Interface

The public interface is a **thin boundary over the Platrixa Kernel**.

A developer uses one import, one client, one call. Everything else — model
interpretation, schema validation, grounding, deterministic accounting,
optional rules, state authority — stays inside the Kernel, exactly as it
runs in the Platrixa production service. This document describes how to use
that interface. It does **not** claim enterprise readiness, hosted-service
readiness, API security, or authentication; those are future concerns.

---

## 1. What Platrixa is

Platrixa turns natural-language financial information into a
deterministic, validated financial-semantic result:

```
raw transaction text
    ↓  (Kernel.process — the single authoritative path)
model interpretation (Qwen2.5-1.5B + specialist LoRA, local or remote)
    ↓
schema validation (18-field structured interpretation contract)
    ↓
grounding / verification gate (fail-closed)
    ↓
deterministic accounting kernel (journal entries, ledger effects)
    ↓
optional developer rules (YAML pack + Python hooks; downgrade-only)
    ↓
final state + evidence (VERIFIED / REVIEW_REQUIRED / BLOCKED, plus fail-closed
failure states such as UNSUPPORTED_TRANSACTION)
```

The developer-facing layer (`platrixa/`) configures the Kernel and projects
its result. It contains **no** accounting, grounding, model, rule-engine, or
persistence logic of its own.

## 2. Installation / setup

The package lives at the repository root (no separate install needed when
working inside the repo):

```bash
git clone <this repository>
cd financial-timeline-engine-v0
pip install -r requirements.txt   # runtime dependencies (torch only needed for local inference)
```

The `platrixa` package imports with no model, no network, and no database.
Model weights load lazily, only when a transaction is actually processed.

Python 3.10+ is required (matches the production runtime).

## 3. Minimal Python example

```python
from platrixa import Platrixa

client = Platrixa()  # provider selection: auto (see §8)
result = client.process("Purchased furniture for cash ₹15,000")

print(result.status)                  # VERIFIED
print(result.status_label)            # Verified
print(result.interpretation)          # 18-field structured interpretation
print(result.accounting)              # deterministic accounting result
print(result.issues)                  # any failure reasons (empty on success)
print(result.rule_evidence)           # structured evidence from rules (if configured)
print(result.to_dict())               # stable JSON-safe serialization
```

Error behavior in this minimal flow:

```python
from platrixa import InputError, ProviderError

try:
    result = client.process("   ")
except InputError as exc:
    ...  # empty input — caller error, Kernel never ran

# Provider failures raise ProviderError with the original cause preserved.
```

## 4. CLI example

```bash
# Direct text
python3 -m platrixa process --text "Purchased furniture for cash ₹15,000" --pretty

# JSON file (a bare string, or {"raw_input": "..."})
python3 -m platrixa process examples/developer_interface/transaction.json

# With a rule pack and a Python hook
python3 -m platrixa process tx.json \
    --rules examples/rules/platrixa_rules.yaml \
    --hook examples.rules.custom_hooks:HolidayBudgetRule

python3 -m platrixa --version
```

Output is machine-readable JSON (`result.to_dict()`), one transaction per
invocation. Exit codes are a deterministic view of the Kernel state:

| Exit | Meaning |
|---|---|
| `0` | VERIFIED |
| `1` | REVIEW_REQUIRED |
| `2` | BLOCKED |
| `3` | any fail-closed failure (MODEL_UNAVAILABLE, VALIDATION_FAILED, GROUNDING_FAILED, FORBIDDEN_OUTPUT, UNSUPPORTED_TRANSACTION) or input/provider/config error |

## 5. Input contract

The canonical input is the **raw transaction string** — the same contract
the production HTTP API accepts:

- a Python `str` (or CLI text / JSON string / JSON object with `"raw_input"`)
- 1–2000 characters after stripping
- sent to the Kernel verbatim; no preprocessing, no competing schema

Non-string, empty, or oversized input raises `InputError` before the Kernel
runs. The interface does not fake any other input capability.

## 6. Result contract

`client.process(...)` returns a `PlatrixaResult` — a read-only projection of
the authoritative `KernelResult` (recomputed by nothing):

| Field | Meaning |
|---|---|
| `status` | terminal state (see §7) — decided by the Kernel only |
| `status_label` | human-readable label ("Verified", "Review Required", …) |
| `success` | True ONLY when `status` is `VERIFIED`; `false` for `REVIEW_REQUIRED`, `BLOCKED`, and every failure state |
| `interpretation` | schema-validated 18-field candidate (or None) |
| `accounting` | deterministic accounting result (debit/credit lines, …) |
| `issues` | failure reasons and model-level issues |
| `grounding_issues` | grounding/verification issues when applicable |
| `rule_evidence` | structured per-rule evidence (Phase 10 boundary) |
| `request_id` | stable per-request identifier |
| `metadata` | provider/model identity + non-secret diagnostics |
| `next_action` | suggested user guidance for non-verified states |
| `to_dict()` | deterministic JSON-safe dict (Decimals → exact strings) |

`to_dict()` never contains Python reprs, memory addresses, credentials, or
stack traces. On a normal (non-crash) run, failures are represented as
states and evidence, not exceptions.

## 7. State meanings

| State | Meaning | `success` |
|---|---|---|
| `VERIFIED` | transaction fully understood, grounded, and deterministically accounted | ✅ |
| `REVIEW_REQUIRED` | valid but ambiguous (e.g. missing party/payment mode) — needs human confirmation | ❌ |
| `BLOCKED` | blocked by deterministic safety/rule policy | ❌ |
| `MODEL_UNAVAILABLE` | model/provider not reachable or not loadable (fail-closed) | ❌ |
| `VALIDATION_FAILED` | model output failed the schema contract (fail-closed) | ❌ |
| `GROUNDING_FAILED` | interpretation not supported by the input (fail-closed) | ❌ |
| `FORBIDDEN_OUTPUT` | model emitted forbidden accounting fields (fail-closed) | ❌ |
| `UNSUPPORTED_TRANSACTION` | outside the currently supported transaction semantics | ❌ |

**Authority:** only the deterministic accounting flow inside
`Kernel.process` can produce `VERIFIED`. Developer rules (below) can only
*downgrade* a success state; the public interface cannot inject or override
any state.

## 8. Provider configuration

`PlatrixaConfig` is a frozen dataclass; pass it at client construction:

```python
from platrixa import Platrixa, PlatrixaConfig

# auto — defers to the runtime's own selection (default):
#   PLATRIXA_MODEL_ENDPOINT_URL set → remote provider
#   unset                           → local Hugging Face provider
client = Platrixa(PlatrixaConfig(provider="auto"))

# local — explicit local inference (downloads/loads the pinned model)
client = Platrixa(PlatrixaConfig(provider="local"))

# remote — explicit remote inference
client = Platrixa(PlatrixaConfig(
    provider="remote",
    endpoint_url="https://your-endpoint.example/run",   # or env PLATRIXA_MODEL_ENDPOINT_URL
    transport="gradio",   # use the HF Space transport
))
```

Model identity stays pinned by default to the production artifacts
(base `Qwen/Qwen2.5-1.5B-Instruct` @ `989aa798…`, LoRA adapter
`Pranay-20/platrixa-fyjc-specialist-v0.1` @ `b5c0a37…`). Supply
`provider_config` only if you deliberately want different artifacts.

No API keys are introduced by the interface. Existing environment
variables keep working (`PLATRIXA_MODEL_ENDPOINT_URL`,
`PLATRIXA_MODEL_ENDPOINT_TOKEN`, `PLATRIXA_MODEL_TIMEOUT`,
`PLATRIXA_MODEL_TRANSPORT`, `PLATRIXA_FYJC_*`, `HF_TOKEN` for local
gated downloads). Secrets are never echoed in results or CLI output.

## 9. RulePack configuration

Declarative YAML rules (Phase 10 format — primitives: `required`,
`allowed_values`, `threshold`):

```python
client = Platrixa(PlatrixaConfig(rule_pack="examples/rules/platrixa_rules.yaml"))
result = client.process("Purchased furniture for cash ₹15,000")
for record in result.rule_evidence:
    print(record["rule_id"], record["result"], record["message"])
```

Guarantees: YAML is parsed with `safe_load` (no code execution); unknown
rule types, malformed rules, and any `decision: VERIFIED` are rejected at
client construction (fail closed); rule outcomes can only downgrade
`VERIFIED → REVIEW_REQUIRED/BLOCKED`.

## 10. RuleHook configuration

Programmatic rules implement the `RuleHook` protocol (Phase 10):

```python
from backend.rules.contract import RuleContext, RuleDecision, RuleHook, OUTCOME_FAIL
from platrixa import Platrixa, PlatrixaConfig

class HighValueReview(RuleHook):
    rule_id = "high_value_review"

    def validate(self, context: RuleContext) -> RuleDecision:
        amount = _extract_amount(context.interpretation)  # your policy
        if amount is not None and amount > 100_000:
            return RuleDecision(rule_id=self.rule_id, outcome=OUTCOME_FAIL,
                                message="amount above policy limit")
        return RuleDecision(rule_id=self.rule_id, outcome="PASS")

client = Platrixa(PlatrixaConfig(rule_hooks=[HighValueReview()]))
```

Hook contract: receive a controlled read-only `RuleContext`; return a
`RuleDecision` (PASS/FAIL/UNAVAILABLE/ERROR). A hook cannot express
VERIFIED — the runtime sanitizes any attempt. Hook exceptions and
unavailable dependencies fail closed (never success).

## 11. Error behavior

| Situation | Behavior |
|---|---|
| non-string / empty / >2000-char input | `InputError` (before Kernel runs) |
| provider raises during processing | `ProviderError` (original cause preserved via `__cause__`) |
| malformed config (bad provider/transport/hook shape) | `ValueError` at construction |
| malformed YAML rule pack | `RulePackError` at construction (client refuses to build) |
| provider unreachable at process time | `MODEL_UNAVAILABLE` result state (+ CLI exit 3) |
| schema / grounding / forbidden-output failures | corresponding fail-closed result states |
| persistence | not performed by the public interface — it is a deployment-level concern (the HTTP service persists; see architecture boundary) |

Operational failures are **never** converted into success and never
silently swallowed.

## 12. Architecture boundary

```
You (developer)
    ↓
platrixa  (thin public boundary: config + result projection)
    ↓
Kernel.process(...)          ← the single authoritative runtime
    ├── ModelProvider        (local / remote interpretation)
    ├── schema validation    (18-field contract)
    ├── grounding gate       (fail-closed)
    ├── accounting kernel    (deterministic truth — owns VERIFIED)
    ├── rule boundary        (your YAML rules + hooks; downgrade-only)
    └── KernelResult         → PlatrixaResult → you
```

The public layer depends on the Kernel — not on the Kernel's internal
orchestration components. There is exactly one accounting engine, one
grounding implementation, one provider abstraction, and one state
authority; the interface adds configuration and projection, nothing else.

The production HTTP service (`api/` + `frontend/` + persistence) is a
separate consumer of the same Kernel and is unaffected by this interface.

---

*Phase 12 deliverable. Limitations: no authentication, no hosted API, no
billing, no persistence from the programmatic interface, no async API, no
batch processing. These are deliberate non-goals for this phase.*
