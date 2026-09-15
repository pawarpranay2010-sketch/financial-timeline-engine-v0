# Platrixa — Rule Authoring Guide (rules.md)

**Audience:** developers writing business rules for Platrixa's rule boundary.
**Applies to:** `backend/rules/` (Phase 10 contract; documentation completed in Phase 17).
**Golden principle:** *if a simple rule can be expressed declaratively, we do not
write a hundred lines of code to express it.*

---

## 1. What a rule is

A rule is a **developer constraint** evaluated deterministically AFTER the
Kernel's deterministic accounting has produced a success state. Rules express
business policy — "high-value transactions need review", "payment mode must be
one of these", "this field is mandatory under our policy" — as constraints ON
an already-verified accounting result.

A rule sees a read-only view of the request (`RuleContext`: `request_id`,
`raw_input`, and the validated 18-field interpretation) and returns a
`RuleDecision`.

## 2. What a rule is NOT

A rule is **not**:

- an accounting engine — it never computes debits, credits, journals, or
  balances;
- a second interpretation pass — it never re-parses the student text;
- a state authority — it can never produce, upgrade, or restore the final
  state `VERIFIED` (or any terminal state);
- a repair mechanism — a failed rule can only make a result *more* blocked,
  never less.

There is deliberately **no field, channel, or method** in the rule contract
(`RuleDecision` is frozen and validated at construction) by which a rule can
request a final state. The runtime owns states; rules own constraints.

## 3. How YAML rules work

A rule pack is a YAML file with a `rules:` list. Three primitives exist
(Phase 10C) — that is the whole declarative surface, deliberately:

```yaml
rules:
  # required: a field must be present and non-empty
  - id: credit_requires_counterparty
    type: required
    field: parties
    decision: REVIEW_REQUIRED
    message: "Credit transactions must name a counterparty under this policy."

  # allowed_values: a field's value must be one of an allowed set
  # (an absent field is not this rule's concern and passes)
  - id: payment_mode_policy
    type: allowed_values
    field: payment_method_enum
    values: [CASH, CREDIT, BANK, CHEQUE, UPI, UNKNOWN]
    decision: REVIEW_REQUIRED

  # threshold: a numeric comparison on a (dotted-path) field
  - id: high_value_review
    type: threshold
    field: amounts.0.value
    operator: ">"
    value: 100000
    decision: REVIEW_REQUIRED
    message: "Transactions above Rs.1,00,000 require manual review."
```

Each rule carries:

| key        | meaning                                                              |
|------------|----------------------------------------------------------------------|
| `id`       | unique rule identifier (appears in evidence)                         |
| `type`     | `required` \| `allowed_values` \| `threshold`                        |
| `field`    | dotted path into the interpretation (e.g. `amounts.0.value`)          |
| `decision` | the downgrade target on failure: `REVIEW_REQUIRED` or `BLOCKED` only |
| `message`  | optional human-readable failure message (recorded in evidence)       |
| `values` / `operator` + `value` | payload for `allowed_values` / `threshold`     |

Field paths resolve into nested mappings and lists (`amounts.0.value` is the
first amount's value). Unknown types, unknown decisions, unknown operators,
duplicate ids, or malformed structures **fail closed at load time** — a
malformed pack prevents the Kernel from being constructed at all.

## 4. When Python hooks are justified

Use a Python hook (`RuleHook`) **only** when the constraint genuinely needs
what YAML cannot express:

- complex computation (e.g. multi-party exposure aggregation),
- external data (an ERP lookup, a sanctions list — with the dependency
  surfaced as `UNAVAILABLE` when absent),
- multi-step enterprise logic,
- domain logic that no combination of the three primitives can express.

If your hook is doing string equality, presence checks, or numeric
comparison, stop and write YAML instead. A hook that could be YAML is a
review finding.

Hook contract (the whole protocol):

```python
from backend.rules.contract import RuleDecision, RuleContext, RuleHook

class ExposureCapRule(RuleHook):
    rule_id = "counterparty_exposure_cap"

    def validate(self, context: RuleContext) -> RuleDecision:
        # complex logic allowed HERE — but the return type is not negotiable
        over = self._exposure_exceeds_cap(context.interpretation)
        return RuleDecision(
            rule_id=self.rule_id,
            outcome="FAIL" if over else "PASS",
            message="counterparty exposure exceeds policy cap" if over else "",
            metadata={"decision_hint": "REVIEW_REQUIRED"},
        )
```

The engine wraps every hook in a fail-closed guard:

- hook raises → `ERROR` decision (blocks `VERIFIED`),
- hook returns anything that is not a `RuleDecision` → `ERROR` decision,
- external dependency missing → return `UNAVAILABLE` (blocks `VERIFIED`).

## 5. Why rules cannot return VERIFIED

`VERIFIED` means *the deterministic accounting established this result and no
constraint objects*. A rule is a constraint producer; granting it the power
to declare `VERIFIED` would make developer opinion outrank deterministic
arithmetic. Concretely:

- `RuleDecision` has no status field — only `outcome`
  (`PASS | FAIL | UNAVAILABLE | ERROR`) and a `decision_hint` metadata key;
- the loader rejects any decision hint outside `{REVIEW_REQUIRED, BLOCKED}`
  at load time;
- even if a hook smuggles `"VERIFIED"` into `decision_hint` metadata, the
  engine's `_sanitize_hint` rebuilds the decision with the fail-safe
  `REVIEW_REQUIRED` hint and records the rejected value in evidence — the
  smuggled value reaches neither the state change nor the evidence trail.

## 6. How downgrade behavior works

Rules run only on accounting-produced success states, and evaluation is
**downgrade-only**:

- `PASS` → no change;
- a blocking outcome (`FAIL` / `UNAVAILABLE` / `ERROR`) downgrades the
  current state to the decision's hint:
  - `VERIFIED` → `REVIEW_REQUIRED` (or `BLOCKED` per hint),
  - `REVIEW_REQUIRED` → `BLOCKED` (when a later rule's hint is `BLOCKED`);
- a downgraded state never moves back up — no composition of decisions can
  produce `VERIFIED`.

## 7. What UNAVAILABLE means

`UNAVAILABLE` = the rule could not run because a dependency it needs is
absent (external service down, dataset not installed). It is treated as
**blocking**: a policy that cannot be checked is not a policy that passed.
External dependency failure is never success. Emit `UNAVAILABLE` rather than
`PASS` when you cannot actually evaluate.

## 8. What ERROR means

`ERROR` = the rule itself failed (raised an exception, returned the wrong
type, or the declarative evaluator faulted). The engine produces this
decision automatically inside the fail-closed guard — you should rarely
construct it yourself. Like `UNAVAILABLE`, it blocks `VERIFIED` and is
recorded in evidence with the failure reason.

## 9. How rule evidence is recorded

Every executed rule produces a frozen `RuleResult` in `KernelResult.rule_evidence`:

```json
{"rule_id": "high_value_review", "source": "yaml", "result": "FAIL",
 "message": "Transactions above Rs.1,00,000 require manual review.",
 "metadata": {"rule_type": "threshold", "field": "amounts.0.value",
              "decision_hint": "REVIEW_REQUIRED"}}
```

`source` is `"yaml"` or `"python_hook"`. The evidence is bound into the
Kernel's `ExecutionEvidence` chain (`rule_pack_hash` = sha256 of the pack
file bytes; `rule_evidence` = these records), so a result always carries
proof of *which* pack executed and *what* each rule decided.

## 10. How to avoid writing unnecessary custom code

Ask, in order:

1. Can this be `required`? (field must exist / be non-empty)
2. Can this be `allowed_values`? (value must be in a fixed set)
3. Can this be `threshold`? (numeric comparison on one field)
4. Only then: does it genuinely need computation/data/multi-step logic?

If you reach (4), keep the hook minimal and push every sub-check that *could*
be declarative back into YAML rules — the hook should contain only the part
that cannot be expressed declaratively.

## 11. Examples

**Simple YAML rule** — required counterparty on every transaction:

```yaml
rules:
  - id: counterparty_required
    type: required
    field: parties
    decision: REVIEW_REQUIRED
    message: "A named counterparty is required by policy."
```

**Simple threshold rule** — review large cash payments:

```yaml
rules:
  - id: large_cash_review
    type: threshold
    field: amounts.0.value
    operator: ">="
    value: 50000
    decision: REVIEW_REQUIRED
    message: "Cash transactions of Rs.50,000 or more require review."
```

**Allowed-values rule** — restrict payment modes to policy-approved channels:

```yaml
rules:
  - id: approved_payment_channels
    type: allowed_values
    field: payment_method_enum
    values: [CASH, BANK, CHEQUE, NEFT, UPI, UNKNOWN]
    decision: REVIEW_REQUIRED
```

**Required-field rule** — references policy:

```yaml
rules:
  - id: invoice_reference_required
    type: required
    field: references
    decision: REVIEW_REQUIRED
    message: "This policy requires an invoice/reference on every transaction."
```

**Complex Python hook** — genuinely non-declarative (external exposure data):

```python
class GroupExposureRule(RuleHook):
    """Cap exposure per counterparty GROUP using the firm's ERP lookup.
    Aggregates today's postings across entities — cannot be one field check."""

    rule_id = "group_exposure_cap"

    def validate(self, context: RuleContext) -> RuleDecision:
        try:
            exposure = erp_client.group_exposure(self._party(context))  # external
        except DependencyUnavailable:
            return RuleDecision(self.rule_id, "UNAVAILABLE",
                                message="ERP exposure service unavailable",
                                metadata={"decision_hint": "REVIEW_REQUIRED"})
        if exposure > self._CAP:
            return RuleDecision(self.rule_id, "FAIL",
                                message=f"group exposure {exposure} exceeds cap",
                                metadata={"decision_hint": "REVIEW_REQUIRED"})
        return RuleDecision(self.rule_id, "PASS")
```

## 12. Bad vs good

The requirement: *"payment method must be declared — flag transactions that
do not state one."*

**BAD — a 100-line hook for a one-field check:**

```python
class PaymentModeRule(RuleHook):
    rule_id = "payment_mode_required"

    def validate(self, context: RuleContext) -> RuleDecision:
        # ~100 lines of careful but unnecessary machinery:
        interp = context.interpretation
        pm = interp.get("payment_method_enum") or interp.get("payment_method") or ""
        legacy = interp.get("payment_method") or ""
        if pm == "" and legacy == "":
            return RuleDecision(self.rule_id, "FAIL",
                                message="payment mode not stated",
                                metadata={"decision_hint": "REVIEW_REQUIRED"})
        if str(pm).strip().upper() in ("", "UNKNOWN") and str(legacy).strip().upper() in ("", "UNKNOWN"):
            return RuleDecision(self.rule_id, "FAIL",
                                message="payment mode unknown",
                                metadata={"decision_hint": "REVIEW_REQUIRED"})
        # ... edge cases, string munging, more branches ...
        return RuleDecision(self.rule_id, "PASS")
```

**GOOD — the same constraint, declarative (7 lines):**

```yaml
rules:
  - id: payment_mode_declared
    type: required
    field: payment_method_enum
    decision: REVIEW_REQUIRED
    message: "Payment mode must be declared under this policy."
```

Same outcome, same evidence trail, load-time validation, zero code to
review, test, or break.

---

## Rule complexity principle

> **Use the smallest rule representation capable of expressing the
> constraint.**

If a declarative rule is growing absurdly large to express one idea, that is
evidence a new *primitive* may be needed — propose one through the Phase 10
contract (a fourth loader type), not a pile of hooks. Do not add primitives
speculatively: the three proven ones cover the overwhelming majority of
business policy.

## Loading

```python
from platrixa import Platrixa, PlatrixaConfig

client = Platrixa(PlatrixaConfig(rule_pack="examples/rules/platrixa_rules.yaml"))
```

Server deployments set `PLATRIXA_RULE_PACK_PATH`; API clients can never
submit rule packs or hook code — packs come exclusively from trusted server
configuration (Phase 15 invariant, unchanged).
