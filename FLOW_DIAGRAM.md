# Sanity Check Automation — Complete Flow Diagram

## Main Flow

```
┌─────────────────────────────────────────────────────────────────┐
│              USER clicks "Run Sanity Check"                      │
│                       (localhost:5000)                           │
└──────────────────────────┬──────────────────────────────────────┘
                           ↓
┌─────────────────────────────────────────────────────────────────┐
│                  PHASE 0: LOAD MERCHANT DATA                     │
│                    (Google Sheets API)                           │
└─────────────────────────────────────────────────────────────────┘
     │
     ├──► 📄 Sheet: "Manish sanity"
     │     → Merchant names + EB MID + Expected rates
     │
     ├──► 📄 Sheet: "Salt n key"
     │     → Expected KEY + SALT per merchant
     │
     └──► 📄 Sheet: "Auto Check" (Tracker)
           → Current status (for writing results back)
                           ↓
┌─────────────────────────────────────────────────────────────────┐
│        PHASE 1: EASEBUZZ PARTNER PORTAL AUTOMATION              │
│              (Browser: partners.easebuzz.in)                     │
└─────────────────────────────────────────────────────────────────┘
     │
     ├──► [1.1] Login
     │        URL: auth.easebuzz.in/easebuzz/login
     │        Creds: .env → EB_EMAIL, EB_PASS
     │
     ├──► [1.2] Navigate: Custom Reports → Merchant Settlements
     │
     ├──► [1.3] Click "Generate New Report"
     │        Fill form:
     │          - Report Name: sanity_{timestamp}
     │          - Category: Settlement Report
     │          - Merchants: Select All
     │          - Date Range: Last 30 days
     │
     ├──► [1.4] Click "Generate" → Wait 60-90 sec
     │
     └──► [1.5] Download CSV (click download icon)
           Result: settlement_report.csv (all merchants txns)
                           ↓
┌─────────────────────────────────────────────────────────────────┐
│            FOR EACH MERCHANT → RUN CHECKS 1-3                   │
└─────────────────────────────────────────────────────────────────┘

    ┌──────────────────────────────────────────────────────────┐
    │  CHECK 1: SETTLEMENT                                     │
    │  Source: settlement_report.csv                           │
    │  Logic: Filter by EB MID → count transactions + amount   │
    │  PASS: Transactions found                                │
    │  WARN: No transactions (new merchant)                    │
    └──────────────────────────────────────────────────────────┘

    ┌──────────────────────────────────────────────────────────┐
    │  CHECK 2: MDR                                            │
    │  Source: settlement_report.csv + "Manish sanity" sheet   │
    │  Formula: (Service Charge / Amount) × 100                │
    │  Compare: Actual vs Expected rates (UPI, CC, DC)         │
    │  PASS: All rates match (±0.05%)                          │
    │  FAIL: Any rate mismatch                                 │
    └──────────────────────────────────────────────────────────┘

    ┌──────────────────────────────────────────────────────────┐
    │  CHECK 3: ACCOUNT NUMBER                                 │
    │                                                          │
    │  EB Account:                                             │
    │    Source: settlement_report.csv                         │
    │    Column: "Settlement Account Number"                   │
    │                                                          │
    │  Cheque Account:                                         │
    │    Source: Google Drive (Cancelled Cheque folder)        │
    │    Method: Download image → OCR (Tesseract)              │
    │    Extract: Account number (regex)                       │
    │                                                          │
    │  MATCH: EB Account == Cheque Account                     │
    │  PASS: Same account number                               │
    │  FAIL: Mismatch (fraud alert)                            │
    │  WARN: Cheque not found in Drive                         │
    └──────────────────────────────────────────────────────────┘
                           ↓
┌─────────────────────────────────────────────────────────────────┐
│          PHASE 2: GOKWIK DASHBOARD AUTOMATION                   │
│              (Browser: dashboard.gokwik.co)                      │
└─────────────────────────────────────────────────────────────────┘
     │
     ├──► [2.1] Login
     │        URL: dashboard.gokwik.co/login
     │        Creds: .env → GK_EMAIL, GK_PASS
     │
     ├──► [2.2] OTP (auto-fetch from Gmail IMAP)
     │        Email: paymentsops@gokwik.co
     │        App Password: .env → GK_APP_PASS
     │        Filter: "GoKwik Signin" emails
     │        Extract: 6-digit OTP
     │
     └──► [2.3] Navigate: Kwik Payment → Settings → Terminals
                           ↓
┌─────────────────────────────────────────────────────────────────┐
│            FOR EACH MERCHANT → RUN CHECKS 4-5                   │
└─────────────────────────────────────────────────────────────────┘

    ┌──────────────────────────────────────────────────────────┐
    │  CHECK 4: SALT & KEY                                     │
    │                                                          │
    │  Step 1: Click "Switch merchant" dropdown                │
    │  Step 2: Search merchant name → Select → "Set Merchant"  │
    │  Step 3: Navigate to Terminals again                     │
    │  Step 4: Click Edit icon on "Easebuzz" terminal          │
    │  Step 5: Extract from form:                              │
    │            - Enter Merchant Key → GK KEY                 │
    │            - Enter Salt → GK SALT                        │
    │  Step 6: Compare with "Salt n key" sheet                 │
    │                                                          │
    │  PASS: GK KEY == Sheet KEY AND GK SALT == Sheet SALT     │
    │  FAIL: Any mismatch                                      │
    │  WARN: Easebuzz terminal not configured                  │
    └──────────────────────────────────────────────────────────┘

    ┌──────────────────────────────────────────────────────────┐
    │  CHECK 5: VPA / WEBHOOK                                  │
    │                                                          │
    │  Step 1: Click "Orders" in sidebar                       │
    │  Step 2: Click first order (KWIK...)                     │
    │  Step 3: Scroll Payment table → find "Link" column       │
    │  Step 4: Extract UPI link (upi://pay?pa=...)             │
    │  Step 5: Parse VPA from pa= parameter                    │
    │  Step 6: Check if merchant name in VPA                   │
    │                                                          │
    │  PASS: VPA contains merchant identifier                  │
    │  WARN: No UPI orders / VPA format different              │
    └──────────────────────────────────────────────────────────┘
                           ↓
┌─────────────────────────────────────────────────────────────────┐
│                   PHASE 3: RESULTS                               │
└─────────────────────────────────────────────────────────────────┘
     │
     ├──► [3.1] Aggregate results per merchant
     │          Overall: PASS / FAIL / WARN
     │
     ├──► [3.2] Show on Dashboard
     │          → localhost:5000 → Results tab
     │
     ├──► [3.3] Write to "Auto Check" sheet (optional)
     │          Columns: Settlement Status, Bank Account,
     │                   Commercial Validation, etc.
     │
     ├──► [3.4] Export options:
     │          - PDF Report
     │          - CSV Export
     │          - Email Draft (for discrepancies)
     │
     └──► [3.5] Daily 5 PM automatic run (scheduler)
           → APScheduler cron job
```

## Data Sources Summary

**Google Sheets:**
- "Manish sanity" — Merchant list, EB MID, Expected MDR rates
- "Salt n key"   — Expected SALT & KEY per merchant
- "Auto Check"   — Tracker (results written here)

**EB Partner Portal (partners.easebuzz.in):**
- Settlement Report — Actual account, transactions, MDR data

**GK Dashboard (dashboard.gokwik.co):**
- Terminals → Easebuzz Edit — Actual KEY/SALT in production
- Orders → Payment Link — Actual VPA

**Google Drive:**
- Cancelled Cheque folder — Account number via OCR

**Gmail IMAP:**
- OTP auto-fetch for GK Dashboard login

## Why Each Check?

| # | Check | Purpose |
|---|---|---|
| 1 | Settlement | Verify settlements are actually happening |
| 2 | MDR | Ensure correct rates are charged (commercial team) |
| 3 | Account Number | Detect fraud (wrong bank = money to wrong person) |
| 4 | SALT & KEY | Ensure production config matches agreed credentials |
| 5 | VPA/Webhook | Verify payment routing (merchant's UPI ID correctness) |

## Production-Ready Features

- Credentials in .env (not hardcoded)
- Error handling per check (one fail doesn't stop others)
- Timeout per check (120s max)
- Retry logic on timeout
- Logging at each stage
- Stage-wise progress on dashboard
- Daily 5 PM scheduler
- Headless mode toggle (.env)
