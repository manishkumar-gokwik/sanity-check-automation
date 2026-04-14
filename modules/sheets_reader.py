"""
Google Sheets Reader Module
Reads merchant data directly from Google Sheets via API.
No local CSV needed — always pulls fresh data.
"""

import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
from config.settings import (
    COMMERCIAL_SHEET_ID,
    COMMERCIAL_SHEET_GID,
    SANITY_TRACKER_SHEET_ID,
    SANITY_TRACKER_GID,
    EASEBUZZ_SALT_KEY_SHEET_ID,
    EASEBUZZ_SALT_KEY_GID,
    SANITY_SAMPLE_SHEET_ID,
    SANITY_SAMPLE_GID,
    SERVICE_ACCOUNT_FILE,
    GOOGLE_SHEETS_SCOPES,
)

# Singleton gspread client
_client = None


def _get_client():
    """Get authenticated gspread client (cached)."""
    global _client
    if _client is None:
        creds = Credentials.from_service_account_file(
            SERVICE_ACCOUNT_FILE, scopes=GOOGLE_SHEETS_SCOPES
        )
        _client = gspread.authorize(creds)
    return _client


def _read_sheet(sheet_id, gid="0"):
    """Read a Google Sheet worksheet into a DataFrame."""
    client = _get_client()
    spreadsheet = client.open_by_key(sheet_id)
    worksheet = spreadsheet.get_worksheet_by_id(int(gid))
    if worksheet is None:
        worksheet = spreadsheet.sheet1
    try:
        data = worksheet.get_all_records()
    except Exception:
        # Fallback for duplicate headers — read raw values
        raw = worksheet.get_all_values()
        if not raw:
            return pd.DataFrame()
        headers = raw[0]
        # Make headers unique
        seen = {}
        unique_headers = []
        for h in headers:
            h = str(h).strip()
            if not h:
                h = f'_col_{len(unique_headers)}'
            if h in seen:
                seen[h] += 1
                h = f'{h}_{seen[h]}'
            else:
                seen[h] = 0
            unique_headers.append(h)
        data = [dict(zip(unique_headers, row)) for row in raw[1:]]
    return pd.DataFrame(data)


def get_sanity_tracker():
    """Read the Sanity Tracker sheet (daily check records)."""
    return _read_sheet(SANITY_TRACKER_SHEET_ID, SANITY_TRACKER_GID)


def get_commercial_rates(merchant_name=None):
    """Read Commercial Rates / MDR sheet."""
    df = _read_sheet(COMMERCIAL_SHEET_ID, COMMERCIAL_SHEET_GID)
    if merchant_name and not df.empty and 'Merchant Name' in df.columns:
        mask = df['Merchant Name'].astype(str).str.lower() == merchant_name.lower()
        filtered = df[mask]
        if not filtered.empty:
            return filtered.reset_index(drop=True)
    return df


def get_salt_key():
    """Read Easebuzz SALT & KEY sheet."""
    return _read_sheet(EASEBUZZ_SALT_KEY_SHEET_ID, EASEBUZZ_SALT_KEY_GID)


def _fix_tracker_columns(df):
    """Fix duplicate Merchant Name columns in Sanity Tracker.
    Col 0 is empty 'Merchant Name', Col 6 (Merchant Name_1) has actual data."""
    if 'Merchant Name_1' in df.columns:
        # The real merchant name is in the duplicate column
        df['Merchant Name'] = df['Merchant Name_1']
    # Also fix Mid - might be in a different column
    if 'Mid' in df.columns and df['Mid'].astype(str).str.strip().eq('').all():
        if 'MID' in df.columns:
            df['Mid'] = df['MID']
    return df


def get_today_merchants(tracker_df=None):
    """
    Get merchants added today (or latest date) from Sanity Tracker.
    Returns DataFrame with merchant list to check.
    """
    if tracker_df is None:
        tracker_df = get_sanity_tracker()

    if tracker_df.empty:
        return tracker_df

    df = _fix_tracker_columns(tracker_df.copy())
    df['Date'] = pd.to_datetime(df['Date'], format='mixed', dayfirst=True, errors='coerce')
    latest_date = df['Date'].max()
    if pd.isna(latest_date):
        return df

    result = df[df['Date'] == latest_date].reset_index(drop=True)
    # Remove rows with empty merchant name
    result = result[result['Merchant Name'].astype(str).str.strip() != ''].reset_index(drop=True)
    return result


def get_merchants_by_date(date_str, tracker_df=None):
    """Get merchants for a specific date from Sanity Tracker."""
    if tracker_df is None:
        tracker_df = get_sanity_tracker()

    if tracker_df.empty:
        return tracker_df

    df = _fix_tracker_columns(tracker_df.copy())
    df['Date'] = pd.to_datetime(df['Date'], format='mixed', dayfirst=True, errors='coerce')
    target = pd.to_datetime(date_str, format='mixed', dayfirst=True, errors='coerce')
    if pd.isna(target):
        return pd.DataFrame()

    result = df[df['Date'].dt.date == target.date()].reset_index(drop=True)
    result = result[result['Merchant Name'].astype(str).str.strip() != ''].reset_index(drop=True)
    return result


def get_sanity_sample():
    """Read the sanity data sample sheet (test merchant list)."""
    return _read_sheet(SANITY_SAMPLE_SHEET_ID, SANITY_SAMPLE_GID)


def test_connection():
    """Test if Google Sheets API connection works."""
    try:
        client = _get_client()
        # Try reading each sheet
        results = {}
        for name, sid in [
            ("Commercial Rates", COMMERCIAL_SHEET_ID),
            ("Sanity Tracker", SANITY_TRACKER_SHEET_ID),
            ("SALT & KEY", EASEBUZZ_SALT_KEY_SHEET_ID),
        ]:
            try:
                sp = client.open_by_key(sid)
                results[name] = {"connected": True, "title": sp.title}
            except Exception as e:
                results[name] = {"connected": False, "error": str(e)}
        return {"success": True, "sheets": results}
    except Exception as e:
        return {"success": False, "error": str(e)}
