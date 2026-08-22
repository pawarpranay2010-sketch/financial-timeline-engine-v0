"""
Platrixa
Sprint 15H - Real-World FYJC BK Validation & Adversarial Hardening benchmark
backend/maths/fyjc_bk_15h_benchmark.py

An INDEPENDENT hand-written golden corpus for the first three FYJC
Book-Keeping & Accountancy chapters (Unit-Test-1 scope - the EXACT 15E/15F
boundary). Independence rule: every expected value below was written from
the standard FYJC journal rules (Real/Personal/Nominal golden rules) and
textbook-style wording - the corpus NEVER calls the engine and is NOT
generated from the 15F pattern registry. It is deliberately able to expose
missing patterns (and several did - see docs/FYJC_15H_COVERAGE.md for the
oracle-correction log and the fixes they produced).

Breakdown:
  * REAL_QUESTION_CASES      - 38 genuine/faithfully transcribed textbook,
                               worksheet and homework-style questions with
                               full record fields (source, chapter, wording,
                               expected accounts/type/amounts/outcome).
  * ADVERSARIAL_MATRIX       - 22 linguistic variants: equivalent-word
                               families that MUST collapse onto ONE
                               canonical treatment + deliberately
                               misleading wording (cash discount != cash
                               purchase, 'on account' == 'on credit',
                               partial payment != full cash purchase, ...).
  * MULTI_TRANSACTION_STRESS - 9 realistic chained questions (semicolons,
                               sentences, pronouns, continuation payments,
                               discount continuation, unrelated follow-on
                               transactions).
  * AMBIGUITY_ATTACKS        - 12 cases a human would ask about -> the
                               oracle demands REVIEW_REQUIRED / BLOCKED
                               (never a guessed answer).
  * STUDENT_ERROR_15H        - 13 student-answer cases: the ten error
                               categories (spec section 5) + three
                               COMBINED-error answers (invented account +
                               unbalanced, wrong account + wrong amount,
                               wrong side + wrong amount) where the gate
                               asserts every diagnostic field agrees.
  * OCR_BOUNDARY_CASES       - 9 extraction-quality cases (Good / Uncertain
                               / Unusable) with explicit OCR signals; the
                               oracle says whether to process, ask, or block.
  * FIX_REGRESSION_CASES     - the exact questions whose failures produced
                               the Sprint 15H minimal fixes (permanent
                               regression pins).

Case fields (REAL / ADVERSARIAL / MULTI / AMBIGUITY)
  question / original_wording / normalized
  source, category, chapter
  status    : VERIFIED | BLOCKED | REVIEW_REQUIRED | NOT_SUPPORTED
  type_key  : expected canonical transaction type (VERIFIED single-tx)
  journals  : expected number of independent journal entries
  debit     : expected DEBIT lines across all journals, (account, amount)
  credit    : expected CREDIT lines across all journals, (account, amount)
  expected_outcome / refusal_conditions : documentation fields

Amounts are integers (rupees); accounts use the canonical FYJC chart
spelling (computer -> Equipment is the canonical chart mapping).
"""

# ---------------------------------------------------------------------------
# A. 38 real-question cases (textbook / worksheet / homework style)
# ---------------------------------------------------------------------------
REAL_QUESTION_CASES = [
    # -- Ch.2 capital & drawings ------------------------------------------
    {"source": "textbook-style - capital introduction",
     "category": "classwork/homework", "chapter": "Ch.2 Basic Accounting Terms",
     "question": "Ramesh started business with cash Rs.80,000.",
     "original_wording": "Ramesh started business with cash Rs.80,000.",
     "normalized": "Started business with cash Rs.80,000.",
     "status": "VERIFIED", "type_key": "START_BUSINESS", "journals": 1,
     "debit": [("Cash", 80000)], "credit": [("Capital", 80000)],
     "expected_outcome": "Cash Dr 80,000 / Capital Cr 80,000",
     "refusal_conditions": "amount missing -> BLOCKED"},
    {"source": "teacher worksheet - compound start",
     "category": "classwork/homework", "chapter": "Ch.2 Basic Accounting Terms",
     "question": "Sheela commenced business with cash Rs.1,00,000 and "
                 "furniture worth Rs.50,000.",
     "original_wording": "Sheela commenced business with cash Rs.1,00,000 "
                         "and furniture worth Rs.50,000.",
     "normalized": "Commenced business with cash Rs.1,00,000 and furniture "
                   "worth Rs.50,000.",
     "status": "VERIFIED", "type_key": "START_BUSINESS", "journals": 1,
     "debit": [("Cash", 100000), ("Furniture", 50000)],
     "credit": [("Capital", 150000)],
     "expected_outcome": "Cash 1,00,000 + Furniture 50,000 Dr / Capital "
                         "1,50,000 Cr (split never guessed)",
     "refusal_conditions": ">1 named asset or unreadable split -> refused"},
    {"source": "homework - bank capital",
     "category": "classwork/homework", "chapter": "Ch.2 Basic Accounting Terms",
     "question": "Sharma started business with bank balance Rs.2,00,000.",
     "original_wording": "Sharma started business with bank balance "
                         "Rs.2,00,000.",
     "normalized": "Started business with bank balance Rs.2,00,000.",
     "status": "VERIFIED", "type_key": "START_BUSINESS", "journals": 1,
     "debit": [("Bank", 200000)], "credit": [("Capital", 200000)],
     "expected_outcome": "Bank Dr 2,00,000 / Capital Cr 2,00,000",
     "refusal_conditions": "amount missing -> BLOCKED"},
    {"source": "textbook exercise style",
     "category": "classwork/homework", "chapter": "Ch.2 Basic Accounting Terms",
     "question": "Brought additional capital in cash Rs.30,000.",
     "original_wording": "Brought additional capital in cash Rs.30,000.",
     "normalized": "Brought additional capital in cash Rs.30,000.",
     "status": "VERIFIED", "type_key": "CAPITAL_INTRODUCED", "journals": 1,
     "debit": [("Cash", 30000)], "credit": [("Capital", 30000)],
     "expected_outcome": "Cash Dr 30,000 / Capital Cr 30,000",
     "refusal_conditions": "amount missing -> BLOCKED"},
    {"source": "worksheet - passive-voice drawings",
     "category": "teacher-style worksheet", "chapter": "Ch.2 Basic Accounting Terms",
     "question": "Cash withdrawn by proprietor for personal expenses Rs.2,000.",
     "original_wording": "Cash withdrawn by proprietor for personal expenses "
                         "Rs.2,000.",
     "normalized": "Cash withdrawn by proprietor for personal expenses "
                   "Rs.2,000.",
     "status": "VERIFIED", "type_key": "DRAWINGS_CASH", "journals": 1,
     "debit": [("Drawings", 2000)], "credit": [("Cash", 2000)],
     "expected_outcome": "Drawings Dr 2,000 / Cash Cr 2,000",
     "refusal_conditions": "amount missing -> BLOCKED"},
    {"source": "textbook - drawings in goods",
     "category": "classwork/homework", "chapter": "Ch.2 Basic Accounting Terms",
     "question": "Withdrew goods worth Rs.3,000 for personal use.",
     "original_wording": "Withdrew goods worth Rs.3,000 for personal use.",
     "normalized": "Withdrew goods worth Rs.3,000 for personal use.",
     "status": "VERIFIED", "type_key": "GOODS_PERSONAL_USE", "journals": 1,
     "debit": [("Drawings", 3000)], "credit": [("Purchases", 3000)],
     "expected_outcome": "Drawings Dr 3,000 / Purchases Cr 3,000 (goods, "
                         "not cash)",
     "refusal_conditions": "amount missing -> BLOCKED"},

    # -- Ch.3 journal: purchases & sales ----------------------------------
    {"source": "textbook exercise style",
     "category": "classwork/homework", "chapter": "Ch.3 Journal",
     "question": "Purchased goods from Suresh on credit Rs.25,000.",
     "original_wording": "Purchased goods from Suresh on credit Rs.25,000.",
     "normalized": "Purchased goods from Suresh on credit Rs.25,000.",
     "status": "VERIFIED", "type_key": "PURCHASE_GOODS_CREDIT", "journals": 1,
     "debit": [("Purchases", 25000)], "credit": [("Suresh", 25000)],
     "expected_outcome": "Purchases Dr 25,000 / Suresh Cr 25,000",
     "refusal_conditions": "amount missing -> BLOCKED; no party -> "
                           "REVIEW_REQUIRED"},
    {"source": "textbook exercise style",
     "category": "classwork/homework", "chapter": "Ch.3 Journal",
     "question": "Sold goods to Kavita for cash Rs.15,000.",
     "original_wording": "Sold goods to Kavita for cash Rs.15,000.",
     "normalized": "Sold goods to Kavita for cash Rs.15,000.",
     "status": "VERIFIED", "type_key": "SALE_GOODS_CASH", "journals": 1,
     "debit": [("Cash", 15000)], "credit": [("Sales", 15000)],
     "expected_outcome": "Cash Dr 15,000 / Sales Cr 15,000 (Kavita never "
                         "becomes a debtor)",
     "refusal_conditions": "amount missing -> BLOCKED"},
    {"source": "exam-format - cost/sale split",
     "category": "common exam format", "chapter": "Ch.3 Journal",
     "question": "Goods costing Rs.40,000 sold for cash Rs.48,000.",
     "original_wording": "Goods costing Rs.40,000 sold for cash Rs.48,000.",
     "normalized": "Goods costing Rs.40,000 sold for cash Rs.48,000.",
     "status": "VERIFIED", "type_key": "SALE_GOODS_CASH", "journals": 1,
     "debit": [("Cash", 48000)], "credit": [("Sales", 48000)],
     "expected_outcome": "Cash Dr 48,000 / Sales Cr 48,000 (cost 40,000 "
                         "is not posted)",
     "refusal_conditions": "amount missing -> BLOCKED"},
    {"source": "textbook - asset purchase on credit",
     "category": "classwork/homework", "chapter": "Ch.3 Journal",
     "question": "Bought machinery from Amar on credit Rs.1,50,000.",
     "original_wording": "Bought machinery from Amar on credit Rs.1,50,000.",
     "normalized": "Bought machinery from Amar on credit Rs.1,50,000.",
     "status": "VERIFIED", "type_key": "PURCHASE_ASSET_CREDIT", "journals": 1,
     "debit": [("Machinery", 150000)], "credit": [("Amar", 150000)],
     "expected_outcome": "Machinery Dr 1,50,000 / Amar Cr 1,50,000",
     "refusal_conditions": "amount missing -> BLOCKED; mode unstated -> "
                           "REVIEW_REQUIRED"},
    {"source": "worksheet - computer asset",
     "category": "teacher-style worksheet", "chapter": "Ch.3 Journal",
     "question": "Purchased a computer for office use for cash Rs.45,000.",
     "original_wording": "Purchased a computer for office use for cash "
                         "Rs.45,000.",
     "normalized": "Purchased a computer for office use for cash Rs.45,000.",
     "status": "VERIFIED", "type_key": "PURCHASE_ASSET_CASH", "journals": 1,
     "debit": [("Equipment", 45000)], "credit": [("Cash", 45000)],
     "expected_outcome": "Equipment Dr 45,000 / Cash Cr 45,000 (canonical "
                         "chart: computer -> Equipment)",
     "refusal_conditions": "amount missing -> BLOCKED"},
    {"source": "textbook - asset sale",
     "category": "classwork/homework", "chapter": "Ch.3 Journal",
     "question": "Sold old furniture for cash Rs.8,000.",
     "original_wording": "Sold old furniture for cash Rs.8,000.",
     "normalized": "Sold old furniture for cash Rs.8,000.",
     "status": "VERIFIED", "type_key": "SALE_ASSET_CASH", "journals": 1,
     "debit": [("Cash", 8000)], "credit": [("Furniture", 8000)],
     "expected_outcome": "Cash Dr 8,000 / Furniture Cr 8,000",
     "refusal_conditions": "amount missing -> BLOCKED"},
    {"source": "textbook - purchase return",
     "category": "classwork/homework", "chapter": "Ch.3 Journal",
     "question": "Returned goods to Suresh worth Rs.2,000.",
     "original_wording": "Returned goods to Suresh worth Rs.2,000.",
     "normalized": "Returned goods to Suresh worth Rs.2,000.",
     "status": "VERIFIED", "type_key": "PURCHASE_RETURN", "journals": 1,
     "debit": [("Suresh", 2000)], "credit": [("Purchase Returns", 2000)],
     "expected_outcome": "Suresh Dr 2,000 / Purchase Returns Cr 2,000",
     "refusal_conditions": "party-less standalone return -> REVIEW_REQUIRED"},

    # -- Ch.3 journal: expenses & incomes ----------------------------------
    {"source": "textbook exercise style",
     "category": "classwork/homework", "chapter": "Ch.3 Journal",
     "question": "Paid salaries Rs.12,000.",
     "original_wording": "Paid salaries Rs.12,000.",
     "normalized": "Paid salaries Rs.12,000.",
     "status": "VERIFIED", "type_key": "EXPENSE_PAID", "journals": 1,
     "debit": [("Salaries", 12000)], "credit": [("Cash", 12000)],
     "expected_outcome": "Salaries Dr 12,000 / Cash Cr 12,000",
     "refusal_conditions": "amount missing -> BLOCKED"},
    {"source": "exam-format - cheque payment",
     "category": "common exam format", "chapter": "Ch.3 Journal",
     "question": "Paid rent by cheque Rs.6,000.",
     "original_wording": "Paid rent by cheque Rs.6,000.",
     "normalized": "Paid rent by cheque Rs.6,000.",
     "status": "VERIFIED", "type_key": "EXPENSE_PAID", "journals": 1,
     "debit": [("Rent", 6000)], "credit": [("Bank", 6000)],
     "expected_outcome": "Rent Dr 6,000 / Bank Cr 6,000 (by cheque -> bank)",
     "refusal_conditions": "amount missing -> BLOCKED"},
    {"source": "worksheet - insurance",
     "category": "teacher-style worksheet", "chapter": "Ch.3 Journal",
     "question": "Paid insurance premium Rs.4,500.",
     "original_wording": "Paid insurance premium Rs.4,500.",
     "normalized": "Paid insurance premium Rs.4,500.",
     "status": "VERIFIED", "type_key": "EXPENSE_PAID", "journals": 1,
     "debit": [("Insurance", 4500)], "credit": [("Cash", 4500)],
     "expected_outcome": "Insurance Dr 4,500 / Cash Cr 4,500",
     "refusal_conditions": "amount missing -> BLOCKED"},
    {"source": "homework - electricity",
     "category": "classwork/homework", "chapter": "Ch.3 Journal",
     "question": "Paid electricity bill Rs.3,200.",
     "original_wording": "Paid electricity bill Rs.3,200.",
     "normalized": "Paid electricity bill Rs.3,200.",
     "status": "VERIFIED", "type_key": "EXPENSE_PAID", "journals": 1,
     "debit": [("Electricity", 3200)], "credit": [("Cash", 3200)],
     "expected_outcome": "Electricity Dr 3,200 / Cash Cr 3,200",
     "refusal_conditions": "amount missing -> BLOCKED"},
    {"source": "textbook - stationery + postage (two entries)",
     "category": "classwork/homework", "chapter": "Ch.3 Journal",
     "question": "Purchased stationery for Rs.500; paid postage Rs.250.",
     "original_wording": "Purchased stationery for Rs.500; paid postage "
                         "Rs.250.",
     "normalized": "Purchased stationery for Rs.500; paid postage Rs.250.",
     "status": "VERIFIED", "type_key": "EXPENSE_PAID", "journals": 2,
     "debit": [("Stationery", 500), ("Postage", 250)],
     "credit": [("Cash", 500), ("Cash", 250)],
     "expected_outcome": "Two independent expense entries",
     "refusal_conditions": "any segment missing amount -> BLOCKED"},
    {"source": "textbook - capital expenditure (installation)",
     "category": "teacher-style worksheet", "chapter": "Ch.3 Journal",
     "question": "Paid wages for installation of machinery Rs.5,000.",
     "original_wording": "Paid wages for installation of machinery Rs.5,000.",
     "normalized": "Paid wages for installation of machinery Rs.5,000.",
     "status": "VERIFIED", "type_key": "CAPITALISE_EXPENSE", "journals": 1,
     "debit": [("Machinery", 5000)], "credit": [("Cash", 5000)],
     "expected_outcome": "Machinery Dr 5,000 / Cash Cr 5,000 (installation "
                         "wages are capitalised, never a Wages expense)",
     "refusal_conditions": ">1 named asset -> refused"},

    # -- Ch.3 journal: incomes ---------------------------------------------
    {"source": "textbook exercise style",
     "category": "classwork/homework", "chapter": "Ch.3 Journal",
     "question": "Received commission Rs.2,000.",
     "original_wording": "Received commission Rs.2,000.",
     "normalized": "Received commission Rs.2,000.",
     "status": "VERIFIED", "type_key": "INCOME_RECEIVED", "journals": 1,
     "debit": [("Cash", 2000)], "credit": [("Commission Received", 2000)],
     "expected_outcome": "Cash Dr 2,000 / Commission Received Cr 2,000",
     "refusal_conditions": "amount missing -> BLOCKED"},
    {"source": "worksheet - interest from bank",
     "category": "teacher-style worksheet", "chapter": "Ch.3 Journal",
     "question": "Received interest from bank Rs.1,500.",
     "original_wording": "Received interest from bank Rs.1,500.",
     "normalized": "Received interest from bank Rs.1,500.",
     "status": "VERIFIED", "type_key": "INCOME_RECEIVED", "journals": 1,
     "debit": [("Bank", 1500)], "credit": [("Interest Received", 1500)],
     "expected_outcome": "Bank Dr 1,500 / Interest Received Cr 1,500",
     "refusal_conditions": "amount missing -> BLOCKED"},

    # -- Ch.3 journal: bank / cash ----------------------------------------
    {"source": "textbook exercise style",
     "category": "classwork/homework", "chapter": "Ch.3 Journal",
     "question": "Deposited cash into bank Rs.10,000.",
     "original_wording": "Deposited cash into bank Rs.10,000.",
     "normalized": "Deposited cash into bank Rs.10,000.",
     "status": "VERIFIED", "type_key": "CASH_INTO_BANK", "journals": 1,
     "debit": [("Bank", 10000)], "credit": [("Cash", 10000)],
     "expected_outcome": "Bank Dr 10,000 / Cash Cr 10,000 (contra)",
     "refusal_conditions": "amount missing -> BLOCKED"},
    {"source": "homework - withdrawal for office",
     "category": "classwork/homework", "chapter": "Ch.3 Journal",
     "question": "Withdrew cash from bank for office use Rs.5,000.",
     "original_wording": "Withdrew cash from bank for office use Rs.5,000.",
     "normalized": "Withdrew cash from bank for office use Rs.5,000.",
     "status": "VERIFIED", "type_key": "CASH_FROM_BANK", "journals": 1,
     "debit": [("Cash", 5000)], "credit": [("Bank", 5000)],
     "expected_outcome": "Cash Dr 5,000 / Bank Cr 5,000 (contra)",
     "refusal_conditions": "amount missing -> BLOCKED"},
    {"source": "exam-format - payment to party",
     "category": "common exam format", "chapter": "Ch.3 Journal",
     "question": "Paid cash to Rahul Rs.6,000.",
     "original_wording": "Paid cash to Rahul Rs.6,000.",
     "normalized": "Paid cash to Rahul Rs.6,000.",
     "status": "VERIFIED", "type_key": "PAID_TO", "journals": 1,
     "debit": [("Rahul", 6000)], "credit": [("Cash", 6000)],
     "expected_outcome": "Rahul Dr 6,000 / Cash Cr 6,000",
     "refusal_conditions": "amount missing -> BLOCKED; no party -> "
                           "REVIEW_REQUIRED"},
    {"source": "exam-format - receipt from party",
     "category": "common exam format", "chapter": "Ch.3 Journal",
     "question": "Received cash from Mohan Rs.8,000.",
     "original_wording": "Received cash from Mohan Rs.8,000.",
     "normalized": "Received cash from Mohan Rs.8,000.",
     "status": "VERIFIED", "type_key": "RECEIVED_FROM", "journals": 1,
     "debit": [("Cash", 8000)], "credit": [("Mohan", 8000)],
     "expected_outcome": "Cash Dr 8,000 / Mohan Cr 8,000",
     "refusal_conditions": "amount missing -> BLOCKED"},

    # -- Ch.3 journal: discounts ------------------------------------------
    {"source": "textbook - trade discount",
     "category": "classwork/homework", "chapter": "Ch.3 Journal",
     "question": "Purchased goods from Rahim Rs.20,000 at 10% trade "
                 "discount.",
     "original_wording": "Purchased goods from Rahim Rs.20,000 at 10% trade "
                         "discount.",
     "normalized": "Purchased goods from Rahim for Rs.20,000 at 10% trade "
                   "discount.",
     "status": "VERIFIED", "type_key": "PURCHASE_GOODS_CREDIT", "journals": 1,
     "debit": [("Purchases", 18000)], "credit": [("Rahim", 18000)],
     "expected_outcome": "List 20,000 - 10% trade discount = 18,000 net "
                         "posted",
     "refusal_conditions": "no amount -> BLOCKED; unreadable % -> "
                           "REVIEW_REQUIRED"},
    {"source": "textbook - trade discount on sale",
     "category": "classwork/homework", "chapter": "Ch.3 Journal",
     "question": "Sold goods to Farhan Rs.30,000 at 10% trade discount.",
     "original_wording": "Sold goods to Farhan Rs.30,000 at 10% trade "
                         "discount.",
     "normalized": "Sold goods to Farhan for Rs.30,000 at 10% trade "
                   "discount.",
     "status": "VERIFIED", "type_key": "SALE_GOODS_CREDIT", "journals": 1,
     "debit": [("Farhan", 27000)], "credit": [("Sales", 27000)],
     "expected_outcome": "List 30,000 - 10% trade discount = 27,000 net "
                         "posted",
     "refusal_conditions": "no amount -> BLOCKED"},
    {"source": "textbook - settlement with discount received",
     "category": "classwork/homework", "chapter": "Ch.3 Journal",
     "question": "Paid to Krishna Rs.9,800 in full settlement of his "
                 "account of Rs.10,000, discount received Rs.200.",
     "original_wording": "Paid to Krishna Rs.9,800 in full settlement of "
                         "his account of Rs.10,000, discount received "
                         "Rs.200.",
     "normalized": "Paid to Krishna Rs.9,800 in full settlement of his "
                   "account of Rs.10,000, discount received Rs.200.",
     "status": "VERIFIED", "type_key": "PAID_TO", "journals": 1,
     "debit": [("Krishna", 10000)],
     "credit": [("Cash", 9800), ("Discount Received", 200)],
     "expected_outcome": "Krishna Dr 10,000 / Cash Cr 9,800 + Discount "
                         "Received Cr 200",
     "refusal_conditions": "party total or discount not stated -> "
                           "REVIEW_REQUIRED"},
    {"source": "textbook - settlement with discount allowed",
     "category": "classwork/homework", "chapter": "Ch.3 Journal",
     "question": "Received from Meena Rs.9,700 in full settlement of "
                 "Rs.10,000, discount allowed Rs.300.",
     "original_wording": "Received from Meena Rs.9,700 in full settlement "
                         "of Rs.10,000, discount allowed Rs.300.",
     "normalized": "Received from Meena Rs.9,700 in full settlement of "
                   "Rs.10,000, discount allowed Rs.300.",
     "status": "VERIFIED", "type_key": "RECEIVED_FROM", "journals": 1,
     "debit": [("Cash", 9700), ("Discount Allowed", 300)],
     "credit": [("Meena", 10000)],
     "expected_outcome": "Cash 9,700 + Discount Allowed 300 Dr / Meena Cr "
                         "10,000",
     "refusal_conditions": "party total or discount not stated -> "
                           "REVIEW_REQUIRED"},
    {"source": "textbook - trade + partial + cash discount",
     "category": "classwork/homework", "chapter": "Ch.3 Journal",
     "question": "Purchased goods from Rahim for Rs.20,000 at 10% trade "
                 "discount; half the amount paid immediately with 2% cash "
                 "discount.",
     "original_wording": "Purchased goods from Rahim for Rs.20,000 at 10% "
                         "trade discount; half the amount paid immediately "
                         "with 2% cash discount.",
     "normalized": "Purchased goods from Rahim for Rs.20,000 at 10% trade "
                   "discount; half the amount paid immediately with 2% "
                   "cash discount.",
     "status": "VERIFIED", "type_key": "PURCHASE_GOODS_CREDIT", "journals": 1,
     "debit": [("Purchases", 18000)],
     "credit": [("Cash", 8820), ("Discount Received", 180), ("Rahim", 9000)],
     "expected_outcome": "List 20,000 - 10% TD = 18,000; half (9,000) paid "
                         "- 2% CD = 180; cash 8,820, balance 9,000 to "
                         "Rahim",
     "refusal_conditions": "fraction or % unreadable -> REVIEW_REQUIRED"},

    # -- Ch.3 journal: multi-transaction exam questions --------------------
    {"source": "exam-format - journalise",
     "category": "common exam format", "chapter": "Ch.3 Journal",
     "question": "Journalise the following transactions: Purchased goods "
                 "for cash Rs.10,000; sold goods to Anil on credit "
                 "Rs.15,000.",
     "original_wording": "Journalise the following transactions: Purchased "
                         "goods for cash Rs.10,000; sold goods to Anil on "
                         "credit Rs.15,000.",
     "normalized": "Purchased goods for cash Rs.10,000; sold goods to Anil "
                   "on credit Rs.15,000.",
     "status": "VERIFIED", "type_key": "PURCHASE_GOODS_CASH", "journals": 2,
     "debit": [("Purchases", 10000), ("Anil", 15000)],
     "credit": [("Cash", 10000), ("Sales", 15000)],
     "expected_outcome": "Two independent chronological entries",
     "refusal_conditions": "any segment unresolved -> whole question "
                           "refuses"},
    {"source": "exam-format - pass journal entries",
     "category": "common exam format", "chapter": "Ch.3 Journal",
     "question": "Pass journal entries for the following: Started business "
                 "with cash Rs.50,000; paid rent Rs.4,000.",
     "original_wording": "Pass journal entries for the following: Started "
                         "business with cash Rs.50,000; paid rent Rs.4,000.",
     "normalized": "Started business with cash Rs.50,000; paid rent "
                   "Rs.4,000.",
     "status": "VERIFIED", "type_key": "START_BUSINESS", "journals": 2,
     "debit": [("Cash", 50000), ("Rent", 4000)],
     "credit": [("Capital", 50000), ("Cash", 4000)],
     "expected_outcome": "Two independent chronological entries",
     "refusal_conditions": "any segment unresolved -> whole question "
                           "refuses"},

    # -- Ch.1-3 boundary refusals (never guessed) --------------------------
    {"source": "common exam format - ambiguous mode",
     "category": "classwork/homework", "chapter": "Ch.3 Journal",
     "question": "Purchased goods for Rs.10,000.",
     "original_wording": "Purchased goods for Rs.10,000.",
     "normalized": "Purchased goods for Rs.10,000.",
     "status": "REVIEW_REQUIRED", "type_key": None, "journals": 0,
     "debit": [], "credit": [],
     "expected_outcome": "Ask: for cash or on credit?",
     "refusal_conditions": "cash vs credit unstated -> REVIEW_REQUIRED"},
    {"source": "worksheet - amount missing",
     "category": "teacher-style worksheet", "chapter": "Ch.3 Journal",
     "question": "Purchased goods from Rahul.",
     "original_wording": "Purchased goods from Rahul.",
     "normalized": "Purchased goods from Rahul.",
     "status": "BLOCKED", "type_key": None, "journals": 0,
     "debit": [], "credit": [],
     "expected_outcome": "BLOCKED - the transaction amount is missing",
     "refusal_conditions": "amount missing -> BLOCKED"},
    {"source": "worksheet - unclear drawings",
     "category": "teacher-style worksheet", "chapter": "Ch.2 Basic Accounting Terms",
     "question": "Withdrew Rs.5,000.",
     "original_wording": "Withdrew Rs.5,000.",
     "normalized": "Withdrew Rs.5,000.",
     "status": "REVIEW_REQUIRED", "type_key": None, "journals": 0,
     "debit": [], "credit": [],
     "expected_outcome": "Ask: cash/bank and purpose - never NOT_SUPPORTED",
     "refusal_conditions": "unclear payment mode -> REVIEW_REQUIRED"},
    {"source": "later-year topic - outside Ch.1-3",
     "category": "outside boundary", "chapter": "Later years (NOT_SUPPORTED)",
     "question": "Charge depreciation on machinery at 10% per annum.",
     "original_wording": "Charge depreciation on machinery at 10% per annum.",
     "normalized": "Charge depreciation on machinery at 10% per annum.",
     "status": "NOT_SUPPORTED", "type_key": None, "journals": 0,
     "debit": [], "credit": [],
     "expected_outcome": "Refused - depreciation is outside Ch.1-3",
     "refusal_conditions": "later-year topic -> NOT_SUPPORTED"},
    {"source": "later-year topic - outside Ch.1-3",
     "category": "outside boundary", "chapter": "Later years (NOT_SUPPORTED)",
     "question": "Ravi and Sumit started a partnership business.",
     "original_wording": "Ravi and Sumit started a partnership business.",
     "normalized": "Ravi and Sumit started a partnership business.",
     "status": "NOT_SUPPORTED", "type_key": None, "journals": 0,
     "debit": [], "credit": [],
     "expected_outcome": "Refused - partnership is outside Ch.1-3",
     "refusal_conditions": "later-year topic -> NOT_SUPPORTED"},
    {"source": "later-year topic - outside Ch.1-3",
     "category": "outside boundary", "chapter": "Later years (NOT_SUPPORTED)",
     "question": "Prepare Trading and Profit and Loss Account for the year "
                 "ended 31 March.",
     "original_wording": "Prepare Trading and Profit and Loss Account for "
                         "the year ended 31 March.",
     "normalized": "Prepare Trading and Profit and Loss Account for the "
                   "year ended 31 March.",
     "status": "NOT_SUPPORTED", "type_key": None, "journals": 0,
     "debit": [], "credit": [],
     "expected_outcome": "Refused - final accounts are outside Ch.1-3",
     "refusal_conditions": "later-year topic -> NOT_SUPPORTED"},
]

# ---------------------------------------------------------------------------
# B. 22-case wording adversarial matrix (spec section 2)
# ---------------------------------------------------------------------------
# Each family: every wording MUST collapse onto the SAME canonical
# treatment (the engine is measured with canonical_equivalent + the gate
# checks every member produces the identical IR lines).
ADVERSARIAL_MATRIX = [
    # -- equivalent-word families -> ONE canonical treatment --------------
    {"family": "PURCHASE_FURNITURE_CASH",
     "type_key": "PURCHASE_ASSET_CASH",
     "debit": [("Furniture", 15000)], "credit": [("Cash", 15000)],
     "wordings": [
         "Purchased furniture for cash Rs.15,000.",
         "Furniture was purchased and paid for in cash Rs.15,000.",
         "Bought furniture, payment made immediately in cash, Rs.15,000.",
         "Furniture purchased against cash Rs.15,000.",
         "Purchased furniture costing Rs.15,000, payment made immediately.",
     ]},
    {"family": "PURCHASE_GOODS_CASH",
     "type_key": "PURCHASE_GOODS_CASH",
     "debit": [("Purchases", 16000)], "credit": [("Cash", 16000)],
     "wordings": [
         "Purchased goods for cash Rs.16,000.",
         "Bought goods for cash Rs.16,000.",
         "Goods purchased for cash Rs.16,000.",
         "Purchased goods paying cash Rs.16,000.",
     ]},
    {"family": "CREDIT_PURCHASE_FROM_RAHUL",
     "type_key": "PURCHASE_GOODS_CREDIT",
     "debit": [("Purchases", 22000)], "credit": [("Rahul", 22000)],
     "wordings": [
         "Purchased goods from Rahul on credit Rs.22,000.",
         "Bought goods on account from Rahul Rs.22,000.",
         "Purchased goods worth Rs.22,000 from Rahul.",
         "Purchased goods from Rahul on account for Rs.22,000.",
     ]},
    {"family": "SALE_GOODS_CASH",
     "type_key": "SALE_GOODS_CASH",
     "debit": [("Cash", 25000)], "credit": [("Sales", 25000)],
     "wordings": [
         "Sold goods for cash Rs.25,000.",
         "Goods sold for cash Rs.25,000.",
         "Cash sale of goods Rs.25,000.",
         "Goods were sold and cash received immediately Rs.25,000.",
     ]},
    {"family": "EXPENSE_RENT",
     "type_key": "EXPENSE_PAID",
     "debit": [("Rent", 6000)], "credit": [("Cash", 6000)],
     "wordings": [
         "Paid rent Rs.6,000.",
         "Rent was paid in cash Rs.6,000.",
         "Payment made for rent Rs.6,000 in cash.",
         "Paid rent for the month Rs.6,000 in cash.",
     ]},

    # -- deliberately misleading wording (spec section 2) ------------------
    # each is a SEPARATE case with its own expected treatment
    {"family": "MISLEADING_CREDIT_ON_ACCOUNT",
     "question": "Purchased goods from Rahul on account for Rs.22,000.",
     "status": "VERIFIED", "type_key": "PURCHASE_GOODS_CREDIT",
     "debit": [("Purchases", 22000)], "credit": [("Rahul", 22000)],
     "note": "'on account' == 'on credit' - Rahul is the creditor; the "
             "word 'account' never means cash."},
    {"family": "MISLEADING_PARTIAL_PAYMENT",
     "question": "Purchased goods from Rahul for Rs.10,000 and paid half "
                 "the amount immediately.",
     "status": "VERIFIED", "type_key": "PURCHASE_GOODS_CREDIT",
     "debit": [("Purchases", 10000)],
     "credit": [("Cash", 5000), ("Rahul", 5000)],
     "note": "Half paid immediately NEVER implies a full cash purchase - "
             "the balance stays with Rahul."},
    {"family": "MISLEADING_CASH_DISCOUNT_NOT_CASH_PURCHASE",
     "question": "Purchased goods from Rahul for Rs.10,000, half the "
                 "amount paid immediately with 2% cash discount.",
     "status": "VERIFIED", "type_key": "PURCHASE_GOODS_CREDIT",
     "debit": [("Purchases", 10000)],
     "credit": [("Cash", 4900), ("Discount Received", 100), ("Rahul", 5000)],
     "note": "'cash discount' never means 'cash purchase' - the 2% applies "
             "to the PAID portion (5,000 -> 100), the balance stays with "
             "Rahul."},
    {"family": "MISLEADING_CASH_SALE_PARTY",
     "question": "Sold goods to Mohan for cash Rs.20,000.",
     "status": "VERIFIED", "type_key": "SALE_GOODS_CASH",
     "debit": [("Cash", 20000)], "credit": [("Sales", 20000)],
     "note": "Mohan is named but 'for cash' decides the mode - Mohan never "
             "becomes a debtor."},
    {"family": "MISLEADING_PARTIAL_COLLECTION",
     "question": "Sold goods to Mohan on credit Rs.20,000; received cash "
                 "for half immediately.",
     "status": "VERIFIED", "type_key": "SALE_GOODS_CREDIT",
     "debit": [("Mohan", 10000), ("Cash", 10000)],
     "credit": [("Sales", 20000)],
     "note": "The 'cash' word describes the collection - Mohan stays a "
             "debtor for the unpaid balance."},
    {"family": "MISLEADING_CONTRADICTORY_MODE",
     "question": "Purchased goods for cash on credit from Rahul Rs.10,000.",
     "status": "REVIEW_REQUIRED", "type_key": None,
     "debit": [], "credit": [],
     "note": "Both a cash mode and a credit mode with no payment step - "
             "REVIEW_REQUIRED, never guessed."},
    {"family": "MISLEADING_DISCOUNT_NEEDS_SETTLEMENT",
     "question": "Discount allowed Rs.200.",
     "status": "REVIEW_REQUIRED", "type_key": None,
     "debit": [], "credit": [],
     "note": "A discount is never a standalone entry - it needs a "
             "settlement with a party or payment amount."},
]

# ---------------------------------------------------------------------------
# C. 9 multi-transaction stress cases (spec section 3)
# ---------------------------------------------------------------------------
MULTI_TRANSACTION_STRESS = [
    {"question": "Started business with cash Rs.1,00,000. Purchased goods "
                 "for cash Rs.20,000. Purchased furniture for Rs.10,000 "
                 "from Rahul. Paid rent Rs.5,000.",
     "status": "VERIFIED", "journals": 4,
     "debit": [("Cash", 100000), ("Purchases", 20000), ("Furniture", 10000),
               ("Rent", 5000)],
     "credit": [("Capital", 100000), ("Cash", 20000), ("Rahul", 10000),
                ("Cash", 5000)],
     "note": "Four independent chronological entries; the furniture "
             "purchase from Rahul is on credit (party named)."},
    {"question": "Purchased goods from Rahul for Rs.10,000 at 10% trade "
                 "discount. Half the amount was paid immediately.",
     "status": "VERIFIED", "journals": 1,
     "debit": [("Purchases", 9000)],
     "credit": [("Cash", 4500), ("Rahul", 4500)],
     "note": "The second sentence CONTINUES the first transaction - folded "
             "into ONE journal (never an independent entry)."},
    {"question": "Purchased goods from Rahul for Rs.10,000. Paid him "
                 "Rs.4,000 immediately.",
     "status": "VERIFIED", "journals": 1,
     "debit": [("Purchases", 10000)],
     "credit": [("Cash", 4000), ("Rahul", 6000)],
     "note": "'him' resolves to Rahul deterministically; the payment step "
             "stays inside the purchase journal."},
    {"question": "Purchased goods from Rahul. Paid rent Rs.4,000.",
     "status": "BLOCKED", "journals": 0, "debit": [], "credit": [],
     "note": "Transaction 1 is missing its amount - the WHOLE question "
             "refuses (no partial confident answer)."},
    {"question": "Sold goods to Meena for cash Rs.12,000; received "
                 "commission Rs.500; paid salaries Rs.6,000.",
     "status": "VERIFIED", "journals": 3,
     "debit": [("Cash", 12000), ("Cash", 500), ("Salaries", 6000)],
     "credit": [("Sales", 12000), ("Commission Received", 500),
                ("Cash", 6000)],
     "note": "Semicolon-separated independent transactions - three "
             "entries in order."},
    {"question": "Purchased goods from Rahim Rs.20,000 at 10% trade "
                 "discount; paid half immediately; paid the balance after "
                 "a month.",
     "status": "VERIFIED", "journals": 1,
     "debit": [("Purchases", 18000)],
     "credit": [("Cash", 9000), ("Rahim", 9000)],
     "note": "Half paid now (9,000); the future 'after a month' payment "
             "does not post cash today - balance stays with Rahim."},
    {"question": "Returned goods to Suresh worth Rs.2,000; purchased goods "
                 "from Suresh for cash Rs.8,000.",
     "status": "VERIFIED", "journals": 2,
     "debit": [("Suresh", 2000), ("Purchases", 8000)],
     "credit": [("Purchase Returns", 2000), ("Cash", 8000)],
     "note": "A return and a NEW purchase - two independent entries; the "
             "return does not absorb the new purchase."},
    {"question": "Sold goods to Mohan for Rs.10,000 on credit. Received "
                 "from him Rs.9,800 in full settlement of his account, "
                 "discount allowed Rs.200.",
     "status": "VERIFIED", "journals": 2,
     "debit": [("Mohan", 10000), ("Cash", 9800), ("Discount Allowed", 200)],
     "credit": [("Sales", 10000), ("Mohan", 10000)],
     "note": "Sale, then 'from him' resolves to Mohan and settles his "
             "account with the stated discount."},
    {"question": "Purchased goods from Rahim for Rs.20,000 at 10% trade "
                 "discount; half the amount paid immediately.",
     "status": "VERIFIED", "journals": 1,
     "debit": [("Purchases", 18000)],
     "credit": [("Cash", 9000), ("Rahim", 9000)],
     "note": "Discount continuation: trade discount is netted BEFORE the "
             "payment split (never after)."},
]

# ---------------------------------------------------------------------------
# D. 12 ambiguity attacks (spec section 4) - REVIEW_REQUIRED / BLOCKED
# ---------------------------------------------------------------------------
AMBIGUITY_ATTACKS = [
    {"question": "Purchased goods for Rs.10,000.", "status": "REVIEW_REQUIRED",
     "why": "cash vs credit unstated"},
    {"question": "Sold goods for Rs.8,000.", "status": "REVIEW_REQUIRED",
     "why": "cash vs credit unstated"},
    {"question": "Paid Rs.5,000.", "status": "REVIEW_REQUIRED",
     "why": "purpose/party unstated"},
    {"question": "Purchased furniture.", "status": "REVIEW_REQUIRED",
     "why": "mode (cash/credit) unstated"},
    {"question": "Received Rs.3,000.", "status": "REVIEW_REQUIRED",
     "why": "source/purpose unstated"},
    {"question": "Paid him Rs.5,000.", "status": "REVIEW_REQUIRED",
     "why": "no prior party - 'him' has no antecedent"},
    {"question": "Purchased goods from Rahul.", "status": "BLOCKED",
     "why": "amount missing"},
    {"question": "Bought machinery at 10% discount.", "status":
     "REVIEW_REQUIRED", "why": "mode unstated and discount basis unclear"},
    {"question": "Sold goods to Mohan for Rs.15,000; discount allowed "
                 "Rs.200.", "status": "REVIEW_REQUIRED",
     "why": "discount without a settlement context"},
    {"question": "Purchased goods for cash on credit from Rahul Rs.10,000.",
     "status": "REVIEW_REQUIRED", "why": "contradictory cash+credit mode"},
    {"question": "Withdrew Rs.5,000.", "status": "REVIEW_REQUIRED",
     "why": "unclear payment mode (cash/bank) and purpose"},
    {"question": "Half the amount paid immediately.", "status": "BLOCKED",
     "why": "no transaction amount and no antecedent"},
]

# ---------------------------------------------------------------------------
# E. 13 student-error cases - the ten categories + 3 combined (spec 5)
# ---------------------------------------------------------------------------
STUDENT_ERROR_15H = [
    # 1. Correct journal
    {"question": "Purchased furniture for cash Rs.15,000.",
     "kind": "journal",
     "student": {"debits": [("Furniture", 15000)],
                 "credits": [("Cash", 15000)]},
     "expected_verdict": "CORRECT", "expected_category": "CORRECT"},
    # 2. Wrong debit/credit side (swapped)
    {"question": "Purchased furniture for cash Rs.15,000.",
     "kind": "journal",
     "student": {"debits": [("Cash", 15000)],
                 "credits": [("Furniture", 15000)]},
     "expected_verdict": "INCORRECT", "expected_category": "WRONG_SIDE"},
    # 3. Wrong account (Machinery instead of Furniture)
    {"question": "Purchased furniture for cash Rs.15,000.",
     "kind": "journal",
     "student": {"debits": [("Machinery", 15000)],
                 "credits": [("Cash", 15000)]},
     "expected_verdict": "INCORRECT", "expected_category": "WRONG_ACCOUNT"},
    # 4. Missing account (compound start, Furniture debit omitted)
    {"question": "Started business with cash Rs.60,000 and furniture worth "
                 "Rs.40,000.",
     "kind": "journal",
     "student": {"debits": [("Cash", 60000)],
                 "credits": [("Capital", 100000)]},
     "expected_verdict": "INCORRECT", "expected_category": "MISSING_ACCOUNT"},
    # 5. Invented account (extra Machinery debit)
    {"question": "Purchased furniture for cash Rs.15,000.",
     "kind": "journal",
     "student": {"debits": [("Furniture", 15000), ("Machinery", 15000)],
                 "credits": [("Cash", 30000)]},
     "expected_verdict": "INCORRECT", "expected_category": "INVENTED_ACCOUNT"},
    # 6. Wrong amount
    {"question": "Purchased goods for cash Rs.15,000.",
     "kind": "journal",
     "student": {"debits": [("Purchases", 14000)],
                 "credits": [("Cash", 14000)]},
     "expected_verdict": "INCORRECT", "expected_category": "WRONG_AMOUNT"},
    # 7. Unbalanced journal
    {"question": "Purchased goods for cash Rs.15,000.",
     "kind": "journal",
     "student": {"debits": [("Purchases", 15000)],
                 "credits": [("Cash", 14000)]},
     "expected_verdict": "INCORRECT", "expected_category": "JOURNAL_UNBALANCED"},
    # 8. Wrong Real/Personal/Nominal classification (Furniture is Real)
    {"question": "Purchased furniture for cash Rs.15,000.",
     "kind": "journal",
     "student": {"debits": [{"account": "Furniture", "amount": 15000,
                             "class": "Nominal"}],
                 "credits": [{"account": "Cash", "amount": 15000,
                              "class": "Real"}]},
     "expected_verdict": "INCORRECT", "expected_category": "WRONG_CLASSIFICATION"},
    # 9. Correct journal, incorrect ledger effect
    {"question": "Purchased furniture for cash Rs.15,000.",
     "kind": "ledger",
     "student": {"account": "Furniture", "balance": "14000", "side": "Dr"},
     "expected_verdict": "INCORRECT", "expected_category": "LEDGER_ERROR"},
    # 10. Correct ledger, incorrect trial-balance effect
    {"question": "Purchased goods for cash Rs.15,000. Paid rent Rs.5,000.",
     "kind": "tb",
     "student": {"rows": [
         {"account": "Purchases", "debit": 14000.0, "credit": 0.0},
         {"account": "Rent", "debit": 5000.0, "credit": 0.0},
         {"account": "Cash", "debit": 0.0, "credit": 20000.0},
     ]},
     "expected_verdict": "INCORRECT", "expected_category": "TRIAL_BALANCE_ERROR"},
    # 11. COMBINED: invented account + unbalanced journal - the category,
    #     first_mistake and affected_component must describe the SAME first
    #     deterministic error (root-cause order: account presence first,
    #     totals after) (Sprint 15H remediation B)
    {"question": "Purchased furniture for cash Rs.15,000.",
     "kind": "journal",
     "student": {"debits": [{"account": "Furniture", "amount": 15000}],
                 "credits": [{"account": "Cash", "amount": 15000},
                             {"account": "Rahul", "amount": 5000}]},
     "expected_verdict": "INCORRECT", "expected_category": "INVENTED_ACCOUNT"},
    # 12. COMBINED: wrong account + wrong amount - the wrong account is the
    #     first root cause, before the amount (Sprint 15H remediation B)
    {"question": "Purchased furniture for cash Rs.15,000.",
     "kind": "journal",
     "student": {"debits": [{"account": "Machinery", "amount": 16000}],
                 "credits": [{"account": "Cash", "amount": 15000}]},
     "expected_verdict": "INCORRECT", "expected_category": "WRONG_ACCOUNT"},
    # 13. COMBINED: wrong side + wrong amount - the swapped sides are the
    #     first root cause, before the amount (Sprint 15H remediation B)
    {"question": "Purchased furniture for cash Rs.15,000.",
     "kind": "journal",
     "student": {"debits": [{"account": "Cash", "amount": 14000}],
                 "credits": [{"account": "Furniture", "amount": 15000}]},
     "expected_verdict": "INCORRECT", "expected_category": "WRONG_SIDE"},
]

# ---------------------------------------------------------------------------
# F. 9 OCR / extraction boundary cases (spec section 6)
# ---------------------------------------------------------------------------
OCR_BOUNDARY_CASES = [
    # Good -> process normally
    {"question": "Purchased furniture for cash Rs.15,000.", "signals": {},
     "expected_state": "GOOD", "expected_status": "VERIFIED",
     "note": "clear typed question"},
    {"question": "Sold goods to Meena for cash Rs.12,000.", "signals": {},
     "expected_state": "GOOD", "expected_status": "VERIFIED",
     "note": "clear textbook-style question"},
    # Uncertain -> REVIEW_REQUIRED (never a parsed digit from a flag)
    {"question": "Purchased furniture for cash Rs.15,000.",
     "signals": {"unreadable_digit": True},
     "expected_state": "UNCERTAIN", "expected_status": "REVIEW_REQUIRED",
     "note": "a digit could not be read - Platrixa never invents it"},
    {"question": "Purchased goods for cash Rs.16,000.",
     "signals": {"mild_blur": True},
     "expected_state": "UNCERTAIN", "expected_status": "REVIEW_REQUIRED",
     "note": "slight blur"},
    {"question": "Paid rent Rs.6,000.",
     "signals": {"partially_cropped": True, "low_confidence_word": True},
     "expected_state": "UNCERTAIN", "expected_status": "REVIEW_REQUIRED",
     "note": "partially cropped question"},
    # Unusable -> BLOCKED with a request for clearer input
    {"question": "Purchased goods for cash Rs.16,000.",
     "signals": {"unreadable_digit": True, "severe_blur": True},
     "expected_state": "UNUSABLE", "expected_status": "BLOCKED",
     "note": "unreadable digits + severe blur"},
    {"question": "Purchased furniture for cash Rs.15,000.",
     "signals": {"severe_blur": True},
     "expected_state": "UNUSABLE", "expected_status": "BLOCKED",
     "note": "severe blur"},
    {"question": "Purchased furniture for cash Rs.15,000.",
     "signals": {"missing_transaction_text": True},
     "expected_state": "UNUSABLE", "expected_status": "BLOCKED",
     "note": "missing transaction text"},
    {"question": "Sold goods to Mohan for cash Rs.20,000.",
     "signals": {"contradictory_output": True},
     "expected_state": "UNUSABLE", "expected_status": "BLOCKED",
     "note": "contradictory OCR output"},
]

# ---------------------------------------------------------------------------
# G. Fix-regression pins - the exact questions whose failures produced the
#    Sprint 15H minimal fixes (permanent regression tests)
# ---------------------------------------------------------------------------
FIX_REGRESSION_CASES = [
    {"question": "Returned goods to Suresh worth Rs.2,000.",
     "status": "VERIFIED", "type_key": "PURCHASE_RETURN",
     "debit": [("Suresh", 2000)], "credit": [("Purchase Returns", 2000)],
     "fix": "party regex now stops at 'worth' (was 'Suresh worth')"},
    {"question": "Sheela commenced business with cash Rs.1,00,000 and "
                 "furniture worth Rs.50,000.",
     "status": "VERIFIED", "type_key": "START_BUSINESS",
     "debit": [("Cash", 100000), ("Furniture", 50000)],
     "credit": [("Capital", 150000)],
     "fix": "compound start splits 'X worth Rs.Y' components (was a "
            "silent drop of the asset)"},
    {"question": "Cash withdrawn by proprietor for personal expenses "
                 "Rs.2,000.",
     "status": "VERIFIED", "type_key": "DRAWINGS_CASH",
     "debit": [("Drawings", 2000)], "credit": [("Cash", 2000)],
     "fix": "passive-voice drawings wording ('cash withdrawn by ... for "
            "personal expenses')"},
    {"question": "Purchased goods for cash on credit from Rahul Rs.10,000.",
     "status": "REVIEW_REQUIRED", "type_key": None,
     "debit": [], "credit": [],
     "fix": "contradictory cash+credit wording is REVIEW_REQUIRED (was a "
            "confident cash answer)"},
    {"question": "Withdrew Rs.5,000.",
     "status": "REVIEW_REQUIRED", "type_key": None,
     "debit": [], "credit": [],
     "fix": "bare 'Withdrew Rs.X' asks for clarification (was NOT_SUPPORTED)"},
    {"question": "Paid wages for installation of machinery Rs.5,000.",
     "status": "VERIFIED", "type_key": "CAPITALISE_EXPENSE",
     "debit": [("Machinery", 5000)], "credit": [("Cash", 5000)],
     "fix": "installation wages are capitalised into the asset (was a "
            "Wages expense)"},
    {"question": "Mohan was paid Rs.5,000.",
     "status": "VERIFIED", "type_key": "PAID_TO",
     "debit": [("Mohan", 5000)], "credit": [("Cash", 5000)],
     "fix": "passive party payment ('X was paid') is PAID_TO - the party "
            "receives the money; the aux verb decides the direction (was a "
            "confident reversed RECEIVED_FROM answer)"},
    {"question": "Rahul has been paid Rs.4,000.",
     "status": "VERIFIED", "type_key": "PAID_TO",
     "debit": [("Rahul", 4000)], "credit": [("Cash", 4000)],
     "fix": "passive party payment, perfect tense ('has been paid') - "
            "same aux-verb direction rule as 'was paid': PAID_TO, never a "
            "reversed receipt (Sprint 15H remediation A)"},
    {"question": "Rahul was paid the balance Rs.4,000 immediately.",
     "status": "VERIFIED", "type_key": "PAID_TO",
     "debit": [("Rahul", 4000)], "credit": [("Cash", 4000)],
     "fix": "passive party payment with an amount + 'immediately' - the "
            "aux verb still decides PAID_TO; 'immediately' never flips the "
            "direction (Sprint 15H remediation A)"},
    {"question": "Mohan paid Rs.12,000 immediately.",
     "status": "VERIFIED", "type_key": "RECEIVED_FROM",
     "debit": [("Cash", 12000)], "credit": [("Mohan", 12000)],
     "fix": "ACTIVE-voice contrast: 'X paid Rs.Y' is a RECEIPT - the "
            "active/passive contrast must never converge to the same "
            "direction (Sprint 15H remediation A)"},
    {"question": "Withdrew Rs.5,000 from bank for office use.",
     "status": "VERIFIED", "type_key": "CASH_FROM_BANK",
     "debit": [("Cash", 5000)], "credit": [("Bank", 5000)],
     "fix": "bank withdrawal without the word 'cash' is still "
            "CASH_FROM_BANK - the direction is structural ('withdraw ... "
            "from bank'), never inferred from the word 'cash' (was "
            "REVIEW_REQUIRED)"},
    {"question": "Withdrew money from bank for office use Rs.5,000.",
     "status": "VERIFIED", "type_key": "CASH_FROM_BANK",
     "debit": [("Cash", 5000)], "credit": [("Bank", 5000)],
     "fix": "withdrawal wording variant - 'withdrew money from bank' "
            "converges onto the same CASH_FROM_BANK IR as the cash-explicit "
            "and amount-first forms (Sprint 15H remediation C)"},
    {"question": "Cash was withdrawn from bank for office use Rs.5,000.",
     "status": "VERIFIED", "type_key": "CASH_FROM_BANK",
     "debit": [("Cash", 5000)], "credit": [("Bank", 5000)],
     "fix": "passive-voice withdrawal variant - 'Cash was withdrawn from "
            "bank' converges onto CASH_FROM_BANK with the same IR (Sprint "
            "15H remediation C)"},
    {"question": "Enter the following transactions in the cash book: "
                 "Purchased goods from Rahul on credit Rs.10,000.",
     "status": "VERIFIED", "type_key": "PURCHASE_GOODS_CREDIT",
     "debit": [("Purchases", 10000)], "credit": [("Rahul", 10000)],
     "fix": "'cash book' names a record-keeping place, never a cash mode - "
            "'... in the cash book ... on credit' is a CREDIT purchase, not "
            "a contradictory mode (was MODE_CONTRADICTORY)"},
    {"question": "Record the cash sales of Rs.3,000 in the cash book.",
     "status": "VERIFIED", "type_key": "SALE_GOODS_CASH",
     "debit": [("Cash", 3000)], "credit": [("Sales", 3000)],
     "fix": "'cash sales' + 'cash book' together - both are terminology, "
            "never a contradictory mode; the sale stays a cash sale with "
            "one entry (Sprint 15H remediation D)"},
    {"question": "The cashier counted Rs.15,000 cash received from cash "
                 "sales.",
     "status": "VERIFIED", "type_key": "SALE_GOODS_CASH",
     "debit": [("Cash", 15000)], "credit": [("Sales", 15000)],
     "fix": "'cashier' + incidental 'cash' words never flip the mode - the "
            "transaction is the cash sale itself (Sprint 15H remediation D)"},
    {"question": "The cashier paid salaries Rs.10,000.",
     "status": "VERIFIED", "type_key": "EXPENSE_PAID",
     "debit": [("Salaries", 10000)], "credit": [("Cash", 10000)],
     "fix": "'The cashier paid salaries' is an expense paid, never a "
            "receipt from a party - 'salaries' was missing from the "
            "subject-position expense guard, so 'The cashier' was being "
            "journaled as a paying debtor (Cash Dr / The cashier Cr). The "
            "guard now carries 'salaries' beside 'salary' (Sprint 15H "
            "remediation D)"},
]

# adversarial families that must converge onto one IR
CONVERGENCE_FAMILIES = [c for c in ADVERSARIAL_MATRIX
                        if c.get("wordings")]

# misleading wording cases (each with its own expected treatment)
MISLEADING_CASES = [c for c in ADVERSARIAL_MATRIX if c.get("question")]

# all family wordings expanded into individual checkable items
FAMILY_WORDINGS = [
    {"question": w, "status": "VERIFIED",
     "type_key": f["type_key"], "debit": f["debit"],
     "credit": f["credit"], "journals": 1, "family": f["family"]}
    for f in CONVERGENCE_FAMILIES for w in f["wordings"]
]

# ---------------------------------------------------------------------------
# Aggregation (mirrors the 15F benchmark conventions)
# ---------------------------------------------------------------------------
# The adversarial matrix splits into CONVERGENCE FAMILIES (equivalent
# wordings -> ONE treatment, expanded per wording by the gate) and
# MISLEADING CASES (each a standalone case with its own expected
# treatment). Only the misleading cases are individual benchmark cases.
BK15H_BENCHMARK = (REAL_QUESTION_CASES + MISLEADING_CASES
                   + MULTI_TRANSACTION_STRESS + AMBIGUITY_ATTACKS)

VERIFIED_CASES = [c for c in BK15H_BENCHMARK if c["status"] == "VERIFIED"]
REFUSAL_CASES = [c for c in BK15H_BENCHMARK if c["status"] != "VERIFIED"]

if __name__ == "__main__":
    total = (len(BK15H_BENCHMARK) + len(STUDENT_ERROR_15H)
             + len(OCR_BOUNDARY_CASES) + len(FIX_REGRESSION_CASES))
    print(f"BK15H benchmark: {len(BK15H_BENCHMARK)} reasoning cases "
          f"(verified {len(VERIFIED_CASES)}, refusals {len(REFUSAL_CASES)}) "
          f"+ {len(STUDENT_ERROR_15H)} student-error + "
          f"{len(OCR_BOUNDARY_CASES)} OCR-boundary + "
          f"{len(FIX_REGRESSION_CASES)} fix-regression = {total}")
