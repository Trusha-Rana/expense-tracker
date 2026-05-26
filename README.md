# 💳 Privacy-First Expense Tracker

Track your spending **without connecting your bank**. Upload PDF credit card statements and get instant analytics, merchant resolution, and AI-powered categorization — all stored in your own MySQL database.

---

## ✨ Features

| Feature | Description |
|---|---|
| 📄 PDF Parsing | Extracts transactions from TD, RBC, CIBC, BMO, Scotiabank, Amex statements |
| 🏪 Merchant Resolution | Normalizes messy names (TMHRTNS → Tim Hortons) using rules + fuzzy matching |
| 🏷️ Categorization | Rule-based (instant) or AI-powered via Claude API |
| 📊 Analytics | Monthly trends, category breakdowns, top merchants, daily timelines |
| 🔮 Forecasting | Predicts next month's spend using Linear Regression or ARIMA |
| 🔁 Recurring Detection | Automatically finds subscriptions and bills |
| ⬇️ CSV Export | Export any filtered transaction set |

---

## 🚀 Quick Start

### 1. Prerequisites

- Python 3.11+
- MySQL 8.0+ (running locally or remotely)

### 2. Clone & Install

```bash
git clone <your-repo-url>
cd expense-tracker

python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

pip install -r requirements.txt
```

### 3. Set Up MySQL

```sql
-- Run in your MySQL client
CREATE DATABASE expense_tracker CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'tracker'@'localhost' IDENTIFIED BY 'your_secure_password';
GRANT ALL PRIVILEGES ON expense_tracker.* TO 'tracker'@'localhost';
FLUSH PRIVILEGES;
```

### 4. Configure Environment

```bash
cp .env.example .env
```

Edit `.env`:
```env
DB_HOST=localhost
DB_PORT=3306
DB_NAME=expense_tracker
DB_USER=tracker
DB_PASSWORD=your_secure_password

# Optional: for AI categorization
ANTHROPIC_API_KEY=sk-ant-...
CATEGORIZATION_MODE=rule_based   # rule_based | hybrid | ai
```

### 5. Run the App

```bash
streamlit run app.py
```

Open **http://localhost:8501** in your browser.

---

## 🗺️ Workflow

```
Upload PDF → Merchant Resolution → Categorization → Analytics → Forecasts
```

1. **Upload & Parse** — Upload your monthly PDF statement. The parser extracts all transactions.
2. **Merchant Resolution** — Review any merchants that couldn't be auto-identified.
3. **Categorization** — Review low-confidence categories and override as needed.
4. **Analytics** — Explore your spending by month, category, and merchant.
5. **Forecasts** — See recurring charges and predict next month's spend.

---

## 🏗️ Project Structure

```
expense-tracker/
├── app.py                          # Streamlit home page + DB initialization
├── pages/
│   ├── 1_Upload_Parse.py           # PDF upload & parsing UI
│   ├── 2_Merchant_Resolution.py    # Merchant review & verification UI
│   ├── 3_Categorization.py         # Category review & override UI
│   ├── 4_Analytics.py              # Full analytics dashboard
│   ├── 5_Forecasts.py              # Forecasting & recurring detection
│   └── 6_Settings.py               # DB config, mode settings, data management
│
├── src/
│   ├── storage/
│   │   ├── models.py               # SQLAlchemy ORM table definitions
│   │   └── database.py             # Connection pool, session manager, init_db()
│   ├── parsing/
│   │   └── pdf_parser.py           # PDF text extraction + transaction regex parser
│   ├── merchants/
│   │   └── resolver.py             # 4-step resolution pipeline (exact→rule→fuzzy→unresolved)
│   ├── categorize/
│   │   └── categorizer.py          # Rule-based + Claude AI categorization engine
│   ├── analytics/
│   │   └── analytics.py            # All analytics queries → DataFrames
│   └── forecasts/
│       └── forecaster.py           # Recurring detection + spend forecasting
│
├── config/
│   ├── categories.yaml             # Category definitions + keyword rules
│   └── merchant_rules.yaml         # Merchant normalization rules (patterns → canonical)
│
├── data/uploads/                   # Temp storage for uploaded PDFs
├── tests/                          # Unit tests
├── requirements.txt
└── .env.example
```

---

## 🧠 How Each Module Works

### PDF Parser (`src/parsing/pdf_parser.py`)
- Uses `pdfplumber` first (best for text-based PDFs)
- Falls back to `PyMuPDF` for complex layouts
- Detects the bank from PDF content (TD, RBC, CIBC, BMO, Scotia, Amex)
- Applies 3 regex patterns to match transaction lines (different date/amount formats)
- Returns a normalized DataFrame regardless of the source bank

### Merchant Resolver (`src/merchants/resolver.py`)
Resolution runs in this order (first match wins):
1. **Exact DB match** — O(1), instant for previously seen merchants
2. **YAML rule match** — handles abbreviations (TMHRTNS → Tim Hortons)
3. **Fuzzy DB match** — RapidFuzz WRatio against known canonical names
4. **Fuzzy YAML match** — same fuzzy match against YAML canonical names
5. **Unresolved** — flagged for manual review in the UI

Every match is saved back to `merchant_raw_names` table → future lookups are instant.

### Categorizer (`src/categorize/categorizer.py`)
Three modes controlled by `CATEGORIZATION_MODE` env var:
- `rule_based` — keyword scan of `categories.yaml` (instant, free)
- `hybrid` — rules first; if confidence < threshold, call Claude API
- `ai` — always use Claude API (most accurate)

### Forecaster (`src/forecasts/forecaster.py`)
- **Recurring detection**: groups by merchant+month, flags if amounts are stable (±10%) across 2+ months
- **Forecasting**: uses LinearRegression for < 8 months, ARIMA for 8+ months
- **Backtesting**: leave-one-out validation to measure model accuracy

---

## ⚙️ Adding Custom Merchants

Edit `config/merchant_rules.yaml`:

```yaml
- canonical: "Your Merchant Name"
  patterns:
    - "raw pdf text"
    - "another variant"
    - "regex:pattern\\d+"   # prefix with regex: for regex patterns
```

## ⚙️ Adding Custom Categories

Edit `config/categories.yaml`:

```yaml
- name: "My Category"
  spending_type: need   # need | want | saving | income
  color_hex: "#FF6B6B"
  icon: "🏷️"
  keywords:
    - "keyword one"
    - "keyword two"
```

Then click **Re-seed from categories.yaml** in Settings → Categories.

---

## 🔒 Privacy

- No bank account connection required
- All data stored in your local MySQL database
- PDF files are parsed in memory and not stored on disk
- API calls (AI mode only) send merchant names to Anthropic — no amounts or personal info

---

## 🧪 Running Tests

```bash
pytest tests/ -v --cov=src
```

---

## 📋 Supported Banks

| Bank | Status |
|---|---|
| TD Bank | ✅ Tested |
| RBC | ✅ Tested |
| CIBC | ✅ Tested |
| BMO | ✅ Tested |
| Scotiabank | ✅ Tested |
| American Express | ✅ Tested |
| Tangerine | ✅ Tested |
| Other banks | ⚠️ May work — check raw text output |

For unsupported formats, open the **Raw Extracted Text** expander on the Upload page to debug.

---

## 🚧 Roadmap

- [ ] Multi-currency support
- [ ] Per-credit-card tracking (multiple cards)
- [ ] Budget alerts / spending limits
- [ ] Google Sheets export
- [ ] Mobile-optimized UI
- [ ] Docker Compose setup
