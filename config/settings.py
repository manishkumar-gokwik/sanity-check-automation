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
# Easebuzz SALT AND KEY
EASEBUZZ_SALT_KEY_SHEET_ID = "1AXFP7jasPRV4sUKMqaLOok6-Ljg1SgYK37HzInHY2wc"
EASEBUZZ_SALT_KEY_GID = "0"

# Commercial / MDR sheet (merchant list + expected rates)
COMMERCIAL_SHEET_ID = "1CPuJSbd4emdVzfUOpr7cyYE0FBLlqlYE3_OHQtQATcY"
COMMERCIAL_SHEET_GID = "0"
SANITY_SAMPLE_SHEET_ID = "1CPuJSbd4emdVzfUOpr7cyYE0FBLlqlYE3_OHQtQATcY"
SANITY_SAMPLE_GID = "0"

# Sanity Check sheet (results written here — Yes/No)
SANITY_TRACKER_SHEET_ID = "18RU1UCrGE6XMpYv4xTfP_3BXp_m6GmL6ADXnV1ZtLVc"
SANITY_TRACKER_GID = "0"

SERVICE_ACCOUNT_FILE = os.path.join(BASE_DIR, "config", "service_account.json")
GOOGLE_SHEETS_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# ─── Google Drive ─────────────────────────────────────────
CHEQUE_DRIVE_FOLDER_ID = '1uNLqPV_0qqLTufJviWJtcB_KQ67ve2Pv'

# ─── Gemini API ───────────────────────────────────────────
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY', '')

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
