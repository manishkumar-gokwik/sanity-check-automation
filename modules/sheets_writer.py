"""
Google Sheets Writer — Writes check results to Sanity 2026 automation sheet.
Maps: Settlement, MDR, Account Number, SALT & KEY, VPA → Yes/No in sheet columns.
"""

import logging
import gspread
from modules.sheets_reader import _get_client
from config.settings import SANITY_TRACKER_SHEET_ID, SANITY_TRACKER_GID

logger = logging.getLogger(__name__)


def _get_tracker_worksheet():
    client = _get_client()
    spreadsheet = client.open_by_key(SANITY_TRACKER_SHEET_ID)
    return spreadsheet.get_worksheet_by_id(int(SANITY_TRACKER_GID))


def write_results(batch_results):
    """
    Write check results to Sanity 2026 automation sheet.

    Column mapping:
    - Settlement       → "Settlement Report Triggered"
    - MDR              → "Commercial Validation"
    - Account Number   → "Bank Accont "
    - SALT & KEY       → "salt and key validation"
    - VPA/Webhook      → "Web hook - VPA"
    """
    try:
        ws = _get_tracker_worksheet()
        all_data = ws.get_all_values()
        if not all_data:
            return {"success": False, "error": "Empty sheet"}

        headers = all_data[0]
        header_map = {h.strip().lower(): i for i, h in enumerate(headers)}

        # Column indices
        col_settlement = header_map.get('settlement report triggered', -1)
        col_mdr = header_map.get('commercial validation', -1)
        col_account = header_map.get('bank accont', header_map.get('bank account', -1))
        col_salt_key = header_map.get('salt and key validation', -1)
        col_vpa = header_map.get('web hook - vpa', -1)
        col_name = header_map.get('merchant name', -1)
        col_mid = header_map.get('mid', -1)

        # Batch updates for efficiency
        batch_updates = []
        merchants_updated = 0

        for result in batch_results:
            merchant_name = result.get('merchant_name', '')
            checks = {c['check_name']: c for c in result.get('checks', [])}

            # Find row by merchant name
            row_idx = None
            for i, row in enumerate(all_data[1:], start=2):
                row_name = str(row[col_name]).strip().lower() if col_name >= 0 and col_name < len(row) else ''
                row_mid = str(row[col_mid]).strip() if col_mid >= 0 and col_mid < len(row) else ''

                if merchant_name.lower() == row_name:
                    row_idx = i
                    break
                result_mid = result.get('eb_mid', '')
                if result_mid and row_mid == str(result_mid):
                    row_idx = i
                    break

            if row_idx is None:
                logger.warning(f"Merchant '{merchant_name}' not found in tracking sheet")
                continue

            # Map checks to columns
            check_mapping = {
                'Settlement': col_settlement,
                'MDR': col_mdr,
                'Account Number': col_account,
                'SALT & KEY': col_salt_key,
                'VPA / Webhook': col_vpa,
            }

            for check_name, col_idx in check_mapping.items():
                if col_idx < 0:
                    continue
                c = checks.get(check_name, {})
                status = c.get('status', '')

                if status == 'PASS':
                    val = 'Yes'
                elif status == 'FAIL':
                    val = 'No'
                elif status == 'WARN':
                    val = 'Warn'
                else:
                    continue

                batch_updates.append({
                    'range': gspread.utils.rowcol_to_a1(row_idx, col_idx + 1),
                    'values': [[val]]
                })

            merchants_updated += 1

        # Write all updates in batch (faster than individual calls)
        if batch_updates:
            ws.batch_update(batch_updates)
            logger.info(f"Written {len(batch_updates)} cells for {merchants_updated} merchants")

        return {
            "success": True,
            "updated_cells": len(batch_updates),
            "merchants_updated": merchants_updated
        }

    except Exception as e:
        logger.exception("Failed to write results to sheet")
        return {"success": False, "error": str(e)}
