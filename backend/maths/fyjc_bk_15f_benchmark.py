"""
Platrixa
Sprint 15F - FYJC Book-Keeping Ch.1-3 Textbook Pattern Expansion benchmark
backend/maths/fyjc_bk_15f_benchmark.py

A hand-verified golden dataset (162 cases) for the first three FYJC
Book-Keeping & Accountancy chapters (Unit Test 1 scope - the EXACT 15E
boundary). The oracle NEVER calls the Platrixa engine - every expected value
was written from the standard FYJC journal rules (Real/Personal/Nominal
golden rules) so the gate measures the engine against an independent
answer.

Breakdown (spec section 15):
  * 52 basic transactions
  * 32 wording variants (semantic equivalence -> ONE pattern)
  * 26 composed / multi-step transactions (trade discount, partial
    payment, cash discount, explicit-discount settlements)
  * 16 multi-transaction questions (chronological, independent entries,
    continuation resolution boundaries)
  * 12 student-answer verification cases (final answer / journal /
    ledger / trial balance - first deterministic mistake)
  * 12 missing / ambiguous cases (BLOCKED / REVIEW_REQUIRED)
  * 12 unsupported / refusal cases (NOT_SUPPORTED)
  = 162

Case fields
-----------
  question : textbook-style question (one or more transactions)
  status   : VERIFIED | BLOCKED | REVIEW_REQUIRED | NOT_SUPPORTED
  type_key : expected canonical transaction key (VERIFIED single-tx)
  journals : expected number of independent journal entries
  debit    : expected DEBIT lines across all journals, (account, amount)
  credit   : expected CREDIT lines across all journals, (account, amount)

Amounts are integers (rupees). Accounts use the canonical FYJC chart
spelling. Student-error cases carry a 'checks' list with the student
submission and the expected first mistake.
"""

# ---------------------------------------------------------------------------
# A. 52 basic transactions (one per supported Ch.1-3 family)
# ---------------------------------------------------------------------------
BASIC_TRANSACTIONS = [
    {"question": "Started business with cash Rs.60,000.", "status": "VERIFIED",
     "type_key": "START_BUSINESS", "journals": 1,
     "debit": [("Cash", 60000)], "credit": [("Capital", 60000)]},
    {"question": "Started business with bank balance Rs.80,000.",
     "status": "VERIFIED", "type_key": "START_BUSINESS", "journals": 1,
     "debit": [("Bank", 80000)], "credit": [("Capital", 80000)]},
    {"question": "Commenced business with cash Rs.45,000 and machinery Rs.55,000.",
     "status": "VERIFIED", "type_key": "START_BUSINESS", "journals": 1,
     "debit": [("Cash", 45000), ("Machinery", 55000)],
     "credit": [("Capital", 100000)]},
    {"question": "Brought additional capital in cash Rs.25,000.",
     "status": "VERIFIED", "type_key": "CAPITAL_INTRODUCED", "journals": 1,
     "debit": [("Cash", 25000)], "credit": [("Capital", 25000)]},
    {"question": "Brought furniture worth Rs.18,000 as additional capital.",
     "status": "VERIFIED", "type_key": "CAPITAL_ASSET_INTRODUCED", "journals": 1,
     "debit": [("Furniture", 18000)], "credit": [("Capital", 18000)]},
    {"question": "Withdrew cash for personal use Rs.4,000.",
     "status": "VERIFIED", "type_key": "DRAWINGS_CASH", "journals": 1,
     "debit": [("Drawings", 4000)], "credit": [("Cash", 4000)]},
    {"question": "Withdrew goods worth Rs.2,500 for private use.",
     "status": "VERIFIED", "type_key": "GOODS_PERSONAL_USE", "journals": 1,
     "debit": [("Drawings", 2500)], "credit": [("Purchases", 2500)]},
    {"question": "Purchased goods for cash Rs.16,000.", "status": "VERIFIED",
     "type_key": "PURCHASE_GOODS_CASH", "journals": 1,
     "debit": [("Purchases", 16000)], "credit": [("Cash", 16000)]},
    {"question": "Purchased goods from Rahul on credit Rs.22,000.",
     "status": "VERIFIED", "type_key": "PURCHASE_GOODS_CREDIT", "journals": 1,
     "debit": [("Purchases", 22000)], "credit": [("Rahul", 22000)]},
    {"question": "Purchased goods by cheque Rs.9,500.", "status": "VERIFIED",
     "type_key": "PURCHASE_GOODS_CASH", "journals": 1,
     "debit": [("Purchases", 9500)], "credit": [("Bank", 9500)]},
    {"question": "Bought furniture for cash Rs.12,000.", "status": "VERIFIED",
     "type_key": "PURCHASE_ASSET_CASH", "journals": 1,
     "debit": [("Furniture", 12000)], "credit": [("Cash", 12000)]},
    {"question": "Bought furniture from Vijay on credit Rs.20,000.",
     "status": "VERIFIED", "type_key": "PURCHASE_ASSET_CREDIT", "journals": 1,
     "debit": [("Furniture", 20000)], "credit": [("Vijay", 20000)]},
    {"question": "Purchased machinery for cash Rs.75,000.", "status": "VERIFIED",
     "type_key": "PURCHASE_ASSET_CASH", "journals": 1,
     "debit": [("Machinery", 75000)], "credit": [("Cash", 75000)]},
    {"question": "Purchased building from Suresh on credit Rs.40,00,000.",
     "status": "VERIFIED", "type_key": "PURCHASE_ASSET_CREDIT", "journals": 1,
     "debit": [("Building", 4000000)], "credit": [("Suresh", 4000000)]},
    {"question": "Sold goods for cash Rs.25,000.", "status": "VERIFIED",
     "type_key": "SALE_GOODS_CASH", "journals": 1,
     "debit": [("Cash", 25000)], "credit": [("Sales", 25000)]},
    {"question": "Sold goods to Mohan on credit Rs.18,000.",
     "status": "VERIFIED", "type_key": "SALE_GOODS_CREDIT", "journals": 1,
     "debit": [("Mohan", 18000)], "credit": [("Sales", 18000)]},
    {"question": "Sold goods by cheque Rs.7,000.", "status": "VERIFIED",
     "type_key": "SALE_GOODS_CASH", "journals": 1,
     "debit": [("Bank", 7000)], "credit": [("Sales", 7000)]},
    {"question": "Sold old furniture for cash Rs.6,000.", "status": "VERIFIED",
     "type_key": "SALE_ASSET_CASH", "journals": 1,
     "debit": [("Cash", 6000)], "credit": [("Furniture", 6000)]},
    {"question": "Sold old machinery to Ramesh on credit Rs.30,000.",
     "status": "VERIFIED", "type_key": "SALE_ASSET_CREDIT", "journals": 1,
     "debit": [("Ramesh", 30000)], "credit": [("Machinery", 30000)]},
    {"question": "Paid rent Rs.6,000.", "status": "VERIFIED",
     "type_key": "EXPENSE_PAID", "journals": 1,
     "debit": [("Rent", 6000)], "credit": [("Cash", 6000)]},
    {"question": "Paid salaries Rs.9,000.", "status": "VERIFIED",
     "type_key": "EXPENSE_PAID", "journals": 1,
     "debit": [("Salaries", 9000)], "credit": [("Cash", 9000)]},
    {"question": "Paid wages Rs.3,500.", "status": "VERIFIED",
     "type_key": "EXPENSE_PAID", "journals": 1,
     "debit": [("Wages", 3500)], "credit": [("Cash", 3500)]},
    {"question": "Paid electricity bill Rs.2,400.", "status": "VERIFIED",
     "type_key": "EXPENSE_PAID", "journals": 1,
     "debit": [("Electricity", 2400)], "credit": [("Cash", 2400)]},
    {"question": "Paid insurance premium Rs.3,000.", "status": "VERIFIED",
     "type_key": "EXPENSE_PAID", "journals": 1,
     "debit": [("Insurance", 3000)], "credit": [("Cash", 3000)]},
    {"question": "Purchased stationery for cash Rs.700.", "status": "VERIFIED",
     "type_key": "EXPENSE_PAID", "journals": 1,
     "debit": [("Stationery", 700)], "credit": [("Cash", 700)]},
    {"question": "Paid telephone bill Rs.1,100.", "status": "VERIFIED",
     "type_key": "EXPENSE_PAID", "journals": 1,
     "debit": [("Telephone Expenses", 1100)], "credit": [("Cash", 1100)]},
    {"question": "Paid carriage inward Rs.900.", "status": "VERIFIED",
     "type_key": "EXPENSE_PAID", "journals": 1,
     "debit": [("Carriage Inward", 900)], "credit": [("Cash", 900)]},
    {"question": "Paid carriage outward Rs.650.", "status": "VERIFIED",
     "type_key": "EXPENSE_PAID", "journals": 1,
     "debit": [("Carriage Outward", 650)], "credit": [("Cash", 650)]},
    {"question": "Paid rent by cheque Rs.5,500.", "status": "VERIFIED",
     "type_key": "EXPENSE_PAID", "journals": 1,
     "debit": [("Rent", 5500)], "credit": [("Bank", 5500)]},
    {"question": "Paid salaries by cheque Rs.8,000.", "status": "VERIFIED",
     "type_key": "EXPENSE_PAID", "journals": 1,
     "debit": [("Salaries", 8000)], "credit": [("Bank", 8000)]},
    {"question": "Received commission Rs.3,600.", "status": "VERIFIED",
     "type_key": "INCOME_RECEIVED", "journals": 1,
     "debit": [("Cash", 3600)], "credit": [("Commission Received", 3600)]},
    {"question": "Received interest Rs.2,200.", "status": "VERIFIED",
     "type_key": "INCOME_RECEIVED", "journals": 1,
     "debit": [("Cash", 2200)], "credit": [("Interest Received", 2200)]},
    {"question": "Interest received by cheque Rs.2,200.", "status": "VERIFIED",
     "type_key": "INCOME_RECEIVED", "journals": 1,
     "debit": [("Bank", 2200)], "credit": [("Interest Received", 2200)]},
    {"question": "Received rent Rs.4,000.", "status": "VERIFIED",
     "type_key": "INCOME_RECEIVED", "journals": 1,
     "debit": [("Cash", 4000)], "credit": [("Rent Received", 4000)]},
    {"question": "Received dividend Rs.1,500.", "status": "VERIFIED",
     "type_key": "INCOME_RECEIVED", "journals": 1,
     "debit": [("Cash", 1500)], "credit": [("Dividend Received", 1500)]},
    {"question": "Deposited cash into bank Rs.12,000.", "status": "VERIFIED",
     "type_key": "CASH_INTO_BANK", "journals": 1,
     "debit": [("Bank", 12000)], "credit": [("Cash", 12000)]},
    {"question": "Withdrew cash from bank Rs.3,000.", "status": "VERIFIED",
     "type_key": "CASH_FROM_BANK", "journals": 1,
     "debit": [("Cash", 3000)], "credit": [("Bank", 3000)]},
    {"question": "Paid to Rahul Rs.7,000 in cash.", "status": "VERIFIED",
     "type_key": "PAID_TO", "journals": 1,
     "debit": [("Rahul", 7000)], "credit": [("Cash", 7000)]},
    {"question": "Received from Mohan Rs.5,500 in cash.", "status": "VERIFIED",
     "type_key": "RECEIVED_FROM", "journals": 1,
     "debit": [("Cash", 5500)], "credit": [("Mohan", 5500)]},
    {"question": "Paid Amit by cheque Rs.6,500.", "status": "VERIFIED",
     "type_key": "CHEQUE_PAID", "journals": 1,
     "debit": [("Amit", 6500)], "credit": [("Bank", 6500)]},
    {"question": "Received a cheque from Mohan Rs.8,500.", "status": "VERIFIED",
     "type_key": "CHEQUE_RECEIVED", "journals": 1,
     "debit": [("Bank", 8500)], "credit": [("Mohan", 8500)]},
    {"question": "Issued a cheque to Rahul Rs.4,500.", "status": "VERIFIED",
     "type_key": "CHEQUE_PAID", "journals": 1,
     "debit": [("Rahul", 4500)], "credit": [("Bank", 4500)]},
    {"question": "Returned goods to Rahul Rs.1,200.", "status": "VERIFIED",
     "type_key": "PURCHASE_RETURN", "journals": 1,
     "debit": [("Rahul", 1200)], "credit": [("Purchase Returns", 1200)]},
    {"question": "Goods returned by Mohan Rs.900.", "status": "VERIFIED",
     "type_key": "SALES_RETURN", "journals": 1,
     "debit": [("Sales Returns", 900)], "credit": [("Mohan", 900)]},
    {"question": "Purchases returns to Rahul Rs.800.", "status": "VERIFIED",
     "type_key": "PURCHASE_RETURN", "journals": 1,
     "debit": [("Rahul", 800)], "credit": [("Purchase Returns", 800)]},
    {"question": "Sales returns from Mohan Rs.600.", "status": "VERIFIED",
     "type_key": "SALES_RETURN", "journals": 1,
     "debit": [("Sales Returns", 600)], "credit": [("Mohan", 600)]},
    {"question": "Received from Mohan Rs.9,700, discount allowed Rs.300.",
     "status": "VERIFIED", "type_key": "RECEIVED_FROM", "journals": 1,
     "debit": [("Cash", 9700), ("Discount Allowed", 300)],
     "credit": [("Mohan", 10000)]},
    {"question": "Paid to Amit Rs.9,700, discount received Rs.300.",
     "status": "VERIFIED", "type_key": "PAID_TO", "journals": 1,
     "debit": [("Amit", 10000)],
     "credit": [("Cash", 9700), ("Discount Received", 300)]},
    {"question": "Discount allowed to Mohan Rs.250.", "status": "VERIFIED",
     "type_key": "DISCOUNT_ALLOWED", "journals": 1,
     "debit": [("Discount Allowed", 250)], "credit": [("Mohan", 250)]},
    {"question": "Discount received from Rahul Rs.150.", "status": "VERIFIED",
     "type_key": "DISCOUNT_RECEIVED", "journals": 1,
     "debit": [("Rahul", 150)], "credit": [("Discount Received", 150)]},
    {"question": "Started business with cash Rs.50,000 and bank balance Rs.30,000.",
     "status": "VERIFIED", "type_key": "START_BUSINESS", "journals": 1,
     "debit": [("Cash", 50000), ("Bank", 30000)],
     "credit": [("Capital", 80000)]},
    {"question": "Goods taken by the proprietor for personal use Rs.3,000.",
     "status": "VERIFIED", "type_key": "GOODS_PERSONAL_USE", "journals": 1,
     "debit": [("Drawings", 3000)], "credit": [("Purchases", 3000)]},
]

# ---------------------------------------------------------------------------
# B. 32 wording variants (semantic equivalence -> ONE canonical pattern)
# ---------------------------------------------------------------------------
WORDING_VARIANTS = [
    {"question": "Bought goods for cash Rs.16,000.", "status": "VERIFIED",
     "type_key": "PURCHASE_GOODS_CASH", "journals": 1,
     "debit": [("Purchases", 16000)], "credit": [("Cash", 16000)]},
    {"question": "Goods purchased for cash Rs.16,000.", "status": "VERIFIED",
     "type_key": "PURCHASE_GOODS_CASH", "journals": 1,
     "debit": [("Purchases", 16000)], "credit": [("Cash", 16000)]},
    {"question": "Goods bought for cash Rs.16,000.", "status": "VERIFIED",
     "type_key": "PURCHASE_GOODS_CASH", "journals": 1,
     "debit": [("Purchases", 16000)], "credit": [("Cash", 16000)]},
    {"question": "Purchased goods paying cash Rs.16,000.", "status": "VERIFIED",
     "type_key": "PURCHASE_GOODS_CASH", "journals": 1,
     "debit": [("Purchases", 16000)], "credit": [("Cash", 16000)]},
    {"question": "Goods purchased from Rahul on credit Rs.22,000.",
     "status": "VERIFIED", "type_key": "PURCHASE_GOODS_CREDIT", "journals": 1,
     "debit": [("Purchases", 22000)], "credit": [("Rahul", 22000)]},
    {"question": "Bought goods on credit from Rahul Rs.22,000.",
     "status": "VERIFIED", "type_key": "PURCHASE_GOODS_CREDIT", "journals": 1,
     "debit": [("Purchases", 22000)], "credit": [("Rahul", 22000)]},
    {"question": "Purchased goods from Rahul for Rs.22,000 on credit.",
     "status": "VERIFIED", "type_key": "PURCHASE_GOODS_CREDIT", "journals": 1,
     "debit": [("Purchases", 22000)], "credit": [("Rahul", 22000)]},
    {"question": "Bought goods on account from Rahul Rs.22,000.",
     "status": "VERIFIED", "type_key": "PURCHASE_GOODS_CREDIT", "journals": 1,
     "debit": [("Purchases", 22000)], "credit": [("Rahul", 22000)]},
    {"question": "Goods sold for cash Rs.25,000.", "status": "VERIFIED",
     "type_key": "SALE_GOODS_CASH", "journals": 1,
     "debit": [("Cash", 25000)], "credit": [("Sales", 25000)]},
    {"question": "Cash sale of goods Rs.25,000.", "status": "VERIFIED",
     "type_key": "SALE_GOODS_CASH", "journals": 1,
     "debit": [("Cash", 25000)], "credit": [("Sales", 25000)]},
    {"question": "Goods sold and cash received immediately Rs.25,000.",
     "status": "VERIFIED", "type_key": "SALE_GOODS_CASH", "journals": 1,
     "debit": [("Cash", 25000)], "credit": [("Sales", 25000)]},
    {"question": "Sold goods to Mohan for Rs.18,000 on credit.",
     "status": "VERIFIED", "type_key": "SALE_GOODS_CREDIT", "journals": 1,
     "debit": [("Mohan", 18000)], "credit": [("Sales", 18000)]},
    {"question": "Sold goods on account to Mohan Rs.18,000.",
     "status": "VERIFIED", "type_key": "SALE_GOODS_CREDIT", "journals": 1,
     "debit": [("Mohan", 18000)], "credit": [("Sales", 18000)]},
    {"question": "Sold goods to Mohan for cash Rs.25,000.", "status": "VERIFIED",
     "type_key": "SALE_GOODS_CASH", "journals": 1,
     "debit": [("Cash", 25000)], "credit": [("Sales", 25000)]},
    {"question": "Purchased furniture for cash Rs.12,000.", "status": "VERIFIED",
     "type_key": "PURCHASE_ASSET_CASH", "journals": 1,
     "debit": [("Furniture", 12000)], "credit": [("Cash", 12000)]},
    {"question": "Furniture purchased for Rs.12,000 in cash.",
     "status": "VERIFIED", "type_key": "PURCHASE_ASSET_CASH", "journals": 1,
     "debit": [("Furniture", 12000)], "credit": [("Cash", 12000)]},
    {"question": "Purchased furniture costing Rs.12,000, payment made immediately.",
     "status": "VERIFIED", "type_key": "PURCHASE_ASSET_CASH", "journals": 1,
     "debit": [("Furniture", 12000)], "credit": [("Cash", 12000)]},
    {"question": "Bought furniture from Vijay for Rs.20,000 on credit.",
     "status": "VERIFIED", "type_key": "PURCHASE_ASSET_CREDIT", "journals": 1,
     "debit": [("Furniture", 20000)], "credit": [("Vijay", 20000)]},
    {"question": "Paid rent Rs.6,000 in cash.", "status": "VERIFIED",
     "type_key": "EXPENSE_PAID", "journals": 1,
     "debit": [("Rent", 6000)], "credit": [("Cash", 6000)]},
    {"question": "Rent paid Rs.6,000.", "status": "VERIFIED",
     "type_key": "EXPENSE_PAID", "journals": 1,
     "debit": [("Rent", 6000)], "credit": [("Cash", 6000)]},
    {"question": "Paid for rent Rs.6,000.", "status": "VERIFIED",
     "type_key": "EXPENSE_PAID", "journals": 1,
     "debit": [("Rent", 6000)], "credit": [("Cash", 6000)]},
    {"question": "Payment made for rent Rs.6,000 in cash.", "status": "VERIFIED",
     "type_key": "EXPENSE_PAID", "journals": 1,
     "debit": [("Rent", 6000)], "credit": [("Cash", 6000)]},
    {"question": "Commission received in cash Rs.3,600.", "status": "VERIFIED",
     "type_key": "INCOME_RECEIVED", "journals": 1,
     "debit": [("Cash", 3600)], "credit": [("Commission Received", 3600)]},
    {"question": "Received commission from Rahul Rs.3,600.", "status": "VERIFIED",
     "type_key": "INCOME_RECEIVED", "journals": 1,
     "debit": [("Cash", 3600)], "credit": [("Commission Received", 3600)]},
    {"question": "Cash deposited into bank Rs.12,000.", "status": "VERIFIED",
     "type_key": "CASH_INTO_BANK", "journals": 1,
     "debit": [("Bank", 12000)], "credit": [("Cash", 12000)]},
    {"question": "Deposited cash into the bank Rs.12,000.", "status": "VERIFIED",
     "type_key": "CASH_INTO_BANK", "journals": 1,
     "debit": [("Bank", 12000)], "credit": [("Cash", 12000)]},
    {"question": "Withdrew cash from the bank Rs.3,000.", "status": "VERIFIED",
     "type_key": "CASH_FROM_BANK", "journals": 1,
     "debit": [("Cash", 3000)], "credit": [("Bank", 3000)]},
    {"question": "Paid cash to Rahul Rs.7,000.", "status": "VERIFIED",
     "type_key": "PAID_TO", "journals": 1,
     "debit": [("Rahul", 7000)], "credit": [("Cash", 7000)]},
    {"question": "Received cash from Mohan Rs.5,500.", "status": "VERIFIED",
     "type_key": "RECEIVED_FROM", "journals": 1,
     "debit": [("Cash", 5500)], "credit": [("Mohan", 5500)]},
    {"question": "Issued a cheque in favour of Amit for Rs.6,500.",
     "status": "VERIFIED", "type_key": "CHEQUE_PAID", "journals": 1,
     "debit": [("Amit", 6500)], "credit": [("Bank", 6500)]},
    {"question": "Cheque received from Mohan Rs.8,500.", "status": "VERIFIED",
     "type_key": "CHEQUE_RECEIVED", "journals": 1,
     "debit": [("Bank", 8500)], "credit": [("Mohan", 8500)]},
    {"question": "Goods worth Rs.10,000 purchased from Rahul at 10% trade discount.",
     "status": "VERIFIED", "type_key": "PURCHASE_GOODS_CREDIT", "journals": 1,
     "debit": [("Purchases", 9000)], "credit": [("Rahul", 9000)]},
]

# ---------------------------------------------------------------------------
# C. 26 composed / multi-step transactions (pipeline with provenance)
# ---------------------------------------------------------------------------
COMPOSED_TRANSACTIONS = [
    {"question": "Purchased goods from Rahul for Rs.10,000 at 10% trade "
     "discount. Half the amount was paid immediately and a cash discount "
     "of 2% was allowed on the amount paid.",
     "status": "VERIFIED", "type_key": "PURCHASE_GOODS_CREDIT", "journals": 1,
     "debit": [("Purchases", 9000)],
     "credit": [("Cash", 4410), ("Discount Received", 90), ("Rahul", 4500)]},
    {"question": "Purchased goods from Rahul for Rs.10,000 at 10% trade "
     "discount.", "status": "VERIFIED", "type_key": "PURCHASE_GOODS_CREDIT",
     "journals": 1, "debit": [("Purchases", 9000)], "credit": [("Rahul", 9000)]},
    {"question": "Purchased goods from Rahul for Rs.10,000; paid him Rs.4,000 "
     "immediately.", "status": "VERIFIED", "type_key": "PURCHASE_GOODS_CREDIT",
     "journals": 1, "debit": [("Purchases", 10000)],
     "credit": [("Cash", 4000), ("Rahul", 6000)]},
    {"question": "Purchased goods from Rahul for Rs.10,000; paid him Rs.4,000 "
     "at once.", "status": "VERIFIED", "type_key": "PURCHASE_GOODS_CREDIT",
     "journals": 1, "debit": [("Purchases", 10000)],
     "credit": [("Cash", 4000), ("Rahul", 6000)]},
    {"question": "Purchased goods for cash Rs.10,000 at 10% trade discount.",
     "status": "VERIFIED", "type_key": "PURCHASE_GOODS_CASH", "journals": 1,
     "debit": [("Purchases", 9000)], "credit": [("Cash", 9000)]},
    {"question": "Sold goods for cash Rs.10,000 at 10% trade discount.",
     "status": "VERIFIED", "type_key": "SALE_GOODS_CASH", "journals": 1,
     "debit": [("Cash", 9000)], "credit": [("Sales", 9000)]},
    {"question": "Sold goods to Mohan for Rs.10,000 at 10% trade discount; "
     "received half immediately.", "status": "VERIFIED",
     "type_key": "SALE_GOODS_CREDIT", "journals": 1,
     "debit": [("Mohan", 4500), ("Cash", 4500)], "credit": [("Sales", 9000)]},
    {"question": "Sold goods to Mohan for Rs.15,000 at 10% trade discount; "
     "received cash for half at once.", "status": "VERIFIED",
     "type_key": "SALE_GOODS_CREDIT", "journals": 1,
     "debit": [("Mohan", 6750), ("Cash", 6750)], "credit": [("Sales", 13500)]},
    {"question": "Purchased goods from Rahul for Rs.20,000 at 15% trade "
     "discount.", "status": "VERIFIED", "type_key": "PURCHASE_GOODS_CREDIT",
     "journals": 1, "debit": [("Purchases", 17000)], "credit": [("Rahul", 17000)]},
    {"question": "Purchased goods from Rahul for Rs.20,000 at 15% trade "
     "discount; paid 50% immediately.", "status": "VERIFIED",
     "type_key": "PURCHASE_GOODS_CREDIT", "journals": 1,
     "debit": [("Purchases", 17000)],
     "credit": [("Cash", 8500), ("Rahul", 8500)]},
    {"question": "Purchased goods from Rahul for Rs.20,000 at 15% trade "
     "discount; paid half immediately and 2% cash discount on the amount "
     "paid.", "status": "VERIFIED", "type_key": "PURCHASE_GOODS_CREDIT",
     "journals": 1, "debit": [("Purchases", 17000)],
     "credit": [("Cash", 8330), ("Discount Received", 170), ("Rahul", 8500)]},
    {"question": "Purchased goods from Rahul for Rs.10,000; paid him Rs.6,000 "
     "immediately.", "status": "VERIFIED", "type_key": "PURCHASE_GOODS_CREDIT",
     "journals": 1, "debit": [("Purchases", 10000)],
     "credit": [("Cash", 6000), ("Rahul", 4000)]},
    {"question": "Purchased goods from Amit for Rs.8,000 at 5% trade discount; "
     "paid the full amount immediately.", "status": "VERIFIED",
     "type_key": "PURCHASE_GOODS_CASH", "journals": 1,
     "debit": [("Purchases", 7600)], "credit": [("Cash", 7600)]},
    {"question": "Purchased goods from Rahul for Rs.10,000 at 10% trade "
     "discount; paid one quarter immediately.", "status": "VERIFIED",
     "type_key": "PURCHASE_GOODS_CREDIT", "journals": 1,
     "debit": [("Purchases", 9000)],
     "credit": [("Cash", 2250), ("Rahul", 6750)]},
    {"question": "Sold goods to Mohan for Rs.12,000 at 10% trade discount; "
     "Mohan paid half by cheque.", "status": "VERIFIED",
     "type_key": "SALE_GOODS_CREDIT", "journals": 1,
     "debit": [("Mohan", 5400), ("Bank", 5400)], "credit": [("Sales", 10800)]},
    {"question": "Received Rs.9,700 from Mohan, allowed discount Rs.300.",
     "status": "VERIFIED", "type_key": "RECEIVED_FROM", "journals": 1,
     "debit": [("Cash", 9700), ("Discount Allowed", 300)],
     "credit": [("Mohan", 10000)]},
    {"question": "Paid Amit Rs.9,700 in full settlement of his account of "
     "Rs.10,000, discount received Rs.300.", "status": "VERIFIED",
     "type_key": "PAID_TO", "journals": 1, "debit": [("Amit", 10000)],
     "credit": [("Cash", 9700), ("Discount Received", 300)]},
    {"question": "Received from Mohan Rs.5,000 in full settlement of his "
     "account of Rs.5,200.", "status": "VERIFIED", "type_key": "RECEIVED_FROM",
     "journals": 1, "debit": [("Cash", 5000), ("Discount Allowed", 200)],
     "credit": [("Mohan", 5200)]},
    {"question": "Paid to Rahul Rs.9,800, discount received Rs.200, in full "
     "settlement of Rs.10,000.", "status": "VERIFIED", "type_key": "PAID_TO",
     "journals": 1, "debit": [("Rahul", 10000)],
     "credit": [("Cash", 9800), ("Discount Received", 200)]},
    {"question": "Goods costing Rs.10,000 sold to Mohan for cash Rs.12,000.",
     "status": "VERIFIED", "type_key": "SALE_GOODS_CASH", "journals": 1,
     "debit": [("Cash", 12000)], "credit": [("Sales", 12000)]},
    {"question": "Goods costing Rs.8,000 sold to Mohan on credit Rs.10,000.",
     "status": "VERIFIED", "type_key": "SALE_GOODS_CREDIT", "journals": 1,
     "debit": [("Mohan", 10000)], "credit": [("Sales", 10000)]},
    {"question": "Purchased goods from Rahul for Rs.10,000, paid him Rs.3,000 "
     "immediately and 2% cash discount on the paid amount.",
     "status": "VERIFIED", "type_key": "PURCHASE_GOODS_CREDIT", "journals": 1,
     "debit": [("Purchases", 10000)],
     "credit": [("Cash", 2940), ("Discount Received", 60), ("Rahul", 7000)]},
    {"question": "Purchased goods from Rahul for Rs.12,000 at 25% trade "
     "discount; paid three-fourths immediately.", "status": "VERIFIED",
     "type_key": "PURCHASE_GOODS_CREDIT", "journals": 1,
     "debit": [("Purchases", 9000)],
     "credit": [("Cash", 6750), ("Rahul", 2250)]},
    {"question": "Purchased goods from Rahul for Rs.10,000 at 10% trade "
     "discount; paid him Rs.2,000 immediately.", "status": "VERIFIED",
     "type_key": "PURCHASE_GOODS_CREDIT", "journals": 1,
     "debit": [("Purchases", 9000)],
     "credit": [("Cash", 2000), ("Rahul", 7000)]},
    {"question": "Purchased goods from Rahul for Rs.10,000 at 10% trade "
     "discount; paid him Rs.2,000 immediately and 5% cash discount on the "
     "amount paid.", "status": "VERIFIED", "type_key": "PURCHASE_GOODS_CREDIT",
     "journals": 1, "debit": [("Purchases", 9000)],
     "credit": [("Cash", 1900), ("Discount Received", 100), ("Rahul", 7000)]},
    {"question": "Purchased goods from Rahul for Rs.10,000 at 10% trade "
     "discount, half paid immediately with 2% cash discount.",
     "status": "VERIFIED", "type_key": "PURCHASE_GOODS_CREDIT", "journals": 1,
     "debit": [("Purchases", 9000)],
     "credit": [("Cash", 4410), ("Discount Received", 90), ("Rahul", 4500)]},
]

# ---------------------------------------------------------------------------
# D. 16 multi-transaction questions (chronological, independent entries)
# ---------------------------------------------------------------------------
MULTI_TRANSACTION = [
    {"question": "Started business with cash Rs.1,00,000. Purchased goods for "
     "cash Rs.20,000. Purchased furniture for Rs.10,000 from Rahul. Paid "
     "rent Rs.5,000.", "status": "VERIFIED", "type_key": "START_BUSINESS",
     "journals": 4,
     "debit": [("Cash", 100000), ("Purchases", 20000), ("Furniture", 10000),
               ("Rent", 5000)],
     "credit": [("Capital", 100000), ("Cash", 20000), ("Rahul", 10000),
                ("Cash", 5000)]},
    {"question": "Started business with cash Rs.50,000. Purchased goods for "
     "cash Rs.10,000. Sold goods for cash Rs.8,000.", "status": "VERIFIED",
     "type_key": "START_BUSINESS", "journals": 3,
     "debit": [("Cash", 50000), ("Purchases", 10000), ("Cash", 8000)],
     "credit": [("Capital", 50000), ("Cash", 10000), ("Sales", 8000)]},
    {"question": "Purchased goods from Rahul for Rs.20,000. Returned goods "
     "worth Rs.1,000 to him.", "status": "VERIFIED",
     "type_key": "PURCHASE_GOODS_CREDIT", "journals": 2,
     "debit": [("Purchases", 20000), ("Rahul", 1000)],
     "credit": [("Rahul", 20000), ("Purchase Returns", 1000)]},
    {"question": "Sold goods to Mohan for Rs.15,000. He returned goods worth "
     "Rs.800.", "status": "VERIFIED", "type_key": "SALE_GOODS_CREDIT",
     "journals": 2, "debit": [("Mohan", 15000), ("Sales Returns", 800)],
     "credit": [("Sales", 15000), ("Mohan", 800)]},
    {"question": "Purchased goods from Rahul for Rs.10,000. Paid him Rs.4,000 "
     "immediately.", "status": "VERIFIED", "type_key": "PURCHASE_GOODS_CREDIT",
     "journals": 1, "debit": [("Purchases", 10000)],
     "credit": [("Cash", 4000), ("Rahul", 6000)]},
    {"question": "Purchased goods from Rahul for Rs.10,000. Paid him Rs.4,000 "
     "immediately. Paid rent Rs.2,000.", "status": "VERIFIED",
     "type_key": "PURCHASE_GOODS_CREDIT", "journals": 2,
     "debit": [("Purchases", 10000), ("Rent", 2000)],
     "credit": [("Cash", 4000), ("Rahul", 6000), ("Cash", 2000)]},
    {"question": "Received commission Rs.3,000. Paid rent Rs.2,000. Deposited "
     "cash into bank Rs.1,000.", "status": "VERIFIED",
     "type_key": "INCOME_RECEIVED", "journals": 3,
     "debit": [("Cash", 3000), ("Rent", 2000), ("Bank", 1000)],
     "credit": [("Commission Received", 3000), ("Cash", 2000), ("Cash", 1000)]},
    {"question": "Sold goods to Mohan for Rs.15,000. Received Rs.9,800 from "
     "him, discount allowed Rs.200.", "status": "VERIFIED",
     "type_key": "SALE_GOODS_CREDIT", "journals": 2,
     "debit": [("Mohan", 15000), ("Cash", 9800), ("Discount Allowed", 200)],
     "credit": [("Sales", 15000), ("Mohan", 10000)]},
    {"question": "Started business with cash Rs.1,00,000 and furniture "
     "Rs.20,000. Purchased goods for cash Rs.15,000.", "status": "VERIFIED",
     "type_key": "START_BUSINESS", "journals": 2,
     "debit": [("Cash", 100000), ("Furniture", 20000), ("Purchases", 15000)],
     "credit": [("Capital", 120000), ("Cash", 15000)]},
    {"question": "Deposited cash into bank Rs.10,000. Paid salary Rs.3,000 by "
     "cheque.", "status": "VERIFIED", "type_key": "CASH_INTO_BANK",
     "journals": 2, "debit": [("Bank", 10000), ("Salaries", 3000)],
     "credit": [("Cash", 10000), ("Bank", 3000)]},
    {"question": "Purchased goods from Rahul for Rs.10,000 at 10% trade "
     "discount. Half the amount was paid immediately. Paid rent Rs.2,000.",
     "status": "VERIFIED", "type_key": "PURCHASE_GOODS_CREDIT", "journals": 2,
     "debit": [("Purchases", 9000), ("Rent", 2000)],
     "credit": [("Cash", 4500), ("Rahul", 4500), ("Cash", 2000)]},
    {"question": "Received commission Rs.2,000 by cheque. Paid rent Rs.1,500 "
     "in cash.", "status": "VERIFIED", "type_key": "INCOME_RECEIVED",
     "journals": 2, "debit": [("Bank", 2000), ("Rent", 1500)],
     "credit": [("Commission Received", 2000), ("Cash", 1500)]},
    {"question": "Purchased goods from Rahul for Rs.10,000 on credit. Paid him "
     "Rs.6,000 in cash.", "status": "VERIFIED",
     "type_key": "PURCHASE_GOODS_CREDIT", "journals": 1,
     "debit": [("Purchases", 10000)],
     "credit": [("Cash", 6000), ("Rahul", 4000)]},
    {"question": "Sold goods to Mohan for Rs.20,000. Mohan paid Rs.12,000 "
     "immediately.", "status": "VERIFIED", "type_key": "SALE_GOODS_CREDIT",
     "journals": 2, "debit": [("Mohan", 20000), ("Cash", 12000)],
     "credit": [("Sales", 20000), ("Mohan", 12000)]},
    {"question": "Sold goods to Mohan for Rs.20,000. Mohan paid Rs.12,000 "
     "immediately. Paid rent Rs.1,000.", "status": "VERIFIED",
     "type_key": "SALE_GOODS_CREDIT", "journals": 3,
     "debit": [("Mohan", 20000), ("Cash", 12000), ("Rent", 1000)],
     "credit": [("Sales", 20000), ("Mohan", 12000), ("Cash", 1000)]},
    {"question": "Started business with cash Rs.50,000. Purchased goods from "
     "Rahul for Rs.20,000 on credit. Paid him Rs.8,000 in cash. Sold goods "
     "for cash Rs.12,000.", "status": "VERIFIED", "type_key": "START_BUSINESS",
     "journals": 3,
     "debit": [("Cash", 50000), ("Purchases", 20000), ("Cash", 12000)],
     "credit": [("Capital", 50000), ("Cash", 8000), ("Rahul", 12000),
                ("Sales", 12000)]},
]

# ---------------------------------------------------------------------------
# E. 12 student-answer verification cases (first deterministic mistake)
# ---------------------------------------------------------------------------
STUDENT_ERROR_CASES = [
    {"question": "Purchased furniture for cash Rs.15,000.",
     "checks": [
         {"kind": "journal",
          "student": {"debits": [("Furniture", 15000)],
                      "credits": [("Cash", 15000)]},
          "expected_verdict": "CORRECT", "hint": None},
         {"kind": "journal",
          "student": {"debits": [("Cash", 15000)],
                      "credits": [("Furniture", 15000)]},
          "expected_verdict": "INCORRECT",
          "hint": "debit side"},
     ]},
    {"question": "Purchased furniture for cash Rs.15,000.",
     "checks": [
         {"kind": "journal",
          "student": {"debits": [("Furniture", 14000)],
                      "credits": [("Cash", 14000)]},
          "expected_verdict": "INCORRECT", "hint": "amount"},
         {"kind": "journal",
          "student": {"debits": [("Furniture", 15000)],
                      "credits": [("Cash", 14000)]},
          "expected_verdict": "INCORRECT", "hint": "balanced"},
     ]},
    {"question": "Purchased furniture for cash Rs.15,000.",
     "checks": [
         {"kind": "journal",
          "student": {"debits": [("Furniture", 15000), ("Machinery", 15000)],
                      "credits": [("Cash", 30000)]},
          "expected_verdict": "INCORRECT", "hint": "Machinery"},
     ]},
    {"question": "Sold goods to Mohan for cash Rs.20,000.",
     "checks": [
         {"kind": "journal",
          "student": {"debits": [("Mohan", 20000)],
                      "credits": [("Sales", 20000)]},
          "expected_verdict": "INCORRECT",
          "hint": "Mohan"},
     ]},
    {"question": "Sold goods to Mohan on credit Rs.20,000.",
     "checks": [
         {"kind": "journal",
          "student": {"debits": [("Cash", 20000)],
                      "credits": [("Sales", 20000)]},
          "expected_verdict": "INCORRECT", "hint": "Cash"},
     ]},
    {"question": "Paid rent Rs.5,000.",
     "checks": [
         {"kind": "journal",
          "student": {"debits": [("Rent", 5000)], "credits": [("Cash", 5000)]},
          "expected_verdict": "CORRECT", "hint": None},
         {"kind": "journal",
          "student": {"debits": [("Cash", 5000)], "credits": [("Rent", 5000)]},
          "expected_verdict": "INCORRECT", "hint": "debit side"},
     ]},
    {"question": "Purchased goods for cash Rs.15,000.",
     "checks": [
         {"kind": "final", "what": "journal_total", "answer": "15000",
          "expected_verdict": "CORRECT", "hint": None},
         {"kind": "final", "what": "journal_total", "answer": "16000",
          "expected_verdict": "INCORRECT", "hint": "15000"},
     ]},
    {"question": "Purchased goods for cash Rs.15,000.",
     "checks": [
         {"kind": "final", "what": "debit:Purchases", "answer": "15000",
          "expected_verdict": "CORRECT", "hint": None},
         {"kind": "final", "what": "debit:Purchases", "answer": "14000",
          "expected_verdict": "INCORRECT", "hint": "15000"},
     ]},
    {"question": "Purchased furniture for cash Rs.15,000.",
     "checks": [
         {"kind": "final", "what": "balance:Furniture", "answer": "15000 Dr",
          "expected_verdict": "CORRECT", "hint": None},
         {"kind": "final", "what": "balance:Furniture", "answer": "15000 Cr",
          "expected_verdict": "INCORRECT", "hint": "Dr"},
     ]},
    {"question": "Purchased furniture for cash Rs.15,000.",
     "checks": [
         {"kind": "ledger", "account": "Furniture", "balance": "15000",
          "side": "Dr", "expected_verdict": "CORRECT", "hint": None},
         {"kind": "ledger", "account": "Furniture", "balance": "14000",
          "side": "Dr", "expected_verdict": "INCORRECT", "hint": "amount"},
     ]},
    {"question": "Purchased goods for cash Rs.15,000. Paid rent Rs.5,000.",
     "checks": [
         {"kind": "tb", "rows": [
             {"account": "Purchases", "debit": 15000.0, "credit": 0.0},
             {"account": "Rent", "debit": 5000.0, "credit": 0.0},
             {"account": "Cash", "debit": 0.0, "credit": 20000.0},
         ], "expected_verdict": "CORRECT", "hint": None},
         {"kind": "tb", "rows": [
             {"account": "Purchases", "debit": 14000.0, "credit": 0.0},
             {"account": "Rent", "debit": 5000.0, "credit": 0.0},
             {"account": "Cash", "debit": 0.0, "credit": 20000.0},
         ], "expected_verdict": "INCORRECT", "hint": "Purchases"},
     ]},
    {"question": "Purchased goods for cash Rs.15,000. Paid rent Rs.5,000.",
     "checks": [
         {"kind": "final", "what": "trial_balance_total", "answer": "20000",
          "expected_verdict": "CORRECT", "hint": None},
     ]},
]

# ---------------------------------------------------------------------------
# F. 12 missing / ambiguous cases (never invented)
# ---------------------------------------------------------------------------
MISSING_AMBIGUOUS = [
    {"question": "Purchased goods from Rahul.", "status": "BLOCKED"},
    {"question": "Purchased goods for Rs.10,000.", "status": "REVIEW_REQUIRED"},
    {"question": "Paid Rs.5,000.", "status": "REVIEW_REQUIRED"},
    {"question": "Received Rs.5,000.", "status": "REVIEW_REQUIRED"},
    {"question": "Purchased goods.", "status": "REVIEW_REQUIRED"},
    {"question": "Sold goods.", "status": "REVIEW_REQUIRED"},
    {"question": "Purchased furniture.", "status": "REVIEW_REQUIRED"},
    {"question": "Paid rent.", "status": "BLOCKED"},
    {"question": "Received commission.", "status": "BLOCKED"},
    {"question": "Purchased goods from Rahul, paid half the amount in cash.",
     "status": "BLOCKED"},
    {"question": "Sold goods to Mohan for Rs.15,000, discount allowed Rs.200.",
     "status": "REVIEW_REQUIRED"},
    {"question": "Discount allowed Rs.200.", "status": "REVIEW_REQUIRED"},
]

# ---------------------------------------------------------------------------
# G. 12 unsupported / refusal cases (outside the Ch.1-3 boundary)
# ---------------------------------------------------------------------------
UNSUPPORTED_REFUSALS = [
    {"question": "Depreciate machinery by 10% per annum.", "status":
     "NOT_SUPPORTED"},
    {"question": "Prepare final accounts from the trial balance.", "status":
     "NOT_SUPPORTED"},
    {"question": "Prepare Trading and Profit and Loss Account for the year "
     "ended 31 March.", "status": "NOT_SUPPORTED"},
    {"question": "Ravi and Suresh started a partnership firm.", "status":
     "NOT_SUPPORTED"},
    {"question": "Charge depreciation on furniture Rs.1,000.", "status":
     "NOT_SUPPORTED"},
    {"question": "Provide for doubtful debts at 5% on debtors.", "status":
     "NOT_SUPPORTED"},
    {"question": "Pass opening entry for the new year.", "status":
     "NOT_SUPPORTED"},
    {"question": "Prepare a Balance Sheet as at 31 March.", "status":
     "NOT_SUPPORTED"},
    {"question": "Issue 10,000 equity shares of Rs.10 each.", "status":
     "NOT_SUPPORTED"},
    {"question": "Consignment of goods sent to Mumbai agent.", "status":
     "NOT_SUPPORTED"},
    {"question": "Goods purchased under hire purchase agreement.", "status":
     "NOT_SUPPORTED"},
    {"question": "Revalue the assets of the firm.", "status": "NOT_SUPPORTED"},
]

# ---------------------------------------------------------------------------
# The complete golden benchmark (never regenerated from the solver)
# ---------------------------------------------------------------------------
BK15F_BENCHMARK = (BASIC_TRANSACTIONS + WORDING_VARIANTS
                   + COMPOSED_TRANSACTIONS + MULTI_TRANSACTION
                   + MISSING_AMBIGUOUS + UNSUPPORTED_REFUSALS)

VERIFIED_CASES = [c for c in BK15F_BENCHMARK if c["status"] == "VERIFIED"]
REFUSAL_CASES = [c for c in BK15F_BENCHMARK if c["status"] != "VERIFIED"]

# Exact-account adversarial oracles: ONLY these accounts may appear.
EXACT_ACCOUNT_CASES = [
    {"question": "Purchased Furniture for Cash Rs.15,000.",
     "allowed": {"Furniture", "Cash"}},
    {"question": "Purchased machinery for cash Rs.75,000.",
     "allowed": {"Machinery", "Cash"}},
    {"question": "Purchased a building for cash Rs.40,00,000.",
     "allowed": {"Building", "Cash"}},
    {"question": "Sold goods to Mohan for cash Rs.20,000.",
     "allowed": {"Cash", "Sales"}},
    {"question": "Bought furniture from Vijay on credit Rs.20,000.",
     "allowed": {"Furniture", "Vijay"}},
    {"question": "Sold old furniture for cash Rs.6,000.",
     "allowed": {"Cash", "Furniture"}},
]


def merged_lines_match(out: dict, case: dict) -> bool:
    """True when the engine's journal lines match the hand-written oracle.

    Compares journal count, every debit line and every credit line
    (account + integer amount) across ALL entries. The oracle was written
    from the FYJC golden rules and never consults the engine.
    """
    journals = out.get("journals") or [out.get("journal")] or []
    if len(journals) != case.get("journals", 1):
        return False
    dr = [l for j in journals for l in (j.get("debit_lines") or [])]
    cr = [l for j in journals for l in (j.get("credit_lines") or [])]
    got_dr = sorted(
        (str(l.get("account") or ""),
         int(round(float(l.get("amount", 0))))) for l in dr)
    got_cr = sorted(
        (str(l.get("account") or ""),
         int(round(float(l.get("amount", 0))))) for l in cr)
    exp_dr = sorted((a, int(v)) for a, v in case["debit"])
    exp_cr = sorted((a, int(v)) for a, v in case["credit"])
    return got_dr == exp_dr and got_cr == exp_cr


if __name__ == "__main__":
    total = len(BK15F_BENCHMARK) + len(STUDENT_ERROR_CASES)
    print(f"BK15F benchmark: {len(BK15F_BENCHMARK)} cases "
          f"(verified {len(VERIFIED_CASES)}, refusals {len(REFUSAL_CASES)}) "
          f"+ {len(STUDENT_ERROR_CASES)} student-error cases = {total}")
