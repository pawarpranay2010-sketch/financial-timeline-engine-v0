"""
Financial Timeline Engine
Sprint 15 - FYJC Real-Question Student Pilot Dataset
backend/maths/fyjc_pilot_dataset.py

A small, controlled pilot dataset of 40 authentic FYJC-style questions
(20 Maths + 20 Book-Keeping & Accountancy) used to validate that a real
student can use FT-E end to end. This is PURE DATA:

* No imports from the engine, no computation.
* Every expected value is an INDEPENDENT, hand-verified constant (an
  oracle that never calls the solver). The engine's actual output is
  compared against it by scripts/fte_fyjc_pilot_test.py.
* Question wordings intentionally mimic how an FYJC student would type
  or photograph a question (Rs. / commas / lakh-style / OCR noise /
  missing amounts / ambiguous wording / common student mistakes).
* Copyrighted textbook content is NOT reproduced - only the minimal
  question representation needed to validate the system, in standard
  FYJC exam style.

Field conventions
-----------------
Maths cases (FYJC_PILOT_MATHS):
    id            P01..P20
    question      the student input (multi-line 'Concept: value' lines
                  are the deterministic format the 12D normalizer parses)
    topic         FYJC topic (Percentages, Profit/Loss, Ratio, SI, CI,
                  AP, GP, GST, Shares/Dividend, Reverse calculation...)
    source_kind   typed | photo | pdf | textbook-style
    difficulty    1 (easy) .. 3 (hard)
    student_answer  the answer the student submits to "Verify Yourself"
    expected      INDEPENDENT expectation:
                  {status, verdict, display} for supported questions;
                  {status} for refusals
    human_reference  the hand-computed true numeric answer for questions
                  the registry does NOT support - documented so the gap
                  is explicit (FT-E must refuse, not guess)

Book-Keeping cases (FYJC_PILOT_BK):
    id            B01..B20
    question      the transaction / task wording
    kind          transaction | journal | ledger | trial_balance |
                  verify_journal | verify_ledger | verify_tb
    expected      {status, debit, credit, balances, tb, verdict, ...}
                  - the independent journal / ledger / trial-balance
                  result
    entries       reference journal entries for ledger/TB/verify cases
                  (derived by the test from the classified transactions
                  - never hand-written into FT-E)
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# A. MATHS - 20 questions
# ---------------------------------------------------------------------------

FYJC_PILOT_MATHS = [
    {
        "id": "P01",
        "metric": "profit",
        "question": "A firm's Revenue is Rs.10,000 and its Expenses are "
                    "Rs.6,000. What is its Profit?",
        "topic": "Profit / Loss",
        "source_kind": "typed",
        "difficulty": 1,
        "student_answer": 4000,
        "expected": {"status": "DERIVED", "verdict": "CORRECT",
                     "display": "4000.00"},
        # narrative prose without 'Concept: value' lines is NOT parsed by the
        # 12D normalizer (documented extraction gap). FT-E must refuse rather
        # than guess - independent human answer is 4000.
        "human_reference": 4000,
    },
    {
        "id": "P02",
        "metric": "loss",
        "question": "Calculate the Loss.\nRevenue: 1,000\nExpenses: 1,200",
        "topic": "Profit / Loss",
        "source_kind": "typed",
        "difficulty": 1,
        "student_answer": 200,
        "expected": {"status": "DERIVED", "verdict": "CORRECT",
                     "display": "200.00"},
    },
    {
        "id": "P03",
        "metric": "profit margin",
        "question": "Calculate the Profit Margin.\nProfit: 200\nRevenue: 1,000",
        "topic": "Percentages",
        "source_kind": "typed",
        "difficulty": 1,
        "student_answer": 20,
        "expected": {"status": "DERIVED", "verdict": "CORRECT",
                     "display": "20.00%"},
        # a common student mistake: 200/1000 x 100 done wrongly as 25
        "mistake_variant": {"student_answer": 25, "expect_verdict": "INCORRECT"},
    },
    {
        "id": "P04",
        "metric": "current ratio",
        "question": "Calculate the Current Ratio.\n"
                    "Current Assets: Rs.5,00,000\n"
                    "Current Liabilities: Rs.2,50,000",
        "topic": "Ratio & Proportion",
        "source_kind": "typed",
        "difficulty": 1,
        "student_answer": 2,
        "expected": {"status": "DERIVED", "verdict": "CORRECT",
                     "display": "2.00"},
    },
    {
        "id": "P05",
        "metric": "roe",
        "question": "Calculate ROE.\nNet Profit: 200\nEquity: 1,000",
        "topic": "Ratio & Proportion",
        "source_kind": "typed",
        "difficulty": 2,
        "student_answer": 20,
        "expected": {"status": "DERIVED", "verdict": "CORRECT",
                     "display": "20.00%"},
    },
    {
        "id": "P06",
        "metric": "eps",
        "question": "Calculate EPS.\nNet Profit: 200\nShares Outstanding: 100",
        "topic": "Shares / Dividend",
        "source_kind": "typed",
        "difficulty": 2,
        "student_answer": 2,
        "expected": {"status": "DERIVED", "verdict": "CORRECT",
                     "display": "2.00"},
    },
    {
        "id": "P07",
        "metric": "expenses",
        "question": "Find the missing figure: Expenses.\n"
                    "Revenue: 1,000\nProfit: 200",
        "topic": "Reverse calculation",
        "source_kind": "typed",
        "difficulty": 3,
        "student_answer": 800,
        "expected": {"status": "DERIVED", "verdict": "CORRECT",
                     "display": "800.00"},
    },
    {
        "id": "P08",
        "metric": "profit",
        "question": "Find the Profit.\nProfit Margin: 20\nRevenue: 1,000",
        "topic": "Reverse calculation",
        "source_kind": "typed",
        "difficulty": 3,
        "student_answer": 200,
        "expected": {"status": "DERIVED", "verdict": "CORRECT",
                     "display": "200.00"},
    },
    {
        "id": "P09",
        "metric": "gross profit",
        "question": "Calculate the Gross Profit.\n"
                    "Revenue: 10,000\nCost of Sales: 6,000",
        "topic": "Profit / Loss",
        "source_kind": "typed",
        "difficulty": 2,
        "student_answer": 4000,
        "expected": {"status": "DERIVED", "verdict": "CORRECT",
                     "display": "4000.00"},
    },
    {
        "id": "P10",
        "metric": "debt to equity",
        "question": "Calculate the Debt to Equity.\nDebt: 50,000\nEquity: 1,00,000",
        "topic": "Ratio & Proportion",
        "source_kind": "typed",
        "difficulty": 2,
        "student_answer": 0.5,
        "expected": {"status": "DERIVED", "verdict": "CORRECT",
                     "display": "0.50"},
    },
    {
        "id": "P11",
        "metric": "profit",
        "question": "From photo of question paper:\n"
                    "Revenue: Rs.10,000\nExpenses: Rs.6,000\nFind the Profit.",
        "topic": "Profit / Loss",
        "source_kind": "photo",
        "difficulty": 2,
        "student_answer": 4000,
        "expected": {"status": "DERIVED", "verdict": "CORRECT",
                     "display": "4000.00"},
    },
    {
        "id": "P12",
        "question": "Calculate the Simple Interest on Rs.8,000 at 8% p.a. "
                    "for 3 years.",
        "topic": "Simple Interest",
        "source_kind": "textbook-style",
        "difficulty": 1,
        "student_answer": 1920,
        "expected": {"status": "UNSUPPORTED"},
        "human_reference": 1920,  # 8000 x 8 x 3 / 100 - NOT computable by FT-E
    },
    {
        "id": "P13",
        "question": "Find the compound interest on Rs.10,000 at 10% p.a. "
                    "compounded yearly for 2 years.",
        "topic": "Compound Interest",
        "source_kind": "textbook-style",
        "difficulty": 2,
        "student_answer": 2100,
        "expected": {"status": "UNSUPPORTED"},
        "human_reference": 2100,  # 10000 x 1.1^2 - 10000
    },
    {
        "id": "P14",
        "question": "The 5th term of an Arithmetic Progression is 14 and "
                    "the common difference is 3. Find the first term.",
        "topic": "Arithmetic Progression",
        "source_kind": "textbook-style",
        "difficulty": 2,
        "student_answer": 2,
        "expected": {"status": "UNSUPPORTED"},
        "human_reference": 2,  # a + 4d = 14, d = 3 -> a = 2
    },
    {
        "id": "P15",
        "question": "Find the 6th term of the Geometric Progression 2, 4, 8, ...",
        "topic": "Geometric Progression",
        "source_kind": "textbook-style",
        "difficulty": 1,
        "student_answer": 64,
        "expected": {"status": "UNSUPPORTED"},
        "human_reference": 64,  # 2 x 2^5
    },
    {
        "id": "P16",
        "question": "A shopkeeper sells goods worth Rs.20,000 with GST at "
                    "18%. Find the GST amount.",
        "topic": "GST / indirect tax",
        "source_kind": "textbook-style",
        "difficulty": 1,
        "student_answer": 3600,
        "expected": {"status": "UNSUPPORTED"},
        "human_reference": 3600,  # 20000 x 18%
    },
    {
        "id": "P17",
        "question": "A company declares a dividend of 12% on shares of face "
                    "value Rs.100. Find the dividend on 50 shares.",
        "topic": "Shares / Dividend / Commission",
        "source_kind": "textbook-style",
        "difficulty": 2,
        "student_answer": 600,
        "expected": {"status": "UNSUPPORTED"},
        "human_reference": 600,  # 100 x 12% x 50
    },
    {
        "id": "P18",
        "question": "Calculate ROE.\nNet Profit: 200",
        "topic": "Ratio & Proportion",
        "source_kind": "typed",
        "difficulty": 1,
        "student_answer": None,
        "expected": {"status": "BLOCKED", "missing": ["Equity"]},
    },
    {
        "id": "P19",
        "question": "Calculate the Current Ratio.",
        "topic": "Conflicting evidence",
        "source_kind": "pdf",
        "difficulty": 3,
        "student_answer": None,
        "expected": {"status": "REVIEW_REQUIRED"},
        # two pages give different Current Assets values - FT-E must not
        # silently pick one
        "documents": [
            {"document_name": "page-1.png", "tier": "DOCUMENT",
             "facts": {"Current Assets": 500000,
                        "Current Liabilities": 250000}},
            {"document_name": "page-2.png", "tier": "DOCUMENT",
             "facts": {"Current Assets": 520000}},
        ],
    },
    {
        "id": "P20",
        "question": "Calculate the Profit Margin.\nProfit: 100\nRevenue: 0",
        "topic": "Percentages",
        "source_kind": "typed",
        "difficulty": 2,
        "student_answer": None,
        "expected": {"status": "BLOCKED"},
        "note": "zero denominator - no division by zero, no guess",
    },
]

# ---------------------------------------------------------------------------
# B. BOOK-KEEPING & ACCOUNTANCY - 20 questions
# ---------------------------------------------------------------------------
# kind: transaction (classify+journal), ledger (post multiple entries),
#       trial_balance (build from posted entries), verify_journal /
#       verify_ledger / verify_tb (student work checked against the
#       engine treatment). Amounts are the independent oracle.

FYJC_PILOT_BK = [
    {
        "id": "B01",
        "question": "Started business with cash Rs.50,000.",
        "topic": "Golden Rules / Journal",
        "source_kind": "typed",
        "difficulty": 1,
        "kind": "transaction",
        "expected": {"status": "VERIFIED",
                     "debit": {"Cash"}, "credit": {"Capital"}},
    },
    {
        "id": "B02",
        "question": "Purchased goods for cash Rs.10,000.",
        "topic": "Golden Rules / Journal",
        "source_kind": "typed",
        "difficulty": 1,
        "kind": "transaction",
        "expected": {"status": "VERIFIED",
                     "debit": {"Purchases"}, "credit": {"Cash"}},
    },
    {
        "id": "B03",
        "question": "Purchased goods on credit from Rahul for Rs.10,000.",
        "topic": "Golden Rules / Journal",
        "source_kind": "typed",
        "difficulty": 1,
        "kind": "transaction",
        "expected": {"status": "VERIFIED",
                     "debit": {"Purchases"}, "credit": {"Rahul"}},
    },
    {
        "id": "B04",
        "question": "Sold goods for cash Rs.15,000.",
        "topic": "Golden Rules / Journal",
        "source_kind": "typed",
        "difficulty": 1,
        "kind": "transaction",
        "expected": {"status": "VERIFIED",
                     "debit": {"Cash"}, "credit": {"Sales"}},
    },
    {
        "id": "B05",
        "question": "Sold goods on credit to Mohan Rs.15,000.",
        "topic": "Golden Rules / Journal",
        "source_kind": "typed",
        "difficulty": 1,
        "kind": "transaction",
        "expected": {"status": "VERIFIED",
                     "debit": {"Mohan"}, "credit": {"Sales"}},
    },
    {
        "id": "B06",
        "question": "Paid rent Rs.3,000.",
        "topic": "Golden Rules / Journal",
        "source_kind": "typed",
        "difficulty": 1,
        "kind": "transaction",
        "expected": {"status": "VERIFIED",
                     "debit": {"Rent"}, "credit": {"Cash"}},
    },
    {
        "id": "B07",
        "question": "Received commission Rs.2,000.",
        "topic": "Golden Rules / Journal",
        "source_kind": "typed",
        "difficulty": 1,
        "kind": "transaction",
        "expected": {"status": "VERIFIED",
                     "debit": {"Cash"}, "credit": {"Commission Received"}},
    },
    {
        "id": "B08",
        "question": "Cash deposited into bank Rs.5,000.",
        "topic": "Golden Rules / Journal",
        "source_kind": "typed",
        "difficulty": 1,
        "kind": "transaction",
        "expected": {"status": "VERIFIED",
                     "debit": {"Bank"}, "credit": {"Cash"}},
    },
    {
        "id": "B09",
        "question": "Withdrew Rs.2,000 for personal use.",
        "topic": "Golden Rules / Journal",
        "source_kind": "typed",
        "difficulty": 1,
        "kind": "transaction",
        "expected": {"status": "VERIFIED",
                     "debit": {"Drawings"}, "credit": {"Cash"}},
    },
    {
        "id": "B10",
        "question": "Discount allowed to Mohan Rs.500.",
        "topic": "Discounts",
        "source_kind": "typed",
        "difficulty": 2,
        "kind": "transaction",
        "expected": {"status": "VERIFIED",
                     "debit": {"Discount Allowed"}, "credit": {"Mohan"}},
    },
    {
        "id": "B11",
        "question": "From photo: Purchased goods from Rahul on credit for "
                    "Rs.10,000.",
        "topic": "Golden Rules / Journal",
        "source_kind": "photo",
        "difficulty": 2,
        "kind": "transaction",
        "expected": {"status": "VERIFIED",
                     "debit": {"Purchases"}, "credit": {"Rahul"}},
    },
    {
        "id": "B12",
        "question": "Post the following transactions to the ledger and "
                    "balance the accounts.",
        "topic": "Ledger Posting",
        "source_kind": "typed",
        "difficulty": 3,
        "kind": "ledger",
        "transactions": [
            "Started business with cash Rs.50,000.",
            "Purchased goods for cash Rs.10,000.",
            "Sold goods for cash Rs.15,000.",
        ],
        "expected": {
            "status": "VERIFIED",
            "balances": {  # independent hand-cast ledger
                "Cash": {"balance": 55000.0, "side": "Dr"},
                "Purchases": {"balance": 10000.0, "side": "Dr"},
                "Sales": {"balance": -15000.0, "side": "Cr"},
                "Capital": {"balance": -50000.0, "side": "Cr"},
            },
            # gross posting totals (sum of all debit/credit LINES posted),
            # NOT the trial-balance net totals (those are 65000/65000)
            "total_debit": 75000.0,
            "total_credit": 75000.0,
            "balanced": True,
        },
    },
    {
        "id": "B13",
        "question": "Prepare a Trial Balance from the following transactions.",
        "topic": "Trial Balance",
        "source_kind": "typed",
        "difficulty": 2,
        "kind": "trial_balance",
        "transactions": [
            "Started business with cash Rs.50,000.",
            "Purchased goods for cash Rs.10,000.",
            "Sold goods for cash Rs.15,000.",
        ],
        "expected": {
            "status": "VERIFIED",
            "total_debit": 65000.0,
            "total_credit": 65000.0,
            "balanced": True,
        },
    },
    {
        "id": "B14",
        "question": "Check the Trial Balance. Cash is 45,000 instead of "
                    "55,000.",
        "topic": "Trial Balance / Error detection",
        "source_kind": "typed",
        "difficulty": 3,
        "kind": "verify_tb",
        "transactions": [
            "Started business with cash Rs.50,000.",
            "Purchased goods for cash Rs.10,000.",
            "Sold goods for cash Rs.15,000.",
        ],
        "student_tb": "Cash, 45000, 0\nPurchases, 10000, 0\n"
                      "Sales, 0, 15000\nCapital, 0, 50000",
        "expected": {"verdict": "INCORRECT"},
        "note": "student misread the Cash balance - FT-E must flag the "
                "discrepancy, not accept it",
    },
    {
        "id": "B15",
        "question": "Verify this journal entry: Purchases Dr 10,000 / "
                    "Rahul Cr 10,000 for 'Purchased goods on credit from "
                    "Rahul for Rs.10,000.'",
        "topic": "Journal Verification",
        "source_kind": "typed",
        "difficulty": 2,
        "kind": "verify_journal",
        "description": "Purchased goods on credit from Rahul for Rs.10,000.",
        "student_debits": [["Purchases", "10000"]],
        "student_credits": [["Rahul", "10000"]],
        "expected": {"verdict": "CORRECT"},
    },
    {
        "id": "B16",
        "question": "Verify this journal entry: Cash Dr 10,000 / Rahul "
                    "Cr 10,000 for a credit purchase.",
        "topic": "Journal Verification",
        "source_kind": "typed",
        "difficulty": 2,
        "kind": "verify_journal",
        "description": "Purchased goods on credit from Rahul for Rs.10,000.",
        "student_debits": [["Cash", "10000"]],
        "student_credits": [["Rahul", "10000"]],
        "expected": {"verdict": "INCORRECT"},
        "note": "common student mistake - debiting Cash instead of "
                "Purchases on a credit purchase",
    },
    {
        "id": "B17",
        "question": "Purchased goods from Rahul on credit.",
        "topic": "Missing information",
        "source_kind": "typed",
        "difficulty": 1,
        "kind": "transaction",
        "expected": {"status": "BLOCKED",
                     "debit": {"Purchases"}, "credit": {"Rahul"}},
        "note": "treatment is determinable but the amount is missing",
    },
    {
        "id": "B18",
        "question": "Purchased goods for Rs.10,000.",
        "topic": "Ambiguous transaction",
        "source_kind": "typed",
        "difficulty": 2,
        "kind": "transaction",
        "expected": {"status": "REVIEW_REQUIRED"},
        "note": "cash vs credit is not stated - FT-E never assumes one",
    },
    {
        "id": "B19",
        "question": "Purchased goods from Rahul for Rs.10,000 on credit "
                    "with 10% trade discount.",
        "topic": "Discounts",
        "source_kind": "photo",
        "difficulty": 3,
        "kind": "transaction",
        "expected": {"status": "REVIEW_REQUIRED"},
        "human_reference": 9000,  # net of 10% discount - no registered
        # formula can net it, so FT-E refuses rather than guess
        "note": "trade-discount netting is not a registered formula - "
                "documented capability gap",
    },
    {
        "id": "B20",
        "question": "The Cash account balance is Rs.55,000 on the Credit "
                    "side. Check it.",
        "topic": "Ledger Verification",
        "source_kind": "typed",
        "difficulty": 2,
        "kind": "verify_ledger",
        "transactions": [
            "Started business with cash Rs.50,000.",
            "Purchased goods for cash Rs.10,000.",
            "Sold goods for cash Rs.15,000.",
        ],
        "student_account": "Cash",
        "student_balance": "55000",
        "student_side": "Cr",
        "expected": {"verdict": "INCORRECT"},
        "note": "amount is right but the side is wrong (Cash is a debit "
                "balance here)",
    },
]

FYJC_PILOT_ALL = FYJC_PILOT_MATHS + FYJC_PILOT_BK


def pilot_summary() -> dict:
    """Dataset metadata for the Sprint 15 report (pure data, no engine)."""
    maths = FYJC_PILOT_MATHS
    bk = FYJC_PILOT_BK
    return {
        "maths_count": len(maths),
        "bk_count": len(bk),
        "total": len(maths) + len(bk),
        "maths_source_kinds": sorted({c["source_kind"] for c in maths}),
        "bk_source_kinds": sorted({c["source_kind"] for c in bk}),
        "maths_difficulty": {
            str(d): sum(1 for c in maths if c["difficulty"] == d)
            for d in (1, 2, 3)
        },
        "bk_difficulty": {
            str(d): sum(1 for c in bk if c["difficulty"] == d)
            for d in (1, 2, 3)
        },
        "maths_topics": sorted({c["topic"] for c in maths}),
        "bk_kinds": sorted({c["kind"] for c in bk}),
    }
