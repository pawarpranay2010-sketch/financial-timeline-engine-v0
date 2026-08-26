# Sprint 38 — Deployed UI Runtime Truth Audit

## Classification: **ROOT CAUSE IDENTIFIED — Category C (Execution-Path Mismatch)**

---

## 1. Root Cause

**`project_student_result()` strips the `problem_engine` key from the projection, causing the application to always route through the old single-transaction renderer — even though the Sprint 36/37 whole-problem renderer exists and is correct.**

### Evidence

```
BEFORE projection:  problem_engine in single_result = True
AFTER projection:   problem_engine in projection   = False
_is_multi_tx_problem(projection)                     = False
ACTUAL RENDERING PATH:                              single-transaction (old UI)
EXPECTED RENDERING PATH:                            whole-problem timeline (Sprint 36/37)
```

### First Stage Where the Issue Occurs

**`backend/maths/fyjc_ui_contract.py` line 830 — `project_student_result()`**

This function constructs a **new dict** with only these hardcoded keys:
```python
return {
    "status", "status_label", "headline", "tone", "summary",
    "understanding", "journal", "verification", "why", "calculation",
    "confidence_gate", "gate_resolution", "why_not", "next_action", "result"
}
```

The `problem_engine` key — which `_compute_projection` attaches at line 1546 of `fyjc_student_ui.py` — is **not included** in this whitelist. It is silently dropped.

### Second Stage Where the Issue Manifests

**`backend/fyjc_student_ui.py` — `_is_multi_tx_problem()`** (line 1397)

```python
def _is_multi_tx_problem(projection):
    return bool(projection.get("problem_engine"))  # Always False!
```

Because `problem_engine` was stripped, this always returns `False`, and the rendering path at line 2420:

```python
if _is_multi_tx_problem(projection):
    _render_problem_workflow(question, projection)  # Sprint 36/37 whole-problem UI
    ...
    return
```

…is **never reached**. The application falls through to:

```python
if projection.get("status") == "VERIFIED":
    _render_verified_result(projection)  # Single-transaction UI
```

### Third Stage — The Sprint 36/37 Code Is Present But Dead

The following functions **exist and are correct** in `fyjc_student_ui.py`:
- `_render_problem_workflow()` — all-visible expandable timeline (Sprint 36)
- `_render_problem_timeline()` — chronological transaction overview
- `_render_tx_detail()` — transaction-specific card with journal, calc, Why?
- `_relevant_calc_records()` — Sprint 37 calculation relevance filter
- `_render_problem_result()` — final ledger and safety summary

But they are **never executed** because the routing gate (`_is_multi_tx_problem`) always returns `False`.

---

## 2. Diagnostic Findings

| Check | Result |
|-------|:------:|
| Local HEAD matches remote HEAD | ✅ 0fc7e23 |
| Module path correct | ✅ `backend/fyjc_student_ui.py` |
| No duplicate modules | ✅ 1 file found |
| Sprint 36 renderer exists in source | ✅ |
| No old step-by-step navigation | ✅ |
| Sprint 37 `_relevant_calc_records` exists | ✅ |
| **`project_student_result` preserves `problem_engine`** | ❌ **CRITICAL** |
| **`_is_multi_tx_problem` returns True when `problem_engine` present** | ❌ |
| Engine produces 13 transactions | ✅ |
| **Full pipeline preserves `problem_engine`** | ❌ |
| **Full pipeline routes to whole-problem renderer** | ❌ |
| Integrity violations | ✅ 0 |
| Sprint 37 filter suppresses irrelevant calcs | ✅ |

---

## 3. Runtime Identity Audit

| Field | Value |
|-------|-------|
| Local commit | `0fc7e23052668e675ba7fb481e672fa57d12c951` |
| Remote commit | `0fc7e23052668e675ba7fb481e672fa57d12c951` |
| Branch | `main` |
| Module path | `/home/daytona/codebase/backend/fyjc_student_ui.py` |
| Duplicate modules | None |
| Total lines | 2,441 |

---

## 4. Python Import Audit

| Item | Result |
|------|--------|
| Module imported | `backend.fyjc_student_ui` |
| `__file__` | `/home/daytona/codebase/backend/fyjc_student_ui.py` |
| Duplicate files | None |
| Stale copies | None |
| Package shadowing | None |

---

## 5. Renderer Execution Trace

```
render_fyjc_student_ui()
  → _render_15i_student_workspace()
    → _compute_projection(question)
      → process_problem(question)         # 13 transactions
      → project_student_result(result)    # ⚠️ problem_engine STRIPPED here
    → _is_multi_tx_problem(projection)    # Always False
    → _render_verified_result()           # Single-transaction UI ← ACTUAL
    ✗ _render_problem_workflow()          # Whole-problem UI ← NEVER REACHED
```

---

## 6. 13-Transaction Problem — Engine Output

| TX | Status | Journal Lines | Calc Records |
|:--:|:------:|:-------------:|:------------:|
| T1 | REVIEW_REQUIRED | 0 | 0 |
| T2 | VERIFIED | 2 | 2 |
| T3 | VERIFIED | 2 | 2 |
| T4 | VERIFIED | 2 | 2 |
| T5 | REVIEW_REQUIRED | 0 | 3 |
| T6 | VERIFIED | 2 | 3 |
| T7 | VERIFIED | 2 | 3 |
| T8 | VERIFIED | 4 | 4 |
| T9 | VERIFIED | 4 | 4 |
| T10 | BLOCKED | 0 | 0 |
| T11 | VERIFIED | 2 | 3 |
| T12 | VERIFIED | 2 | 3 |
| T13 | VERIFIED | 2 | 3 |

**Engine output is correct.** The defect is solely in the projection → routing layer.

---

## 7. Decision Tree Classification

### **Category C — Execution-Path Mismatch**

- The deployed application **is running** the pushed Sprint 36/37 code.
- The Sprint 36/37 renderer **exists** in the codebase.
- But `project_student_result()` strips the `problem_engine` key.
- So `_is_multi_tx_problem()` always returns `False`.
- The application **always routes through the old single-transaction renderer**.
- The whole-problem UI is **dead code** — present but unreachable.

---

## 8. Minimal Fix Specification (for Sprint 39)

**The smallest correct fix** is to modify `project_student_result()` in `backend/maths/fyjc_ui_contract.py` to pass through any extra keys from the input `result` dict:

```python
def project_student_result(result, question, gate_resolution=None):
    ...
    projection = {
        "status": status,
        "status_label": ...,
        # ... existing keys ...
    }
    # Pass through problem_engine and any other extra keys
    for key in ("problem_engine",):
        if key in result:
            projection[key] = result[key]
    return projection
```

This is a **+3 LOC change** in 1 file. No architecture expansion needed.

---

## 9. Safety

```
INCORRECT_VERIFIED:    0
Integrity violations:  0
Mutation violations:   0
```

---

## 10. Architecture Impact

```
Production files modified:  0  (Sprint 38 is diagnostic only)
Production LOC delta:       0
New modules: 0 | New classes: 0 | New dependencies: 0
Kernel modified: NO | Splitter modified: NO
```

**Diagnostic file added:** `scripts/fte_fyjc_38_ui_runtime_audit.py` (+218 LOC)

---

## 11. Recommended Next Action

**Sprint 39 — Fix the execution-path mismatch** by adding a 3-line passthrough in `project_student_result()` to preserve the `problem_engine` key. This is the smallest possible fix that restores the Sprint 36/37 whole-problem UI.
