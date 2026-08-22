"""
Platrixa
Sprint 15E - FYJC Book-Keeping Unit-Test-1 golden benchmark
backend/maths/fyjc_bk_15e_benchmark.py

A hand-verified textbook-style benchmark for the first three FYJC
Book-Keeping & Accountancy chapters (Unit Test 1 scope):

  Ch 1 - Introduction to Book-Keeping & Accountancy (business entity,
         double entry, account classification Real/Personal/Nominal)
  Ch 2 - Basic Accounting Terms / the accounting equation
  Ch 3 - Journal (the complete basic transaction family: capital,
         drawings, purchases, sales, expenses, incomes, bank/cash,
         discounts, returns, multi-transaction questions)

Every oracle below was written BY HAND from standard FYJC textbook
treatment of each transaction. The oracle NEVER calls the Platrixa solver -
the benchmark therefore measures the engine against an independent
expected answer, not against itself.

Case fields
-----------
  question   : the textbook-style question (one or more transactions)
  status     : VERIFIED | BLOCKED | REVIEW_REQUIRED | NOT_SUPPORTED
  type_key   : expected canonical transaction key (VERIFIED cases)
  journals   : expected number of independent journal entries
  debit      : expected DEBIT lines across all journals, (account, amount)
  credit     : expected CREDIT lines across all journals, (account, amount)

Amounts are integers (rupees). Accounts use the canonical FYJC chart
spelling (Cash, Bank, Purchases, Sales, Furniture, Rahul, ...).

Hard-oracle rule: these expected values were derived from the standard
FYJC journal rules (Real/Personal/Nominal golden rules) - not from any
run of the engine.
"""

# ---------------------------------------------------------------------------
# A. Capital & Drawings (Ch 2/3 - capital introduction, assets as capital)
# ---------------------------------------------------------------------------
CAPITAL_DRAWINGS = [
    {"question": "Started business with cash Rs.50,000.",
     "status": "VERIFIED", "type_key": "START_BUSINESS", "journals": 1,
     "debit": [("Cash", 50000)], "credit": [("Capital", 50000)]},
    {"question": "Started business with bank balance Rs.1,00,000.",
     "status": "VERIFIED", "type_key": "START_BUSINESS", "journals": 1,
     "debit": [("Bank", 100000)], "credit": [("Capital", 100000)]},
    {"question": "Started business with cash Rs.50,000 and furniture Rs.20,000.",
     "status": "VERIFIED", "type_key": "START_BUSINESS", "journals": 1,
     "debit": [("Cash", 50000), ("Furniture", 20000)],
     "credit": [("Capital", 70000)]},
    {"question": "Commenced business with cash Rs.40,000 and machinery Rs.60,000.",
     "status": "VERIFIED", "type_key": "START_BUSINESS", "journals": 1,
     "debit": [("Cash", 40000), ("Machinery", 60000)],
     "credit": [("Capital", 100000)]},
    {"question": "Started business with cash Rs.1,00,000 and bank balance Rs.50,000.",
     "status": "VERIFIED", "type_key": "START_BUSINESS", "journals": 1,
     "debit": [("Cash", 100000), ("Bank", 50000)],
     "credit": [("Capital", 150000)]},
    {"question": "Brought in additional capital of Rs.30,000 in cash.",
     "status": "VERIFIED", "type_key": "CAPITAL_INTRODUCED", "journals": 1,
     "debit": [("Cash", 30000)], "credit": [("Capital", 30000)]},
    {"question": "Introduced furniture worth Rs.25,000 as additional capital.",
     "status": "VERIFIED", "type_key": "CAPITAL_ASSET_INTRODUCED", "journals": 1,
     "debit": [("Furniture", 25000)], "credit": [("Capital", 25000)]},
    {"question": "Brought machinery worth Rs.50,000 into the business.",
     "status": "VERIFIED", "type_key": "CAPITAL_ASSET_INTRODUCED", "journals": 1,
     "debit": [("Machinery", 50000)], "credit": [("Capital", 50000)]},
    {"question": "Brought furniture into the business as capital Rs.20,000.",
     "status": "VERIFIED", "type_key": "CAPITAL_ASSET_INTRODUCED", "journals": 1,
     "debit": [("Furniture", 20000)], "credit": [("Capital", 20000)]},
    {"question": "Withdrew cash Rs.5,000 for personal use.",
     "status": "VERIFIED", "type_key": "DRAWINGS_CASH", "journals": 1,
     "debit": [("Drawings", 5000)], "credit": [("Cash", 5000)]},
    {"question": "Withdrew goods worth Rs.3,000 for personal use.",
     "status": "VERIFIED", "type_key": "GOODS_PERSONAL_USE", "journals": 1,
     "debit": [("Drawings", 3000)], "credit": [("Purchases", 3000)]},
    {"question": "Goods taken by the proprietor for private use Rs.4,000.",
     "status": "VERIFIED", "type_key": "GOODS_PERSONAL_USE", "journals": 1,
     "debit": [("Drawings", 4000)], "credit": [("Purchases", 4000)]},
    {"question": "Withdrew cash from bank for personal use Rs.2,000.",
     "status": "VERIFIED", "type_key": "DRAWINGS_CASH", "journals": 1,
     "debit": [("Drawings", 2000)], "credit": [("Bank", 2000)]},
]

# ---------------------------------------------------------------------------
# B. Purchases (Ch 3 - cash/credit/cheque/discount/partial payment/costing)
# ---------------------------------------------------------------------------
PURCHASES = [
    {"question": "Purchased goods for cash Rs.15,000.",
     "status": "VERIFIED", "type_key": "PURCHASE_GOODS_CASH", "journals": 1,
     "debit": [("Purchases", 15000)], "credit": [("Cash", 15000)]},
    {"question": "Bought goods for cash Rs.15,000.",
     "status": "VERIFIED", "type_key": "PURCHASE_GOODS_CASH", "journals": 1,
     "debit": [("Purchases", 15000)], "credit": [("Cash", 15000)]},
    {"question": "Goods purchased for Rs.15,000 in cash.",
     "status": "VERIFIED", "type_key": "PURCHASE_GOODS_CASH", "journals": 1,
     "debit": [("Purchases", 15000)], "credit": [("Cash", 15000)]},
    {"question": "Purchased goods costing Rs.15,000, payment made immediately.",
     "status": "VERIFIED", "type_key": "PURCHASE_GOODS_CASH", "journals": 1,
     "debit": [("Purchases", 15000)], "credit": [("Cash", 15000)]},
    {"question": "Purchased goods by cheque Rs.9,000.",
     "status": "VERIFIED", "type_key": "PURCHASE_GOODS_CASH", "journals": 1,
     "debit": [("Purchases", 9000)], "credit": [("Bank", 9000)]},
    {"question": "Purchased goods from Rahul on credit Rs.20,000.",
     "status": "VERIFIED", "type_key": "PURCHASE_GOODS_CREDIT", "journals": 1,
     "debit": [("Purchases", 20000)], "credit": [("Rahul", 20000)]},
    {"question": "Purchased goods from Rahul for Rs.20,000 on credit.",
     "status": "VERIFIED", "type_key": "PURCHASE_GOODS_CREDIT", "journals": 1,
     "debit": [("Purchases", 20000)], "credit": [("Rahul", 20000)]},
    {"question": "Bought goods from Amit on credit Rs.12,000.",
     "status": "VERIFIED", "type_key": "PURCHASE_GOODS_CREDIT", "journals": 1,
     "debit": [("Purchases", 12000)], "credit": [("Amit", 12000)]},
    {"question": "Bought goods on credit from Rahul Rs.20,000.",
     "status": "VERIFIED", "type_key": "PURCHASE_GOODS_CREDIT", "journals": 1,
     "debit": [("Purchases", 20000)], "credit": [("Rahul", 20000)]},
    {"question": "Purchased goods worth Rs.10,000 from Rahul at 10% trade discount.",
     "status": "VERIFIED", "type_key": "PURCHASE_GOODS_CREDIT", "journals": 1,
     "debit": [("Purchases", 9000)], "credit": [("Rahul", 9000)]},
    {"question": "Purchased goods from Rahul for Rs.10,000 at 10% trade discount.",
     "status": "VERIFIED", "type_key": "PURCHASE_GOODS_CREDIT", "journals": 1,
     "debit": [("Purchases", 9000)], "credit": [("Rahul", 9000)]},
    {"question": "Purchased goods from Rahul for Rs.10,000 at 10% trade discount. "
     "Half the amount was paid immediately and a cash discount of 2% was "
     "allowed on the amount paid.",
     "status": "VERIFIED", "type_key": "PURCHASE_GOODS_CREDIT", "journals": 1,
     "debit": [("Purchases", 9000)],
     "credit": [("Cash", 4410), ("Discount Received", 90), ("Rahul", 4500)]},
    {"question": "Purchased goods from Rahul for Rs.10,000, paid him Rs.4,000 immediately.",
     "status": "VERIFIED", "type_key": "PURCHASE_GOODS_CREDIT", "journals": 1,
     "debit": [("Purchases", 10000)],
     "credit": [("Cash", 4000), ("Rahul", 6000)]},
]

# ---------------------------------------------------------------------------
# C. Sales (Ch 3 - cash/credit/named customer/discount/costing wording)
# ---------------------------------------------------------------------------
SALES = [
    {"question": "Sold goods for cash Rs.20,000.",
     "status": "VERIFIED", "type_key": "SALE_GOODS_CASH", "journals": 1,
     "debit": [("Cash", 20000)], "credit": [("Sales", 20000)]},
    {"question": "Sold goods to Mohan for cash Rs.20,000.",
     "status": "VERIFIED", "type_key": "SALE_GOODS_CASH", "journals": 1,
     "debit": [("Cash", 20000)], "credit": [("Sales", 20000)]},
    {"question": "Cash sale of goods Rs.20,000.",
     "status": "VERIFIED", "type_key": "SALE_GOODS_CASH", "journals": 1,
     "debit": [("Cash", 20000)], "credit": [("Sales", 20000)]},
    {"question": "Goods sold and cash received immediately Rs.20,000.",
     "status": "VERIFIED", "type_key": "SALE_GOODS_CASH", "journals": 1,
     "debit": [("Cash", 20000)], "credit": [("Sales", 20000)]},
    {"question": "Sold goods to Mohan on credit Rs.20,000.",
     "status": "VERIFIED", "type_key": "SALE_GOODS_CREDIT", "journals": 1,
     "debit": [("Mohan", 20000)], "credit": [("Sales", 20000)]},
    {"question": "Sold goods to Mohan for Rs.20,000 on credit.",
     "status": "VERIFIED", "type_key": "SALE_GOODS_CREDIT", "journals": 1,
     "debit": [("Mohan", 20000)], "credit": [("Sales", 20000)]},
    {"question": "Goods costing Rs.10,000 sold to Mohan for cash Rs.12,000.",
     "status": "VERIFIED", "type_key": "SALE_GOODS_CASH", "journals": 1,
     "debit": [("Cash", 12000)], "credit": [("Sales", 12000)]},
    {"question": "Goods costing Rs.10,000 sold to Mohan on credit Rs.12,000.",
     "status": "VERIFIED", "type_key": "SALE_GOODS_CREDIT", "journals": 1,
     "debit": [("Mohan", 12000)], "credit": [("Sales", 12000)]},
    {"question": "Sold goods for cash Rs.10,000 at 10% trade discount.",
     "status": "VERIFIED", "type_key": "SALE_GOODS_CASH", "journals": 1,
     "debit": [("Cash", 9000)], "credit": [("Sales", 9000)]},
    {"question": "Sold goods to Mohan for Rs.10,000 at 10% trade discount. "
     "Received half the amount immediately.",
     "status": "VERIFIED", "type_key": "SALE_GOODS_CREDIT", "journals": 1,
     "debit": [("Mohan", 4500), ("Cash", 4500)], "credit": [("Sales", 9000)]},
]

# ---------------------------------------------------------------------------
# D. Expenses (Ch 3 - the Unit-Test-1 expense family, cash/cheque wording)
# ---------------------------------------------------------------------------
EXPENSES = [
    {"question": "Paid rent Rs.5,000.",
     "status": "VERIFIED", "type_key": "EXPENSE_PAID", "journals": 1,
     "debit": [("Rent", 5000)], "credit": [("Cash", 5000)]},
    {"question": "Paid rent Rs.5,000 in cash.",
     "status": "VERIFIED", "type_key": "EXPENSE_PAID", "journals": 1,
     "debit": [("Rent", 5000)], "credit": [("Cash", 5000)]},
    {"question": "Paid salary Rs.8,000.",
     "status": "VERIFIED", "type_key": "EXPENSE_PAID", "journals": 1,
     "debit": [("Salaries", 8000)], "credit": [("Cash", 8000)]},
    {"question": "Paid wages Rs.3,000.",
     "status": "VERIFIED", "type_key": "EXPENSE_PAID", "journals": 1,
     "debit": [("Wages", 3000)], "credit": [("Cash", 3000)]},
    {"question": "Paid electricity bill Rs.2,000.",
     "status": "VERIFIED", "type_key": "EXPENSE_PAID", "journals": 1,
     "debit": [("Electricity", 2000)], "credit": [("Cash", 2000)]},
    {"question": "Paid insurance premium Rs.4,000.",
     "status": "VERIFIED", "type_key": "EXPENSE_PAID", "journals": 1,
     "debit": [("Insurance", 4000)], "credit": [("Cash", 4000)]},
    {"question": "Purchased stationery for cash Rs.500.",
     "status": "VERIFIED", "type_key": "EXPENSE_PAID", "journals": 1,
     "debit": [("Stationery", 500)], "credit": [("Cash", 500)]},
    {"question": "Paid for stationery in cash Rs.500.",
     "status": "VERIFIED", "type_key": "EXPENSE_PAID", "journals": 1,
     "debit": [("Stationery", 500)], "credit": [("Cash", 500)]},
    {"question": "Paid salaries Rs.8,000 by cheque.",
     "status": "VERIFIED", "type_key": "EXPENSE_PAID", "journals": 1,
     "debit": [("Salaries", 8000)], "credit": [("Bank", 8000)]},
    {"question": "Paid insurance premium by cheque Rs.4,000.",
     "status": "VERIFIED", "type_key": "EXPENSE_PAID", "journals": 1,
     "debit": [("Insurance", 4000)], "credit": [("Bank", 4000)]},
    {"question": "Paid carriage on purchases Rs.800.",
     "status": "VERIFIED", "type_key": "EXPENSE_PAID", "journals": 1,
     "debit": [("Carriage Inward", 800)], "credit": [("Cash", 800)]},
    {"question": "Paid carriage outward Rs.600.",
     "status": "VERIFIED", "type_key": "EXPENSE_PAID", "journals": 1,
     "debit": [("Carriage Outward", 600)], "credit": [("Cash", 600)]},
    {"question": "Paid for repairs Rs.1,200.",
     "status": "VERIFIED", "type_key": "EXPENSE_PAID", "journals": 1,
     "debit": [("Repairs", 1200)], "credit": [("Cash", 1200)]},
    {"question": "Paid postage and telegram Rs.400.",
     "status": "VERIFIED", "type_key": "EXPENSE_PAID", "journals": 1,
     "debit": [("Postage", 400)], "credit": [("Cash", 400)]},
    {"question": "Paid legal fees Rs.5,000.",
     "status": "VERIFIED", "type_key": "EXPENSE_PAID", "journals": 1,
     "debit": [("Legal Fees", 5000)], "credit": [("Cash", 5000)]},
    {"question": "Paid audit fees Rs.3,000.",
     "status": "VERIFIED", "type_key": "EXPENSE_PAID", "journals": 1,
     "debit": [("Audit Fees", 3000)], "credit": [("Cash", 3000)]},
    {"question": "Paid income tax Rs.10,000.",
     "status": "VERIFIED", "type_key": "EXPENSE_PAID", "journals": 1,
     "debit": [("Income Tax", 10000)], "credit": [("Cash", 10000)]},
    {"question": "Paid rent to Mr. Sharma Rs.5,000.",
     "status": "VERIFIED", "type_key": "EXPENSE_PAID", "journals": 1,
     "debit": [("Rent", 5000)], "credit": [("Cash", 5000)]},
]

# ---------------------------------------------------------------------------
# E. Incomes (Ch 3 - commission/interest/rent/dividend, cash/cheque)
# ---------------------------------------------------------------------------
INCOMES = [
    {"question": "Received commission Rs.3,000.",
     "status": "VERIFIED", "type_key": "INCOME_RECEIVED", "journals": 1,
     "debit": [("Cash", 3000)], "credit": [("Commission Received", 3000)]},
    {"question": "Commission received in cash Rs.3,000.",
     "status": "VERIFIED", "type_key": "INCOME_RECEIVED", "journals": 1,
     "debit": [("Cash", 3000)], "credit": [("Commission Received", 3000)]},
    {"question": "Received commission from Rahul Rs.3,000.",
     "status": "VERIFIED", "type_key": "INCOME_RECEIVED", "journals": 1,
     "debit": [("Cash", 3000)], "credit": [("Commission Received", 3000)]},
    {"question": "Received interest Rs.2,500.",
     "status": "VERIFIED", "type_key": "INCOME_RECEIVED", "journals": 1,
     "debit": [("Cash", 2500)], "credit": [("Interest Received", 2500)]},
    {"question": "Interest received by cheque Rs.2,500.",
     "status": "VERIFIED", "type_key": "INCOME_RECEIVED", "journals": 1,
     "debit": [("Bank", 2500)], "credit": [("Interest Received", 2500)]},
    {"question": "Received rent Rs.6,000.",
     "status": "VERIFIED", "type_key": "INCOME_RECEIVED", "journals": 1,
     "debit": [("Cash", 6000)], "credit": [("Rent Received", 6000)]},
    {"question": "Rent received Rs.6,000.",
     "status": "VERIFIED", "type_key": "INCOME_RECEIVED", "journals": 1,
     "debit": [("Cash", 6000)], "credit": [("Rent Received", 6000)]},
    {"question": "Received dividend Rs.1,000.",
     "status": "VERIFIED", "type_key": "INCOME_RECEIVED", "journals": 1,
     "debit": [("Cash", 1000)], "credit": [("Dividend Received", 1000)]},
]

# ---------------------------------------------------------------------------
# F. Bank / cash / parties (Ch 3 - contra entries, cheque transactions)
# ---------------------------------------------------------------------------
BANK_CASH = [
    {"question": "Deposited cash into bank Rs.10,000.",
     "status": "VERIFIED", "type_key": "CASH_INTO_BANK", "journals": 1,
     "debit": [("Bank", 10000)], "credit": [("Cash", 10000)]},
    {"question": "Cash deposited into bank Rs.10,000.",
     "status": "VERIFIED", "type_key": "CASH_INTO_BANK", "journals": 1,
     "debit": [("Bank", 10000)], "credit": [("Cash", 10000)]},
    {"question": "Withdrew cash from bank Rs.4,000.",
     "status": "VERIFIED", "type_key": "CASH_FROM_BANK", "journals": 1,
     "debit": [("Cash", 4000)], "credit": [("Bank", 4000)]},
    {"question": "Cash withdrawn from bank for office use Rs.4,000.",
     "status": "VERIFIED", "type_key": "CASH_FROM_BANK", "journals": 1,
     "debit": [("Cash", 4000)], "credit": [("Bank", 4000)]},
    {"question": "Paid to Rahul Rs.8,000 in cash.",
     "status": "VERIFIED", "type_key": "PAID_TO", "journals": 1,
     "debit": [("Rahul", 8000)], "credit": [("Cash", 8000)]},
    {"question": "Paid Rahul Rs.8,000 in cash.",
     "status": "VERIFIED", "type_key": "PAID_TO", "journals": 1,
     "debit": [("Rahul", 8000)], "credit": [("Cash", 8000)]},
    {"question": "Received Rs.6,000 from Amit in cash.",
     "status": "VERIFIED", "type_key": "RECEIVED_FROM", "journals": 1,
     "debit": [("Cash", 6000)], "credit": [("Amit", 6000)]},
    {"question": "Received cash from Mohan Rs.6,000.",
     "status": "VERIFIED", "type_key": "RECEIVED_FROM", "journals": 1,
     "debit": [("Cash", 6000)], "credit": [("Mohan", 6000)]},
    {"question": "Paid Amit by cheque Rs.7,500.",
     "status": "VERIFIED", "type_key": "CHEQUE_PAID", "journals": 1,
     "debit": [("Amit", 7500)], "credit": [("Bank", 7500)]},
    {"question": "Received a cheque from Mohan Rs.9,000.",
     "status": "VERIFIED", "type_key": "CHEQUE_RECEIVED", "journals": 1,
     "debit": [("Bank", 9000)], "credit": [("Mohan", 9000)]},
    {"question": "Issued a cheque to Rahul for Rs.5,000.",
     "status": "VERIFIED", "type_key": "CHEQUE_PAID", "journals": 1,
     "debit": [("Rahul", 5000)], "credit": [("Bank", 5000)]},
    {"question": "Received cash from Mohan against his account Rs.6,000.",
     "status": "VERIFIED", "type_key": "RECEIVED_FROM", "journals": 1,
     "debit": [("Cash", 6000)], "credit": [("Mohan", 6000)]},
    {"question": "Paid cash to Rahul against his account Rs.4,000.",
     "status": "VERIFIED", "type_key": "PAID_TO", "journals": 1,
     "debit": [("Rahul", 4000)], "credit": [("Cash", 4000)]},
]

# ---------------------------------------------------------------------------
# G. Discounts & settlements (Ch 3 - trade vs cash discount, settlements)
# ---------------------------------------------------------------------------
DISCOUNTS = [
    {"question": "Received from Mohan Rs.9,800, discount allowed Rs.200.",
     "status": "VERIFIED", "type_key": "RECEIVED_FROM", "journals": 1,
     "debit": [("Cash", 9800), ("Discount Allowed", 200)],
     "credit": [("Mohan", 10000)]},
    {"question": "Received cash from Mohan Rs.9,800 and discount allowed Rs.200.",
     "status": "VERIFIED", "type_key": "RECEIVED_FROM", "journals": 1,
     "debit": [("Cash", 9800), ("Discount Allowed", 200)],
     "credit": [("Mohan", 10000)]},
    {"question": "Paid to Amit Rs.9,800, discount received Rs.200.",
     "status": "VERIFIED", "type_key": "PAID_TO", "journals": 1,
     "debit": [("Amit", 10000)],
     "credit": [("Cash", 9800), ("Discount Received", 200)]},
    {"question": "Paid Amit Rs.9,800 in full settlement and discount received Rs.200.",
     "status": "VERIFIED", "type_key": "PAID_TO", "journals": 1,
     "debit": [("Amit", 10000)],
     "credit": [("Cash", 9800), ("Discount Received", 200)]},
    {"question": "Received from Mohan Rs.5,000 in full settlement of his account of Rs.5,200.",
     "status": "VERIFIED", "type_key": "RECEIVED_FROM", "journals": 1,
     "debit": [("Cash", 5000), ("Discount Allowed", 200)],
     "credit": [("Mohan", 5200)]},
    {"question": "Paid Rahul Rs.9,800, discount received Rs.200, in full settlement of Rs.10,000.",
     "status": "VERIFIED", "type_key": "PAID_TO", "journals": 1,
     "debit": [("Rahul", 10000)],
     "credit": [("Cash", 9800), ("Discount Received", 200)]},
    {"question": "Received from Mohan Rs.9,800 in full settlement of Rs.10,000, discount allowed Rs.200.",
     "status": "VERIFIED", "type_key": "RECEIVED_FROM", "journals": 1,
     "debit": [("Cash", 9800), ("Discount Allowed", 200)],
     "credit": [("Mohan", 10000)]},
    {"question": "Received Rs.5,000 from Mohan, discount allowed Rs.200, the balance due to him being Rs.5,200.",
     "status": "VERIFIED", "type_key": "RECEIVED_FROM", "journals": 1,
     "debit": [("Cash", 5000), ("Discount Allowed", 200)],
     "credit": [("Mohan", 5200)]},
    {"question": "Received from Mohan Rs.10,000; allowed him discount Rs.200.",
     "status": "VERIFIED", "type_key": "RECEIVED_FROM", "journals": 1,
     "debit": [("Cash", 10000), ("Discount Allowed", 200)],
     "credit": [("Mohan", 10200)]},
    {"question": "Discount allowed to Mohan Rs.200.",
     "status": "VERIFIED", "type_key": "DISCOUNT_ALLOWED", "journals": 1,
     "debit": [("Discount Allowed", 200)], "credit": [("Mohan", 200)]},
    {"question": "Discount received from Rahul Rs.150.",
     "status": "VERIFIED", "type_key": "DISCOUNT_RECEIVED", "journals": 1,
     "debit": [("Rahul", 150)], "credit": [("Discount Received", 150)]},
]

# ---------------------------------------------------------------------------
# H. Returns (Ch 3 - purchase returns / sales returns, worth & pronoun wording)
# ---------------------------------------------------------------------------
RETURNS = [
    {"question": "Returned goods to Rahul Rs.1,000.",
     "status": "VERIFIED", "type_key": "PURCHASE_RETURN", "journals": 1,
     "debit": [("Rahul", 1000)], "credit": [("Purchase Returns", 1000)]},
    {"question": "Goods returned by Mohan Rs.800.",
     "status": "VERIFIED", "type_key": "SALES_RETURN", "journals": 1,
     "debit": [("Sales Returns", 800)], "credit": [("Mohan", 800)]},
    {"question": "Purchases returns to Rahul Rs.1,000.",
     "status": "VERIFIED", "type_key": "PURCHASE_RETURN", "journals": 1,
     "debit": [("Rahul", 1000)], "credit": [("Purchase Returns", 1000)]},
    {"question": "Sales returns from Mohan Rs.800.",
     "status": "VERIFIED", "type_key": "SALES_RETURN", "journals": 1,
     "debit": [("Sales Returns", 800)], "credit": [("Mohan", 800)]},
    {"question": "Purchased goods from Rahul on credit Rs.20,000. Returned goods worth Rs.1,000 to him.",
     "status": "VERIFIED", "type_key": "PURCHASE_GOODS_CREDIT", "journals": 2,
     "debit": [("Purchases", 20000), ("Rahul", 1000)],
     "credit": [("Rahul", 20000), ("Purchase Returns", 1000)]},
    {"question": "Sold goods to Mohan on credit Rs.15,000. Mohan returned goods worth Rs.800.",
     "status": "VERIFIED", "type_key": "SALE_GOODS_CREDIT", "journals": 2,
     "debit": [("Mohan", 15000), ("Sales Returns", 800)],
     "credit": [("Sales", 15000), ("Mohan", 800)]},
    {"question": "Sold goods to Mohan on credit Rs.15,000. He returned goods worth Rs.800.",
     "status": "VERIFIED", "type_key": "SALE_GOODS_CREDIT", "journals": 2,
     "debit": [("Mohan", 15000), ("Sales Returns", 800)],
     "credit": [("Sales", 15000), ("Mohan", 800)]},
    {"question": "Purchased goods from Rahul on credit Rs.20,000, returned goods worth Rs.1,000.",
     "status": "VERIFIED", "type_key": "PURCHASE_GOODS_CREDIT", "journals": 2,
     "debit": [("Purchases", 20000), ("Rahul", 1000)],
     "credit": [("Rahul", 20000), ("Purchase Returns", 1000)]},
]

# ---------------------------------------------------------------------------
# I. Multi-transaction questions (Ch 3 - chronological, independent entries)
# ---------------------------------------------------------------------------
MULTI = [
    {"question": "Started business with cash Rs.1,00,000. Purchased goods for cash "
     "Rs.20,000. Paid rent Rs.5,000.",
     "status": "VERIFIED", "type_key": "START_BUSINESS", "journals": 3,
     "debit": [("Cash", 100000), ("Purchases", 20000), ("Rent", 5000)],
     "credit": [("Capital", 100000), ("Cash", 20000), ("Cash", 5000)]},
    {"question": "Sold goods to Mohan for Rs.15,000. Received Rs.9,800 from him, "
     "discount allowed Rs.200.",
     "status": "VERIFIED", "type_key": "SALE_GOODS_CREDIT", "journals": 2,
     "debit": [("Mohan", 15000), ("Cash", 9800), ("Discount Allowed", 200)],
     "credit": [("Sales", 15000), ("Mohan", 10000)]},
    {"question": "Purchased goods from Rahul for Rs.10,000. Paid him Rs.4,000 immediately.",
     "status": "VERIFIED", "type_key": "PURCHASE_GOODS_CREDIT", "journals": 1,
     "debit": [("Purchases", 10000)],
     "credit": [("Cash", 4000), ("Rahul", 6000)]},
    {"question": "Journalise the following transactions and prepare the trial balance: "
     "Started business with cash Rs.50,000. Purchased goods for cash Rs.10,000.",
     "status": "VERIFIED", "type_key": "START_BUSINESS", "journals": 2,
     "debit": [("Cash", 50000), ("Purchases", 10000)],
     "credit": [("Capital", 50000), ("Cash", 10000)]},
    {"question": "Received commission Rs.3,000. Paid rent Rs.2,000.",
     "status": "VERIFIED", "type_key": "INCOME_RECEIVED", "journals": 2,
     "debit": [("Cash", 3000), ("Rent", 2000)],
     "credit": [("Commission Received", 3000), ("Cash", 2000)]},
    {"question": "Started business with cash Rs.1,00,000 and furniture Rs.20,000. "
     "Purchased goods for cash Rs.15,000.",
     "status": "VERIFIED", "type_key": "START_BUSINESS", "journals": 2,
     "debit": [("Cash", 100000), ("Furniture", 20000), ("Purchases", 15000)],
     "credit": [("Capital", 120000), ("Cash", 15000)]},
    {"question": "Deposited cash into bank Rs.10,000. Paid salary Rs.3,000 by cheque.",
     "status": "VERIFIED", "type_key": "CASH_INTO_BANK", "journals": 2,
     "debit": [("Bank", 10000), ("Salaries", 3000)],
     "credit": [("Cash", 10000), ("Bank", 3000)]},
]

# ---------------------------------------------------------------------------
# J. Refusals (missing / ambiguous / outside scope - never fabricated)
# ---------------------------------------------------------------------------
REFUSALS = [
    {"question": "Paid rent.", "status": "BLOCKED"},
    {"question": "Received commission.", "status": "BLOCKED"},
    {"question": "Purchased goods from Rahul.", "status": "BLOCKED"},
    {"question": "Sold goods to Mohan.", "status": "BLOCKED"},
    {"question": "Started business with cash.", "status": "BLOCKED"},
    {"question": "Purchased goods for cash from Amit.", "status": "BLOCKED"},
    {"question": "Purchased goods from Rahul, paid half the amount in cash.",
     "status": "BLOCKED"},
    {"question": "Paid half the amount to Rahul in cash and the balance on credit.",
     "status": "BLOCKED"},
    {"question": "Purchased goods.", "status": "REVIEW_REQUIRED"},
    {"question": "Sold goods.", "status": "REVIEW_REQUIRED"},
    {"question": "Purchased goods for Rs.10,000.", "status": "REVIEW_REQUIRED"},
    {"question": "Purchased furniture.", "status": "REVIEW_REQUIRED"},
    {"question": "Discount allowed Rs.200.", "status": "REVIEW_REQUIRED"},
    {"question": "Received Rs.5,000 in full settlement of Rs.5,200.",
     "status": "REVIEW_REQUIRED"},
    {"question": "Sold goods to Mohan for Rs.15,000, discount allowed Rs.200.",
     "status": "REVIEW_REQUIRED"},
    {"question": "Depreciate machinery by 10% per annum.", "status": "NOT_SUPPORTED"},
    {"question": "Pass opening entry for the new year.", "status": "NOT_SUPPORTED"},
    {"question": "Prepare final accounts from the trial balance.",
     "status": "NOT_SUPPORTED"},
    {"question": "Ravi and Suresh started a partnership firm.", "status": "NOT_SUPPORTED"},
    {"question": "Charge depreciation on furniture Rs.1,000.", "status": "NOT_SUPPORTED"},
    {"question": "Provide for doubtful debts at 5% on debtors.", "status": "NOT_SUPPORTED"},
]

# ---------------------------------------------------------------------------
# The complete golden benchmark (never regenerated from the solver)
# ---------------------------------------------------------------------------
BK15E_BENCHMARK = (CAPITAL_DRAWINGS + PURCHASES + SALES + EXPENSES
                   + INCOMES + BANK_CASH + DISCOUNTS + RETURNS + MULTI
                   + REFUSALS)

VERIFIED_CASES = [c for c in BK15E_BENCHMARK if c["status"] == "VERIFIED"]
REFUSAL_CASES = [c for c in BK15E_BENCHMARK if c["status"] != "VERIFIED"]

# Adversarial exact-account cases: the ONLY accounts allowed are the ones
# the question names (never Machinery/Building/Debtors/Creditors).
EXACT_ACCOUNT_CASES = [
    {"question": "Purchased Furniture for Cash Rs.15,000.",
     "allowed": {"Furniture", "Cash"}},
    {"question": "Purchased machinery for cash Rs.60,000.",
     "allowed": {"Machinery", "Cash"}},
    {"question": "Purchased a building for cash Rs.50,00,000.",
     "allowed": {"Building", "Cash"}},
    {"question": "Sold goods to Mohan for cash Rs.20,000.",
     "allowed": {"Cash", "Sales"}},
    {"question": "Purchased furniture from Rahul on credit Rs.25,000.",
     "allowed": {"Furniture", "Rahul"}},
]
