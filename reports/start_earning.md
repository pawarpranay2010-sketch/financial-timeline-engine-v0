# Start Earning — Platrixa for Developers & Fintech Startups

**Report date:** 2026-09-17 · **Scope:** what developers can use today, what is honestly not ready, how to charge, and the highest-power acquisition plan for the next 7–10 days.

---

## 1. What a developer or fintech startup can DO with Platrixa

The product surface is real and already ships (verified in the repository):

| # | Use case | How it works today |
|---|---|---|
| 1 | **Transaction text → verified accounting result** | `POST /v1/process` (hosted) or `Platrixa().process("Purchased furniture for cash ₹15,000")` (library). Returns status (VERIFIED / REVIEW_REQUIRED / BLOCKED), the 18-field structured interpretation, and the deterministic accounting result as JSON. |
| 2 | **Semantic layer inside a bookkeeping/expense app** | Their app collects messy user text (narrations, chats, receipts notes) → Platrixa returns a schema-validated, grounded, entity-extracted interpretation → their app writes journal entries. They never build the NLP layer. |
| 3 | **Fail-safe automation** | The fail-closed pipeline (schema verifier → grounding gate → deterministic kernel) means outputs the system cannot prove are marked `REVIEW_REQUIRED` / `BLOCKED` — not guessed. Apps can automate VERIFIED results and queue the rest for humans. |
| 4 | **Domain policy without touching the core** | Rule packs (YAML + Python hooks) are **downgrade-only**: a startup can tighten rules to their vertical (retail, services, GST) but cannot override the kernel or forge VERIFIED. |
| 5 | **EdTech productization** | The FYJC practice slice — Platrixa's early development/evaluation domain, not its overall scope (UI in `frontend/`, 95% locked-test accuracy, 0% leakage) — is a ready auto-checked accounting practice engine for coaching institutes and EdTech apps. |

**The pitch in one line:** *"You send financial language; we return a deterministic, evidence-backed, schema-verified accounting interpretation — with hard failure states instead of confident hallucinations."*

---

## 2. READY vs NOT READY (honest audit)

### READY (shipped, in the repo)

- ✅ Python library (`platrixa/`), CLI (`python -m platrixa`), minimal examples (`examples/developer_interface/`)
- ✅ Hosted developer API: `POST /v1/process`, `/v1/health`, `/v1/ready` (FastAPI, `api/`)
- ✅ **Per-developer API keys** — SHA-256 hashed, constant-time compare, 401 fail-closed
- ✅ **Monthly quota metering per tenant** (Phase 16): atomic reservation, `YYYY-MM` buckets, 429 `QUOTA_EXHAUSTED`, zero units consumed on auth failures, no scheduler needed
- ✅ Deterministic kernel + grounding + schema verifier (the moat — 0% accounting leakage measured on the locked test)
- ✅ Rule packs & hooks with downgrade-only authority
- ✅ Lightweight ops: ~49 MiB core install, lazy model load, boots with no DB/keys
- ✅ Deployment posture documented (`DEPLOYMENT.md`, Procfile, Python 3.10 pin)

### NOT READY (must not be promised)

- ❌ **Billing & automated key issuance** — tenants are provisioned by the operator (`python -m backend.auth.dev_seed_tenant`); there is no self-serve signup portal or payment webhook
- ❌ Self-serve dashboard (usage visibility, key rotation, plan changes)
- ❌ Application-level rate limiting, idempotency keys, exactly-once dedup
- ❌ SLAs / enterprise posture (the docs themselves disclaim it)
- ❌ **Breadth beyond the proven FYJC-style transaction slice** — bank narrations, invoices, and document understanding are exactly what the Phase 24 Layer-1 benchmark is designed to measure; the scout evaluation has not run yet. Market narrations/document features only after that evidence. (FYJC-style accounting language is Platrixa's early development/evaluation slice, not the product's overall scope.)
- ❌ Consumer "finance advice" (the architecture deliberately produces accounting truth, not advice)

**Consequence for GTM:** your first revenue motion must be **operator-provisioned, payment-link-based, and transaction-text-focused**. That is genuinely viable for your first 10–50 customers and requires ~zero new code.

---

## 3. How we charge them

### The mechanism that exists today

1. Customer pays via a **payment link/page** (no code needed).
2. You provision their tenant: `python -m backend.auth.dev_seed_tenant --tenant <id> --limit <monthly_quota>` — the raw key is printed **exactly once**.
3. You email the key + the quickstart. Their quota is enforced automatically by Phase 16 metering; monthly rollover is built in.

### Recommended pricing (quota units = admitted requests/month)

| Plan | Price (₹/mo) | Quota | Target | Margin logic |
|---|---|---|---|---|
| **Free / Eval** | ₹0 | 50 | Trials, hackathons, students | Groq free-tier absorbable; seeds the funnel |
| **Developer** | ₹499 | 2,000 | Solo devs, side projects | Covers provider cost (Groq ~₹1–10 per 1K-call range at 1.5B) + margin |
| **Startup** | ₹1,999 | 10,000 | Fintech MVPs in build | Volume discount for them, real revenue for you |
| **Scale** | Custom | 10,000+ | Funded startups | Talk to us — first 5 hand-held, case studies harvested |

Anchors: price against the value of *not building* an NLP + validation + accounting pipeline (weeks of engineer time), not against raw tokens. The Free tier is your acquisition weapon, not your revenue.

### Payment provider recommendation

**Razorpay** (India-first): UPI + cards + netbanking for Indian customers, GST-compliant invoices, subscriptions, and **Payment Pages** — a hosted checkout page you can publish today and link from the README, so "charging" works before any billing code exists.

For international developer sales later (tax-free global selling without you handling foreign compliance), add a Merchant-of-Record such as **Dodo Payments**; Stripe is the classic option if you incorporate abroad.

Setup button (Razorpay — creates account, returns the keys to paste into Settings → Environment):

👉 Get Razorpay keys (then add `RAZORPAY_KEY_ID` / `RAZORPAY_KEY_SECRET` via Settings → Environment; neither is required for the day-1 payment-link flow).

### What to build next, in billing order (each unlocks the next customer type)

1. **Nothing (day 1–30):** manual provisioning + payment page covers 10–50 customers.
2. **Webhook → auto-provision:** Razorpay webhook creates the tenant row and emails the key (a day of work when volume demands it).
3. **Usage endpoint + dashboard:** customers with money in ask "how much have I used?" — expose the metering table.

---

## 4. Most powerful ways to bring users in 7–10 days

Ranked by (expected signups ÷ effort) for *this specific product* — developer tools win on **proof, not ads**.

### Day 1–2 — Weapons-grade demo assets
- Deploy one public playground (HF Space already exists in-repo) + one 60-second GIF/Short: *messy SMS-style transaction text in → VERIFIED journal-ready result out*, including one input where Platrixa says REVIEW_REQUIRED instead of hallucinating (this clip **is** the differentiation).
- Put "Get a free API key (50 calls/mo)" with the payment-page link in the README and every post.

### Day 2–3 — Technical content (highest yield for dev tools)
- **Dev.to + Hashnode + Hashnode cross-post:** "I built a fail-closed accounting interpretation API — journal entries your app can trust" with code, curl, and the playground. Target keywords: *transaction parsing, bookkeeping automation, fintech API India*.
- **Show HN** (day 3–4, Tuesday–Thursday morning IST-adjusted): one honest paragraph, playground link, README. HN rewards exactly this architecture story (determinism over LLM bravado).

### Day 3–5 — Community seeding (where Indian fintech builders already are)
- **Reddit:** r/developersIndia, r/fintech, r/SideProject, r/india — build-log framing, never spam.
- **WhatsApp/Telegram fintech & builder groups, X (Twitter) build-in-public thread** with the GIF.
- **GitHub visibility:** keep the core open, add a killer README quickstart (3 lines of code), label `hacktoberfest`/`good-first-issue`. Stars → organic dev traffic for months.

### Day 5–7 — Direct outreach (the actual money)
- **20–30 hand-picked** Indian bookkeeping/expense-tracker/invoice-app founders and 2–3 student-EdTech operators: personalized 5-line email/LinkedIn message with *their* use case, free Startup plan for 30 days in exchange for feedback. Target conversion: 3–5 replies, 1–2 pilots.
- **Coaching-institute pilot (EdTech):** the proven 95%-accuracy slice means you can sell a paid FYJC practice pilot *this week* (an early evaluation slice of the product, not its whole scope) — one institute paying ₹5–15k/mo is real revenue and a case study.

### Day 7–10 — Compounding
- Publish the first case study ("How X cut transaction-entry time with Platrixa").
- **Product Hunt launch** (prepare on day 7, launch day 10 with everything above already warm).
- Offer a **hackathon track / free credits** to 2–3 college tech fests — students build on it, teams keep building after.

### What NOT to spend on in 10 days
Paid ads (no landing-funnel to convert), enterprise sales calls (posture honestly not ready), influencer marketing (wrong audience), or building the self-serve portal before you have 10 manual customers.

---

## 5. Realistic 30-day expectation (bottom-up)

| Motion | Conservative | Good |
|---|---|---|
| Free keys issued | 40–100 | 200+ |
| Developer ₹499 | 2–4 | 8–12 |
| Startup ₹1,999 | 0–2 | 2–4 |
| EdTech pilot | 0–1 | 1–2 |
| **MRR by day 30** | **₹2–8k** | **₹15–40k** |

The number that matters is not MRR — it is **2–3 teams that ship something real on Platrixa**. Those become case studies, testimonials, and your Series of proof for the next 100 users.

---

## 6. Go / no-go gates

- **GO sell now:** transaction-text use cases, EdTech pilots — everything needed ships today.
- **WAIT for Phase 24 scout evidence before selling:** bank-narration parsing, invoice/document features, "any financial language" claims.
- **BEFORE first paid customer:** set `PLATRIXA_DEV_API_KEY` or enable Phase 16 metering on the public deployment; never expose an unkeyed API.

---

*Grounding: `docs/DEVELOPER_INTERFACE.md`, `docs/HOSTED_API.md` (auth/metering/limits), `DEPLOYMENT.md`, `platrixa/_facade.py`, `backend/auth/` (gate, init_metering, dev_seed_tenant), `examples/developer_interface/`, Phase 6C-v02 locked-test results, Phase 24 pre-run state.*
