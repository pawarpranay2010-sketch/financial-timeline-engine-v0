"""Final model->authority security invariant probe (Phase 10). Read-only."""
import sys, os, base64, inspect, warnings
warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("PLATRIXA_WEBHOOK_SIGNING_KEY", "invariant-probe-key-0123456789")

from backend.maths.fyjc_grounding_gate import ExpandedGroundingGate
from backend.kernel.kernel import Kernel
from backend.model_provider.base import InterpretationResult, ProviderStatus

S = "Bought machinery for Rs. 90,000 cash from Iyer and Co."


def cand(**o):
    d = {"transaction_type": "Purchase of machinery", "transaction_type_enum": "PURCHASE",
         "parties": ["Iyer and Co"],
         "amounts": [{"value": "90000", "currency": "INR", "source": "explicit"}],
         "payment_method": "Cash", "payment_method_enum": "CASH", "references": [],
         "ambiguities": [],
         "grounding": {"all_fields_explicitly_grounded": True, "inferred_fields": []},
         "ambiguity_flags": [], "field_confidences": [], "overall_confidence": "0.50",
         "referenced_transaction_index": None, "referenced_party": None,
         "referenced_amount": None, "safety_flags": ["NONE"],
         "scope_flags": ["SINGLE_TRANSACTION"], "suggested_status": "REVIEW_REQUIRED"}
    d.update(o)
    return d


class Stub:
    def __init__(s, c): s.c = c
    def status(s):
        return ProviderStatus(available=True, model_id="s", base_model_revision="s",
                              adapter_repo_id="s", adapter_revision="s", reason="p",
                              loadable=True)
    def interpret(s, raw):
        return InterpretationResult(raw_input=raw, candidate=dict(s.c), model_id="s",
                                    provider_revision="s")
    @property
    def config(s): return None


def run(c):
    k = Kernel(); k.set_model_provider(Stub(c))
    try:
        r = k.process(S)
        return r.status, r.accounting_result or {}
    except Exception as e:
        return f"RAISED:{type(e).__name__}", {}


g = ExpandedGroundingGate()
P, F = [], []


def ck(n, ok, d=""):
    (P if ok else F).append(n)
    print(f"  [{'PASS' if ok else 'FAIL'}] {n}" + (f" — {d}" if d else ""))


print("=== MODEL -> AUTHORITY BOUNDARY INVARIANTS ===")
st, _ = run(cand())
ck("INV0 control: valid candidate reaches VERIFIED", st == "VERIFIED", f"status={st}")

r1 = g.ground(cand(transaction_type="Sale of goods", transaction_type_enum="SALE"), S)
ck("INV1 fabricated transaction_type -> safe_for_kernel False", r1.safe_for_kernel is False)
ck("INV2 failed field grounding -> overall grounded False", r1.grounded is False)
st1, acc1 = run(cand(transaction_type="Sale of goods", transaction_type_enum="SALE"))
ck("INV3 failed grounding cannot reach VERIFIED", st1 != "VERIFIED", f"status={st1}")
ck("INV4 no accounting action on ungrounded type", not acc1, f"accounting={acc1 or 'none'}")

r2 = g.ground(cand(amounts=[{"value": "9,000", "currency": "INR", "source": "explicit"}]), S)
ck("INV5 substring amount (9,000 vs 90,000) does not ground", r2.grounded is False)
st2, acc2 = run(cand(amounts=[{"value": "9,000", "currency": "INR", "source": "explicit"}]))
ck("INV6 substring amount cannot reach VERIFIED", st2 != "VERIFIED", f"status={st2}")
dr = acc2.get("debit_lines") or []
ck("INV7 model amount cannot alter the posting",
   all(str(x.get("amount")) == "90000" for x in dr),
   f"debit={[(x.get('account'), str(x.get('amount'))) for x in dr]}")

st3, acc3 = run(cand(transaction_type="X", transaction_type_enum="UNKNOWN",
                     payment_method="Cheque", payment_method_enum="CHEQUE",
                     amounts=[{"value": "1", "currency": "INR", "source": "explicit"}]))
dr3 = acc3.get("debit_lines") or []
ck("INV8 model fields cannot dictate the posting (multi-field tamper)",
   all(str(x.get("amount")) == "90000" for x in dr3),
   f"debit={[(x.get('account'), str(x.get('amount'))) for x in dr3]}")

from api.routes import async_api
src = inspect.getsource(async_api._post_webhook)
ck("INV9 webhook delivery pins allow_redirects=False", "allow_redirects=False" in src)
try:
    async_api._assert_safe_delivery_target("https://169.254.169.254/latest/meta-data/")
    ok = False
except Exception:
    ok = True
ck("INV10 non-public delivery target rejected", ok)

from backend.auth import async_jobs
sealed = async_jobs.seal_webhook_secret("whsec_probe")
v, _, pl = sealed.partition(":")
b = bytearray(base64.urlsafe_b64decode(pl)); b[12] ^= 1
try:
    async_jobs.unseal_webhook_secret(f"{v}:{base64.urlsafe_b64encode(bytes(b)).decode()}")
    ok = False
except Exception:
    ok = True
ck("INV11 tampered webhook secret ciphertext is rejected", ok)

from fastapi.testclient import TestClient
from api.main import create_app
c = TestClient(create_app(), raise_server_exceptions=False)
codes = {}
codes["db/init"] = c.post("/api/v1/db/init", json={}).status_code
codes["analyze"] = c.post("/api/v1/intelligence/analyze",
                          json={"ticker": "A", "goal": "probe", "max_iterations": 1}).status_code
codes["market"] = c.get("/api/v1/market/X").status_code
codes["providers"] = c.get("/api/v1/providers/status").status_code
ck("INV12 anonymous cannot reach protected endpoints",
   all(v2 in (401, 403) for v2 in codes.values()), str(codes))
hr = c.get("/api/v1/health")
ck("INV13 public /api/v1/health still reachable (Render)", hr.status_code == 200)
hb = (hr.text or "").lower()
ck("INV14 health discloses no driver/DSN/secret",
   not any(t in hb for t in ("psycopg2", "postgresql://", "password", "traceback")),
   f"len={len(hb)}")

from api import rate_limit as rl
rl.reset()
rs = [c.post("/api/v1/kernel/process",
             json={"raw_input": "Bought machinery for Rs. 90,000 cash."}).status_code
      for _ in range(60)]
ck("INV15 anonymous request COUNT is bounded (429)", 429 in rs,
   f"429 at #{rs.index(429) if 429 in rs else 'never'}")

print(f"\nINVARIANTS: {len(P)} passed, {len(F)} failed")
sys.exit(1 if F else 0)
