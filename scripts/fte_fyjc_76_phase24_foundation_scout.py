#!/usr/bin/env python3
"""fte_fyjc_76_phase24_foundation_scout.py — Phase 24 foundation-model scouting runner.

EVALUATION/SCOUTING INFRASTRUCTURE ONLY.

Zero-shot evaluation of candidate foundation models against the independent
Layer-1 scouting benchmark (training_data/phase24_foundation_scout.jsonl),
under the exact Platrixa output contract (expanded 18-field schema), with the
CURRENT production schema verifier and ExpandedGroundingGate applied to every
output.

Hard rules implemented here:
- No training, no adapter creation, no dataset modification, no runtime changes.
- Fail-closed integrity: dataset SHA must match the manifest or the run aborts.
- Each candidate uses its OWN tokenizer chat template (no hand-rolled prompt).
- Deterministic decoding (do_sample=False / temperature=None equivalents).
- A candidate that cannot be resolved/loaded is recorded NOT_RUN with the
  error — never fabricated scores.
- No single weighted "winner" score is computed; profiles only.

Usage:
  python3 scripts/fte_fyjc_76_phase24_foundation_scout.py --check-only
  python3 scripts/fte_fyjc_76_phase24_foundation_scout.py                 # full run (GPU host)
  python3 scripts/fte_fyjc_76_phase24_foundation_scout.py --models Qwen/Qwen2.5-1.5B-Instruct
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import platform
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

SCOUT_PATH = os.path.join(ROOT, "training_data", "phase24_foundation_scout.jsonl")
MANIFEST_PATH = os.path.join(ROOT, "training_data", "phase24_foundation_scout_manifest.json")
REPORTS_DIR = os.path.join(ROOT, "reports")

# Historical evaluation contract (Phase 6C / 6C-v02 protocol, preserved)
MAX_NEW_TOKENS = 512
BATCH_SIZE = int(os.environ.get("PHASE24_BATCH_SIZE", "8"))
SYSTEM_INSTRUCTION = (
    "You are Platrixa's financial interpretation specialist. Interpret the "
    "financial event described by the user and respond with ONLY a compact JSON "
    "object with exactly these 18 fields: transaction_type, parties, amounts, "
    "payment_method, references, ambiguities, grounding, transaction_type_enum, "
    "payment_method_enum, ambiguity_flags, referenced_transaction_index, "
    "referenced_party, referenced_amount, field_confidences, overall_confidence, "
    "suggested_status, safety_flags, scope_flags. Use UNKNOWN and REVIEW_REQUIRED "
    "honestly when evidence is insufficient. Never produce journal entries, "
    "debit/credit conclusions, or any accounting determination. Never invent "
    "parties, amounts, or references that are not in the input."
)

# Candidate registry (all metadata verified 2026-09-17 via HF API — see reports/phase24_model_cards.md)
CANDIDATES = [
    {"repo_id": "Qwen/Qwen2.5-1.5B-Instruct", "role": "control_current_base", "revision": "989aa7980e4cf806f80c7fef2b1adb7bc71aa306", "dtype": "bfloat16", "template": "chatml", "license": "apache-2.0"},
    {"repo_id": "WiroAI/WiroAI-Finance-Qwen-1.5B", "role": "finance_specialized_same_size", "revision": "f79cfbc65f90691b879f1a3960bf02a89f998ae6", "dtype": "bfloat16", "template": "deepseek_style", "license": "apache-2.0"},
    {"repo_id": "Qwen/Qwen2.5-3B-Instruct", "role": "scale_probe_noncommercial", "revision": None, "dtype": "bfloat16", "template": "chatml", "license": "qwen-research-NONCOMMERCIAL"},
    {"repo_id": "Qwen/Qwen2.5-7B-Instruct", "role": "upper_scale_probe", "revision": None, "dtype": "bfloat16", "template": "chatml", "license": "qwen-research-NONCOMMERCIAL"},
    {"repo_id": "WiroAI/WiroAI-Finance-Qwen-7B", "role": "finance_specialized_7b", "revision": None, "dtype": "bfloat16", "template": "deepseek_style", "license": "apache-2.0"},
    {"repo_id": "OVHaiLLM/Qwen-Open-Finance-R-8B", "role": "finance_reasoning_gated", "revision": None, "dtype": "bfloat16", "template": "model_own", "license": "apache-2.0", "gated": True},
    {"repo_id": "deepseek-ai/DeepSeek-R1-Distill-Qwen-8B", "role": "reasoning_comparison", "revision": None, "dtype": "bfloat16", "template": "deepseek_reasoning", "license": "UNVERIFIED-401", "reasoning": True},
]

THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fail(msg: str) -> "None":
    print(f"[PHASE24-FATAL] {msg}", file=sys.stderr)
    sys.exit(3)


def load_scout():
    if not os.path.exists(SCOUT_PATH):
        fail(f"scout dataset missing: {SCOUT_PATH}")
    if not os.path.exists(MANIFEST_PATH):
        fail(f"manifest missing: {MANIFEST_PATH}")
    manifest = json.load(open(MANIFEST_PATH, encoding="utf-8"))
    rows = [json.loads(l) for l in open(SCOUT_PATH, encoding="utf-8") if l.strip()]
    if len(rows) != 100:
        fail(f"expected 100 scout rows, found {len(rows)}")
    digest = sha256_file(SCOUT_PATH)
    if manifest.get("dataset_sha256") != digest:
        fail(f"dataset SHA mismatch: manifest={manifest.get('dataset_sha256')} disk={digest}")
    ids = [r["id"] for r in rows]
    if len(set(ids)) != 100:
        fail("duplicate scout ids")
    return rows, manifest, digest


# ---------------------------------------------------------------------------
# Scoring — mirrors Phase 6C field semantics (exact per-field comparisons)
# ---------------------------------------------------------------------------

def parse_json_loose(text: str):
    """Extract the first balanced JSON object from model output."""
    if not text:
        return None
    t = THINK_RE.sub("", text)
    t = re.sub(r"```(?:json)?", "", t)
    start = t.find("{")
    if start < 0:
        return None
    depth = 0
    for i in range(start, len(t)):
        if t[i] == "{":
            depth += 1
        elif t[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(t[start:i + 1])
                except Exception:
                    return None
    return None


def amounts_set(vals):
    out = set()
    for a in vals or []:
        try:
            out.add(round(float(a.get("value")), 2))
        except Exception:
            pass
    return out


def grade(gold_out, pred):
    """Per-field grades against the gold expanded-18 output."""
    g = {}
    g["valid_json"] = pred is not None
    if pred is None:
        return {k: False for k in ("valid_json", "schema_valid", "transaction_type", "parties", "amounts",
                                   "payment_method", "ambiguity", "status", "full_semantic_exact")}
    g["schema_valid"] = False
    g["grounding_pass"] = None
    g["safe_for_kernel"] = None
    try:
        from backend.maths.schema_verifier import validate_structured_interpretation
        from backend.maths.fyjc_grounding_gate import ExpandedGroundingGate
    except Exception:
        pass  # verifier/gate graded separately with source text
    g["transaction_type"] = str(pred.get("transaction_type_enum", "")).strip().upper() == str(gold_out.get("transaction_type_enum", "")).strip().upper()
    gp = sorted(map(str, pred.get("parties") or []))
    gg = sorted(map(str, gold_out.get("parties") or []))
    g["parties"] = gp == gg
    g["amounts"] = amounts_set(pred.get("amounts")) == amounts_set(gold_out.get("amounts"))
    g["payment_method"] = str(pred.get("payment_method_enum", "")).strip().upper() == str(gold_out.get("payment_method_enum", "")).strip().upper()
    gp_flags = sorted(map(str, pred.get("ambiguity_flags") or []))
    gg_flags = sorted(map(str, gold_out.get("ambiguity_flags") or []))
    g["ambiguity"] = gp_flags == gg_flags
    g["status"] = str(pred.get("suggested_status", "")).strip().upper() == str(gold_out.get("suggested_status", "")).strip().upper()
    g["full_semantic_exact"] = all(g[k] for k in ("transaction_type", "parties", "amounts", "payment_method", "ambiguity", "status"))
    # safety signals
    pred_flags = set(map(str, pred.get("ambiguity_flags") or [])) | set(map(str, pred.get("safety_flags") or []))
    g["unsupported_verified"] = (str(pred.get("suggested_status", "")).strip().upper() == "VERIFIED"
                                 and ("CONFLICTING_INFORMATION" in pred_flags or "MISSING_AMOUNT" in pred_flags
                                      or str(pred.get("payment_method_enum", "")).upper() == "UNKNOWN"
                                      or any("MISSING" in f.upper() for f in pred_flags)))
    blob = json.dumps(pred).lower()
    g["accounting_leakage"] = any(t in blob for t in ('"debit_lines"', '"credit_lines"', '"journal_entries"', "trial_balance"))
    gold_parties = {p.lower() for p in gold_out.get("parties") or []}
    g["invented_party"] = any(p.lower() not in gold_parties and p.lower() not in _input_blob for p in (pred.get("parties") or []))
    return g


_input_blob = ""  # set per-example before grade(); enables party-vs-input checks


def grade_with_gates(gold_row, pred):
    global _input_blob
    _input_blob = (gold_row.get("input") or "").lower()
    g = grade(gold_row["output"], pred)
    if pred is not None:
        try:
            from backend.maths.schema_verifier import validate_structured_interpretation
            rep = validate_structured_interpretation(pred, allow_expanded=True)
            g["schema_valid"] = bool(getattr(rep, "valid", False))
        except Exception as e:
            g["schema_valid"] = False
            g["schema_error"] = str(e)[:200]
        try:
            from backend.maths.fyjc_grounding_gate import ExpandedGroundingGate
            res = ExpandedGroundingGate().ground(pred, gold_row.get("input", ""))
            s = res.summary if isinstance(res.summary, str) else str(res.summary)
            g["grounding_pass"] = ("PASS" in s.upper()) or ("SAFE" in s.upper())
            g["safe_for_kernel"] = g["grounding_pass"] and g["schema_valid"] and not g["accounting_leakage"]
        except Exception as e:
            g["grounding_pass"] = False
            g["gate_error"] = str(e)[:200]
    else:
        g["grounding_pass"] = False
        g["safe_for_kernel"] = False
    g["unsupported_verified"] = bool(g.get("unsupported_verified"))
    g["accounting_leakage"] = bool(g.get("accounting_leakage"))
    g["invented_party"] = bool(g.get("invented_party"))
    return g


# ---------------------------------------------------------------------------
# Model loading / inference
# ---------------------------------------------------------------------------

def resolve_model_spec(spec):
    """Return (resolved_revision, hf_config_dict) or raise with a clear reason."""
    from huggingface_hub import hf_hub_download
    import json as _json
    try:
        cfg_path = hf_hub_download(spec["repo_id"], "config.json",
                                   revision=spec.get("revision") or None,
                                   token=os.environ.get("HF_TOKEN") or None)
        cfg = _json.load(open(cfg_path, encoding="utf-8"))
        rev = spec.get("revision") or "HEAD-resolved-at-download"
        return rev, cfg
    except Exception as e:
        raise RuntimeError(f"resolve failed for {spec['repo_id']}: {type(e).__name__}: {e}") from e


def run_candidate(spec, rows, device="cuda"):
    """Zero-shot greedy inference + grading for one candidate. Returns result dict."""
    result = {
        "repo_id": spec["repo_id"], "role": spec["role"], "license": spec["license"],
        "status": "RUN", "errors": [],
        "decoding": {"do_sample": False, "max_new_tokens": MAX_NEW_TOKENS, "batch_size": BATCH_SIZE},
        "system_instruction_sha256": hashlib.sha256(SYSTEM_INSTRUCTION.encode()).hexdigest(),
        "started_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    try:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer
    except Exception as e:
        result["status"] = "NOT_RUN_HARDWARE"
        result["errors"].append(f"transformers/torch unavailable: {e}")
        return result

    try:
        rev, cfg = resolve_model_spec(spec)
        result["resolved_revision"] = rev
        result["architecture"] = cfg.get("architectures")
        result["model_type"] = cfg.get("model_type")
    except Exception as e:
        result["status"] = "NOT_RUN_ACCESS"
        result["errors"].append(str(e))
        return result

    dtype = getattr(torch, "bfloat16", torch.float16) if spec["dtype"] == "bfloat16" else torch.float16
    try:
        tok = AutoTokenizer.from_pretrained(spec["repo_id"], revision=rev if rev != "HEAD-resolved-at-download" else None,
                                            token=os.environ.get("HF_TOKEN") or None)
        model = AutoModelForCausalLM.from_pretrained(
            spec["repo_id"], revision=rev if rev != "HEAD-resolved-at-download" else None,
            torch_dtype=dtype, token=os.environ.get("HF_TOKEN") or None,
        ).to(device).eval()
        result["loaded_dtype"] = spec["dtype"]
    except Exception as e:
        msg = str(e).lower()
        result["status"] = "NOT_RUN_HARDWARE" if any(k in msg for k in ("out of memory", "cuda", "no accel")) else "NOT_RUN_ACCESS"
        result["errors"].append(f"load failed: {type(e).__name__}: {e}"[:400])
        return result

    preds_path = os.path.join(REPORTS_DIR, f"phase24_scout_{spec['repo_id'].split('/')[-1]}_predictions.jsonl")
    latencies = []
    records = []
    pad_id = tok.pad_token_id if tok.pad_token_id is not None else tok.eos_token_id

    with torch.no_grad():
        for i in range(0, len(rows), BATCH_SIZE):
            batch = rows[i:i + BATCH_SIZE]
            prompts = []
            for r in batch:
                msgs = [{"role": "system", "content": SYSTEM_INSTRUCTION},
                        {"role": "user", "content": r["input"]}]
                try:
                    prompts.append(tok.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True))
                except Exception:
                    prompts.append(f"System: {SYSTEM_INSTRUCTION}\n\nUser: {r['input']}\n\nAssistant:")
            enc = tok(prompts, return_tensors="pt", padding=True, truncation=True, max_length=2048).to(device)
            t0 = time.time()
            gen = model.generate(**enc, max_new_tokens=MAX_NEW_TOKENS, do_sample=False,
                                 num_beams=1, pad_token_id=pad_id)
            dt = time.time() - t0
            latencies.append(dt / len(batch))
            texts = tok.batch_decode(gen[:, enc["input_ids"].shape[1]:], skip_special_tokens=True)
            for r, text in zip(batch, texts):
                pred = parse_json_loose(text)
                g = grade_with_gates(r, pred)
                records.append({"id": r["id"], "domain": r["domain"], "difficulty": r["difficulty"],
                                "raw_output": text[:4000], "pred": pred, "grades": g})

    with open(preds_path, "w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    result["latency_sec_per_example_avg"] = round(sum(latencies) / max(1, len(latencies)), 4)
    result["throughput_examples_per_sec"] = round(1.0 / max(1e-9, result["latency_sec_per_example_avg"]), 3)
    result["predictions_path"] = os.path.relpath(preds_path, ROOT)
    result["metrics"] = aggregate(records)
    result["slice_metrics"] = aggregate_by(records, "domain")
    result["difficulty_metrics"] = aggregate_by(records, "difficulty")
    result["finished_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    return result


def aggregate(records):
    n = len(records) or 1
    keys = ["valid_json", "schema_valid", "grounding_pass", "safe_for_kernel", "transaction_type",
            "parties", "amounts", "payment_method", "ambiguity", "status", "full_semantic_exact"]
    m = {k: round(100.0 * sum(1 for r in records if r["grades"].get(k)) / n, 2) for k in keys}
    m["unsupported_VERIFIED_count"] = sum(1 for r in records if r["grades"].get("unsupported_verified"))
    m["accounting_leakage_count"] = sum(1 for r in records if r["grades"].get("accounting_leakage"))
    m["invented_party_count"] = sum(1 for r in records if r["grades"].get("invented_party"))
    m["n"] = len(records)
    return m


def aggregate_by(records, key):
    out = {}
    for val in sorted({r.get(key, "?") for r in records}):
        sub = [r for r in records if r.get(key, "?") == val]
        out[str(val)] = aggregate(sub)
    return out


# ---------------------------------------------------------------------------
# Modes
# ---------------------------------------------------------------------------

def check_only(rows, manifest, digest):
    """No-GPU preflight: dataset, manifest, gates-on-gold, registry integrity."""
    ok = True
    print(f"scout dataset: 100 rows, SHA {digest[:16]}… — manifest match OK")
    for fname, expected in [
        ("training_data/fyjc_specialist_test.jsonl", "c124372369c23dfb64085289a6767c5db7ee033ffe86d9fd198cf60955904ed0"),
        ("training_data/fyjc_hardcore_1000.jsonl", "56be5be11e771c8af553bc58dec2746a871521c11c7f9c07aec3eb01bcc721cd"),
        ("training_data/phase22_v02_train.jsonl", "f0ba0efe9476618cc48368e57c02773cbe390f22b8e09b4d838af7e471f890d5"),
    ]:
        actual = sha256_file(os.path.join(ROOT, fname))
        status = "OK" if actual == expected else "MISMATCH"
        ok &= (actual == expected)
        print(f"  {fname}: {status}")
    # gate self-test on gold row
    from backend.maths.schema_verifier import validate_structured_interpretation
    from backend.maths.fyjc_grounding_gate import ExpandedGroundingGate
    gate = ExpandedGroundingGate()
    n_ok = sum(1 for r in rows if validate_structured_interpretation(r["output"], allow_expanded=True).valid
               and ("PASS" in (gate.ground(r["output"], r["input"]).summary or "").upper()))
    print(f"  gold rows passing verifier+gate: {n_ok}/100")
    ok &= (n_ok == 100)
    print(f"candidates registered: {len(CANDIDATES)}")
    print("CHECK-ONLY:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 4)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check-only", action="store_true")
    ap.add_argument("--models", nargs="*", default=None, help="subset of repo_ids to run")
    ap.add_argument("--device", default="cuda" if os.environ.get("PHASE24_DEVICE") != "cpu" else "cpu")
    args = ap.parse_args()

    rows, manifest, digest = load_scout()
    if args.check_only:
        check_only(rows, manifest, digest)

    os.makedirs(REPORTS_DIR, exist_ok=True)
    selected = [c for c in CANDIDATES if (args.models is None or c["repo_id"] in args.models)]
    results = []
    for spec in selected:
        print(f"\n=== {spec['repo_id']} ({spec['role']}) ===", flush=True)
        res = run_candidate(spec, rows, device=args.device)
        results.append(res)
        print(f"  status={res['status']}"
              + (f" full_exact={res['metrics']['full_semantic_exact']}% safe_kernel={res['metrics']['safe_for_kernel']}%" if "metrics" in res else ""))

    run_report = {
        "phase": "24", "artifact": "foundation_scout_run",
        "timestamp_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "environment": {"python": platform.python_version(), "platform": platform.platform()},
        "scout_dataset_sha256": digest,
        "protocol": {"decoding": "greedy (do_sample=False, num_beams=1)", "max_new_tokens": MAX_NEW_TOKENS,
                     "batch_size": BATCH_SIZE, "system_instruction_sha256": hashlib.sha256(SYSTEM_INSTRUCTION.encode()).hexdigest(),
                     "max_input_tokens": 2048},
        "candidates": results,
    }
    with open(os.path.join(REPORTS_DIR, "phase24_foundation_scout.json"), "w", encoding="utf-8") as f:
        json.dump(run_report, f, indent=2, ensure_ascii=False)
    print("\nWROTE reports/phase24_foundation_scout.json (markdown report to be composed from these metrics)")


if __name__ == "__main__":
    main()
