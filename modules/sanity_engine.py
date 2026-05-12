"""
Sanity Engine — Production-Ready
Runs all 5 checks for multiple merchants:
1. Settlement (EB Partner Portal)
2. MDR (Settlement Report vs Sheet)
3. Account Number (EB Settlement vs Cancelled Cheque OCR)
4. SALT & KEY (GK Dashboard Terminals vs Sheet)
5. VPA/Webhook (GK Dashboard Orders → Payment Link)
"""

import asyncio
import imaplib
import email
import re
import time
import json
import os
import logging
import pandas as pd
from datetime import datetime, timedelta
from playwright.async_api import async_playwright
from config.settings import (
    EB_EMAIL, EB_PASS, EB_LOGIN_URL, EB_PARTNER_URL,
    GK_EMAIL, GK_PASS, GK_APP_PASS, GK_DASHBOARD_URL,
    HEADLESS, BROWSER_TIMEOUT, CHECK_TIMEOUT, OTP_WAIT_SECONDS,
    REPORT_WAIT_TIMEOUT, MAX_RETRIES
)

logger = logging.getLogger(__name__)


# ─── HELPERS ──────────────────────────────────────────────

def _clean(val):
    s = str(val).strip()
    return '' if s.lower() in ('nan', 'none', '', 'nat') else s


def _fetch_gk_otp(retries=3):
    """Fetch latest OTP from Gmail with retries."""
    for attempt in range(retries):
        try:
            time.sleep(OTP_WAIT_SECONDS)
            m = imaplib.IMAP4_SSL('imap.gmail.com')
            m.login(GK_EMAIL, GK_APP_PASS)
            m.select('inbox')
            s, msgs = m.search(None, '(FROM "no-reply@gokwik.co" SUBJECT "GoKwik Signin")')
            ids = msgs[0].split()
            if not ids:
                m.logout()
                continue
            s, d = m.fetch(ids[-1], '(RFC822)')
            msg = email.message_from_bytes(d[0][1])
            body = ''
            for p in msg.walk():
                if p.get_content_type() in ('text/plain', 'text/html'):
                    body = p.get_payload(decode=True).decode('utf-8', 'ignore')
                    break
            m.logout()
            otps = re.findall(r'\b(\d{6})\b', body)
            if otps:
                return otps[0]
        except Exception as e:
            logger.warning(f"OTP fetch attempt {attempt+1} failed: {e}")
    return None


def _make_check(name, status, message, expected='', actual=''):
    return {"check_name": name, "status": status, "message": message,
            "expected": expected, "actual": actual}


# ─── INDIVIDUAL CHECKS ───────────────────────────────────

def _normalize_mid(val):
    """Normalize MID to clean string: strip whitespace, remove trailing .0 (float artifact)."""
    s = str(val).strip()
    if s.endswith('.0'):
        s = s[:-2]
    return s


def _find_mid_column(df):
    """Find the Merchant ID column case-insensitively, tolerating extra spaces."""
    for c in df.columns:
        cl = c.strip().lower()
        if cl in ('merchant id', 'merchantid', 'mid'):
            return c
    return None


async def check_settlement(sr_df, eb_mid, merchant_name):
    """Check 1: Settlement — find merchant in settlement report by EB MID."""
    try:
        if sr_df.empty:
            return _make_check("Settlement", "WARN",
                             "Reason: Could not download settlement report from EB Partner Portal.",
                             expected="Settlement report CSV",
                             actual="Empty / Failed download")

        if not eb_mid:
            return _make_check("Settlement", "WARN",
                             f"Reason: No EB MID found in sheet for '{merchant_name}'. Cannot verify settlement.",
                             expected="EB MID in sheet",
                             actual="Empty")
        mid_col = _find_mid_column(sr_df)
        if not mid_col:
            return _make_check("Settlement", "WARN",
                             f"Reason: No 'Merchant ID' column in settlement CSV. Columns: {list(sr_df.columns)[:5]}",
                             expected="Merchant ID column", actual="Missing")
        target = _normalize_mid(eb_mid)
        normalized_col = sr_df[mid_col].apply(_normalize_mid)
        txns = sr_df[normalized_col == target]
        if txns.empty:
            return _make_check("Settlement", "WARN",
                             f"Reason: No transactions found for EB MID {eb_mid}. This merchant may be new or has no transactions yet.",
                             expected=f"Transactions for EB MID {eb_mid}",
                             actual="No transactions in last 7 days")

        total_amt = pd.to_numeric(txns.get('Transaction Settlement Amount', 0), errors='coerce').sum()
        acct = str(txns.iloc[0].get('Settlement Account Number', '')).replace('.0', '').strip()
        acct = re.sub(r'[=""]', '', acct).strip()
        ifsc = str(txns.iloc[0].get('IFSC Code', '')).strip()

        return _make_check("Settlement", "PASS",
                         f"Settlement is active: {len(txns)} transactions totaling Rs.{total_amt:.2f}",
                         expected="Active settlements",
                         actual=f"Account: {acct} | IFSC: {ifsc}")
    except Exception as e:
        logger.exception(f"Settlement check failed: {merchant_name}")
        return _make_check("Settlement", "FAIL",
                         f"Reason: An error occurred during check — {str(e)[:80]}")


async def check_mdr(sr_df, eb_mid, expected_rates, merchant_name):
    """Check 2: MDR — calculate actual vs expected. Looks up by EB MID."""
    try:
        if sr_df.empty:
            return _make_check("MDR", "WARN",
                             "Reason: Settlement report is not available, so MDR cannot be calculated.",
                             expected="Settlement report CSV", actual="Empty")

        if not eb_mid:
            return _make_check("MDR", "WARN",
                             f"Reason: No EB MID found in sheet for '{merchant_name}'. Cannot calculate MDR.",
                             expected="EB MID in sheet", actual="Empty")
        mid_col = _find_mid_column(sr_df)
        if not mid_col:
            return _make_check("MDR", "WARN",
                             "Reason: No 'Merchant ID' column in settlement CSV.",
                             expected="Merchant ID column", actual="Missing")
        target = _normalize_mid(eb_mid)
        txns = sr_df[sr_df[mid_col].apply(_normalize_mid) == target]
        if txns.empty:
            return _make_check("MDR", "WARN",
                             f"Reason: No transactions found for EB MID {eb_mid}. MDR cannot be calculated without transaction data.",
                             expected="Transactions to calculate MDR", actual="No transactions")

        details = []
        mismatches = []
        all_match = True
        for txn_type, exp_key in [('UPI', 'upi'), ('Credit Card', 'cc'), ('Debit Card', 'dc')]:
            type_txns = txns[txns['Transaction Type'].astype(str).str.lower().str.contains(txn_type.lower())]
            if type_txns.empty:
                continue
            total_amount = pd.to_numeric(type_txns['Transaction Amount'], errors='coerce').sum()
            total_charge = pd.to_numeric(type_txns['Transaction Service Charge'], errors='coerce').sum()
            if total_amount <= 0:
                continue
            actual_mdr = (total_charge / total_amount) * 100
            expected = expected_rates.get(exp_key, '')
            try:
                exp_val = float(expected) if expected and expected != 'nan' else None
            except (ValueError, TypeError):
                exp_val = None
            if exp_val is not None:
                match = abs(actual_mdr - exp_val) < 0.05
                if not match:
                    all_match = False
                    mismatches.append(f"{txn_type}: actual {actual_mdr:.2f}% != expected {exp_val:.2f}%")
                details.append(f"{txn_type}: {actual_mdr:.2f}% vs {exp_val:.2f}% {'✅' if match else '❌'}")

        if not details:
            return _make_check("MDR", "WARN",
                             "Reason: Expected rates not found in sheet, or no matching transaction types (UPI/CC/DC).",
                             expected="UPI/CC/DC rates in sheet", actual="No data")

        if all_match:
            return _make_check("MDR", "PASS",
                             "All MDR rates match with the values in sheet.",
                             expected="Rates as per sheet",
                             actual=" | ".join(details))
        else:
            return _make_check("MDR", "FAIL",
                             f"Reason: MDR rates do not match the sheet. Mismatches: {'; '.join(mismatches)}",
                             expected="Rates as per sheet",
                             actual=" | ".join(details))
    except Exception as e:
        logger.exception(f"MDR check failed: {merchant_name}")
        return _make_check("MDR", "FAIL",
                         f"Reason: An error occurred during MDR calculation — {str(e)[:80]}")


async def check_account_number(sr_df, eb_mid, merchant_name):
    """Check 3: Account Number — EB settlement vs cancelled cheque OCR."""
    try:
        from modules.cheque_verifier import verify_cheque

        # Get EB account from settlement report (match by EB MID, robust)
        eb_account = ''
        if not sr_df.empty and eb_mid:
            mid_col = _find_mid_column(sr_df)
            if mid_col:
                target = _normalize_mid(eb_mid)
                txns = sr_df[sr_df[mid_col].apply(_normalize_mid) == target]
                if not txns.empty:
                    eb_account = str(txns.iloc[0].get('Settlement Account Number', '')).replace('.0', '').strip()
                    eb_account = re.sub(r'[=""]', '', eb_account).strip()

        # Get cheque account from Drive OCR
        cheque_result = verify_cheque(merchant_name)
        cheque_account = cheque_result.get('account_number', '')

        # Search EB account in raw OCR text (exact match only)
        if eb_account and cheque_account != eb_account:
            from modules.cheque_verifier import _find_merchant_folder, _find_cheque_file, _download_file, _ocr_extract
            try:
                folder = _find_merchant_folder(merchant_name)
                if folder:
                    cheque_file = _find_cheque_file(folder['id'])
                    if cheque_file:
                        content = _download_file(cheque_file['id'])
                        raw_text = _ocr_extract(content, cheque_file['mimeType'])
                        clean_text = raw_text.replace(' ', '')
                        if eb_account in clean_text:
                            cheque_account = eb_account
            except Exception:
                pass

        # Check all extracted numbers for exact match only
        if eb_account and cheque_account != eb_account:
            all_accounts = cheque_result.get('account_numbers', []) if isinstance(cheque_result, dict) else []
            for acc in all_accounts:
                if acc == eb_account:
                    cheque_account = acc
                    break

        if cheque_account and eb_account:
            if cheque_account == eb_account:
                return _make_check("Account Number", "PASS",
                                 f"Cheque account and EB settlement account match: {cheque_account}",
                                 expected=f"EB Settlement: {eb_account}",
                                 actual=f"Cancelled Cheque: {cheque_account}")

            return _make_check("Account Number", "FAIL",
                             f"Reason: Account number mismatch. EB Settlement shows {eb_account} but cancelled cheque shows {cheque_account}. This discrepancy must be investigated.",
                             expected=f"EB Settlement: {eb_account}",
                             actual=f"Cancelled Cheque: {cheque_account}")
        elif cheque_account:
            return _make_check("Account Number", "WARN",
                             f"Reason: EB settlement has no account data (no transactions yet). Cheque account found: {cheque_account}",
                             expected="EB Settlement account to match",
                             actual=f"Cheque: {cheque_account} (File: {cheque_result.get('file_name', '')})")
        elif eb_account:
            return _make_check("Account Number", "WARN",
                             f"Reason: Cancelled cheque not found in Google Drive folder for this merchant. EB account: {eb_account}",
                             expected=f"Cheque to match with EB {eb_account}",
                             actual=f"Drive: {cheque_result.get('message', '')[:80]}")
        else:
            return _make_check("Account Number", "WARN",
                             f"Reason: No data available from EB settlement or Drive cheque. {cheque_result.get('message', '')[:60]}",
                             expected="Both EB account + Cheque",
                             actual="Neither found")
    except Exception as e:
        logger.exception(f"Account check failed: {merchant_name}")
        return _make_check("Account Number", "FAIL", f"Error: {str(e)[:80]}")


async def check_salt_key(gk_page, merchant_name, sheet_key, sheet_salt):
    """Check 4: SALT & KEY — GK Dashboard Terminals vs Sheet."""
    try:
        # Edit Easebuzz terminal
        eb = await gk_page.evaluate("""() => {
            for (const row of document.querySelectorAll('tr')) {
                if (row.textContent.includes('Easebuzz')) {
                    const tds = row.querySelectorAll('td');
                    const last = tds[tds.length-1];
                    const icon = last.querySelector('svg,button,span,a') || last;
                    const r = icon.getBoundingClientRect();
                    if (r.width > 0) return {x: r.left+r.width/2, y: r.top+r.height/2};
                }
            } return null;
        }""")

        if not eb:
            return _make_check("SALT & KEY", "WARN",
                             "Reason: Easebuzz terminal not found in GK Dashboard. This merchant has not configured Easebuzz yet.",
                             expected="Easebuzz terminal in GK Dashboard",
                             actual="Not configured")

        await gk_page.mouse.click(eb['x'], eb['y'])
        await asyncio.sleep(10)

        # Extract SALT & KEY from edit form
        fields = await gk_page.evaluate("""() => {
            const items = [];
            document.querySelectorAll('input').forEach(inp => {
                const r = inp.getBoundingClientRect();
                if (r.width > 50 && r.y > 100)
                    items.push({ph: (inp.placeholder || '').toLowerCase(), value: inp.value});
            });
            return items;
        }""")

        gk_key = ''
        gk_salt = ''
        for f in fields:
            if f['ph'] == 'enter salt':
                gk_salt = f['value']
            if f['ph'] == 'enter merchant key':
                gk_key = f['value']

        if gk_key and sheet_key:
            key_match = gk_key == sheet_key
            salt_match = gk_salt == sheet_salt
            if key_match and salt_match:
                return _make_check("SALT & KEY", "PASS",
                                 "KEY and SALT in GK Dashboard match the values in sheet.",
                                 expected=f"Sheet KEY: {sheet_key} | SALT: {sheet_salt}",
                                 actual=f"GK KEY: {gk_key} | SALT: {gk_salt}")
            else:
                reasons = []
                if not key_match:
                    reasons.append(f"KEY mismatch (GK: {gk_key}, Sheet: {sheet_key})")
                if not salt_match:
                    reasons.append(f"SALT mismatch (GK: {gk_salt}, Sheet: {sheet_salt})")
                return _make_check("SALT & KEY", "FAIL",
                                 f"Reason: {'; '.join(reasons)}. The credentials must be corrected.",
                                 expected=f"Sheet KEY: {sheet_key} | SALT: {sheet_salt}",
                                 actual=f"GK KEY: {gk_key} | SALT: {gk_salt}")
        else:
            if not gk_key and not gk_salt:
                reason = "Could not extract SALT/KEY from GK Dashboard edit form (fields appear empty)."
            elif not sheet_key:
                reason = "No SALT/KEY entry found in sheet for this merchant."
            else:
                reason = "Partial data available — cannot compare."
            return _make_check("SALT & KEY", "WARN",
                             f"Reason: {reason}",
                             expected=f"Sheet KEY: {sheet_key or 'N/A'}",
                             actual=f"GK KEY: {gk_key or 'Empty'} | SALT: {gk_salt or 'Empty'}")
    except Exception as e:
        logger.exception(f"SALT & KEY check failed: {merchant_name}")
        return _make_check("SALT & KEY", "FAIL", f"Error: {str(e)[:80]}")


async def check_vpa(gk_page, merchant_name):
    """Check 5: VPA — GK Dashboard Orders → Payment Link."""
    try:
        # Click Orders in sidebar
        await gk_page.evaluate("""() => {
            for (const el of document.querySelectorAll('.ant-menu-title-content')) {
                if (el.textContent.trim() === 'Orders') { el.click(); return; }
            }
        }""")
        await asyncio.sleep(10)

        # Click first order
        first_order = await gk_page.evaluate("""() => {
            for (const a of document.querySelectorAll('a')) {
                if (a.textContent.trim().startsWith('KWIK')) {
                    const r = a.getBoundingClientRect();
                    if (r.width > 0) return {x: r.left+r.width/2, y: r.top+r.height/2};
                }
            } return null;
        }""")

        if not first_order:
            return _make_check("VPA / Webhook", "WARN",
                             "Reason: No orders found in GK Dashboard for this merchant. VPA cannot be verified without a UPI order.",
                             expected="At least one UPI order",
                             actual="No orders found")

        await gk_page.mouse.click(first_order['x'], first_order['y'])
        await asyncio.sleep(8)

        # Extract UPI link
        body = await gk_page.inner_text('body')
        upi_links = re.findall(r'upi://[^\s<>"]+', body)

        if not upi_links:
            # Scroll payment table
            await gk_page.evaluate("""() => {
                document.querySelectorAll('.ant-table-body, .ant-table-content').forEach(t => {
                    t.scrollLeft = t.scrollWidth;
                });
            }""")
            await asyncio.sleep(3)
            body = await gk_page.inner_text('body')
            upi_links = re.findall(r'upi://[^\s<>"]+', body)

        if upi_links:
            vpa_match = re.search(r'pa=([^&]+)', upi_links[0])
            if vpa_match:
                vpa = vpa_match.group(1)
                merchant_lower = merchant_name.lower().replace(' ', '')
                has_name = any(p in vpa.lower() for p in [merchant_lower[:6], merchant_lower[:5]])
                if has_name:
                    return _make_check("VPA / Webhook", "PASS",
                                     f"Merchant name found in VPA: {vpa}",
                                     expected=f"VPA containing '{merchant_name}'",
                                     actual=f"VPA: {vpa}")
                else:
                    return _make_check("VPA / Webhook", "WARN",
                                     f"Reason: VPA is {vpa} but it does not contain the merchant name '{merchant_name}'. Please verify manually.",
                                     expected=f"VPA with merchant identifier",
                                     actual=f"VPA: {vpa}")

        return _make_check("VPA / Webhook", "WARN",
                         "Reason: No UPI payment link found in the order. The order may be COD or a non-UPI payment.",
                         expected="UPI payment link in order",
                         actual="No UPI link found")
    except Exception as e:
        logger.exception(f"VPA check failed: {merchant_name}")
        return _make_check("VPA / Webhook", "FAIL", f"Error: {str(e)[:80]}")


# ─── GK DASHBOARD HELPERS ────────────────────────────────

async def _gk_login(page):
    """Login to GK Dashboard with auto OTP. Handles both old (2-step) and new (1-step) login UI."""
    logger.info(f"    GK login: navigating to {GK_DASHBOARD_URL}/login")
    await page.goto(f"{GK_DASHBOARD_URL}/login")
    await asyncio.sleep(5)
    logger.info(f"    GK login: page loaded, URL={page.url}")

    # Fill email
    email_input = await page.query_selector('input[type="email"]')
    if email_input:
        await email_input.fill(GK_EMAIL)
        await asyncio.sleep(1)
        logger.info(f"    GK login: email filled")
    else:
        logger.warning(f"    GK login: email input NOT found")

    # Try to fill password on the SAME page (new GK UI)
    pwd_input = await page.query_selector('input[type="password"]')
    if pwd_input and await pwd_input.is_visible():
        logger.info(f"    GK login: password field visible (new 1-step UI)")
        await pwd_input.fill(GK_PASS)
        await asyncio.sleep(1)
        # Click Next once — both fields filled
        await page.click('button:has-text("Next")')
        await asyncio.sleep(5)
        logger.info(f"    GK login: clicked Next, URL={page.url}")
    else:
        logger.info(f"    GK login: no password field on first page (old 2-step UI)")
        # Old 2-step UI: click Next, then fill password on next screen
        await page.click('button:has-text("Next")')
        await asyncio.sleep(5)
        logger.info(f"    GK login: clicked first Next, URL={page.url}")
        pwd_input = await page.query_selector('input[type="password"]')
        if pwd_input and await pwd_input.is_visible():
            await pwd_input.fill(GK_PASS)
            await page.click('button:has-text("Next")')
            await asyncio.sleep(5)
            logger.info(f"    GK login: filled password and clicked Next, URL={page.url}")
        else:
            logger.warning(f"    GK login: password field NOT visible after first Next, URL={page.url}")

    if "verify-otp" in page.url:
        otp = _fetch_gk_otp()
        logger.info(f"GK OTP: {otp}")
        if otp:
            visible_inputs = []
            for inp in await page.query_selector_all('input'):
                if await inp.is_visible():
                    visible_inputs.append(inp)
            if len(visible_inputs) >= len(otp):
                for i, digit in enumerate(otp):
                    await visible_inputs[i].click()
                    await visible_inputs[i].fill(digit)
                    await asyncio.sleep(0.1)
            elif visible_inputs:
                await visible_inputs[0].click()
                await visible_inputs[0].fill(otp)
            await page.click('button:has-text("Next")')
            await asyncio.sleep(15)
    if "verify-otp" in page.url or "login" in page.url:
        try:
            await page.screenshot(path='config/gk_login_failed.png', full_page=True)
            logger.error(f"    GK login FAILED at URL: {page.url} — screenshot saved to config/gk_login_failed.png")
        except Exception:
            pass
        return False
    logger.info(f"    GK login: success, landed at URL={page.url}")
    return True


async def _gk_navigate_terminals(page):
    """Navigate: Kwik Payment → Settings → Terminals."""
    for name in ['Kwik Payment', 'Settings', 'Terminals']:
        await page.evaluate("""(n) => {
            for (const el of document.querySelectorAll('.ant-menu-title-content')) {
                if (el.textContent.trim() === n) { el.click(); return; }
            }
        }""", name)
        await asyncio.sleep(4)


async def _gk_ensure_logged_in(page):
    """If session died (redirected to login), re-login and navigate to Terminals."""
    if "login" in page.url or "verify-otp" in page.url:
        logger.info("Session expired — re-logging in")
        if await _gk_login(page):
            await _gk_navigate_terminals(page)
            return True
        return False
    return True


async def _gk_switch_merchant(page, merchant_name, mid=''):
    """Switch merchant in GK Dashboard. Searches by MID (primary) then merchant name (fallback)."""
    logger.info(f"    GK switch: starting for merchant='{merchant_name}', mid='{mid}', URL={page.url}")
    try:
        await page.keyboard.press('Escape')
        await asyncio.sleep(0.5)
    except Exception:
        pass

    if not await _gk_ensure_logged_in(page):
        logger.warning(f"Could not re-login before switching to {merchant_name}")
        return False

    # Newer GK UI: merchant switcher is opened via the header dropdown showing the
    # current merchant name (e.g. "gokwikproduction2"), not a "Switch merchant" link.
    # Save a "before" screenshot + dump top-right candidates for debug.
    try:
        await page.screenshot(path='config/gk_switch_before.png', full_page=True)
    except Exception:
        pass
    try:
        topright = await page.evaluate("""() => {
            const out = [];
            for (const el of document.querySelectorAll('*')) {
                if (!el || el.children.length > 6) continue;
                const r = el.getBoundingClientRect();
                if (r.y < 0 || r.y > 100 || r.x < window.innerWidth * 0.55) continue;
                if (r.width < 40 || r.width > 400 || r.height < 18 || r.height > 80) continue;
                const text = (el.textContent || '').trim();
                if (!text || text.length > 60) continue;
                out.push({tag: el.tagName, cls: (el.className || '').toString().substring(0,80), text: text.substring(0,60), x: Math.round(r.left + r.width/2), y: Math.round(r.top + r.height/2)});
                if (out.length > 12) break;
            }
            return out;
        }""")
        logger.info(f"    GK switch: top-right header candidates ({len(topright)}):")
        for c in topright:
            logger.info(f"      <{c.get('tag')}> cls='{c.get('cls')}' text='{c.get('text')}' @({c.get('x')},{c.get('y')})")
    except Exception as e:
        logger.warning(f"    GK switch: header dump failed: {e}")

    clicked = False

    # Strategy 1 — explicit "Switch merchant" text (older UI / sidebar / dropdown menu item)
    for selector_text in ['Switch merchant', 'Switch Merchant', 'Change merchant']:
        try:
            await page.locator(f'text={selector_text}').first.click(timeout=2000)
            logger.info(f"    GK switch: clicked '{selector_text}' link (strategy 1)")
            clicked = True
            break
        except Exception:
            continue

    # Strategy 2 — click the header merchant dropdown (top-right corner). Try clicking
    # whichever element in the top-right has text + appears clickable, then look for
    # a "Switch Merchant" option in the resulting menu, OR detect a modal opening.
    if not clicked:
        try:
            opened = await page.evaluate("""() => {
                const sels = [
                    '.ant-dropdown-trigger',
                    '[class*="merchant"]',
                    '[class*="dropdown"]',
                    '[class*="select"]',
                    '[class*="profile"]',
                    '[class*="avatar"]',
                    '[role="button"]',
                    'button',
                    'header *',
                ];
                const candidates = [];
                const seen = new Set();
                for (const sel of sels) {
                    for (const el of document.querySelectorAll(sel)) {
                        if (seen.has(el)) continue;
                        seen.add(el);
                        const r = el.getBoundingClientRect();
                        if (r.y < 0 || r.y > 100 || r.x < window.innerWidth * 0.55) continue;
                        if (r.width < 40 || r.width > 400 || r.height < 18 || r.height > 80) continue;
                        const text = (el.textContent || '').trim();
                        if (text.length > 80) continue;
                        const cls = (el.className || '').toString();
                        candidates.push({x: Math.round(r.left + r.width/2), y: Math.round(r.top + r.height/2), text, sel, cls: cls.substring(0,60), tag: el.tagName});
                    }
                }
                // Order: rightmost first (the merchant indicator is usually furthest right).
                candidates.sort((a, b) => b.x - a.x);
                return candidates.slice(0, 6);
            }""")
            logger.info(f"    GK switch: trying {len(opened)} top-right candidates (strategy 2)")
            for cand in opened:
                logger.info(f"    GK switch: clicking <{cand.get('tag')}> '{cand.get('text','')[:40]}' @({cand['x']},{cand['y']}) sel='{cand.get('sel')}'")
                try:
                    await page.mouse.click(cand['x'], cand['y'])
                    await asyncio.sleep(2)
                except Exception as e:
                    logger.warning(f"      click failed: {e}")
                    continue
                # Check if a menu/modal opened
                for opt in ['Switch Merchant', 'Switch merchant', 'Change Merchant', 'Change merchant']:
                    try:
                        await page.locator(f'text={opt}').first.click(timeout=1500)
                        logger.info(f"    GK switch: clicked '{opt}' from header menu")
                        clicked = True
                        break
                    except Exception:
                        continue
                if clicked:
                    break
                # OR — modal opened directly with search input
                try:
                    modal_open = await page.query_selector('.ant-modal-body, .gk-text-input')
                    if modal_open:
                        logger.info(f"    GK switch: candidate opened modal directly")
                        clicked = True
                        break
                except Exception:
                    pass
                # Otherwise, dismiss any partially-opened popup and try next candidate
                try:
                    await page.keyboard.press('Escape')
                    await asyncio.sleep(0.5)
                except Exception:
                    pass
        except Exception as e:
            logger.warning(f"    GK switch: header dropdown loop failed: {e}")

    if not clicked:
        try:
            await page.screenshot(path='config/gk_switch_failed.png', full_page=True)
            logger.warning(f"    GK switch: button not clickable for {merchant_name} (URL={page.url}) — screenshot saved to config/gk_switch_failed.png")
        except Exception:
            logger.warning(f"    GK switch: button not clickable for {merchant_name} (URL={page.url})")
        return False
    await asyncio.sleep(5)

    gk_input = await page.query_selector('.gk-text-input')
    if not gk_input:
        logger.warning(f"Search input not found for {merchant_name}")
        try:
            await page.keyboard.press('Escape')
        except Exception:
            pass
        return False

    # Search terms: MID first (primary — most reliable), then merchant name as fallback
    search_terms = []
    mid_str = str(mid).strip() if mid else ''
    if mid_str:
        search_terms.append(('mid', mid_str))
    if merchant_name:
        search_terms.append(('name', merchant_name))
        if len(merchant_name) > 5:
            search_terms.append(('name', merchant_name[:5]))

    logger.info(f"    GK switch: search plan = {search_terms}")

    clicked = False
    for term_type, term in search_terms:
        try:
            logger.info(f"    GK switch: trying {term_type}='{term}'")
            await page.click('.gk-text-input', timeout=10000, force=True)
            await asyncio.sleep(0.5)
            await page.keyboard.press('Control+a')
            await page.keyboard.press('Backspace')
            await asyncio.sleep(0.5)
            await page.fill('.gk-text-input', term, timeout=10000)
            await asyncio.sleep(4)

            # Find result — MID match looks for the MID anywhere with non-digit boundaries.
            # Format examples seen: "Murphy (17069)shopify", "Murphy - 17069 - shopify"
            r = await page.evaluate("""({term, termType, name}) => {
                const lower = (name || '').toLowerCase();
                const lowerNoSpace = lower.replace(/\\s+/g, '');
                // Boundary-aware MID match: digit sequence not adjacent to other digits
                const midRegex = new RegExp('(^|[^0-9])' + term + '([^0-9]|$)');
                const candidates = [];
                for (const el of document.querySelectorAll('.ant-modal-body div, .ant-modal-body label, .ant-modal-body span')) {
                    const text = el.textContent.trim();
                    if (!text || text.length > 200) continue;
                    const textLower = text.toLowerCase();
                    const textNoSpace = textLower.replace(/\\s+/g, '');
                    let match = false;
                    if (termType === 'mid') {
                        match = midRegex.test(text);
                    } else {
                        match = lower && (textLower.includes(lower) || textNoSpace.includes(lowerNoSpace));
                    }
                    if (match && el.children.length <= 5) {
                        const r = el.getBoundingClientRect();
                        if (r.width > 100 && r.height > 15 && r.height < 80 && r.y > 200) {
                            candidates.push({x: r.left + 20, y: r.top + r.height / 2, text: text.substring(0, 80)});
                        }
                    }
                }
                return candidates.length > 0 ? candidates[0] : {candidates: 0, _debug: 'no match'};
            }""", {"term": term, "termType": term_type, "name": merchant_name})

            if r and r.get('x') is not None:
                logger.info(f"    GK switch: found merchant via {term_type}='{term}': {r.get('text', '')}")
                await page.mouse.click(r['x'], r['y'])
                await asyncio.sleep(2)
                clicked = True
                break
            else:
                logger.warning(f"    GK switch: no match for {term_type}='{term}' in modal")
        except Exception as e:
            logger.warning(f"    GK switch: search '{term}' ({term_type}) raised: {e}")
            continue

    if not clicked:
        logger.warning(f"    GK switch: no search result found for {merchant_name} (tried: {search_terms})")
        try:
            await page.screenshot(path='config/gk_switch_no_result.png', full_page=True)
            logger.warning(f"    GK switch: screenshot saved to config/gk_switch_no_result.png")
        except Exception:
            pass
        try:
            await page.keyboard.press('Escape')
            await asyncio.sleep(2)
        except Exception:
            pass
        return False

    # Click Set Merchant
    try:
        await page.evaluate("()=>document.querySelectorAll('button').forEach(b=>{if(b.textContent.trim()==='Set Merchant')b.click()})")
        await asyncio.sleep(10)
    except Exception as e:
        logger.warning(f"Set Merchant click failed: {e}")
        return False
    return True


# ─── EB PARTNER PORTAL HELPERS ────────────────────────────

async def _eb_login(page):
    """Login to EB Partner Portal with retry."""
    for attempt in range(1, 4):
        try:
            logger.info(f"  EB login attempt {attempt}/3")
            await page.goto(EB_LOGIN_URL, wait_until="domcontentloaded")
            await asyncio.sleep(5)

            email = await page.wait_for_selector('input[name="email"]', timeout=15000)
            await email.fill(EB_EMAIL)
            pwd = await page.wait_for_selector('input[name="password"]', timeout=10000)
            await pwd.fill(EB_PASS)
            await page.click('button:has-text("Login")')

            # Poll the URL for up to 30 seconds — login redirect can be slow
            for _ in range(30):
                await asyncio.sleep(1)
                url = page.url
                if "partners.easebuzz.in" in url or "/custom-reports" in url or "/dashboard" in url:
                    logger.info(f"  EB login OK (URL: {url[:60]})")
                    return True
                if "login" not in url:
                    logger.info(f"  EB login OK (left login page; URL: {url[:60]})")
                    return True

            logger.warning(f"  EB login attempt {attempt} timed out — final URL: {page.url[:80]}")
        except Exception as e:
            logger.warning(f"  EB login attempt {attempt} error: {str(e)[:100]}")

        if attempt < 3:
            await asyncio.sleep(5)
    return False


async def _eb_navigate_to_settlements(page):
    """Navigate to Custom Reports → Merchant Settlements tab."""
    await page.goto(f"{EB_PARTNER_URL}/custom-reports", wait_until="domcontentloaded")
    await asyncio.sleep(8)
    await page.evaluate("""() => {
        document.querySelectorAll('div').forEach(el => {
            if (el.textContent.trim() === 'Merchant Settlements' && el.children.length === 0) {
                const r = el.getBoundingClientRect();
                if (r.width > 0 && r.height < 50) el.click();
            }
        });
    }""")
    await asyncio.sleep(5)


async def _eb_generate_one_merchant_report(page, mid, report_name, merchant_name=''):
    """Generate and download settlement report for ONE merchant by NAME. Returns path to CSV or None."""
    # Open Generate form
    await page.evaluate("""() => {
        document.querySelectorAll('button').forEach(btn => {
            if (btn.textContent.includes('Generate New')) {
                const r = btn.getBoundingClientRect();
                if (r.width > 0) btn.click();
            }
        });
    }""")
    await asyncio.sleep(5)

    # Report Name
    name_input = await page.query_selector('input[placeholder="Please enter report name"]')
    if name_input:
        await name_input.fill(report_name)

    # Find the merchant dropdown wrapper (the one with "Search Merchant" placeholder OR with merchant tags)
    # First clear ALL existing selections (close pills) — this prevents prior runs leaking into this report
    cleared_count = await page.evaluate("""() => {
        // Find merchant select wrapper. It's typically the one whose placeholder says 'Search Merchant'
        // OR which already has selected items showing merchant tags.
        // Clear by clicking each tag's remove (×) button.
        let total = 0;
        const wrappers = document.querySelectorAll('.ant-select-multiple, .ant-select');
        for (const w of wrappers) {
            const ph = w.querySelector('.ant-select-selection-placeholder');
            const isMerchant = (ph && ph.textContent.includes('Search Merchant'))
                || w.querySelector('.ant-select-selection-overflow-item .ant-select-selection-item-content');
            if (!isMerchant) continue;
            const removes = w.querySelectorAll('.ant-select-selection-item-remove, .anticon-close, [aria-label="close"]');
            for (const r of removes) { try { r.click(); total++; } catch(e){} }
        }
        return total;
    }""")
    if cleared_count > 0:
        logger.info(f"  MID {mid}: cleared {cleared_count} previously-selected merchant(s)")
        await asyncio.sleep(1)

    # Find merchant dropdown wrapper (parent .ant-select that contains "Search Merchant" placeholder)
    # Prefer MID over name: MID is unique and avoids typos / spelling variants.
    # Falls back to merchant_name only when MID is missing.
    search_term = (str(mid).strip() or (merchant_name or '').strip())
    logger.info(f"  EB search: using {'MID' if str(mid).strip() else 'name'} → '{search_term}'")
    try:
        # Click the SELECTOR div (the actual clickable wrapper) with force=True
        # to bypass Ant Design's overflow wrapper intercepting pointer events
        merchant_selector = page.locator(
            '.ant-select:has(.ant-select-selection-placeholder:text("Search Merchant")) .ant-select-selector'
        ).first
        await merchant_selector.click(timeout=10000, force=True)
        await asyncio.sleep(2)
    except Exception as e:
        logger.warning(f"  Could not click merchant dropdown selector: {e}")
        # Fallback: try clicking the placeholder area via mouse coords
        try:
            box = await page.evaluate("""() => {
                for (const sel of document.querySelectorAll('.ant-select-selector')) {
                    const ph = sel.querySelector('.ant-select-selection-placeholder');
                    if (ph && ph.textContent.includes('Search Merchant')) {
                        const r = sel.getBoundingClientRect();
                        return {x: r.left + 30, y: r.top + r.height/2};
                    }
                }
                return null;
            }""")
            if box:
                await page.mouse.click(box['x'], box['y'])
                await asyncio.sleep(2)
                logger.info(f"  Used mouse click fallback at {box}")
            else:
                return None
        except Exception:
            return None

    # Now type into whatever input received focus.
    # Try multiple input selectors in order of specificity.
    typed = False
    for selector in [
        '.ant-select-open input.ant-select-selection-search-input',
        '.ant-select-focused input.ant-select-selection-search-input',
        'input.ant-select-selection-search-input:focus',
        'input[placeholder*="Search Merchant" i]',
    ]:
        try:
            inp = page.locator(selector).first
            if await inp.count() > 0:
                await inp.fill(search_term, timeout=5000)
                typed = True
                logger.info(f"  Typed '{search_term}' via selector: {selector}")
                break
        except Exception:
            continue

    if not typed:
        # Last resort: keyboard.type into focused element
        await page.keyboard.type(search_term, delay=100)
        logger.info(f"  Typed '{search_term}' via keyboard fallback")
    await asyncio.sleep(3)

    # Click matching option — match precisely by MID (unique) first, then by name with word-boundary check
    # to avoid false positives like "Kalai" matching "shevalonvarmakalai".
    selected = await page.evaluate("""({name, mid}) => {
        const nameLower = (name || '').toLowerCase().trim();
        const midStr = (mid || '').toString().trim();

        // PRIMARY: match by MID using the exact " - <mid> - " pattern in EB's "Name - MID - email" format
        const matchesByMid = (text) => {
            if (!midStr) return false;
            return text.includes(' - ' + midStr + ' - ') || text.includes('- ' + midStr + ' -');
        };
        // SECONDARY: name match — must be at start, or preceded/followed by non-letter (word boundary)
        const matchesByName = (text) => {
            if (!nameLower) return false;
            const t = text.toLowerCase();
            const idx = t.indexOf(nameLower);
            if (idx === -1) return false;
            const before = idx === 0 ? '' : t[idx - 1];
            const after = idx + nameLower.length >= t.length ? '' : t[idx + nameLower.length];
            const isWordBoundary = (c) => !c || /[^a-z0-9]/.test(c);
            return isWordBoundary(before) && isWordBoundary(after);
        };

        const tryClick = (collector, label) => {
            // First pass: MID match
            for (const el of document.querySelectorAll(collector)) {
                if (matchesByMid(el.textContent)) {
                    const node = el.closest('.ant-select-tree-treenode') || el.closest('.ant-select-tree-node-content-wrapper') || el;
                    const cb = node.querySelector('.ant-select-tree-checkbox') || node.querySelector('.ant-checkbox-input, input[type="checkbox"]');
                    (cb || node).click();
                    return label + ' (mid): ' + el.textContent.trim().substring(0, 80);
                }
            }
            // Second pass: name with word-boundary
            for (const el of document.querySelectorAll(collector)) {
                if (matchesByName(el.textContent)) {
                    const node = el.closest('.ant-select-tree-treenode') || el.closest('.ant-select-tree-node-content-wrapper') || el;
                    const cb = node.querySelector('.ant-select-tree-checkbox') || node.querySelector('.ant-checkbox-input, input[type="checkbox"]');
                    (cb || node).click();
                    return label + ' (name): ' + el.textContent.trim().substring(0, 80);
                }
            }
            return null;
        };

        return tryClick('.ant-select-tree-title', 'tree')
            || tryClick('.ant-select-item-option', 'option')
            || tryClick('label, .ant-checkbox-wrapper, [role="option"]', 'label');
    }""", {"name": merchant_name, "mid": str(mid)})

    if not selected:
        # Retry once — re-click dropdown and re-type. If the first attempt used the MID
        # and didn't find a match, fall back to the merchant name (handles cases where
        # EB's search box doesn't index MIDs the way we expect).
        retry_term = (merchant_name or '').strip() if str(mid).strip() else search_term
        if retry_term and retry_term != search_term:
            logger.info(f"  Retry: switching from MID '{search_term}' to NAME '{retry_term}' for '{merchant_name}'")
        else:
            logger.info(f"  Retry: re-opening dropdown for '{merchant_name}'")
        try:
            await page.keyboard.press('Escape')
            await asyncio.sleep(1)
            box = await page.evaluate("""() => {
                for (const sel of document.querySelectorAll('.ant-select-selector')) {
                    const ph = sel.querySelector('.ant-select-selection-placeholder');
                    if (ph && ph.textContent.includes('Search Merchant')) {
                        const r = sel.getBoundingClientRect();
                        return {x: r.left + 30, y: r.top + r.height/2};
                    }
                }
                return null;
            }""")
            if box:
                await page.mouse.click(box['x'], box['y'])
                await asyncio.sleep(2)
                await page.keyboard.type(retry_term or search_term, delay=120)
                await asyncio.sleep(4)
                # Try matching again with same logic
                selected = await page.evaluate("""({name, mid}) => {
                    const nameLower = (name || '').toLowerCase().trim();
                    const midStr = (mid || '').toString().trim();
                    const matchesByMid = (text) => midStr && (text.includes(' - ' + midStr + ' - ') || text.includes('- ' + midStr + ' -'));
                    const matchesByName = (text) => {
                        if (!nameLower) return false;
                        const t = text.toLowerCase();
                        const idx = t.indexOf(nameLower);
                        if (idx === -1) return false;
                        const before = idx === 0 ? '' : t[idx - 1];
                        const after = idx + nameLower.length >= t.length ? '' : t[idx + nameLower.length];
                        return (!before || /[^a-z0-9]/.test(before)) && (!after || /[^a-z0-9]/.test(after));
                    };
                    for (const el of document.querySelectorAll('.ant-select-tree-title, .ant-select-item-option, label')) {
                        if (matchesByMid(el.textContent) || matchesByName(el.textContent)) {
                            const node = el.closest('.ant-select-tree-treenode') || el.closest('.ant-select-tree-node-content-wrapper') || el;
                            const cb = node.querySelector('.ant-select-tree-checkbox') || node.querySelector('.ant-checkbox-input, input[type="checkbox"]');
                            (cb || node).click();
                            return 'retry: ' + el.textContent.trim().substring(0, 80);
                        }
                    }
                    return null;
                }""", {"name": merchant_name, "mid": str(mid)})
        except Exception as e:
            logger.warning(f"  Retry error: {e}")

    if not selected:
        # Diagnostic — log what was visible in the dropdown
        visible_opts = await page.evaluate("""() => {
            const out = [];
            for (const el of document.querySelectorAll('.ant-select-item, .ant-select-tree-title, label, [role="option"]')) {
                const r = el.getBoundingClientRect();
                if (r.width > 0 && r.height > 0) {
                    const t = el.textContent.trim();
                    if (t && t.length < 200) out.push(t);
                }
                if (out.length >= 5) break;
            }
            return out;
        }""")
        logger.warning(f"  Merchant '{merchant_name}' (MID {mid}): NOT FOUND in EB merchant dropdown")
        if visible_opts:
            logger.warning(f"  Visible dropdown options (sample): {visible_opts}")
        try:
            await page.keyboard.press('Escape')
        except Exception:
            pass
        return None

    logger.info(f"  Merchant '{merchant_name}' (MID {mid}): selected via {selected}")
    # Close dropdown
    await page.mouse.click(770, 280)
    await asyncio.sleep(2)

    # Date range — last 7 days
    today = datetime.now()
    start = today - timedelta(days=7)
    for ph, date in [("Start date", start), ("End date", today)]:
        inp = await page.query_selector(f'input[placeholder="{ph}"]')
        if inp:
            await inp.click()
            await asyncio.sleep(1)
            await page.keyboard.press('Control+a')
            await page.keyboard.type(date.strftime('%Y-%m-%d'), delay=50)
            await page.keyboard.press('Enter')
            await asyncio.sleep(2)

    # Generate
    await page.evaluate("""() => {
        document.querySelectorAll('button').forEach(btn => {
            if (btn.textContent.trim() === 'Generate') btn.click();
        });
    }""")
    await asyncio.sleep(8)

    # Wait for OUR specific report row to show "Success" status (max 3 minutes)
    logger.info(f"  Waiting for report '{report_name}' to be ready …")
    row_ready = False
    for poll in range(60):  # 60 × 3s = 180s max
        await asyncio.sleep(3)
        # Refresh the page list periodically to pick up new reports
        if poll == 5 or poll == 15 or poll == 30:
            await _eb_navigate_to_settlements(page)
            await asyncio.sleep(2)
        status = await page.evaluate("""(name) => {
            const rows = document.querySelectorAll('tr');
            for (const row of rows) {
                const cells = row.querySelectorAll('td');
                for (const cell of cells) {
                    if (cell.textContent.trim() === name) {
                        // Found our row — get its full text to find the status
                        return row.textContent;
                    }
                }
            }
            return null;
        }""", report_name)
        if status:
            if 'Success' in status:
                row_ready = True
                logger.info(f"  Report '{report_name}' is ready (Success)")
                break
            elif 'Failed' in status or 'Error' in status:
                logger.warning(f"  Report '{report_name}' FAILED on EB side")
                return None
            # else: still processing — keep polling

    if not row_ready:
        logger.warning(f"  MID {mid}: report '{report_name}' did not become 'Success' in 3 min")
        return None

    # Click download icon in OUR specific row — match by exact name + click anchor/button with download attribute
    try:
        async with page.expect_download(timeout=60000) as dl:
            clicked = await page.evaluate("""(name) => {
                const rows = document.querySelectorAll('tr');
                for (const row of rows) {
                    const cells = row.querySelectorAll('td');
                    let nameMatched = false;
                    for (const cell of cells) {
                        if (cell.textContent.trim() === name) {
                            nameMatched = true;
                            break;
                        }
                    }
                    if (!nameMatched) continue;

                    // Strategy A: anchor with download attribute or .csv href
                    for (const a of row.querySelectorAll('a')) {
                        const href = a.getAttribute('href') || '';
                        if (a.hasAttribute('download') || href.includes('.csv') || href.includes('download')) {
                            a.click();
                            return 'anchor: ' + (href.substring(0, 60) || 'download attr');
                        }
                    }
                    // Strategy B: download SVG/icon via aria-label or class
                    for (const el of row.querySelectorAll('[aria-label*="ownload" i], [title*="ownload" i], .anticon-download, [class*="download" i]')) {
                        // Skip if it's the eye/view icon
                        const cls = (el.className && el.className.baseVal !== undefined) ? el.className.baseVal : (el.className || '');
                        if (typeof cls === 'string' && (cls.includes('eye') || cls.includes('view'))) continue;
                        el.click();
                        return 'icon: ' + (el.getAttribute('aria-label') || el.getAttribute('title') || cls.substring(0, 40));
                    }
                    // Strategy C: last action icon in the row (download is typically the rightmost action)
                    const actionIcons = row.querySelectorAll('button, [role="button"], svg');
                    if (actionIcons.length >= 2) {
                        const last = actionIcons[actionIcons.length - 1];
                        last.click();
                        return 'last-icon';
                    }
                }
                return null;
            }""", report_name)
            if not clicked:
                logger.warning(f"  MID {mid}: could not find download trigger in row '{report_name}'")
                raise Exception("Download trigger not found")
            logger.info(f"  Download click: {clicked}")
        download = await dl.value
        path = f'/tmp/eb_report_{mid}.csv'
        await download.save_as(path)
        logger.info(f"  Downloaded to {path}")

        # VERIFY the downloaded CSV actually contains the requested MID
        try:
            verify_df = pd.read_csv(path, low_memory=False, nrows=50)
            if 'Merchant ID' in verify_df.columns:
                csv_mids = set(verify_df['Merchant ID'].astype(str).str.strip().unique())
                if str(mid).strip() not in csv_mids:
                    logger.error(f"  MID {mid}: ❌ downloaded CSV contains different MIDs {csv_mids} — DISCARDING")
                    return None
                logger.info(f"  MID {mid}: ✅ verified CSV contains correct MID")
            else:
                logger.warning(f"  MID {mid}: CSV has no 'Merchant ID' column — keeping anyway")
        except Exception as ve:
            logger.warning(f"  MID {mid}: could not verify CSV: {ve}")

        return path
    except Exception as e:
        logger.warning(f"  MID {mid}: download failed: {e}")
        return None


async def _eb_generate_settlement_report(page, merchants=None):
    """Generate per-merchant settlement reports (search by NAME) and combine into one DataFrame.

    `merchants` is a list of dicts: [{'name': 'Mostunderated', 'mid': '271119'}, ...]
    If a merchant is not found in EB, it is logged and skipped.
    """
    merchants = merchants or []
    if not merchants:
        logger.warning("No merchants provided — cannot generate per-merchant reports")
        return pd.DataFrame()

    logger.info(f"Generating per-merchant reports for {len(merchants)} merchants (searching by name)")

    combined_dfs = []
    not_found = []
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')

    for idx, m in enumerate(merchants):
        name = m.get('name', '').strip()
        mid = m.get('mid', '').strip()
        logger.info(f"[{idx+1}/{len(merchants)}] Processing '{name}' (MID {mid})")
        await _eb_navigate_to_settlements(page)
        report_name = f"sanity_{mid or 'noid'}_{timestamp}"
        try:
            csv_path = await _eb_generate_one_merchant_report(page, mid, report_name, merchant_name=name)
            if csv_path and os.path.exists(csv_path):
                df = pd.read_csv(csv_path, low_memory=False)
                logger.info(f"  '{name}': loaded {len(df)} rows")
                combined_dfs.append(df)
            else:
                logger.warning(f"  '{name}': merchant not found / no data — skipping")
                not_found.append(name)
        except Exception as e:
            logger.warning(f"  '{name}': error — {e}")
            not_found.append(name)

    if not_found:
        logger.warning(f"Merchants not found in EB Partner Portal: {not_found}")

    if not combined_dfs:
        logger.error("No per-merchant reports succeeded")
        return pd.DataFrame()

    combined = pd.concat(combined_dfs, ignore_index=True)
    combined.to_csv('config/settlement_report.csv', index=False)
    logger.info(f"Combined settlement report: {len(combined)} rows from {len(combined_dfs)} merchants")
    return combined




# ─── MAIN BATCH CHECK ────────────────────────────────────

async def run_batch_sanity_check(selected_date='', progress=None):
    """Run all 5 checks for all merchants."""
    from modules.sheets_reader import get_sanity_sample, get_salt_key

    def update_progress(stage, merchant='', idx=0, total=0):
        if progress is not None:
            progress['stage'] = stage
            progress['merchant'] = merchant
            progress['merchant_idx'] = idx
            progress['total'] = total

    # 1. Get merchant list from tracking sheet
    # Two categories:
    #   a) Previous day merchants (newly added yesterday)
    #   b) All merchants with any check empty or "No" (incomplete/failed)
    from modules.sheets_reader import get_sanity_tracker

    logger.info("=" * 70)
    logger.info(f"[SETUP] Selected date input: {selected_date or '(none — auto mode)'}")
    logger.info("[SETUP] Reading tracker sheet …")
    tracker = get_sanity_tracker()
    if tracker.empty:
        logger.error("[SETUP] Tracker sheet is empty — aborting")
        return {"error": "Tracking sheet is empty", "results": []}
    logger.info(f"[SETUP] Tracker rows: {len(tracker)} | Columns: {list(tracker.columns)}")

    # Check columns in tracker
    check_cols = []
    for col in tracker.columns:
        cl = col.strip().lower()
        if cl in ('settlement report triggered', 'commercial validation', 'bank accont',
                  'bank accont ', 'web hook - vpa', 'salt and key validation'):
            check_cols.append(col)
    logger.info(f"[SETUP] Check columns detected: {check_cols}")

    # Find merchant name column
    name_col = None
    for col in tracker.columns:
        if col.strip().lower() == 'merchant name':
            name_col = col
            break
    if not name_col:
        logger.error("[SETUP] No 'Merchant Name' column in tracker — aborting")
        return {"error": "No 'Merchant Name' column in tracking sheet", "results": []}
    logger.info(f"[SETUP] Merchant Name column: '{name_col}'")

    # Category A: Previous day merchants — find the date column flexibly
    date_col = None
    for c in tracker.columns:
        if c.strip().lower() == 'date':
            date_col = c
            break
    # Fallback: first column if no explicit "Date" column found
    if not date_col and len(tracker.columns) > 0:
        first_col = tracker.columns[0]
        # Check if first column looks like dates (try parsing a non-null value)
        sample = tracker[first_col].dropna().astype(str).head(3).tolist()
        if sample:
            test_parse = pd.to_datetime(sample[0], format='mixed', dayfirst=True, errors='coerce')
            if pd.notna(test_parse):
                date_col = first_col
                logger.info(f"[FILTER] No 'Date' header — auto-detected first column '{first_col}' as date column (sample: {sample[0]})")

    prev_day_merchants = pd.DataFrame()
    tracker_dates = None
    if date_col:
        tracker_dates = tracker.copy()
        tracker_dates['_parsed_date'] = pd.to_datetime(tracker_dates[date_col], format='mixed', dayfirst=True, errors='coerce')

        if selected_date:
            # ISO format (YYYY-MM-DD) → parse without dayfirst (avoids 2026-04-12 → Dec 4 bug)
            # Other formats (DD-MMM-YY, etc.) → use dayfirst
            if re.match(r'^\d{4}-\d{2}-\d{2}', str(selected_date).strip()):
                target_date = pd.to_datetime(selected_date, format='%Y-%m-%d', errors='coerce')
            else:
                target_date = pd.to_datetime(selected_date, format='mixed', dayfirst=True, errors='coerce')
            logger.info(f"[FILTER] User selected date: {selected_date} → parsed as {target_date.date() if pd.notna(target_date) else 'INVALID'}")
        else:
            target_date = pd.to_datetime(datetime.now().strftime('%Y-%m-%d')) - timedelta(days=1)
            logger.info(f"[FILTER] No date selected — defaulting to yesterday: {target_date.date()}")

        if pd.notna(target_date):
            prev_day_merchants = tracker_dates[tracker_dates['_parsed_date'].dt.date == target_date.date()]
            prev_day_merchants = prev_day_merchants[prev_day_merchants[name_col].astype(str).str.strip() != '']
            logger.info(f"[FILTER] Merchants matching date {target_date.date()}: {len(prev_day_merchants)}")

            # Skip merchants whose check columns are ALL already filled with any value
            # (yes / no / warn / fail / pass). Only re-process merchants with at least
            # one empty check column — never overwrite existing results.
            if check_cols and not prev_day_merchants.empty:
                def has_empty_check(row):
                    for col in check_cols:
                        v = str(row.get(col, '')).strip().lower()
                        if v in ('', 'nan', 'none'):
                            return True
                    return False

                before = len(prev_day_merchants)
                prev_day_merchants = prev_day_merchants[prev_day_merchants.apply(has_empty_check, axis=1)]
                skipped = before - len(prev_day_merchants)
                if skipped:
                    logger.info(f"[FILTER] Skipped {skipped} merchants whose check columns are already filled — {len(prev_day_merchants)} remaining")

            if not prev_day_merchants.empty:
                names = prev_day_merchants[name_col].tolist()
                logger.info(f"[FILTER] Names: {names}")
        else:
            logger.warning(f"[FILTER] Could not parse target date — skipping date filter")
    else:
        logger.warning("[FILTER] No 'Date' column in tracker — cannot filter by date")

    # Category B: incomplete merchants — ONLY when no specific date was selected.
    # Bounded to the last 7 days so the fallback can never balloon into the
    # entire tracker history (which would re-process thousands of merchants).
    incomplete_merchants = pd.DataFrame()
    if selected_date:
        logger.info("[FILTER] Specific date selected → SKIPPING 'incomplete merchants' fallback")
    elif check_cols:
        # A merchant is "incomplete" ONLY when one of its check columns is truly empty.
        # If the cell already contains any value (yes / no / warn / fail / pass / etc.),
        # we treat it as already processed and skip the merchant — the user explicitly
        # asked that previously-written results should never be overwritten.
        def is_incomplete(row):
            for col in check_cols:
                val = str(row.get(col, '')).strip().lower()
                if val in ('', 'nan', 'none'):
                    return True
            return False

        # Use tracker_dates (which has the parsed date column) when available, so the
        # date bound below can actually be applied. Falls back to plain tracker otherwise.
        source = tracker_dates if tracker_dates is not None else tracker
        mask = source.apply(is_incomplete, axis=1)
        incomplete_merchants = source[mask]
        incomplete_merchants = incomplete_merchants[incomplete_merchants[name_col].astype(str).str.strip() != '']

        # Bound to last 7 days using the '_parsed_date' column.
        if '_parsed_date' in incomplete_merchants.columns:
            cutoff = pd.Timestamp.now().normalize() - pd.Timedelta(days=7)
            before = len(incomplete_merchants)
            incomplete_merchants = incomplete_merchants[
                incomplete_merchants['_parsed_date'].notna() &
                (incomplete_merchants['_parsed_date'] >= cutoff)
            ]
            logger.info(f"[FILTER] Incomplete merchants: {before} total → {len(incomplete_merchants)} within last 7 days")
        else:
            # No date column parsed — apply a hard safety cap to prevent runaway.
            if len(incomplete_merchants) > 50:
                logger.warning(f"[FILTER] No date column to bound by — capping {len(incomplete_merchants)} incomplete merchants to most recent 50")
                incomplete_merchants = incomplete_merchants.tail(50)
            logger.info(f"[FILTER] Incomplete merchants (any check empty/No/Warn): {len(incomplete_merchants)}")

    # If a specific date was selected → use ONLY merchants from that date
    # Otherwise → merge previous day + incomplete merchants
    if selected_date:
        if prev_day_merchants.empty:
            logger.error(f"[FILTER] No merchants found for date {selected_date} — aborting")
            return {"error": f"No merchants found for date {selected_date}", "results": []}
        combined = prev_day_merchants
        logger.info(f"[FILTER] Using ONLY date-filtered merchants: {len(combined)}")
    elif not prev_day_merchants.empty and not incomplete_merchants.empty:
        combined = pd.concat([prev_day_merchants, incomplete_merchants]).drop_duplicates(subset=[name_col], keep='first')
        logger.info(f"[FILTER] Merged previous-day + incomplete (deduped): {len(combined)}")
    elif not prev_day_merchants.empty:
        combined = prev_day_merchants
        logger.info(f"[FILTER] Using only previous-day merchants: {len(combined)}")
    elif not incomplete_merchants.empty:
        combined = incomplete_merchants
        logger.info(f"[FILTER] Using only incomplete merchants: {len(combined)}")
    else:
        logger.error("[FILTER] No merchants to check — aborting")
        return {"error": "No merchants to check (no previous day entries and no incomplete checks)", "results": []}

    combined = combined.reset_index(drop=True)
    # Drop internal columns
    if '_parsed_date' in combined.columns:
        combined = combined.drop(columns=['_parsed_date'])

    logger.info(f"[FILTER] FINAL merchant list ({len(combined)}): {combined[name_col].tolist()}")
    logger.info("=" * 70)

    # Now get commercial sheet for MDR rates
    sample = get_sanity_sample()
    sample = sample[sample['Merchant Name'].astype(str).str.strip() != ''].reset_index(drop=True)

    # Use combined as merchant list — merge with commercial for rates
    merchants_to_check = combined

    # 2. Get SALT & KEY sheet
    sk_df = get_salt_key()

    latest_date = selected_date or datetime.now().strftime('%Y-%m-%d')
    logger.info(f"[RUN] Date label: {latest_date} | Merchants: {len(merchants_to_check)}")

    pw = await async_playwright().start()
    batch_results = []

    # ═══ PHASE 1: EB Partner Portal (Settlement + MDR + Account) ═══
    update_progress('eb-login')
    logger.info("=" * 70)
    logger.info("[PHASE 1] EB Partner Portal — Settlement + MDR + Account Number")
    logger.info("=" * 70)
    sr_df = pd.DataFrame()
    try:
        eb_browser = await pw.chromium.launch(
            headless=HEADLESS,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--disable-extensions",
                "--disable-background-networking",
                "--disable-default-apps",
                "--disable-sync",
                "--disable-translate",
                "--no-first-run",
                "--single-process",
            ]
        )
        eb_page = await (await eb_browser.new_context(
            viewport={"width": 1366, "height": 768}, accept_downloads=True
        )).new_page()
        eb_page.set_default_timeout(BROWSER_TIMEOUT)

        logger.info("[PHASE 1] Logging into EB Partner Portal …")
        if await _eb_login(eb_page):
            logger.info("[PHASE 1] EB login OK")
            update_progress('settlement')
            # Build merchant list with name (used for EB search) + MID (for verification)
            eb_merchants = []
            for _, row in merchants_to_check.iterrows():
                m_name = _clean(row.get('Merchant Name', row.get(name_col, '')))
                m_mid = _clean(row.get('EB MID', row.get('Mid', '')))
                if m_name:
                    eb_merchants.append({'name': m_name, 'mid': m_mid})
            logger.info(f"[PHASE 1] Will request settlement reports for: {[m['name'] for m in eb_merchants]}")
            try:
                sr_df = await asyncio.wait_for(_eb_generate_settlement_report(eb_page, eb_merchants), timeout=900)
                logger.info(f"[PHASE 1] Settlement report combined: {len(sr_df)} rows")
            except asyncio.TimeoutError:
                logger.error("[PHASE 1] Settlement report timed out after 15 min — falling back to cached CSV")
                if os.path.exists('config/settlement_report.csv'):
                    sr_df = pd.read_csv('config/settlement_report.csv', low_memory=False)
                    logger.info(f"[PHASE 1] Loaded cached settlement report: {len(sr_df)} rows")
        else:
            logger.error("[PHASE 1] EB login FAILED")

        await eb_browser.close()
    except Exception as e:
        logger.exception(f"EB phase failed: {e}")

    # ═══ PHASE 2: GK Dashboard — fresh login per merchant (inside the loop) ═══
    logger.info("=" * 70)
    logger.info("[PHASE 2] GK Dashboard — fresh login per merchant")
    logger.info("=" * 70)
    gk_browser_args = [
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--disable-gpu",
        "--disable-extensions",
        "--disable-background-networking",
        "--disable-default-apps",
        "--disable-sync",
        "--disable-translate",
        "--no-first-run",
        "--single-process",
    ]

    # ═══ PHASE 3: Run checks per merchant ═══
    # Every merchant iteration is wrapped in a top-level try/except so that
    # an unexpected failure for ONE merchant never aborts the batch — the
    # loop continues to the next merchant and records a FAIL for the broken one.
    for idx, row in merchants_to_check.iterrows():
        merchant_name = ''
        eb_mid = ''
        try:
            merchant_name = _clean(row.get('Merchant Name', row.get(name_col, '')))
            eb_mid = _clean(row.get('EB MID', row.get('Mid', '')))
            mid = _clean(row.get('MID', row.get('Mid', '')))
            if not merchant_name:
                continue

            logger.info("─" * 70)
            logger.info(f"[MERCHANT {idx+1}/{len(merchants_to_check)}] {merchant_name} (EB MID={eb_mid}, GK MID={mid})")
            update_progress('mdr', merchant_name, idx+1, len(merchants_to_check))
            checks = []

            # Get expected rates from commercial sheet
            expected_rates = {'upi': '', 'cc': '', 'dc': ''}
            if not sample.empty:
                comm_row = sample[sample['Merchant Name'].astype(str).str.lower() == merchant_name.lower()]
                if not comm_row.empty:
                    cr = comm_row.iloc[0]
                    expected_rates = {
                        'upi': _clean(cr.get('UPI', '')),
                        'cc': _clean(cr.get('CC', '')),
                        'dc': _clean(cr.get('DC below 2K', '')),
                    }
                    if not eb_mid:
                        eb_mid = _clean(cr.get('EB MID', ''))
            logger.info(f"  Expected MDR rates: UPI={expected_rates['upi']}, CC={expected_rates['cc']}, DC={expected_rates['dc']}")

            # Get SALT & KEY from sheet
            sk = sk_df[sk_df['Merchant Name'].astype(str).str.lower().str.strip() == merchant_name.lower()]
            if sk.empty and mid:
                sk = sk_df[sk_df['MID'].astype(str).str.strip() == mid]
            sheet_key = str(sk.iloc[0].get('KEY', '')).strip() if not sk.empty else ''
            sheet_salt = str(sk.iloc[0].get('SALT', '')).strip() if not sk.empty else ''
            if not eb_mid and not sk.empty:
                eb_mid = _clean(sk.iloc[0].get('MID', ''))
            logger.info(f"  Sheet KEY={sheet_key or '(empty)'} | SALT={sheet_salt or '(empty)'}")

            # Check 1: Settlement
            logger.info(f"  [CHECK 1/5] Settlement …")
            try:
                c1 = await asyncio.wait_for(check_settlement(sr_df, eb_mid, merchant_name), timeout=CHECK_TIMEOUT)
            except asyncio.TimeoutError:
                c1 = _make_check("Settlement", "FAIL", "Timed out")
            except Exception as e:
                c1 = _make_check("Settlement", "FAIL", str(e)[:80])
            logger.info(f"  [CHECK 1/5] Settlement → {c1.get('status')}: {c1.get('message', '')[:100]}")
            checks.append(c1)

            # Check 2: MDR
            logger.info(f"  [CHECK 2/5] MDR …")
            try:
                c2 = await asyncio.wait_for(check_mdr(sr_df, eb_mid, expected_rates, merchant_name), timeout=CHECK_TIMEOUT)
            except asyncio.TimeoutError:
                c2 = _make_check("MDR", "FAIL", "Timed out")
            except Exception as e:
                c2 = _make_check("MDR", "FAIL", str(e)[:80])
            logger.info(f"  [CHECK 2/5] MDR → {c2.get('status')}: {c2.get('message', '')[:100]}")
            checks.append(c2)

            # Check 3: Account Number
            logger.info(f"  [CHECK 3/5] Account Number (Drive cheque + Gemini) …")
            update_progress('account', merchant_name, idx+1, len(sample))
            try:
                c3 = await asyncio.wait_for(check_account_number(sr_df, eb_mid, merchant_name), timeout=CHECK_TIMEOUT)
            except asyncio.TimeoutError:
                c3 = _make_check("Account Number", "FAIL", "Timed out")
            except Exception as e:
                c3 = _make_check("Account Number", "FAIL", str(e)[:80])
            logger.info(f"  [CHECK 3/5] Account Number → {c3.get('status')}: {c3.get('message', '')[:100]}")
            checks.append(c3)

            # Checks 4 & 5: SALT & KEY + VPA — fresh GK browser + login for this merchant
            logger.info(f"  [CHECK 4/5] SALT & KEY (GK Dashboard) — fresh browser+login …")
            update_progress('saltkey', merchant_name, idx+1, len(sample))
            gk_browser = None
            gk_page = None
            c4 = None
            c5 = None
            try:
                gk_browser = await pw.chromium.launch(headless=HEADLESS, args=gk_browser_args)
                gk_page = await (await gk_browser.new_context(
                    viewport={"width": 1366, "height": 768}
                )).new_page()
                gk_page.set_default_timeout(BROWSER_TIMEOUT)

                if await _gk_login(gk_page):
                    logger.info(f"    GK login OK → navigating to Terminals")
                    await _gk_navigate_terminals(gk_page)
                    switched = await _gk_switch_merchant(gk_page, merchant_name, mid)
                    if switched:
                        logger.info(f"    Switched to merchant in GK → checking SALT & KEY")
                        await _gk_navigate_terminals(gk_page)
                        c4 = await asyncio.wait_for(check_salt_key(gk_page, merchant_name, sheet_key, sheet_salt), timeout=CHECK_TIMEOUT)
                        logger.info(f"  [CHECK 4/5] SALT & KEY → {c4.get('status')}: {c4.get('message', '')[:100]}")
                        update_progress('vpa', merchant_name, idx+1, len(sample))
                        logger.info(f"  [CHECK 5/5] VPA / Webhook …")
                        try:
                            c5 = await asyncio.wait_for(check_vpa(gk_page, merchant_name), timeout=CHECK_TIMEOUT)
                        except asyncio.TimeoutError:
                            c5 = _make_check("VPA / Webhook", "FAIL", "Timed out")
                        except Exception as e:
                            c5 = _make_check("VPA / Webhook", "FAIL", str(e)[:80])
                        logger.info(f"  [CHECK 5/5] VPA / Webhook → {c5.get('status')}: {c5.get('message', '')[:100]}")
                    else:
                        logger.warning(f"    Could not switch to merchant in GK Dashboard")
                        c4 = _make_check("SALT & KEY", "WARN",
                                       f"Reason: Could not switch to merchant '{merchant_name}' in GK Dashboard. The merchant name may be different in production.")
                else:
                    logger.error(f"    GK login FAILED")
                    c4 = _make_check("SALT & KEY", "WARN",
                                   "Reason: GK Dashboard login failed.")
            except asyncio.TimeoutError:
                logger.error(f"  [CHECK 4/5] SALT & KEY timed out after {CHECK_TIMEOUT}s")
                if gk_page:
                    try:
                        await gk_page.screenshot(path='config/gk_saltkey_timeout.png', full_page=True)
                        logger.error(f"    Page URL at timeout: {gk_page.url} — screenshot saved to config/gk_saltkey_timeout.png")
                    except Exception:
                        pass
                c4 = c4 or _make_check("SALT & KEY", "FAIL", "Reason: Request timed out.")
            except Exception as e:
                logger.exception(f"  [CHECK 4/5] SALT & KEY exception: {type(e).__name__}: {e}")
                if gk_page:
                    try:
                        await gk_page.screenshot(path='config/gk_saltkey_exception.png', full_page=True)
                        logger.error(f"    Page URL at exception: {gk_page.url} — screenshot saved to config/gk_saltkey_exception.png")
                    except Exception:
                        pass
                c4 = c4 or _make_check("SALT & KEY", "FAIL", f"Reason: An error occurred — {str(e)[:80]}")
            finally:
                if gk_browser:
                    try:
                        await gk_browser.close()
                    except Exception:
                        pass

            if c4 is None:
                c4 = _make_check("SALT & KEY", "WARN", "Reason: GK Dashboard not reachable.")
            if c5 is None:
                c5 = _make_check("VPA / Webhook", "WARN", "GK Dashboard not available")
            checks.append(c4)
            checks.append(c5)

            # Overall
            statuses = [c["status"] for c in checks]
            pass_count = sum(1 for s in statuses if s == "PASS")
            fail_count = sum(1 for s in statuses if s == "FAIL")
            overall = "FAIL" if fail_count > 0 else "PASS" if pass_count == len(checks) else "WARN"
            logger.info(f"[MERCHANT {idx+1}/{len(merchants_to_check)}] {merchant_name} → OVERALL {overall} ({pass_count}/{len(checks)} passed)")

            batch_results.append({
                "merchant_name": merchant_name,
                "eb_mid": eb_mid,
                "date": latest_date,
                "overall_status": overall,
                "pass_count": pass_count,
                "total_checks": len(checks),
                "checks": checks,
            })
        except Exception as e:
            # Catch-all: an unexpected error for ONE merchant must not stop the whole batch.
            logger.exception(f"[MERCHANT {idx+1}/{len(merchants_to_check)}] {merchant_name or '(unknown)'} — unexpected error: {type(e).__name__}: {e}")
            batch_results.append({
                "merchant_name": merchant_name or f"row_{idx}",
                "eb_mid": eb_mid,
                "date": latest_date,
                "overall_status": "FAIL",
                "pass_count": 0,
                "total_checks": 5,
                "checks": [_make_check("Batch", "FAIL", f"Reason: Unexpected error — {type(e).__name__}: {str(e)[:80]}")],
            })
            continue

    # Cleanup
    await pw.stop()

    total = len(batch_results)
    passed = sum(1 for r in batch_results if r["overall_status"] == "PASS")
    failed = sum(1 for r in batch_results if r["overall_status"] == "FAIL")
    warned = total - passed - failed

    logger.info("=" * 70)
    logger.info(f"[SUMMARY] Total: {total} | PASS: {passed} | WARN: {warned} | FAIL: {failed}")
    logger.info("=" * 70)

    # Auto-write results to sheet
    logger.info("[WRITE] Writing results back to tracker sheet …")
    try:
        from modules.sheets_writer import write_results
        write_result = write_results(batch_results)
        logger.info(f"[WRITE] Result: {write_result}")
    except Exception as e:
        logger.error(f"[WRITE] FAILED: {e}")

    return {
        "date": latest_date,
        "total_merchants": total,
        "passed": passed,
        "failed": failed,
        "warned": warned,
        "results": batch_results,
    }
