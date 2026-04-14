"""
Google Sheets Writer Module
Writes sanity check results back to the Sanity Tracker sheet.
"""

import gspread
from modules.sheets_reader import _get_client
from config.settings import SANITY_TRACKER_SHEET_ID, SANITY_TRACKER_GID


def _get_tracker_worksheet():
    """Get the Sanity Tracker worksheet."""
    client = _get_client()
    spreadsheet = client.open_by_key(SANITY_TRACKER_SHEET_ID)
    return spreadsheet.get_worksheet_by_id(int(SANITY_TRACKER_GID))


def write_results(batch_results):
    """
    Write batch check results back to Sanity Tracker sheet.
    Matches by MID column, updates status columns.
    """
    ws = _get_tracker_worksheet()
    all_data = ws.get_all_values()
    if not all_data:
        return {"success": False, "error": "Empty sheet"}

    headers = all_data[0]
    header_map = {h.strip().lower(): i for i, h in enumerate(headers)}

    # Column indices we want to update
    col_bank = header_map.get('bank accont', header_map.get('bank account', -1))
    col_webhook = header_map.get('web hook - vpa', -1)
    col_settlement = header_map.get('settlement report triggered', -1)
    col_commercial = header_map.get('commercial validation', -1)
    col_config = header_map.get('confiq new validation', header_map.get('config new validation', -1))
    col_salt = header_map.get('salt and key validation', -1)
    col_mid = header_map.get('mid', -1)
    col_name = header_map.get('merchant name', -1)

    updated = 0
    for result in batch_results:
        merchant_name = result.get('merchant_name', '')
        checks = {c['check_name']: c for c in result.get('checks', [])}

        # Find the row by MID or Merchant Name
        row_idx = None
        for i, row in enumerate(all_data[1:], start=2):  # 1-indexed, skip header
            row_mid = str(row[col_mid]).strip() if col_mid >= 0 and col_mid < len(row) else ''
            row_name = str(row[col_name]).strip().lower() if col_name >= 0 and col_name < len(row) else ''

            # Match by merchant name
            if merchant_name.lower() == row_name:
                row_idx = i
                break
            # Match by MID from result
            result_mid = result.get('eb_mid', '')
            if result_mid and row_mid == str(result_mid):
                row_idx = i
                break

        if row_idx is None:
            continue

        # Build updates
        updates = []

        # Bank Account check
        if col_bank >= 0:
            c = checks.get('Bank Account', checks.get('Settlement Report', {}))
            val = 'Yes' if c.get('status') == 'PASS' else c.get('message', '')[:50] if c else ''
            updates.append({'row': row_idx, 'col': col_bank + 1, 'val': val})

        # Webhook/VPA check
        if col_webhook >= 0:
            c = checks.get('VPN (Payment Notification)', checks.get('GK Orders', {}))
            val = 'Yes' if c.get('status') == 'PASS' else 'No' if c.get('status') == 'FAIL' else ''
            updates.append({'row': row_idx, 'col': col_webhook + 1, 'val': val})

        # Settlement check
        if col_settlement >= 0:
            c = checks.get('Settlement Status', checks.get('Settlement Report', {}))
            val = 'Yes' if c.get('status') == 'PASS' else 'No' if c.get('status') == 'FAIL' else ''
            updates.append({'row': row_idx, 'col': col_settlement + 1, 'val': val})

        # Commercial Validation
        if col_commercial >= 0:
            c = checks.get('Commercial Rates', {})
            val = 'Yes' if c.get('status') == 'PASS' else 'No' if c.get('status') == 'FAIL' else ''
            updates.append({'row': row_idx, 'col': col_commercial + 1, 'val': val})

        # Config/New Validation
        if col_config >= 0:
            c = checks.get('Payment Gateway Config', {})
            val = 'Yes' if c.get('status') == 'PASS' else ''
            updates.append({'row': row_idx, 'col': col_config + 1, 'val': val})

        # SALT & KEY validation
        if col_salt >= 0:
            c = checks.get('SALT & KEY', {})
            val = 'Yes' if c.get('status') == 'PASS' else 'Missing' if c.get('status') == 'FAIL' else ''
            updates.append({'row': row_idx, 'col': col_salt + 1, 'val': val})

        # Write updates
        for u in updates:
            if u['val']:
                ws.update_cell(u['row'], u['col'], u['val'])
                updated += 1

    return {"success": True, "updated_cells": updated, "merchants_updated": len(batch_results)}
