# Sanity Check Automation

Automated daily sanity checks for merchant onboarding. Verifies Settlement, MDR, Bank Account, SALT & KEY, and VPA across Easebuzz Partner Portal, GoKwik Dashboard, Google Sheets, and Google Drive.

## Architecture

- **5 Checks:** Settlement, MDR, Account Number, SALT & KEY, VPA/Webhook
- **Data Sources:** Google Sheets (3), Google Drive (cancelled cheques), EB Partner Portal, GK Dashboard
- **Stack:** Flask, Playwright, Google APIs, Tesseract OCR, APScheduler
- **Frontend:** HTML/CSS/JS dashboard with real-time stage progress

## Setup

### 1. Install dependencies
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
playwright install chromium
sudo apt-get install tesseract-ocr
```

### 2. Configure environment
Copy `.env.example` to `.env` and fill credentials:
```bash
# Easebuzz Partner Portal
EB_EMAIL=easebuzzpg@gokwik.co
EB_PASS=<password>

# GoKwik Dashboard (Production)
GK_EMAIL=paymentsops@gokwik.co
GK_PASS=<password>
GK_APP_PASS=<gmail_app_password>

# URLs
GK_DASHBOARD_URL=https://dashboard.gokwik.co

# Browser (true for production)
HEADLESS=true

# Scheduler (24-hour format)
DAILY_RUN_HOUR=17
DAILY_RUN_MINUTE=0
```

### 3. Google Service Account
- Place `service_account.json` in `config/` folder
- Share all 3 Google Sheets + Drive folder with the service account email

## Running

### Development
```bash
source venv/bin/activate
python server.py
```

### Production (Gunicorn)
```bash
./start_production.sh
```
Or directly:
```bash
gunicorn --config gunicorn_config.py wsgi:app
```

### As systemd service
```bash
sudo cp sanity-automation.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable sanity-automation
sudo systemctl start sanity-automation
sudo systemctl status sanity-automation
```

## Usage

1. Open browser: `http://localhost:5000`
2. Go to "Run Sanity Check" tab
3. Click "Run Sanity Check"
4. Watch stage-wise progress
5. Results appear in "Results & Reports" tab

## File Structure

```
Automation/
├── server.py                 # Flask app
├── wsgi.py                   # Production WSGI entry
├── gunicorn_config.py        # Gunicorn config
├── start_production.sh       # Production startup
├── sanity-automation.service # Systemd unit
├── .env                      # Secrets (gitignored)
├── config/
│   ├── settings.py           # Configuration
│   └── service_account.json  # Google creds (gitignored)
├── modules/
│   ├── sanity_engine.py      # Core check logic
│   ├── sheets_reader.py      # Google Sheets API
│   ├── sheets_writer.py      # Write results to sheet
│   ├── cheque_verifier.py    # Drive OCR
│   ├── scheduler.py          # Daily 5 PM cron
│   ├── partner_portal.py     # EB automation
│   ├── gk_dashboard.py       # GK automation
│   ├── report_generator.py   # PDF report
│   └── email_drafter.py      # Email drafts
├── templates/
│   └── index.html            # Dashboard UI
├── static/
│   ├── css/style.css
│   └── js/app.js
├── logs/                     # Runtime logs (gitignored)
└── requirements.txt
```

## Logs

- `logs/access.log` — Gunicorn access
- `logs/error.log` — Gunicorn errors
- `logs/scheduler.log` — Daily scheduler runs
- `logs/daily_runs.log` — Summary of each daily run

## Troubleshooting

**GK Dashboard login fails:**
- Check OTP auto-fetch — Gmail App Password may have expired
- Login manually first to verify credentials work

**Settlement report download fails:**
- EB Portal may be slow — retry will help
- Check report generation completed (may take 60-90s)

**Cheque OCR fails:**
- Poor image quality — add to manual review
- Multiple cheques in folder — only first is used

**Production dashboard slow:**
- Reduce browser timeout in `settings.py`
- Ensure `HEADLESS=true` in `.env`
