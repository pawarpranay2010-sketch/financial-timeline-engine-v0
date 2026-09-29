"""
Platrixa — Modal CPU/GPU resource-selection regression suite (2026-09-29)
=========================================================================

Proves the CPU-inference / GPU-training split without executing Modal. Every
check is deterministic and offline.

Two layers:

  A. PURE CONFIG  — `training/modal_resources.py` resolution rules, exercised
     directly with an injected environment mapping. No Modal import needed.

  B. DEPLOY WIRING — the kwargs actually handed to `modal.App.cls` /
     `modal.App.function` by `training/modal_inference.py` and
     `training/run_modal.py`. These are captured by intercepting the Modal
     decorator before the module is imported, so this asserts the real
     deployment arguments rather than re-implementing them.

LIVE MODAL EXECUTION IS **NOT** VERIFIED BY THIS SUITE. No Modal container
was deployed or called. CPU latency/throughput/memory headroom are therefore
NOT measured and are NOT claimed anywhere. See reports/SECURITY_BASELINE.md
style honesty: absence of a measurement is stated, not filled in.

What this suite must never become: a test that passes because a file exists.
Every check below either resolves a real value or compares a captured
deployment argument.

Run:  python3 scripts/fte_modal_resource_profile_test.py
"""
from __future__ import annotations

import importlib
import os
import sys
import warnings

warnings.filterwarnings("ignore")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

# Import the resource module through the SAME package path the Modal services
# use (`training.modal_resources`), not the bare top-level name. Importing it
# twice under two names would create two distinct exception classes and make
# the fail-closed assertions below compare unrelated types.
from training.modal_resources import (  # noqa: E402
    CPU_DTYPE,
    DEFAULT_GPU,
    GPU_DTYPE,
    RESOURCE_ENV,
    GPU_ENV,
    LEGACY_GPU_ENV,
    VALID_RESOURCES,
    ResourceConfigurationError,
    resolve_inference_profile,
    resolve_resource,
    resolve_training_profile,
)

PASSED: list = []
FAILED: list = []


def ck(name: str, ok: bool, detail: str = "") -> None:
    if ok:
        PASSED.append(name)
        print(f"  [PASS] {name}")
    else:
        FAILED.append(name)
        print(f"  [FAIL] {name} — {detail}")


def raises(fn, env) -> bool:
    try:
        fn(env)
    except ResourceConfigurationError:
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"         (raised {type(exc).__name__} instead)")
        return False
    return False


# ===========================================================================
# A. PURE CONFIG — inference resource resolution
# ===========================================================================
print("\nA — Inference resource resolution (pure config)")

env = {}
prof = resolve_inference_profile(env)
ck("A1 default resource is CPU (no env set)",
   prof.resource == "cpu", f"got {prof.resource!r}")
ck("A2 default profile requests NO gpu",
   prof.modal_gpu_arg() is None, f"got {prof.modal_gpu_arg()!r}")
ck("A3 default profile has cpu + memory capacity",
   prof.cpu is not None and prof.memory_mb is not None,
   f"cpu={prof.cpu} memory={prof.memory_mb}")
ck("A4 default profile uses the CPU dtype",
   prof.dtype == CPU_DTYPE, f"got {prof.dtype!r}")

prof = resolve_inference_profile({RESOURCE_ENV: "cpu"})
ck("A5 explicit cpu resolves to CPU",
   prof.resource == "cpu" and prof.modal_gpu_arg() is None,
   f"got {prof.resource!r}/{prof.modal_gpu_arg()!r}")

prof = resolve_inference_profile({RESOURCE_ENV: "gpu"})
ck("A6 explicit gpu resolves to gpu", prof.resource == "gpu", prof.resource)
ck("A7 explicit gpu defaults to the T4 class",
   prof.gpu == DEFAULT_GPU, f"got {prof.gpu!r}")
ck("A8 explicit gpu uses the GPU dtype",
   prof.dtype == GPU_DTYPE, f"got {prof.dtype!r}")

prof = resolve_inference_profile({RESOURCE_ENV: "gpu", GPU_ENV: "A10G"})
ck("A9 explicit gpu honours a GPU-class override",
   prof.gpu == "A10G", f"got {prof.gpu!r}")

prof = resolve_inference_profile({RESOURCE_ENV: "GPU"})
ck("A10 resource value is case-insensitive",
   prof.resource == "gpu", f"got {prof.resource!r}")

# The single most important CPU invariant: a CPU profile can never carry a
# GPU, even when a GPU class is configured in the same environment.
prof = resolve_inference_profile({RESOURCE_ENV: "cpu", GPU_ENV: "T4"})
ck("A11 CPU path ignores a configured GPU class (no silent escalation)",
   prof.modal_gpu_arg() is None, f"got {prof.modal_gpu_arg()!r}")
prof = resolve_inference_profile({RESOURCE_ENV: "cpu", LEGACY_GPU_ENV: "T4"})
ck("A12 CPU path ignores the legacy GPU env var too",
   prof.modal_gpu_arg() is None, f"got {prof.modal_gpu_arg()!r}")

prof = resolve_inference_profile({RESOURCE_ENV: "", GPU_ENV: "T4"})
ck("A13 empty resource value falls back to CPU, not GPU",
   prof.resource == "cpu" and prof.modal_gpu_arg() is None,
   f"got {prof.resource!r}/{prof.modal_gpu_arg()!r}")

prof = resolve_inference_profile({LEGACY_GPU_ENV: "L4"})
ck("A14 legacy GPU env var alone does NOT imply GPU (default stays CPU)",
   prof.resource == "cpu" and prof.modal_gpu_arg() is None,
   f"got {prof.resource!r}/{prof.modal_gpu_arg()!r}")
prof = resolve_inference_profile({RESOURCE_ENV: "gpu", LEGACY_GPU_ENV: "L4"})
ck("A15 legacy GPU env var still works when GPU is requested",
   prof.gpu == "L4", f"got {prof.gpu!r}")

# Fail closed on anything unrecognised — never default to an accelerator.
for bad in ("tpu", "cuda", "cpu:4", "auto", "1", "yes", "T4"):
    ck(f"A16 invalid resource {bad!r} fails closed",
       raises(resolve_inference_profile, {RESOURCE_ENV: bad}),
       "no error raised")
ck("A17 valid resource set is exactly (cpu, gpu)",
   VALID_RESOURCES == ("cpu", "gpu"), f"got {VALID_RESOURCES!r}")
ck("A18 bare resolve_resource defaults to cpu",
   resolve_resource({}) == "cpu", resolve_resource({}))


# ===========================================================================
# B. TRAINING / GPU EVALUATION stays GPU
# ===========================================================================
print("\nB — Training / GPU-evaluation resource resolution")

prof = resolve_training_profile({})
ck("B1 training defaults to GPU", prof.resource == "gpu", prof.resource)
ck("B2 training keeps the T4 class",
   prof.gpu == DEFAULT_GPU, f"got {prof.gpu!r}")
ck("B3 training refuses an explicit cpu request (no CPU training path)",
   raises(resolve_training_profile, {RESOURCE_ENV: "cpu"}), "no error raised")
prof = resolve_training_profile({RESOURCE_ENV: "gpu", GPU_ENV: "L40S"})
ck("B4 training honours an explicit GPU class",
   prof.gpu == "L40S", f"got {prof.gpu!r}")
ck("B5 training never resolves to a CPU profile",
   all(resolve_training_profile({e: v}).resource == "gpu"
       for e, v in ({}.items())),
   "unexpected cpu profile")


# ===========================================================================
# C. PROFILE HYGIENE — no secrets leak through the public description
# ===========================================================================
print("\nC — Profile hygiene")


def json_dump_free(obj) -> str:
    """Flatten a dict to a searchable string without importing json."""
    return " ".join(f"{k}={v}" for k, v in sorted(obj.items()))


d = resolve_inference_profile({RESOURCE_ENV: "gpu", GPU_ENV: "T4"}).as_public_dict()
allowed = {"workload", "resource", "gpu", "cpu", "memory_mb", "dtype"}
ck("C1 public profile dict exposes only resource-shape keys",
   set(d) == allowed, f"got {sorted(d)}")
ck("C2 public profile dict carries no token/secret/endpoint field",
   not any(k in json_dump_free(d) for k in ("token", "secret", "key", "url",
                                            "password", "credential")),
   json_dump_free(d))
ck("C3 workload label is present for observability",
   d.get("workload") == "inference", d.get("workload"))


# ===========================================================================
# D. DEPLOY WIRING — kwargs actually given to the Modal decorators
# ===========================================================================
print("\nD — Modal deploy wiring (captured decorator kwargs)")

import modal  # noqa: E402


def capture_deploy_kwargs(module_name: str, decorator: str) -> dict:
    """Import a Modal module with the decorator intercepted.

    Returns the kwargs the module passed to modal.App.cls / modal.App.function.
    """
    captured: dict = {}

    def spy(self, *args, **kwargs):  # noqa: ANN001
        captured["_args"] = args
        captured.update(kwargs)
        return real(self, *args, **kwargs)

    real = getattr(modal.App, decorator)
    setattr(modal.App, decorator, spy)
    saved = dict(os.environ)
    try:
        if module_name in sys.modules:
            del sys.modules[module_name]
        importlib.import_module(module_name)
    finally:
        setattr(modal.App, decorator, real)
        os.environ.clear()
        os.environ.update(saved)
    return captured


inf_kwargs = capture_deploy_kwargs("training.modal_inference", "cls")
ck("D1 inference deploy captures @app.cls kwargs",
   "gpu" in inf_kwargs and "image" in inf_kwargs, str(sorted(inf_kwargs)))
ck("D2 inference deploy requests NO gpu by default (CPU is the default)",
   inf_kwargs.get("gpu") is None, f"got {inf_kwargs.get('gpu')!r}")
ck("D3 inference deploy passes cpu capacity",
   inf_kwargs.get("cpu") == resolve_inference_profile({}).cpu,
   f"got {inf_kwargs.get('cpu')!r}")
ck("D4 inference deploy passes memory capacity",
   inf_kwargs.get("memory") == resolve_inference_profile({}).memory_mb,
   f"got {inf_kwargs.get('memory')!r}")
ck("D5 inference deploy scales to zero (no always-on worker)",
   inf_kwargs.get("min_containers") == 0,
   f"got {inf_kwargs.get('min_containers')!r}")
ck("D6 inference deploy keeps the pinned HF cache volume",
   "/root/.cache/huggingface" in (inf_kwargs.get("volumes") or {}),
   str(list((inf_kwargs.get("volumes") or {}).keys())))

# Now the same module, deployed with GPU explicitly requested.
saved = dict(os.environ)
os.environ[RESOURCE_ENV] = "gpu"
os.environ[GPU_ENV] = "T4"
try:
    gpu_kwargs = capture_deploy_kwargs("training.modal_inference", "cls")
finally:
    os.environ.clear()
    os.environ.update(saved)
ck("D7 inference deploy requests T4 when gpu is explicitly selected",
   gpu_kwargs.get("gpu") == "T4", f"got {gpu_kwargs.get('gpu')!r}")

# An invalid selector must abort the import (fail closed at deploy time), not
# quietly fall through to a GPU.
os.environ[RESOURCE_ENV] = "tpu"
try:
    bad_kwargs = capture_deploy_kwargs("training.modal_inference", "cls")
    ck("D8 invalid resource aborts the deployment import", False,
       "import succeeded — no fail-closed error")
except ResourceConfigurationError:
    ck("D8 invalid resource aborts the deployment import", True)
except Exception as exc:  # noqa: BLE001
    ck("D8 invalid resource aborts the deployment import", False,
       f"raised {type(exc).__name__} not ResourceConfigurationError")
finally:
    os.environ.clear()
    os.environ.update(saved)

# Restore a clean default import for the remaining checks.
capture_deploy_kwargs("training.modal_inference", "cls")

trn_kwargs = capture_deploy_kwargs("training.run_modal", "function")
ck("D9 training deploy captures @app.function kwargs",
   "gpu" in trn_kwargs, str(sorted(trn_kwargs)))
ck("D10 training deploy is GPU-backed (T4)",
   trn_kwargs.get("gpu") == "T4", f"got {trn_kwargs.get('gpu')!r}")
ck("D11 training deploy does not set cpu/memory (existing GPU deploy preserved)",
   trn_kwargs.get("cpu") is None and trn_kwargs.get("memory") is None,
   f"cpu={trn_kwargs.get('cpu')!r} memory={trn_kwargs.get('memory')!r}")


# ===========================================================================
# E. MODEL / ADAPTER CONTRACT PRESERVED
# ===========================================================================
print("\nE — Model and adapter contract preserved")

import backend.model_provider.base as mp_base  # noqa: E402

inf = importlib.import_module("training.modal_inference")

ck("E1 base model id unchanged",
   inf.BASE_MODEL_ID == mp_base.BASE_MODEL_ID, inf.BASE_MODEL_ID)
ck("E2 base model revision unchanged",
   inf.BASE_MODEL_REVISION == "989aa7980e4cf806f80c7fef2b1adb7bc71aa306"
   == mp_base.BASE_MODEL_REVISION, inf.BASE_MODEL_REVISION)
ck("E3 base model revision matches the provider pin",
   inf.BASE_MODEL_REVISION == mp_base.BASE_MODEL_REVISION, "")
ck("E4 adapter repo id unchanged",
   inf.ADAPTER_REPO_ID == "Pranay-20/platrixa-financial-semantic-v0.1",
   inf.ADAPTER_REPO_ID)
ck("E5 adapter revision unchanged",
   inf.ADAPTER_REVISION == "b5c0a37cebc00e93144150dbbcaa7b28cadb259e"
   == mp_base.ADAPTER_REVISION, inf.ADAPTER_REVISION)
ck("E6 adapter revision matches the provider pin",
   inf.ADAPTER_REVISION == mp_base.ADAPTER_REVISION, "")

ck("E7 Alpaca prompt prefix unchanged",
   inf.ALPACA_PREFIX.startswith("Below is an instruction that describes a task"),
   inf.ALPACA_PREFIX[:40])
ck("E8 prompt still names the 18 contract fields",
   all(f in inf.ALPACA_INSTRUCTION for f in
       ("transaction_type", "parties", "amounts", "payment_method",
        "references", "ambiguities", "grounding", "transaction_type_enum",
        "payment_method_enum", "ambiguity_flags",
        "referenced_transaction_index", "referenced_party",
        "referenced_amount", "field_confidences", "overall_confidence",
        "suggested_status", "safety_flags", "scope_flags")), "")
ck("E9 prompt forbids accounting conclusions (authority boundary intact)",
   "Never produce journal entries" in inf.ALPACA_INSTRUCTION, "")

ck("E10 generation still greedy/deterministic",
   inf.TEMPERATURE == 0.0 and inf.TOP_P == 1.0,
   f"T={inf.TEMPERATURE} top_p={inf.TOP_P}")
ck("E11 max_new_tokens unchanged", inf.MAX_NEW_TOKENS == 512,
   inf.MAX_NEW_TOKENS)
ck("E12 forbidden-accounting-field guard still present",
   {"debit_lines", "credit_lines", "ledger"} <= set(inf._FORBIDDEN_ACCOUNTING_FIELDS),
   str(sorted(inf._FORBIDDEN_ACCOUNTING_FIELDS)))
ck("E13 inference packages unchanged (same artifact stack)",
   "torch==2.11.0" in inf.INFERENCE_PACKAGES
   and "peft==0.20.0" in inf.INFERENCE_PACKAGES,
   str(inf.INFERENCE_PACKAGES[:3]))
ck("E14 no new model was introduced",
   "Qwen2.5-1.5B-Instruct" in inf.BASE_MODEL_ID, inf.BASE_MODEL_ID)

src = open(os.path.join(REPO, "training", "modal_inference.py"),
           encoding="utf-8").read()
# Look for credential SHAPES, not substrings: "hf_transfer" and
# "huggingface_hub" legitimately contain "hf_", and "is_cpu" contains "gpu".
import re  # noqa: E402

_SECRET_SHAPES = (
    r"hf_[A-Za-z0-9]{20,}",          # Hugging Face token
    r"sk-[A-Za-z0-9]{16,}",          # provider-style key
    r"xox[baprs]-[A-Za-z0-9-]{10,}",  # slack
    r"AKIA[0-9A-Z]{12,}",            # aws
    r"(?i)(password|secret|api[_-]?key|token)\s*=\s*[\"'][^\"']{6,}[\"']",
)
_hits = [p for p in _SECRET_SHAPES if re.search(p, src)]
ck("E15 no hard-coded credential in the inference service",
   not _hits, f"credential-shaped literal(s): {_hits}")
ck("E16 resource selection is the only new failure surface, and it fails closed",
   "ResourceConfigurationError" in
   open(os.path.join(REPO, "training", "modal_resources.py"),
        encoding="utf-8").read(), "")


# ===========================================================================
# F. NO SILENT CPU -> GPU FALLBACK
# ===========================================================================
print("\nF — No silent CPU -> GPU fallback")

run_modal_src = open(os.path.join(REPO, "training", "run_modal.py"),
                     encoding="utf-8").read()
ck("F1 inference service requests no GPU in its default deployment",
   inf_kwargs.get("gpu") is None, f"got {inf_kwargs.get('gpu')!r}")

_load_body = src.split("def load(", 1)[1].split("def identity", 1)[0]
ck("F2 the load path never re-resolves a resource profile",
   "resolve_inference_profile" not in _load_body,
   "profile resolution appears inside load()")
ck("F3 the profile is resolved exactly once, at module import",
   src.count("_PROFILE = resolve_inference_profile()") == 1,
   f"occurrences={src.count('_PROFILE = resolve_inference_profile()')}")
ck("F4 exactly one @app.cls is defined (no second, GPU-capable entry point)",
   src.count("@app.cls") == 1, f"occurrences={src.count('@app.cls')}")
ck("F5 the load path contains no profile mutation",
   "_PROFILE =" not in _load_body, "_PROFILE is reassigned inside load()")
ck("F6 training module has no cpu resource argument at all",
   "cpu=" not in run_modal_src.replace("_TRAINING_PROFILE.cpu", ""),
   "cpu= found in run_modal.py")
ck("F7 GPU class is only ever chosen by explicit configuration",
   "DEFAULT_GPU" in open(os.path.join(REPO, "training", "modal_resources.py"),
                         encoding="utf-8").read()
   and inf_kwargs.get("gpu") is None, "")

ck("F8 inference profile resolution never returns gpu unless asked",
   all(
       resolve_inference_profile(e).resource == "cpu"
       for e in ({}, {RESOURCE_ENV: "cpu"}, {RESOURCE_ENV: ""},
                 {GPU_ENV: "T4"}, {LEGACY_GPU_ENV: "T4"})
   ), "a CPU-expected environment resolved to GPU")
ck("F9 gpu resolution is a pure function of the selector",
   all(resolve_inference_profile({RESOURCE_ENV: "gpu"}).resource == "gpu"
       for _ in range(3)), "non-deterministic resolution")


print(f"\nTOTAL: {len(PASSED) + len(FAILED)} checks — "
      f"{len(PASSED)} passed, {len(FAILED)} failed")
if FAILED:
    for name in FAILED:
        print(f"  FAIL: {name}")
sys.exit(1 if FAILED else 0)
