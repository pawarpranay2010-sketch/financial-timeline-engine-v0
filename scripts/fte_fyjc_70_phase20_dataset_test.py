#!/usr/bin/env python3
"""fte_fyjc_70 — Phase 20: hardcore dataset evolution test suite.

Proves (per Phase 20 spec §18):

  A/P. original 1,000 dataset remains byte-for-byte unchanged
  B.   new candidate contains exactly 1,000 rows
  C.   every row is valid JSON
  D.   every row satisfies the current 18-field contract (production verifier)
  E.   IDs are unique and sequential
  F.   rejected candidates never enter the final dataset (validators re-run;
       manifest shows nonzero rejections)
  G.   unsupported / forbidden concepts are rejected (probes)
  H.   duplicate detection works (exact original input probe)
  I.   near-duplicate detection works (light modification probe)
  J.   generation failure does not corrupt the final dataset (fail-closed run)
  K.   missing credentials fail closed (no silent provider switch)
  L.   partial generation is safely recoverable (guard + fresh full run)
  M.   secrets never appear in outputs
  N.   manifest distributions match the actual final dataset
  O.   (run separately after this suite: fte_52 / fte_68 / fte_67)

Pure test module: touches only training_data/ artifacts under tmp for J/L.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

# Deterministic subprocess environment: the specialist iterates keyword sets,
# so hash randomisation changes 'first matching keyword' order across
# processes. The generator self-pins via re-exec; subprocesses get it too.
PINNED_ENV = dict(os.environ, PYTHONHASHSEED="0")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

ORIGINAL = ROOT / "training_data" / "fyjc_specialist_1000.jsonl"
CANDIDATE = ROOT / "training_data" / "fyjc_hardcore_1000.jsonl"
MANIFEST = ROOT / "training_data" / "fyjc_hardcore_1000.manifest.json"
GENERATOR = ROOT / "training" / "phase20_generate.py"

# Byte-for-byte baseline captured during the Phase 20 audit (§1/§2).
ORIGINAL_SHA256 = "feb7bfe5c1f415228d3ab9ccdc43beaa2df1498af8f4e66fc5856c441df61e24"

_checks: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    _checks.append((name, bool(cond), detail))


def load_jsonl(path: Path) -> list:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def _load_generator_module():
    spec = importlib.util.spec_from_file_location("phase20_gen", GENERATOR)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------

def main() -> int:
    from backend.maths.schema_verifier import validate_structured_interpretation
    from backend.maths.fyjc_grounding_gate import ExpandedGroundingGate

    gate = ExpandedGroundingGate()

    # ---- A/P: original untouched -------------------------------------------
    orig_bytes = ORIGINAL.read_bytes()
    orig_hash = hashlib.sha256(orig_bytes).hexdigest()
    check("A/P.original_byte_identical", orig_hash == ORIGINAL_SHA256, orig_hash[:16] + "…")
    originals = load_jsonl(ORIGINAL)
    check("A.original_row_count_1000", len(originals) == 1000, str(len(originals)))

    # ---- B/C/E: candidate shape ---------------------------------------------
    check("B.candidate_exists", CANDIDATE.exists(), str(CANDIDATE))
    raw_lines = [l for l in CANDIDATE.read_text(encoding="utf-8").splitlines() if l.strip()]
    check("B.exactly_1000_rows", len(raw_lines) == 1000, str(len(raw_lines)))
    parsed = []
    bad_json = 0
    for l in raw_lines:
        try:
            parsed.append(json.loads(l))
        except json.JSONDecodeError:
            bad_json += 1
    check("C.all_valid_json", bad_json == 0, f"bad={bad_json}")

    ids = [r["id"] for r in parsed]
    check("E.ids_unique", len(set(ids)) == len(ids), f"distinct={len(set(ids))}")
    expected_ids = [f"hc_{i:05d}" for i in range(1, len(parsed) + 1)]
    check("E.ids_sequential_hc", ids == expected_ids, ids[:2] + ["…"] + ids[-1:])

    # ---- D: schema compliance (production verifier) -------------------------
    schema_bad = []
    for r in parsed:
        rep = validate_structured_interpretation(r["output"], allow_expanded=True)
        if not rep.valid:
            schema_bad.append(r["id"])
    check("D.schema_valid_all", not schema_bad, f"bad={schema_bad[:3]}")

    # ---- F: accepted rows re-pass the full gauntlet -------------------------
    from collections import Counter

    p20 = _load_generator_module()
    orig_norms = {p20.normalise(r["input"]) for r in originals}
    orig_tokens = [p20.token_set(r["input"]) for r in originals]

    gate_bad, dup_bad, near_bad, leak_bad = [], [], [], []
    seen_inputs = set(orig_norms)
    acc_tokens = []
    event_counts: Counter = Counter()
    for r in parsed:
        ok, reason = p20.validate_candidate(r, gate, seen_inputs, orig_tokens)
        if not ok:
            if "gate" in reason:
                gate_bad.append(reason)
            elif "duplicate" in reason:
                dup_bad.append(reason)
            elif "near_dup" in reason:
                near_bad.append(reason)
            else:
                leak_bad.append(reason)
            continue
        seen_inputs.add(p20.normalise(r["input"]))
        acc_tokens.append(p20.token_set(r["input"]))
        event_counts[p20.event_signature(r["metadata"])] += 1
    check("F.all_rows_pass_gauntlet", not (gate_bad or dup_bad or near_bad or leak_bad),
          f"gate={gate_bad[:2]} dup={dup_bad[:2]} near={near_bad[:2]} leak={leak_bad[:2]}")
    check("F.max_event_signature_respected", max(event_counts.values()) <= p20.SAME_EVENT_CAP,
          f"max={max(event_counts.values())}")
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    check("F.rejections_actually_occurred",
          sum(manifest["rejection_statistics"].values()) > 0,
          str(manifest["rejection_statistics"]))

    # ---- G: unsupported / forbidden concepts rejected ------------------------
    base_out = dict(parsed[0]["output"])
    text0 = parsed[0]["input"]
    meta0 = dict(parsed[0]["metadata"])

    forbidden = dict(base_out)
    forbidden["journal"] = [{"debit": "Cash", "credit": "Sales"}]
    ok, reason = p20.validate_candidate(
        {"id": "probe_g1", "input": text0, "output": forbidden, "metadata": meta0},
        gate, set(), [])
    # the production schema verifier AND/OR the gate must reject the extra
    # accounting-truth field — either rejection is a valid fail-closed path
    check("G.forbidden_fields_rejected",
          not ok and ("forbidden_fields" in reason or "gate" in reason or "schema" in reason),
          reason)

    verified = dict(base_out)
    verified["suggested_status"] = "VERIFIED"
    ok, reason = p20.validate_candidate(
        {"id": "probe_g2", "input": text0, "output": verified, "metadata": meta0},
        gate, set(), [])
    check("G.verified_claim_rejected", not ok and "VERIFIED" in reason, reason)

    fabricated = json.loads(json.dumps(base_out))
    fabricated["amounts"] = [{"value": "999999", "currency": "INR", "source": "explicit"}]
    ok, reason = p20.validate_candidate(
        {"id": "probe_g3", "input": text0, "output": fabricated, "metadata": meta0},
        gate, set(), [])
    check("G.unsupported_amount_rejected", not ok and "gate" in reason, reason)

    # ---- H: exact duplicate of an original input ------------------------------
    dup_src = next(r for r in originals if r["metadata"]["transaction_type"] == "PURCHASE")
    dup_out = json.loads(json.dumps(dup_src["output"]))
    p20.normalise_amount_format(dup_out)
    dup_meta = p20.build_metadata(dup_out, "probe", "single", "standard")
    ok, reason = p20.validate_candidate(
        {"id": "probe_h", "input": dup_src["input"],
         "output": dup_out, "metadata": dup_meta},
        gate, set(orig_norms), orig_tokens)
    check("H.exact_duplicate_rejected", not ok and "duplicate_input" in reason, reason)

    # ---- I: near-duplicate of an original input -------------------------------
    nd_tokens = dup_src["input"].split()
    near_text = " ".join(nd_tokens[:-1]) if len(nd_tokens) > 6 else dup_src["input"] + " today"
    ok, reason = p20.validate_candidate(
        {"id": "probe_i", "input": near_text,
         "output": dup_out, "metadata": dup_meta},
        gate, set(orig_norms), orig_tokens)
    check("I.near_duplicate_rejected", not ok and "near_dup" in reason, reason)

    # ---- J: generation failure leaves dataset + outputs uncorrupted ----------
    with tempfile.TemporaryDirectory() as tmp:
        fail_out = Path(tmp) / "fail.jsonl"
        fail_manifest = Path(tmp) / "fail_manifest.json"

        code = (
            "import importlib.util, sys\n"
            f"spec = importlib.util.spec_from_file_location('p20', {str(GENERATOR)!r})\n"
            "mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)\n"
            "mod.FAMILIES = [(lambda rng, ctx: ('Purchased goods from Sharma Traders for Rs.10,000 cash.', 'dup', 'single', 'standard'), 10)]\n"
            "sys.exit(mod.main())\n"
        )
        proc = subprocess.run(
            [sys.executable, "-c", code, "--out", str(fail_out),
             "--manifest", str(fail_manifest)],
            capture_output=True, text=True, cwd=str(ROOT), timeout=300,
            env=PINNED_ENV,
        )
        check("J.failed_run_exits_fail_closed", proc.returncode == 3,
              f"rc={proc.returncode}")
        check("J.failed_run_writes_nothing", not fail_out.exists(),
              str(fail_out))
        check("J.original_still_untouched_after_failed_run",
              hashlib.sha256(ORIGINAL.read_bytes()).hexdigest() == ORIGINAL_SHA256)

    # ---- K: missing credentials fail closed -----------------------------------
    saved = os.environ.pop("GROQ_API_KEY", None)
    try:
        result = p20.groq_reword("Purchased goods for Rs.100 cash.")
        check("K.no_key_returns_none", result is None, repr(result))
    finally:
        if saved is not None:
            os.environ["GROQ_API_KEY"] = saved

    # ---- L: partial file is guarded; fresh run recovers fully -----------------
    with tempfile.TemporaryDirectory() as tmp:
        part = Path(tmp) / "part.jsonl"
        part_manifest = Path(tmp) / "part_manifest.json"
        part.write_text("\n".join(raw_lines[:10]) + "\n", encoding="utf-8")

        proc = subprocess.run(
            [sys.executable, str(GENERATOR), "--out", str(part),
             "--manifest", str(part_manifest)],
            capture_output=True, text=True, cwd=str(ROOT), timeout=600,
            env=PINNED_ENV,
        )
        check("L.existing_partial_refused", proc.returncode == 2, f"rc={proc.returncode}")
        check("L.partial_not_overwritten",
              len(part.read_text(encoding="utf-8").splitlines()) == 10)

        fresh = Path(tmp) / "fresh.jsonl"
        proc2 = subprocess.run(
            [sys.executable, str(GENERATOR), "--out", str(fresh),
             "--manifest", str(part_manifest)],
            capture_output=True, text=True, cwd=str(ROOT), timeout=600,
            env=PINNED_ENV,
        )
        check("L.fresh_run_recovers_1000",
              proc2.returncode == 0 and len(load_jsonl(fresh)) == 1000,
              f"rc={proc2.returncode}")
        # determinism: same seed → identical bytes
        check("L.deterministic_regeneration",
              hashlib.sha256(fresh.read_bytes()).hexdigest()
              == hashlib.sha256(CANDIDATE.read_bytes()).hexdigest())

    # ---- M: secrets never in outputs ------------------------------------------
    blob = CANDIDATE.read_text(encoding="utf-8") + MANIFEST.read_text(encoding="utf-8")
    import re as _re
    secret_hits = _re.findall(
        r"(sk-[A-Za-z0-9]{16,}|gsk_[A-Za-z0-9]{16,}|ghp_[A-Za-z0-9]{16,}"
        r"|postgres(?:ql)?://[^\s\"']*:[^\s\"']*@|DATABASE_URL\s*=)", blob)
    check("M.no_secrets_in_outputs", not secret_hits, str(secret_hits[:2]))

    # ---- N: manifest matches the actual dataset --------------------------------
    def dist(field: str) -> dict:
        c = Counter(str(r["metadata"][field]) for r in parsed)
        return dict(sorted(c.items(), key=lambda kv: (-kv[1], kv[0])))

    mdist = manifest["distributions"]
    check("N.manifest_row_count", manifest["row_count"] == 1000 == len(parsed))
    ok_n = (mdist["difficulty"] == dist("difficulty")
            and mdist["transaction_type"] == dist("transaction_type")
            and mdist["payment_method"] == dist("payment_method")
            and mdist["language_style"] == dist("language_style")
            and mdist["category"] == dist("category"))
    check("N.manifest_distributions_match", ok_n,
          f"diff={mdist['difficulty'] != dist('difficulty')}")
    cand_hash = hashlib.sha256(CANDIDATE.read_bytes()).hexdigest()
    check("N.manifest_candidate_hash", manifest["candidate_dataset_sha256"] == cand_hash,
          cand_hash[:16] + "…")
    check("N.manifest_source_hash", manifest["source_dataset_sha256"] == ORIGINAL_SHA256)
    check("N.no_verified_claims", manifest["validation"]["verified_claims"] == 0)

    # ---- report -----------------------------------------------------------------
    passed = sum(1 for _, ok, _ in _checks if ok)
    print(f"fte_fyjc_70 (phase20 dataset): {passed}/{len(_checks)} checks passed")
    for name, ok, detail in _checks:
        mark = "✅" if ok else "❌"
        print(f"  {mark} {name}" + (f"  — {detail}" if (detail and not ok) else ""))
    return 0 if passed == len(_checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
