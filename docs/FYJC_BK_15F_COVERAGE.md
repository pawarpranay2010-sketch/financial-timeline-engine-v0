# FYJC BOOK-KEEPING CH.1-3 PATTERN COVERAGE (SPRINT 15F)

**Total test cases:** 150  **Passed:** 150  **Pass rate:** 100.0%

| Pattern ID | Description | Category | Wording variants | Refusal conditions | Tests | Pass |
|---|------------|----------|------------------|-------------------|-----:|----:|
| `CAPITAL_ASSET_INTRODUCED` | An asset brought into the business as capital | Capital & Drawings | brought machinery worth Rs.X into the business; introduced furniture worth Rs.X  | amount missing -> BLOCKED; >1 named asset -> refused (never split) | 1 | 1 |
| `CAPITAL_INTRODUCED` | Additional capital brought in during the year | Capital & Drawings | brought in additional capital; introduced capital; brought into the business as  | amount missing -> BLOCKED | 1 | 1 |
| `CASH_FROM_BANK` | Cash withdrawn from the bank for office use (contra) | Bank / Cash / Parties | withdrew cash from bank; cash withdrawn from the bank; drew cash from the bank | amount missing -> BLOCKED | 2 | 2 |
| `CASH_INTO_BANK` | Cash deposited into the bank (contra entry) | Bank / Cash / Parties | deposited cash into bank; cash deposited into bank; deposited cash into the bank | amount missing -> BLOCKED | 3 | 3 |
| `CHEQUE_PAID` | A cheque issued/paid to a party | Bank / Cash / Parties | paid Amit by cheque; issued a cheque to Rahul; gave a cheque to Amit; issued a c | amount missing -> BLOCKED; party not named -> REVIEW_REQUIRED | 3 | 3 |
| `CHEQUE_RECEIVED` | A cheque received from a party | Bank / Cash / Parties | received a cheque from Mohan; cheque received from Mohan; got a cheque from Moha | amount missing -> BLOCKED; party not named -> REVIEW_REQUIRED | 2 | 2 |
| `DISCOUNT_ALLOWED` | A cash discount allowed to a customer (alone or as part of a settlemen | Discounts | discount allowed to Mohan; allowed him discount Rs.X; received from Mohan Rs.A,  | no settlement context -> REVIEW_REQUIRED | 1 | 1 |
| `DISCOUNT_RECEIVED` | A cash discount received from a supplier | Discounts | discount received from Rahul; paid to Amit Rs.A, discount received Rs.B | no settlement context -> REVIEW_REQUIRED | 1 | 1 |
| `DRAWINGS_CASH` | Cash (or bank) withdrawn for personal/private use | Capital & Drawings | withdrew cash for personal use; withdrawn for private use; cash withdrawn from b | amount missing -> BLOCKED | 1 | 1 |
| `EXPENSE_PAID` | An expense paid in cash, by cheque or at once | Expenses | paid rent; paid salaries in cash; rent paid; paid for stationery in cash (+4 mor | amount missing -> BLOCKED; expense word not recognised -> REVIEW_REQUIRED | 15 | 15 |
| `GOODS_PERSONAL_USE` | Goods taken by the proprietor for personal use | Capital & Drawings | withdrew goods worth Rs.X for personal use; goods taken by the proprietor for pr | amount missing -> BLOCKED | 2 | 2 |
| `INCOME_RECEIVED` | Income received in cash or by cheque | Incomes | received commission; commission received in cash; interest received by cheque; r | amount missing -> BLOCKED | 7 | 7 |
| `MULTI_TRANSACTION` | A question with several independent transactions - split chronological | Multi-transaction questions | Started business ... . Purchased goods ... . Paid rent ... .; Sold goods to Moha | any segment missing an amount -> BLOCKED (no partial fabrication); continuation  | 17 | 17 |
| `PAID_TO` | Cash/cheque paid TO a party (settling a creditor or any payment to a p | Bank / Cash / Parties | paid to Rahul Rs.X in cash; paid Rahul Rs.X in cash; paid cash to Rahul; paid Am | amount missing -> BLOCKED; party not named -> REVIEW_REQUIRED | 5 | 5 |
| `PURCHASE_ASSET_CASH` | A fixed asset purchased for cash/cheque - EXACT named asset only | Purchases (assets) | purchased furniture for cash; bought machinery for cash; building purchased for  | amount missing -> BLOCKED; >1 asset named -> refused (+1 more) | 5 | 5 |
| `PURCHASE_ASSET_CREDIT` | A fixed asset purchased on credit from a supplier | Purchases (assets) | bought furniture from Vijay on credit; purchased machinery from Suresh on credit | amount missing -> BLOCKED; >1 asset named -> refused (+1 more) | 3 | 3 |
| `PURCHASE_GOODS_CASH` | Goods purchased for cash or by cheque | Purchases | purchased goods for cash; bought goods for cash; goods purchased for Rs.X in cas | amount missing -> BLOCKED; cash vs credit not stated -> REVIEW_REQUIRED | 8 | 8 |
| `PURCHASE_GOODS_CREDIT` | Goods purchased on credit from a named supplier | Purchases | purchased goods from Rahul on credit; bought goods on credit from Rahul; goods p | amount missing -> BLOCKED; no supplier named -> REVIEW_REQUIRED | 22 | 22 |
| `PURCHASE_RETURN` | Goods returned to the supplier (returns outward) | Returns | returned goods to Rahul; purchases returns to Rahul; returned goods worth Rs.X t | amount missing -> BLOCKED; supplier not named -> REVIEW_REQUIRED (party inherite | 2 | 2 |
| `RECEIVED_FROM` | Cash/cheque received FROM a party (settling a debtor); also '<Party> p | Bank / Cash / Parties | received from Mohan Rs.X in cash; received cash from Mohan; received Rs.X from A | amount missing -> BLOCKED; party not named -> REVIEW_REQUIRED | 5 | 5 |
| `REFUSAL::BLOCKED` | Essential information (usually the amount) is missing - the transactio | Refusals | Purchased goods from Rahul.; Paid rent. | amount missing -> BLOCKED | 4 | 4 |
| `REFUSAL::NOT_SUPPORTED` | The topic is outside the approved Ch.1-3 Unit-Test-1 boundary | Refusals | depreciation; final accounts; balance sheet; partnership (+2 more) | any later-year topic -> NOT_SUPPORTED | 12 | 12 |
| `REFUSAL::REVIEW_REQUIRED` | The wording is ambiguous (cash vs credit, mode, discount context) - FT | Refusals | Purchased goods for Rs.10,000.; Paid Rs.5,000.; Received Rs.5,000.; Purchased go | cash/credit unstated -> REVIEW_REQUIRED; purpose/context missing -> REVIEW_REQUI | 8 | 8 |
| `SALES_RETURN` | Goods returned by the customer (returns inward) | Returns | goods returned by Mohan; sales returns from Mohan; Mohan returned goods worth Rs | amount missing -> BLOCKED; customer not named -> REVIEW_REQUIRED | 2 | 2 |
| `SALE_ASSET_CASH` | An old fixed asset sold for cash/cheque | Sales (assets) | sold old furniture for cash; sold machinery for Rs.X in cash | amount missing -> BLOCKED; mode not stated -> REVIEW_REQUIRED | 1 | 1 |
| `SALE_ASSET_CREDIT` | An old fixed asset sold on credit | Sales (assets) | sold old furniture to Ramesh on credit; sold machinery on credit to Ramesh | amount missing -> BLOCKED; no customer -> REVIEW_REQUIRED | 1 | 1 |
| `SALE_GOODS_CASH` | Goods sold for cash or by cheque (a named customer never becomes a deb | Sales | sold goods for cash; sold goods to Mohan for cash; cash sale of goods; goods sol | amount missing -> BLOCKED | 8 | 8 |
| `SALE_GOODS_CREDIT` | Goods sold on credit to a named customer | Sales | sold goods to Mohan on credit; sold to Mohan for Rs.X on credit; goods sold on c | amount missing -> BLOCKED; no customer named -> REVIEW_REQUIRED | 4 | 4 |
| `START_BUSINESS` | Starting the business with capital (cash, bank or named assets) | Capital & Drawings | started business with cash; commenced business with bank balance; began business | amount missing -> BLOCKED; more than one named asset -> refused (never guessed s | 4 | 4 |

## Per-pattern detail

### CAPITAL_ASSET_INTRODUCED

**Description:** An asset brought into the business as capital

**Category:** Capital & Drawings

**Required inputs:** amount, the exact asset word

**Account structure:** Debit ['exact named asset'] / Credit ['Capital']

**Golden rule:** ['Asset: Real - Debit what comes in', 'Capital: Personal - Credit the giver']

**Journal structure:** <Asset> A/c Dr ... / To Capital A/c ...

**Ledger effect:** Asset balance increases (Dr); Capital increases (Cr)

**Trial-balance effect:** Asset on the debit side; Capital on the credit side

**Supported wording variants:** brought machinery worth Rs.X into the business; introduced furniture worth Rs.X as additional capital; brought furniture into the business as capital Rs.X

**Refusal conditions:** amount missing -> BLOCKED; >1 named asset -> refused (never split)

**Coverage:** 1/1 tests pass

---

### CAPITAL_INTRODUCED

**Description:** Additional capital brought in during the year

**Category:** Capital & Drawings

**Required inputs:** amount, the capital side (cash | bank)

**Account structure:** Debit ['Cash | Bank'] / Credit ['Capital']

**Golden rule:** ['Cash/Bank: Real - Debit what comes in', 'Capital: Personal - Credit the giver']

**Journal structure:** Cash/Bank A/c Dr ... / To Capital A/c ...

**Ledger effect:** Cash/Bank balance increases (Dr); Capital balance increases (Cr)

**Trial-balance effect:** Cash/Bank on the debit side; Capital on the credit side

**Supported wording variants:** brought in additional capital; introduced capital; brought into the business as capital

**Refusal conditions:** amount missing -> BLOCKED

**Coverage:** 1/1 tests pass

---

### CASH_FROM_BANK

**Description:** Cash withdrawn from the bank for office use (contra)

**Category:** Bank / Cash / Parties

**Required inputs:** amount

**Account structure:** Debit ['Cash'] / Credit ['Bank']

**Golden rule:** ['Cash: Real - Debit what comes in', 'Bank: Personal - Credit the giver']

**Journal structure:** Cash A/c Dr ... / To Bank A/c ...

**Ledger effect:** Cash increases (Dr); Bank decreases (Cr)

**Trial-balance effect:** Cash on the debit side; Bank on the credit side

**Supported wording variants:** withdrew cash from bank; cash withdrawn from the bank; drew cash from the bank

**Refusal conditions:** amount missing -> BLOCKED

**Coverage:** 2/2 tests pass

---

### CASH_INTO_BANK

**Description:** Cash deposited into the bank (contra entry)

**Category:** Bank / Cash / Parties

**Required inputs:** amount

**Account structure:** Debit ['Bank'] / Credit ['Cash']

**Golden rule:** ['Bank: Personal - Debit the receiver', 'Cash: Real - Credit what goes out']

**Journal structure:** Bank A/c Dr ... / To Cash A/c ...

**Ledger effect:** Bank balance increases (Dr); Cash decreases (Cr)

**Trial-balance effect:** Bank on the debit side; Cash on the credit side

**Supported wording variants:** deposited cash into bank; cash deposited into bank; deposited cash into the bank; paid into the bank; cash deposited in bank

**Refusal conditions:** amount missing -> BLOCKED

**Coverage:** 3/3 tests pass

---

### CHEQUE_PAID

**Description:** A cheque issued/paid to a party

**Category:** Bank / Cash / Parties

**Required inputs:** amount, the party

**Account structure:** Debit ['<party> (Personal)'] / Credit ['Bank']

**Golden rule:** ['<party>: Personal - Debit the receiver', 'Bank: Personal - Credit the giver']

**Journal structure:** <party> A/c Dr ... / To Bank A/c ...

**Ledger effect:** Party balance decreases (Dr); Bank decreases (Cr)

**Trial-balance effect:** Party on the debit side; Bank on the credit side

**Supported wording variants:** paid Amit by cheque; issued a cheque to Rahul; gave a cheque to Amit; issued a cheque in favour of Amit

**Refusal conditions:** amount missing -> BLOCKED; party not named -> REVIEW_REQUIRED

**Coverage:** 3/3 tests pass

---

### CHEQUE_RECEIVED

**Description:** A cheque received from a party

**Category:** Bank / Cash / Parties

**Required inputs:** amount, the party

**Account structure:** Debit ['Bank'] / Credit ['<party> (Personal)']

**Golden rule:** ['Bank: Personal - Debit the receiver', '<party>: Personal - Credit the giver']

**Journal structure:** Bank A/c Dr ... / To <party> A/c ...

**Ledger effect:** Bank increases (Dr); party debtor decreases (Cr)

**Trial-balance effect:** Bank on the debit side; party on the credit side

**Supported wording variants:** received a cheque from Mohan; cheque received from Mohan; got a cheque from Mohan

**Refusal conditions:** amount missing -> BLOCKED; party not named -> REVIEW_REQUIRED

**Coverage:** 2/2 tests pass

---

### DISCOUNT_ALLOWED

**Description:** A cash discount allowed to a customer (alone or as part of a settlement)

**Category:** Discounts

**Required inputs:** amount, the customer

**Account structure:** Debit ['Discount Allowed'] / Credit ['<customer> (Personal)']

**Golden rule:** ['Discount Allowed: Nominal - Debit expenses/losses', '<customer>: Personal - Credit the giver']

**Journal structure:** Discount Allowed A/c Dr ... / To <customer> A/c ...

**Ledger effect:** Discount Allowed increases (Dr); customer balance decreases (Cr)

**Trial-balance effect:** Discount Allowed on the debit side; customer on the credit side

**Supported wording variants:** discount allowed to Mohan; allowed him discount Rs.X; received from Mohan Rs.A, discount allowed Rs.B

**Refusal conditions:** no settlement context -> REVIEW_REQUIRED

**Coverage:** 1/1 tests pass

---

### DISCOUNT_RECEIVED

**Description:** A cash discount received from a supplier

**Category:** Discounts

**Required inputs:** amount, the supplier

**Account structure:** Debit ['<supplier> (Personal)'] / Credit ['Discount Received']

**Golden rule:** ['<supplier>: Personal - Debit the receiver', 'Discount Received: Nominal - Credit incomes/gains']

**Journal structure:** <supplier> A/c Dr ... / To Discount Received A/c ...

**Ledger effect:** Supplier balance decreases (Dr); Discount Received increases (Cr)

**Trial-balance effect:** Supplier on the debit side; Discount Received on the credit side

**Supported wording variants:** discount received from Rahul; paid to Amit Rs.A, discount received Rs.B

**Refusal conditions:** no settlement context -> REVIEW_REQUIRED

**Coverage:** 1/1 tests pass

---

### DRAWINGS_CASH

**Description:** Cash (or bank) withdrawn for personal/private use

**Category:** Capital & Drawings

**Required inputs:** amount

**Account structure:** Debit ['Drawings'] / Credit ['Cash | Bank']

**Golden rule:** ['Drawings: Personal - Debit the receiver (the proprietor)', 'Cash/Bank: Real - Credit what goes out']

**Journal structure:** Drawings A/c Dr ... / To Cash/Bank A/c ...

**Ledger effect:** Drawings balance increases (Dr); Cash/Bank decreases (Cr)

**Trial-balance effect:** Drawings on the debit side; Cash/Bank on the credit side

**Supported wording variants:** withdrew cash for personal use; withdrawn for private use; cash withdrawn from bank for personal use

**Refusal conditions:** amount missing -> BLOCKED

**Coverage:** 1/1 tests pass

---

### EXPENSE_PAID

**Description:** An expense paid in cash, by cheque or at once

**Category:** Expenses

**Required inputs:** amount, the expense word

**Account structure:** Debit ['expense A/c (Nominal)'] / Credit ['Cash | Bank']

**Golden rule:** ['Expense: Nominal - Debit expenses and losses', 'Cash/Bank: Real - Credit what goes out']

**Journal structure:** <Expense> A/c Dr ... / To Cash/Bank A/c ...

**Ledger effect:** Expense balance increases (Dr); Cash/Bank decreases (Cr)

**Trial-balance effect:** Expense on the debit side; Cash/Bank on the credit side

**Supported wording variants:** paid rent; paid salaries in cash; rent paid; paid for stationery in cash; payment made for rent in cash; paid electricity bill; paid carriage inward/outward; paid rent by cheque

**Refusal conditions:** amount missing -> BLOCKED; expense word not recognised -> REVIEW_REQUIRED

**Coverage:** 15/15 tests pass

---

### GOODS_PERSONAL_USE

**Description:** Goods taken by the proprietor for personal use

**Category:** Capital & Drawings

**Required inputs:** amount

**Account structure:** Debit ['Drawings'] / Credit ['Purchases']

**Golden rule:** ['Drawings: Personal - Debit the receiver', 'Purchases: Nominal - Credit incomes/gains (goods returned to the business)']

**Journal structure:** Drawings A/c Dr ... / To Purchases A/c ...

**Ledger effect:** Drawings increases (Dr); Purchases decreases (Cr)

**Trial-balance effect:** Drawings on the debit side; Purchases on the credit side

**Supported wording variants:** withdrew goods worth Rs.X for personal use; goods taken by the proprietor for private use; goods for personal use

**Refusal conditions:** amount missing -> BLOCKED

**Coverage:** 2/2 tests pass

---

### INCOME_RECEIVED

**Description:** Income received in cash or by cheque

**Category:** Incomes

**Required inputs:** amount, the income word

**Account structure:** Debit ['Cash | Bank'] / Credit ['income A/c (Nominal)']

**Golden rule:** ['Cash/Bank: Real - Debit what comes in', 'Income: Nominal - Credit incomes and gains']

**Journal structure:** Cash/Bank A/c Dr ... / To <Income> A/c ...

**Ledger effect:** Cash/Bank increases (Dr); income balance increases (Cr)

**Trial-balance effect:** Cash/Bank on the debit side; income on the credit side

**Supported wording variants:** received commission; commission received in cash; interest received by cheque; received rent; received dividend

**Refusal conditions:** amount missing -> BLOCKED

**Coverage:** 7/7 tests pass

---

### MULTI_TRANSACTION

**Description:** A question with several independent transactions - split chronologically, journaled independently, aggregated into ONE ledger and ONE trial balance

**Category:** Multi-transaction questions

**Required inputs:** each transaction with its amount

**Account structure:** Debit ['per-entry'] / Credit ['per-entry']

**Golden rule:** Each entry follows its own pattern's golden rule

**Journal structure:** N independent journal entries in order

**Ledger effect:** Aggregate of every entry's postings

**Trial-balance effect:** Aggregate ledger balances; Dr == Cr

**Supported wording variants:** Started business ... . Purchased goods ... . Paid rent ... .; Sold goods to Mohan ... . Received from him ... discount allowed ...; Purchased goods ... . Paid him Rs.X immediately.

**Refusal conditions:** any segment missing an amount -> BLOCKED (no partial fabrication); continuation pronouns resolve only to a previously named party

**Coverage:** 17/17 tests pass

---

### PAID_TO

**Description:** Cash/cheque paid TO a party (settling a creditor or any payment to a person)

**Category:** Bank / Cash / Parties

**Required inputs:** amount, the party

**Account structure:** Debit ['<party> (Personal)'] / Credit ['Cash | Bank']

**Golden rule:** ['<party>: Personal - Debit the receiver', 'Cash/Bank: Real - Credit what goes out']

**Journal structure:** <party> A/c Dr ... / To Cash/Bank A/c ...

**Ledger effect:** Party balance decreases (Dr); Cash/Bank decreases (Cr)

**Trial-balance effect:** Party on the debit side; Cash/Bank on the credit side

**Supported wording variants:** paid to Rahul Rs.X in cash; paid Rahul Rs.X in cash; paid cash to Rahul; paid Amit by cheque; issued a cheque in favour of Amit

**Refusal conditions:** amount missing -> BLOCKED; party not named -> REVIEW_REQUIRED

**Coverage:** 5/5 tests pass

---

### PURCHASE_ASSET_CASH

**Description:** A fixed asset purchased for cash/cheque - EXACT named asset only

**Category:** Purchases (assets)

**Required inputs:** amount, the exact asset word

**Account structure:** Debit ['exact asset'] / Credit ['Cash | Bank']

**Golden rule:** ['Asset: Real - Debit what comes in', 'Cash/Bank: Real - Credit what goes out']

**Journal structure:** <Asset> A/c Dr ... / To Cash/Bank A/c ...

**Ledger effect:** Asset balance increases (Dr); Cash/Bank decreases (Cr)

**Trial-balance effect:** Asset on the debit side; Cash/Bank on the credit side

**Supported wording variants:** purchased furniture for cash; bought machinery for cash; building purchased for Rs.X in cash; purchased furniture costing Rs.X, payment made immediately

**Refusal conditions:** amount missing -> BLOCKED; >1 asset named -> refused; mode not stated -> REVIEW_REQUIRED

**Coverage:** 5/5 tests pass

---

### PURCHASE_ASSET_CREDIT

**Description:** A fixed asset purchased on credit from a supplier

**Category:** Purchases (assets)

**Required inputs:** amount, the exact asset word, supplier

**Account structure:** Debit ['exact asset'] / Credit ['<supplier> (Personal)']

**Golden rule:** ['Asset: Real - Debit what comes in', '<supplier>: Personal - Credit the giver']

**Journal structure:** <Asset> A/c Dr ... / To <supplier> A/c ...

**Ledger effect:** Asset increases (Dr); supplier creditor increases (Cr)

**Trial-balance effect:** Asset on the debit side; supplier on the credit side

**Supported wording variants:** bought furniture from Vijay on credit; purchased machinery from Suresh on credit; furniture purchased from Rahul for Rs.X on credit

**Refusal conditions:** amount missing -> BLOCKED; >1 asset named -> refused; no supplier -> REVIEW_REQUIRED

**Coverage:** 3/3 tests pass

---

### PURCHASE_GOODS_CASH

**Description:** Goods purchased for cash or by cheque

**Category:** Purchases

**Required inputs:** amount

**Account structure:** Debit ['Purchases'] / Credit ['Cash | Bank']

**Golden rule:** ['Purchases: Nominal - Debit expenses and losses', 'Cash/Bank: Real - Credit what goes out']

**Journal structure:** Purchases A/c Dr ... / To Cash/Bank A/c ...

**Ledger effect:** Purchases increases (Dr); Cash/Bank decreases (Cr)

**Trial-balance effect:** Purchases on the debit side; Cash/Bank on the credit side

**Supported wording variants:** purchased goods for cash; bought goods for cash; goods purchased for Rs.X in cash; purchased goods paying cash; purchased goods by cheque; goods purchased and payment made immediately

**Refusal conditions:** amount missing -> BLOCKED; cash vs credit not stated -> REVIEW_REQUIRED

**Coverage:** 8/8 tests pass

---

### PURCHASE_GOODS_CREDIT

**Description:** Goods purchased on credit from a named supplier

**Category:** Purchases

**Required inputs:** amount, the supplier (party)

**Account structure:** Debit ['Purchases'] / Credit ['<supplier> (Personal)']

**Golden rule:** ['Purchases: Nominal - Debit expenses and losses', '<supplier>: Personal - Credit the giver']

**Journal structure:** Purchases A/c Dr ... / To <supplier> A/c ...

**Ledger effect:** Purchases increases (Dr); <supplier> creditor balance increases (Cr)

**Trial-balance effect:** Purchases on the debit side; <supplier> on the credit side

**Supported wording variants:** purchased goods from Rahul on credit; bought goods on credit from Rahul; goods purchased from Rahul for Rs.X on credit; bought goods on account from Rahul; purchased goods worth Rs.X from Rahul

**Refusal conditions:** amount missing -> BLOCKED; no supplier named -> REVIEW_REQUIRED

**Coverage:** 22/22 tests pass

---

### PURCHASE_RETURN

**Description:** Goods returned to the supplier (returns outward)

**Category:** Returns

**Required inputs:** amount, the supplier

**Account structure:** Debit ['<supplier> (Personal)'] / Credit ['Purchase Returns']

**Golden rule:** ['<supplier>: Personal - Debit the receiver', 'Purchase Returns: Nominal - Credit incomes/gains']

**Journal structure:** <supplier> A/c Dr ... / To Purchase Returns A/c

**Ledger effect:** Supplier balance decreases (Dr); Purchase Returns increases (Cr)

**Trial-balance effect:** Supplier on the debit side; Purchase Returns on the credit side

**Supported wording variants:** returned goods to Rahul; purchases returns to Rahul; returned goods worth Rs.X to him

**Refusal conditions:** amount missing -> BLOCKED; supplier not named -> REVIEW_REQUIRED (party inherited from the previous transaction when deterministic)

**Coverage:** 2/2 tests pass

---

### RECEIVED_FROM

**Description:** Cash/cheque received FROM a party (settling a debtor); also '<Party> paid ...' subject wording

**Category:** Bank / Cash / Parties

**Required inputs:** amount, the party

**Account structure:** Debit ['Cash | Bank'] / Credit ['<party> (Personal)']

**Golden rule:** ['Cash/Bank: Real - Debit what comes in', '<party>: Personal - Credit the giver']

**Journal structure:** Cash/Bank A/c Dr ... / To <party> A/c ...

**Ledger effect:** Cash/Bank increases (Dr); party debtor balance decreases (Cr)

**Trial-balance effect:** Cash/Bank on the debit side; party on the credit side

**Supported wording variants:** received from Mohan Rs.X in cash; received cash from Mohan; received Rs.X from Amit in cash; Mohan paid Rs.X immediately; Mohan paid us Rs.X

**Refusal conditions:** amount missing -> BLOCKED; party not named -> REVIEW_REQUIRED

**Coverage:** 5/5 tests pass

---

### REFUSAL::BLOCKED

**Description:** Essential information (usually the amount) is missing - the transaction is NOT solved

**Category:** Refusals

**Required inputs:** none - refuses

**Account structure:** Debit [] / Credit []

**Golden rule:** Never invent an amount

**Journal structure:** No journal lines are produced

**Ledger effect:** No ledger effect

**Trial-balance effect:** No trial-balance effect

**Supported wording variants:** Purchased goods from Rahul.; Paid rent.

**Refusal conditions:** amount missing -> BLOCKED

**Coverage:** 4/4 tests pass

---

### REFUSAL::NOT_SUPPORTED

**Description:** The topic is outside the approved Ch.1-3 Unit-Test-1 boundary

**Category:** Refusals

**Required inputs:** none - refuses

**Account structure:** Debit [] / Credit []

**Golden rule:** Never answer outside the syllabus

**Journal structure:** No journal lines are produced

**Ledger effect:** No ledger effect

**Trial-balance effect:** No trial-balance effect

**Supported wording variants:** depreciation; final accounts; balance sheet; partnership; opening entry; issue of shares

**Refusal conditions:** any later-year topic -> NOT_SUPPORTED

**Coverage:** 12/12 tests pass

---

### REFUSAL::REVIEW_REQUIRED

**Description:** The wording is ambiguous (cash vs credit, mode, discount context) - FT-E never guesses

**Category:** Refusals

**Required inputs:** none - refuses

**Account structure:** Debit [] / Credit []

**Golden rule:** Never assume a treatment

**Journal structure:** No journal lines are produced

**Ledger effect:** No ledger effect

**Trial-balance effect:** No trial-balance effect

**Supported wording variants:** Purchased goods for Rs.10,000.; Paid Rs.5,000.; Received Rs.5,000.; Purchased goods.

**Refusal conditions:** cash/credit unstated -> REVIEW_REQUIRED; purpose/context missing -> REVIEW_REQUIRED

**Coverage:** 8/8 tests pass

---

### SALES_RETURN

**Description:** Goods returned by the customer (returns inward)

**Category:** Returns

**Required inputs:** amount, the customer

**Account structure:** Debit ['Sales Returns'] / Credit ['<customer> (Personal)']

**Golden rule:** ['Sales Returns: Nominal - Debit expenses/losses', '<customer>: Personal - Credit the giver']

**Journal structure:** Sales Returns A/c Dr ... / To <customer> A/c ...

**Ledger effect:** Sales Returns increases (Dr); customer balance decreases (Cr)

**Trial-balance effect:** Sales Returns on the debit side; customer on the credit side

**Supported wording variants:** goods returned by Mohan; sales returns from Mohan; Mohan returned goods worth Rs.X

**Refusal conditions:** amount missing -> BLOCKED; customer not named -> REVIEW_REQUIRED

**Coverage:** 2/2 tests pass

---

### SALE_ASSET_CASH

**Description:** An old fixed asset sold for cash/cheque

**Category:** Sales (assets)

**Required inputs:** amount, the exact asset word

**Account structure:** Debit ['Cash | Bank'] / Credit ['exact asset']

**Golden rule:** ['Cash/Bank: Real - Debit what comes in', 'Asset: Real - Credit what goes out']

**Journal structure:** Cash/Bank A/c Dr ... / To <Asset> A/c ...

**Ledger effect:** Cash/Bank increases (Dr); Asset decreases (Cr)

**Trial-balance effect:** Cash/Bank on the debit side; Asset on the credit side

**Supported wording variants:** sold old furniture for cash; sold machinery for Rs.X in cash

**Refusal conditions:** amount missing -> BLOCKED; mode not stated -> REVIEW_REQUIRED

**Coverage:** 1/1 tests pass

---

### SALE_ASSET_CREDIT

**Description:** An old fixed asset sold on credit

**Category:** Sales (assets)

**Required inputs:** amount, the exact asset word, customer

**Account structure:** Debit ['<customer> (Personal)'] / Credit ['exact asset']

**Golden rule:** ['<customer>: Personal - Debit the receiver', 'Asset: Real - Credit what goes out']

**Journal structure:** <customer> A/c Dr ... / To <Asset> A/c ...

**Ledger effect:** Customer debtor increases (Dr); Asset decreases (Cr)

**Trial-balance effect:** Customer on the debit side; Asset on the credit side

**Supported wording variants:** sold old furniture to Ramesh on credit; sold machinery on credit to Ramesh

**Refusal conditions:** amount missing -> BLOCKED; no customer -> REVIEW_REQUIRED

**Coverage:** 1/1 tests pass

---

### SALE_GOODS_CASH

**Description:** Goods sold for cash or by cheque (a named customer never becomes a debtor)

**Category:** Sales

**Required inputs:** amount

**Account structure:** Debit ['Cash | Bank'] / Credit ['Sales']

**Golden rule:** ['Cash/Bank: Real - Debit what comes in', 'Sales: Nominal - Credit incomes and gains']

**Journal structure:** Cash/Bank A/c Dr ... / To Sales A/c ...

**Ledger effect:** Cash/Bank increases (Dr); Sales increases (Cr)

**Trial-balance effect:** Cash/Bank on the debit side; Sales on the credit side

**Supported wording variants:** sold goods for cash; sold goods to Mohan for cash; cash sale of goods; goods sold and cash received immediately; goods sold by cheque

**Refusal conditions:** amount missing -> BLOCKED

**Coverage:** 8/8 tests pass

---

### SALE_GOODS_CREDIT

**Description:** Goods sold on credit to a named customer

**Category:** Sales

**Required inputs:** amount, the customer (party)

**Account structure:** Debit ['<customer> (Personal)'] / Credit ['Sales']

**Golden rule:** ['<customer>: Personal - Debit the receiver', 'Sales: Nominal - Credit incomes and gains']

**Journal structure:** <customer> A/c Dr ... / To Sales A/c ...

**Ledger effect:** <customer> debtor balance increases (Dr); Sales increases (Cr)

**Trial-balance effect:** <customer> on the debit side; Sales on the credit side

**Supported wording variants:** sold goods to Mohan on credit; sold to Mohan for Rs.X on credit; goods sold on credit to Mohan; sold goods on account to Mohan

**Refusal conditions:** amount missing -> BLOCKED; no customer named -> REVIEW_REQUIRED

**Coverage:** 4/4 tests pass

---

### START_BUSINESS

**Description:** Starting the business with capital (cash, bank or named assets)

**Category:** Capital & Drawings

**Required inputs:** amount, the capital side (cash | bank | named asset(s))

**Account structure:** Debit ['Cash | Bank | named asset(s)'] / Credit ['Capital']

**Golden rule:** ['Cash/Bank/assets: Real - Debit what comes in', 'Capital: Personal - Credit the giver']

**Journal structure:** Cash/Bank/<asset> A/c Dr ... / To Capital A/c ...

**Ledger effect:** Cash/Bank/asset balances increase (Dr); Capital balance increases (Cr)

**Trial-balance effect:** Cash/Bank/assets on the debit side; Capital on the credit side

**Supported wording variants:** started business with cash; commenced business with bank balance; began business with cash and furniture; started the business with cash Rs.X and bank balance Rs.Y

**Refusal conditions:** amount missing -> BLOCKED; more than one named asset -> refused (never guessed split)

**Coverage:** 4/4 tests pass

---
