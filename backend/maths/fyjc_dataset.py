"""
Financial Timeline Engine
Sprint 13 - FYJC Student Maths & Book-Keeping Readiness
backend/maths/fyjc_dataset.py

Dedicated FYJC golden dataset (Sprint 13 section G) - realistic
student-level cases used by scripts/fte_fyjc_readiness_test.py.

The dataset is PURE DATA: no imports from the engine, no computation.
Every expected value below is hand-verified (an independent oracle - it
never calls the solver). The Maths cases cover ONLY relationships the
existing 12A-12F registries already compute; everything else must be
refused deterministically (UNSUPPORTED / BLOCKED / REVIEW_REQUIRED).

Wording intentionally mimics what an FYJC student would actually type or
photograph (Rs./commas/percentages/OCR noise/missing amounts ...).

Pure module: no Streamlit, no AI, no network. Deterministic.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# A. MATHS - forward / reverse / refusal cases
# ---------------------------------------------------------------------------
# expect_verdict: CORRECT | INCORRECT | REFUSED
# expect_status:  STUDENT_INPUT (typed facts) | DERIVED (document/text
#                 facts) | BLOCKED | REVIEW_REQUIRED | UNSUPPORTED
# expect_display: expected display value in display units when resolved

FYJC_MATHS_CASES = [
    {
        "id": "M01",
        "question": "A firm's Revenue is Rs.10,000 and its Expenses are "
                    "Rs.6,000. What is its Profit?",
        "metric": "Profit",
        "facts": {"Revenue": 10000, "Expenses": 6000},
        "text": None,
        "student_answer": 4000,
        "expect_verdict": "CORRECT",
        "expect_display": "4000.00",
    },
    {
        "id": "M02",
        "question": "From a photo of the question paper: Revenue Rs.10,000 "
                    "and Expenses Rs.6,000. Find the Profit.",
        "metric": "Profit",
        "facts": None,
        "text": "Revenue: Rs.10,000\nExpenses: Rs.6,000",
        "student_answer": 4000,
        "expect_verdict": "CORRECT",
        "expect_display": "4000.00",
    },
    {
        "id": "M03",
        "question": "Profit Rs.200 and Revenue Rs.1,000. Calculate the "
                    "Profit Margin (%).",
        "metric": "Profit Margin",
        "facts": {"Profit": 200, "Revenue": 1000},
        "text": None,
        "student_answer": 20,
        "expect_verdict": "CORRECT",
        "expect_display": "20.00%",
    },
    {
        "id": "M04",
        "question": "Profit Rs.200 and Revenue Rs.1,000. My answer for the "
                    "Profit Margin is 30%. Is that right?",
        "metric": "Profit Margin",
        "facts": {"Profit": 200, "Revenue": 1000},
        "text": None,
        "student_answer": 30,
        "expect_verdict": "INCORRECT",
        "expect_display": "20.00%",
    },
    {
        "id": "M05",
        "question": "Net Profit Rs.200 and Equity Rs.1,000. Calculate ROE.",
        "metric": "ROE",
        "facts": {"Net Profit": 200, "Equity": 1000},
        "text": None,
        "student_answer": 20,
        "expect_verdict": "CORRECT",
        "expect_display": "20.00%",
    },
    {
        "id": "M06",
        "question": "Current Assets Rs.500 and Current Liabilities "
                    "Rs.250. Find the Current Ratio.",
        "metric": "Current Ratio",
        "facts": {"Current Assets": 500, "Current Liabilities": 250},
        "text": None,
        "student_answer": 2,
        "expect_verdict": "CORRECT",
        "expect_display": "2.00",
    },
    {
        "id": "M07",
        "question": "Revenue Rs.1,000 and Expenses Rs.1,200. Find the Loss.",
        "metric": "Loss",
        "facts": {"Revenue": 1000, "Expenses": 1200},
        "text": None,
        "student_answer": 200,
        "expect_verdict": "CORRECT",
        "expect_display": "200.00",
    },
    {
        "id": "M08",
        "question": "If the Loss is Rs.200, what is the Profit "
                    "(negative of Loss)?",
        "metric": "Profit",
        "facts": {"Loss": 200},
        "text": None,
        "student_answer": -200,
        "expect_verdict": "CORRECT",
        "expect_display": "-200.00",
    },
    {
        "id": "M09",
        "question": "Revenue Rs.1,000 and Profit Rs.200. Find the missing "
                    "figure: Expenses.",
        "metric": "Expenses",
        "facts": {"Revenue": 1000, "Profit": 200},
        "text": None,
        "student_answer": 800,
        "expect_verdict": "CORRECT",
        "expect_display": "800.00",
    },
    {
        "id": "M10",
        "question": "Profit Margin is 20% and Revenue is Rs.1,000. Find "
                    "the Profit.",
        "metric": "Profit",
        "facts": {"Profit Margin": 20, "Revenue": 1000},
        "text": None,
        "student_answer": 200,
        "expect_verdict": "CORRECT",
        "expect_display": "200.00",
    },
    {
        "id": "M11",
        "question": "Net Profit Rs.200 and Shares Outstanding 100. Find "
                    "the EPS.",
        "metric": "EPS",
        "facts": {"Net Profit": 200, "Shares Outstanding": 100},
        "text": None,
        "student_answer": 2,
        "expect_verdict": "CORRECT",
        "expect_display": "2.00",
    },
    {
        "id": "M12",
        "question": "Net Profit Rs.200 is known. Calculate ROE.",
        "metric": "ROE",
        "facts": {"Net Profit": 200},
        "text": None,
        "student_answer": None,
        "expect_verdict": "REFUSED",
        "expect_status": "BLOCKED",
        "expect_display": "—",
        "expect_missing": ["Equity"],
    },
    {
        "id": "M13",
        "question": "Calculate the Simple Interest on Rs.10,000 at 10% for "
                    "2 years.",
        "metric": "Simple Interest",
        "facts": {"Principal": 10000},
        "text": None,
        "student_answer": 2000,
        "expect_verdict": "REFUSED",
        "expect_status": "UNSUPPORTED",
        "expect_display": "—",
    },
    {
        "id": "M14",
        "question": "ROE 20% with Equity Rs.1,000, but EPS 1 with Shares "
                    "Outstanding 100. Find Net Profit.",
        "metric": "Net Profit",
        "facts": {"ROE": 20, "Equity": 1000,
                  "EPS": 1, "Shares Outstanding": 100},
        "text": None,
        "student_answer": None,
        "expect_verdict": "REFUSED",
        "expect_status": "REVIEW_REQUIRED",
        "expect_display": "—",
    },
    {
        "id": "M15",
        "question": "Net Profit Rs.200 and Equity Rs.0. Calculate ROE.",
        "metric": "ROE",
        "facts": {"Net Profit": 200, "Equity": 0},
        "text": None,
        "student_answer": None,
        "expect_verdict": "REFUSED",
        "expect_status": "BLOCKED",
        "expect_display": "—",
    },
    {
        "id": "M16",
        "question": "Current Assets Rs.5,00,000 and Current Liabilities "
                    "Rs.2,50,000. Find the Current Ratio.",
        "metric": "Current Ratio",
        "facts": None,
        "text": "Current Assets: Rs.5,00,000\n"
                "Current Liabilities: Rs.2,50,000",
        "student_answer": 2,
        "expect_verdict": "CORRECT",
        "expect_display": "2.00",
    },
    {
        "id": "M17",
        "question": "Revenue reads 1.234,56 and Expenses Rs.600. Find "
                    "Profit.",
        "metric": "Profit",
        "facts": None,
        "text": "Revenue: 1.234,56\nExpenses: 600",
        "student_answer": None,
        "expect_verdict": "REFUSED",
        "expect_status": "BLOCKED",
        "expect_display": "—",
    },
    {
        "id": "M18",
        "question": "Revenue Rs.50,000 and Cost of Sales Rs.30,000. Find "
                    "the Gross Profit.",
        "metric": "Gross Profit",
        "facts": {"Revenue": 50000, "Cost of Sales": 30000},
        "text": None,
        "student_answer": 20000,
        "expect_verdict": "CORRECT",
        "expect_display": "20000.00",
    },
]

# ---------------------------------------------------------------------------
# B. ACCOUNTANCY - transaction classification (golden rules)
# ---------------------------------------------------------------------------
# expect_status: VERIFIED | BLOCKED | REVIEW_REQUIRED
# expect_debit / expect_credit: account sets (parties are personal
# accounts); expect_rule_key: the deterministic pattern key when known.

FYJC_ACCOUNTING_CASES = [
    {
        "id": "A01",
        "question": "Started business with cash Rs.50,000.",
        "expect_status": "VERIFIED",
        "expect_debit": {"Cash"},
        "expect_credit": {"Capital"},
        "expect_rule_key": "START_BUSINESS",
    },
    {
        "id": "A02",
        "question": "Purchased goods for cash Rs.10,000.",
        "expect_status": "VERIFIED",
        "expect_debit": {"Purchases"},
        "expect_credit": {"Cash"},
        "expect_rule_key": "PURCHASE_CASH",
    },
    {
        "id": "A03",
        "question": "Purchased goods on credit from Rahul for Rs.10,000.",
        "expect_status": "VERIFIED",
        "expect_debit": {"Purchases"},
        "expect_credit": {"Rahul"},
        "expect_rule_key": "PURCHASE_CREDIT",
    },
    {
        "id": "A04",
        "question": "Purchased goods from Rahul for Rs.10,000.",
        "expect_status": "VERIFIED",
        "expect_debit": {"Purchases"},
        "expect_credit": {"Rahul"},
        "expect_rule_key": "PURCHASE_CREDIT",
    },
    {
        "id": "A05",
        "question": "Sold goods for cash Rs.15,000.",
        "expect_status": "VERIFIED",
        "expect_debit": {"Cash"},
        "expect_credit": {"Sales"},
        "expect_rule_key": "SALE_CASH",
    },
    {
        "id": "A06",
        "question": "Sold goods on credit to Mohan Rs.15,000.",
        "expect_status": "VERIFIED",
        "expect_debit": {"Mohan"},
        "expect_credit": {"Sales"},
        "expect_rule_key": "SALE_CREDIT",
    },
    {
        "id": "A07",
        "question": "Sold goods to Mohan for Rs.5,000.",
        "expect_status": "VERIFIED",
        "expect_debit": {"Mohan"},
        "expect_credit": {"Sales"},
        "expect_rule_key": "SALE_CREDIT",
    },
    {
        "id": "A08",
        "question": "Paid rent Rs.5,000.",
        "expect_status": "VERIFIED",
        "expect_debit": {"Rent"},
        "expect_credit": {"Cash"},
        "expect_rule_key": "EXPENSE_PAID",
    },
    {
        "id": "A09",
        "question": "Received commission Rs.2,000.",
        "expect_status": "VERIFIED",
        "expect_debit": {"Cash"},
        "expect_credit": {"Commission Received"},
        "expect_rule_key": "INCOME_RECEIVED",
    },
    {
        "id": "A10",
        "question": "Withdrew cash for personal use Rs.3,000.",
        "expect_status": "VERIFIED",
        "expect_debit": {"Drawings"},
        "expect_credit": {"Cash"},
        "expect_rule_key": "DRAWINGS_CASH",
    },
    {
        "id": "A11",
        "question": "Deposited cash into bank Rs.8,000.",
        "expect_status": "VERIFIED",
        "expect_debit": {"Bank"},
        "expect_credit": {"Cash"},
        "expect_rule_key": "CASH_INTO_BANK",
    },
    {
        "id": "A12",
        "question": "Took a loan from the bank Rs.50,000.",
        "expect_status": "VERIFIED",
        "expect_debit": {"Bank"},
        "expect_credit": {"Loan"},
        "expect_rule_key": "LOAN_TAKEN",
    },
    {
        "id": "A13",
        "question": "Returned goods to Rahul Rs.1,000.",
        "expect_status": "VERIFIED",
        "expect_debit": {"Rahul"},
        "expect_credit": {"Purchase Returns"},
        "expect_rule_key": "PURCHASE_RETURN",
    },
    {
        "id": "A14",
        "question": "Goods returned by Mohan Rs.500.",
        "expect_status": "VERIFIED",
        "expect_debit": {"Sales Returns"},
        "expect_credit": {"Mohan"},
        "expect_rule_key": "SALES_RETURN",
    },
    {
        "id": "A15",
        "question": "Paid salaries Rs.12,000.",
        "expect_status": "VERIFIED",
        "expect_debit": {"Salaries"},
        "expect_credit": {"Cash"},
        "expect_rule_key": "EXPENSE_PAID",
    },
    {
        "id": "A16",
        "question": "Purchased goods for cash.",
        "expect_status": "BLOCKED",
        "expect_debit": {"Purchases"},
        "expect_credit": {"Cash"},
        "expect_rule_key": "PURCHASE_CASH",
        "expect_missing": "amount",
    },
    {
        "id": "A17",
        "question": "Purchased goods Rs.5,000.",
        "expect_status": "REVIEW_REQUIRED",
        "expect_debit": set(),
        "expect_credit": set(),
        "expect_rule_key": None,
    },
    {
        "id": "A18",
        "question": "Sold goods Rs.5,000.",
        "expect_status": "REVIEW_REQUIRED",
        "expect_debit": set(),
        "expect_credit": set(),
        "expect_rule_key": None,
    },
    {
        "id": "A19",
        "question": "Purchased goods for cash Rs.15,000 and paid rent "
                    "Rs.2,000.",
        "expect_status": "REVIEW_REQUIRED",
        "expect_debit": set(),
        "expect_credit": set(),
        "expect_rule_key": None,
    },
    {
        "id": "A20",
        "question": "Purchased goods for cash Rs.1,0X0.",
        "expect_status": "REVIEW_REQUIRED",
        "expect_debit": set(),
        "expect_credit": set(),
        "expect_rule_key": None,
    },
]

# ---------------------------------------------------------------------------
# C. JOURNAL VERIFICATION - correct / incorrect / refused student entries
# ---------------------------------------------------------------------------
# entries use the student shape: {"debits": [{account, amount}],
# "credits": [{account, amount}]}. expect_verdict: CORRECT | INCORRECT |
# REFUSED | BALANCED. expect_discrepancy when arithmetic differs.

FYJC_JOURNAL_CASES = [
    {
        "id": "J01",
        "description": "Purchased goods for cash Rs.10,000.",
        "entry": {"debits": [{"account": "Purchases", "amount": 10000}],
                  "credits": [{"account": "Cash", "amount": 10000}]},
        "expect_verdict": "CORRECT",
    },
    {
        "id": "J02",
        "description": "Purchased goods for cash Rs.10,000.",
        "entry": {"debits": [{"account": "Purchases", "amount": 10000}],
                  "credits": [{"account": "Cash", "amount": 9000}]},
        "expect_verdict": "INCORRECT",
        "expect_discrepancy": 1000.0,
    },
    {
        "id": "J03",
        "description": "Sold goods on credit to Mohan Rs.15,000.",
        "entry": {"debits": [{"account": "Mohan", "amount": 15000}],
                  "credits": [{"account": "Sales", "amount": 15000}]},
        "expect_verdict": "CORRECT",
    },
    {
        "id": "J04",
        "description": "Purchased goods on credit from Rahul for Rs.10,000.",
        "entry": {"debits": [{"account": "Purchases", "amount": 10000}],
                  "credits": [{"account": "Rahul", "amount": 10000}]},
        "expect_verdict": "CORRECT",
    },
    {
        "id": "J05",
        "description": "Sold goods for cash Rs.15,000.",
        "entry": {"debits": [{"account": "Sales", "amount": 15000}],
                  "credits": [{"account": "Cash", "amount": 15000}]},
        "expect_verdict": "INCORRECT",
    },
    {
        "id": "J06",
        "description": "Purchased goods for cash Rs.10,000.",
        "entry": {"debits": [{"account": "Purchases"}],
                  "credits": [{"account": "Cash", "amount": 10000}]},
        "expect_verdict": "REFUSED",
    },
    {
        "id": "J07",
        "description": None,
        "entry": {"debits": [{"account": "Cash", "amount": 5000}],
                  "credits": [{"account": "Bank", "amount": 5000}]},
        "expect_verdict": "BALANCED",
    },
]

# ---------------------------------------------------------------------------
# D. LEDGER - posting + balance verification
# ---------------------------------------------------------------------------

# The standard FYJC mini-scenario used by the ledger / trial-balance cases:
#  1) started business with cash 50,000
#  2) purchased goods for cash 10,000
#  3) sold goods for cash 15,000
# Hand-verified ledger balances:
#  Cash:      Dr 65,000 - Cr 10,000 = 55,000 Dr
#  Capital:   Cr 50,000                     = 50,000 Cr
#  Purchases: Dr 10,000                     = 10,000 Dr
#  Sales:     Cr 15,000                     = 15,000 Cr
#  Totals:    Dr 75,000 = Cr 75,000 (balanced)
FYJC_LEDGER_ENTRIES = [
    {"debits": [{"account": "Cash", "amount": 50000}],
     "credits": [{"account": "Capital", "amount": 50000}]},
    {"debits": [{"account": "Purchases", "amount": 10000}],
     "credits": [{"account": "Cash", "amount": 10000}]},
    {"debits": [{"account": "Cash", "amount": 15000}],
     "credits": [{"account": "Sales", "amount": 15000}]},
]

FYJC_LEDGER_EXPECT = {
    "Cash": {"debit": 65000.0, "credit": 10000.0, "balance": 55000.0,
             "balance_side": "Dr"},
    "Capital": {"debit": 0.0, "credit": 50000.0, "balance": -50000.0,
                "balance_side": "Cr"},
    "Purchases": {"debit": 10000.0, "credit": 0.0, "balance": 10000.0,
                  "balance_side": "Dr"},
    "Sales": {"debit": 0.0, "credit": 15000.0, "balance": -15000.0,
              "balance_side": "Cr"},
}
FYJC_LEDGER_TOTALS = {"total_debit": 75000.0, "total_credit": 75000.0}

FYJC_LEDGER_VERIFY_CASES = [
    {"id": "L01", "account": "Cash", "student_balance": 55000,
     "student_side": "Dr", "expect_verdict": "CORRECT"},
    {"id": "L02", "account": "Cash", "student_balance": 55000,
     "student_side": "Cr", "expect_verdict": "INCORRECT"},
    {"id": "L03", "account": "Cash", "student_balance": 54000,
     "student_side": "Dr", "expect_verdict": "INCORRECT",
     "expect_discrepancy": 1000.0},
]

# ---------------------------------------------------------------------------
# E. TRIAL BALANCE - construction + tally detection + discrepancies
# ---------------------------------------------------------------------------
# From the ledger above the trial balance is:
#  Cash Dr 55,000 | Capital Cr 50,000 | Purchases Dr 10,000 | Sales Cr 15,000
#  Dr total 65,000 = Cr total 65,000  -> TALLIES

FYJC_TB_EXPECT = {
    "total_debit": 65000.0,
    "total_credit": 65000.0,
    "balanced": True,
    "rows": {
        "Cash": {"debit": 55000.0, "credit": 0.0},
        "Capital": {"debit": 0.0, "credit": 50000.0},
        "Purchases": {"debit": 10000.0, "credit": 0.0},
        "Sales": {"debit": 0.0, "credit": 15000.0},
    },
}

FYJC_TB_STUDENT_CORRECT = [
    {"account": "Cash", "debit": 55000, "credit": 0},
    {"account": "Capital", "debit": 0, "credit": 50000},
    {"account": "Purchases", "debit": 10000, "credit": 0},
    {"account": "Sales", "debit": 0, "credit": 15000},
]

FYJC_TB_CASES = [
    {
        "id": "T01",
        "student_rows": FYJC_TB_STUDENT_CORRECT,
        "expect_verdict": "CORRECT",
    },
    {
        "id": "T02",
        "student_rows": [
            {"account": "Cash", "debit": 55000, "credit": 0},
            {"account": "Capital", "debit": 0, "credit": 50000},
            {"account": "Purchases", "debit": 10000, "credit": 0},
            # Sales row missing
        ],
        "expect_verdict": "INCORRECT",
        "expect_mention": "Sales",
    },
    {
        "id": "T03",
        "student_rows": [
            {"account": "Cash", "debit": 54000, "credit": 0},
            {"account": "Capital", "debit": 0, "credit": 50000},
            {"account": "Purchases", "debit": 10000, "credit": 0},
            {"account": "Sales", "debit": 0, "credit": 15000},
        ],
        "expect_verdict": "INCORRECT",
        "expect_mention": "Cash",
    },
    {
        "id": "T04",
        "student_rows": [
            {"account": "Cash", "debit": 56000, "credit": 0},
            {"account": "Capital", "debit": 0, "credit": 50000},
            {"account": "Purchases", "debit": 10000, "credit": 0},
            {"account": "Sales", "debit": 0, "credit": 15000},
        ],
        "expect_verdict": "INCORRECT",
        "expect_discrepancy": 1000.0,
    },
]

# ---------------------------------------------------------------------------
# F. QUESTION CLASSIFICATION (section C / test area 1)
# ---------------------------------------------------------------------------

FYJC_QUESTION_CASES = [
    {
        "id": "Q01",
        "question": "Calculate the Profit Margin when Profit is Rs.200 and "
                    "Revenue is Rs.1,000.",
        "expect_domain": "maths",
        "expect_kind": "metric",
        "expect_metric": "profit margin",
    },
    {
        "id": "Q02",
        "question": "What is the Current Ratio? Current Assets Rs.5,00,000; "
                    "Current Liabilities Rs.2,50,000.",
        "expect_domain": "maths",
        "expect_kind": "metric",
        "expect_metric": "current ratio",
    },
    {
        "id": "Q03",
        "question": "Pass the following journal entries in the books of Ram.",
        "expect_domain": "bookkeeping",
        "expect_kind": "journal",
    },
    {
        "id": "Q04",
        "question": "Journalise the following transactions.",
        "expect_domain": "bookkeeping",
        "expect_kind": "journal",
    },
    {
        "id": "Q05",
        "question": "Post the following transactions to the ledger and "
                    "balance the accounts.",
        "expect_domain": "bookkeeping",
        "expect_kind": "ledger",
    },
    {
        "id": "Q06",
        "question": "Prepare a Trial Balance from the following ledger "
                    "balances.",
        "expect_domain": "bookkeeping",
        "expect_kind": "trial_balance",
    },
    {
        "id": "Q07",
        "question": "Purchased goods from Rahul on credit for Rs.10,000.",
        "expect_domain": "bookkeeping",
        "expect_kind": "transaction",
    },
    {
        "id": "Q08",
        "question": "Paid rent by cheque Rs.5,000.",
        "expect_domain": "bookkeeping",
        "expect_kind": "transaction",
    },
    {
        "id": "Q09",
        "question": "From the following information calculate the Gross "
                    "Profit: Revenue Rs.50,000; Cost of Sales Rs.30,000.",
        "expect_domain": "maths",
        "expect_kind": "metric",
        "expect_metric": "gross profit",
    },
    {
        "id": "Q10",
        "question": "Find the simple interest on Rs.10,000 at 10% for 2 "
                    "years.",
        "expect_domain": "maths",
        "expect_kind": "metric",
        "expect_metric": None,
    },
    {
        "id": "Q11",
        "question": "Explain the concept of goodwill.",
        "expect_domain": "unrecognised",
        "expect_kind": "unknown",
    },
]

# ---------------------------------------------------------------------------
# G. STUDENT ACCEPTANCE (section I) - realistic student workflow
# ---------------------------------------------------------------------------

FYJC_ACCEPTANCE_CASES = [
    {
        "id": "S01",
        "question": "Photo of textbook question: 'Calculate the Current "
                    "Ratio. Current Assets Rs.5,00,000 and Current "
                    "Liabilities Rs.2,50,000.'",
        "metric": "Current Ratio",
        "text": "Current Assets: Rs.5,00,000\n"
                "Current Liabilities: Rs.2,50,000",
        "student_answer": 2,
        "expect_verdict": "CORRECT",
        "expect_display": "2.00",
    },
    {
        "id": "S02",
        "question": "Purchased goods from Rahul on credit for Rs.10,000.",
        "kind": "journal",
        "expect_status": "VERIFIED",
        "expect_debit": {"Purchases"},
        "expect_credit": {"Rahul"},
    },
    {
        "id": "S03",
        "question": "Purchased goods from Rahul on credit for Rs.10,000.",
        "kind": "journal_verify",
        "description": "Purchased goods from Rahul on credit for Rs.10,000.",
        "entry": {"debits": [{"account": "Purchases", "amount": 10000}],
                  "credits": [{"account": "Cash", "amount": 10000}]},
        "expect_verdict": "INCORRECT",
    },
    {
        "id": "S04",
        "question": "Purchased goods from Rahul on credit for Rs.10,000.",
        "kind": "journal_verify",
        "description": "Purchased goods from Rahul on credit for Rs.10,000.",
        "entry": {"debits": [{"account": "Purchases", "amount": 10000}],
                  "credits": [{"account": "Rahul", "amount": 10000}]},
        "expect_verdict": "CORRECT",
    },
    {
        "id": "S05",
        "question": "Calculate the Current Ratio. Current Assets "
                    "Rs.5,00,000 (Current Liabilities not provided).",
        "metric": "Current Ratio",
        "text": "Current Assets: Rs.5,00,000",
        "student_answer": None,
        "expect_verdict": "REFUSED",
        "expect_status": "BLOCKED",
        "expect_next_action_mentions": "Current Liabilities",
    },
]
