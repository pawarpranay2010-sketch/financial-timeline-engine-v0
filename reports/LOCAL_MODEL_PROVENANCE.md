# Platrixa — Gate 0: Local GGUF Model Provenance Record

**Question:** Can `~/storage/downloads/platrixa-phase-h-Q4_K_M.gguf` (Android device,
~941 MB as reported by the operator) be attributed to a specific training artifact?

**Verdict: `PROVENANCE_UNKNOWN`.**

The filename must NOT be used to infer the training phase. Until a SHA-256 match
against a build manifest is produced, every report, eval, or gate result that
involves this artifact must carry the label `PROVENANCE_UNKNOWN`.

- Measurement window (UTC): 2026-10-02
- Repository: `financial-timeline-engine-v0`, branch `main` (worktree inspected read-only)
- Labels: `MEASURED` (observed in this workspace) · `REPORTED` (operator-supplied) ·
  `NOT_MEASURED` (cannot be observed from this workspace) · `DOCUMENTED` (stated in a repo file)

---

## 1. Measurements taken in this workspace

| # | Check | Result | Label |
|---|---|---|---|
| 1 | `find . -name "*.gguf"` (repo tree) | no matches | MEASURED |
| 2 | `find . -name "artifact-manifest.json"` (repo tree) | no matches | MEASURED |
| 3 | `grep -rn "local_llama_bridge"` (py/md/json) | no matches — the bridge source lives only on the device | MEASURED |
| 4 | `curl http://127.0.0.1:8080/health` and `:8081/health` from the workspace | both unreachable (connection refused) | MEASURED |
| 5 | GGUF SHA-256 of the phone artifact | **cannot be computed here** — the file exists only on the Android device and the device is bound to its own localhost | NOT_MEASURED |
| 6 | Exact byte size of the phone artifact | ~941 MB (operator estimate); not `stat`-ed | REPORTED |
| 7 | GGUF header metadata (architecture, quantization) of the phone artifact | not readable from this workspace | NOT_MEASURED |

**How to close measurements 5–7 (run on the device, e.g. Termux):**

```sh
sha256sum ~/storage/downloads/platrixa-phase-h-Q4_K_M.gguf
stat -c '%s' ~/storage/downloads/platrixa-phase-h-Q4_K_M.gguf
```

A GGUF header read (via `backend/model_provider/gguf_artifact.inspect`) can confirm
architecture and quantization **but contains no adapter identity** — only a SHA-256
match against an `artifact-manifest.json` produced at build time can attribute the file.

---

## 2. Known source revisions (DOCUMENTED, from repository evidence)

| Role | Identity | Source |
|---|---|---|
| Base model | `Qwen/Qwen2.5-1.5B-Instruct` @ `989aa7980e4cf806f80c7fef2b1adb7bc71aa306` | `backend/model_provider/base.py:56-57` |
| Pinned LoRA adapter | `Pranay-20/platrixa-financial-semantic-v0.1` @ `b5c0a37cebc00e93144150dbbcaa7b28cadb259e` | `backend/model_provider/base.py:61-62` |
| **What that pinned adapter is** | "**the exact Phase 6C model artifact** (a historical FYJC-focused development/evaluation slice)" | `hf_space/README.md:19-21` — DOCUMENTED |
| Phase H adapter | local path `/content/drive/MyDrive/platrixa/phase_h_output` (Colab Drive; saved by `training/phase_h_sft_job.py` to `PLATRIXA_PHASE_H_OUTPUT_DIR`) with its own `phase_h_manifest.json` | `reports/phase_h/phase_h_v01_dev_evaluation.md:37` — DOCUMENTED |
| Phase H adapter on Hugging Face | no evidence of any upload; repo-wide pin hunt found zero competing pins for either repo id | `reports/LOCAL_GGUF_STAGE1_BUILD_ENV.md` §5 — DOCUMENTED |

---

## 3. Why the filename proves nothing

1. `scripts/build_local_gguf_artifact.py` builds **from the pins imported at runtime
   from `base.py`** (asserted by `assert_pins_consistent()`), i.e. from the adapter that
   `hf_space/README.md` identifies as the **Phase 6C** artifact.
2. The same script **unconditionally names** its outputs
   `platrixa-phase-h-f16.gguf` / `platrixa-phase-h-<quant>.gguf`
   (`stage_convert()` / `stage_quantize()`) regardless of which adapter produced the
   merged weights. The string "phase-h" in a filename is assigned by the build script,
   not derived from the weights.
3. Therefore a GGUF produced by that script would be **Phase 6C weights wearing a
   Phase H filename** — while a GGUF built manually from the Colab Phase H adapter
   directory would genuinely be Phase H. The two are indistinguishable by name.
4. The recorded build attempt **never ran**: `reports/LOCAL_GGUF_STAGE1_BUILD_ENV.md`
   (2026-10-01) verdict is `BLOCKED` (1.10 GiB free disk vs ~15 GB required; 2.00 GiB
   RAM cap vs ~10 GB required), "No model was downloaded. No merge, conversion,
   quantization … was performed in this stage." No artifact, no manifest was produced
   on the only host whose build was ever documented.
5. No `artifact-manifest.json` exists anywhere in the repository (measurement 2), so
   there is no recorded SHA-256 to compare the device file against.
6. The ~941 MB reported size does not resolve this: the pipeline docstring's Q4_K_M
   figure (~1.1 GB) is a planning estimate, and other builds (different f16 master,
   different toolchain, partial file) are all consistent with 941 MB. Size is not
   provenance.

### Possible states (cannot be distinguished from available evidence)

| Scenario | Meaning |
|---|---|
| A | Built by `scripts/build_local_gguf_artifact.py` → **Phase 6C** adapter weights, mislabeled `phase-h` |
| B | Built manually from the Colab `phase_h_output` adapter → genuine **Phase H** weights |
| C | Built from some other adapter/base state → unknown weights |

---

## 4. Rules in force until provenance is established

1. The artifact is referred to as `PROVENANCE_UNKNOWN` — never as "the Phase H model".
2. No evaluation number obtained against this artifact is attributed to any training phase.
3. The acceptance harness (`scripts/local_bridge_acceptance.py`) embeds this verdict in
   every report it writes.
4. Provider identity fields reported to the kernel evidence chain for this path are set
   to `PROVENANCE_UNKNOWN` (see `backend/model_provider/local_bridge.py`) so no
   downstream artifact can silently claim the pinned revisions.

## 5. Path to attribution

1. `sha256sum` the device file (command in §1).
2. Locate the build host where the file was produced and retrieve its
   `artifact-manifest.json` (schema `platrixa.local-model-artifact/1`, contains
   `artifacts[].sha256`, base/adapter revisions, `llama_cpp_revision`).
3. Compare hashes. Match → attributed (report the manifest's adapter revision).
4. No manifest found → provenance stays `PROVENANCE_UNKNOWN` permanently for that file;
   the only fix is a **rebuild** with `scripts/build_local_gguf_artifact.py` on a host
   meeting the §4 budget of the Stage 1 report, which produces a manifest by design.
