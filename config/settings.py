"""
Configuration settings — loads from .env file
"""
import os
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ─── Credentials ──────────────────────────────────────────
EB_EMAIL = os.getenv('EB_EMAIL', '')
EB_PASS = os.getenv('EB_PASS', '')
GK_EMAIL = os.getenv('GK_EMAIL', '')
GK_PASS = os.getenv('GK_PASS', '')
GK_APP_PASS = os.getenv('GK_APP_PASS', '')

# ─── URLs ─────────────────────────────────────────────────
EB_LOGIN_URL = os.getenv('EB_LOGIN_URL', 'https://auth.easebuzz.in/easebuzz/login?ep=y')
EB_PARTNER_URL = os.getenv('EB_PARTNER_URL', 'https://partners.easebuzz.in')
GK_DASHBOARD_URL = os.getenv('GK_DASHBOARD_URL', 'https://dashboard.gokwik.co')

# ─── Google Sheets ────────────────────────────────────────
# Easebuzz SALT AND KEY (main sheet)
EASEBUZZ_SALT_KEY_SHEET_ID = "1AXFP7jasPRV4sUKMqaLOok6-Ljg1SgYK37HzInHY2wc"
EASEBUZZ_SALT_KEY_GID = "0"

# Merchant Onboarding - Sanity (tracker — quality check 2026 tab)
SANITY_TRACKER_SHEET_ID = "1d6keVwEImXgieLzQG0RHfDKcNoe-vUpORDOph5YHoGk"
SANITY_TRACKER_GID = "1800442686"

# Merchant list + MDR rates (using tracker as sample for now)
SANITY_SAMPLE_SHEET_ID = "19dNJcefPXGl3CNE8Dd1_YiCzha6kbPf9NGUL1rm2URI"
SANITY_SAMPLE_GID = "0"

# Old sheets (kept for reference)
COMMERCIAL_SHEET_ID = "1CPuJSbd4emdVzfUOpr7cyYE0FBLlqlYE3_OHQtQATcY"
COMMERCIAL_SHEET_GID = "0"

SERVICE_ACCOUNT_FILE = os.path.join(BASE_DIR, "config", "service_account.json")
GOOGLE_SHEETS_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# ─── Google Drive ─────────────────────────────────────────
CHEQUE_DRIVE_FOLDER_ID = '1hlk956QtbMGy1p2TlwSjBVbhoqom3vky'

# ─── Browser ──────────────────────────────────────────────
HEADLESS = os.getenv('HEADLESS', 'true').lower() == 'true'
BROWSER_TIMEOUT = 30000  # Reduced from 60s — faster fail

# ─── Timeouts ─────────────────────────────────────────────
CHECK_TIMEOUT = 90        # Reduced from 120s
REPORT_WAIT_TIMEOUT = 90
OTP_WAIT_SECONDS = 15
MAX_RETRIES = 1           # Reduced retries — don't waste time

# ─── Scheduler ────────────────────────────────────────────
DAILY_RUN_HOUR = int(os.getenv('DAILY_RUN_HOUR', '17'))
DAILY_RUN_MINUTE = int(os.getenv('DAILY_RUN_MINUTE', '0'))

# ─── Reports ─────────────────────────────────────────────
REPORT_OUTPUT_DIR = "reports"
