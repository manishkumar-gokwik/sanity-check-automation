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

async def check_settlement(sr_df, eb_mid, merchant_name):
    """Check 1: Settlement — find merchant in settlement report."""
    try:
        if sr_df.empty:
            return _make_check("Settlement", "WARN",
                             "Reason: Could not download settlement report from EB Partner Portal.",
                             expected="Settlement report CSV",
                             actual="Empty / Failed download")

        txns = sr_df[sr_df['Merchant ID'].astype(str).str.strip() == str(eb_mid).strip()]
        if txns.empty:
            return _make_check("Settlement", "WARN",
                             f"Reason: No transactions found for EB MID {eb_mid}. This merchant may be new or has no transactions yet.",
                             expected=f"Transactions for EB MID {eb_mid}",
                             actual="No transactions in last 30 days")

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
    """Check 2: MDR — calculate actual vs expected."""
    try:
        if sr_df.empty:
            return _make_check("MDR", "WARN",
                             "Reason: Settlement report is not available, so MDR cannot be calculated.",
                             expected="Settlement report CSV", actual="Empty")

        txns = sr_df[sr_df['Merchant ID'].astype(str).str.strip() == str(eb_mid).strip()]
        if txns.empty:
            return _make_check("MDR", "WARN",
                             f"Reason: No transactions found for MID {eb_mid}. MDR cannot be calculated without transaction data.",
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

        # Get EB account from settlement report
        eb_account = ''
        if not sr_df.empty:
            txns = sr_df[sr_df['Merchant ID'].astype(str).str.strip() == str(eb_mid).strip()]
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
    """Login to GK Dashboard with auto OTP."""
    await page.goto(f"{GK_DASHBOARD_URL}/login")
    await asyncio.sleep(5)
    await (await page.query_selector('input[type="email"]')).fill(GK_EMAIL)
    await page.click('button:has-text("Next")')
    await asyncio.sleep(5)
    pi = await page.query_selector('input[type="password"]')
    if pi and await pi.is_visible():
        await pi.fill(GK_PASS)
        await page.click('button:has-text("Next")')
        await asyncio.sleep(5)
    if "verify-otp" in page.url:
        otp = _fetch_gk_otp()
        logger.info(f"GK OTP: {otp}")
        if otp:
            for inp in await page.query_selector_all('input'):
                if await inp.is_visible():
                    await inp.click()
                    await inp.fill(otp)
                    break
            await page.click('button:has-text("Next")')
            await asyncio.sleep(15)
    return "verify-otp" not in page.url


async def _gk_navigate_terminals(page):
    """Navigate: Kwik Payment → Settings → Terminals."""
    for name in ['Kwik Payment', 'Settings', 'Terminals']:
        await page.evaluate("""(n) => {
            for (const el of document.querySelectorAll('.ant-menu-title-content')) {
                if (el.textContent.trim() === n) { el.click(); return; }
            }
        }""", name)
        await asyncio.sleep(4)


async def _gk_switch_merchant(page, merchant_name):
    """Switch merchant in GK Dashboard. Tries multiple search terms."""
    try:
        await page.locator('text=Switch merchant').first.click(timeout=10000)
    except Exception:
        logger.warning(f"Switch merchant button not clickable for {merchant_name}")
        return False
    await asyncio.sleep(5)

    gk_input = await page.query_selector('.gk-text-input')
    if not gk_input:
        logger.warning(f"Search input not found for {merchant_name}")
        # Close modal before returning
        try:
            await page.keyboard.press('Escape')
        except Exception:
            pass
        return False

    # Try 2 search terms: full name, then first 5 chars
    search_terms = [merchant_name]
    if len(merchant_name) > 5:
        search_terms.append(merchant_name[:5])

    clicked = False
    for term in search_terms:
        try:
            await gk_input.click()
            await asyncio.sleep(0.5)
            await page.keyboard.press('Control+a')
            await page.keyboard.press('Backspace')
            await asyncio.sleep(0.5)
            await gk_input.fill(term)
            await asyncio.sleep(4)

            # Find result — match by merchant name substring
            r = await page.evaluate("""(name) => {
                const lower = name.toLowerCase();
                const lowerNoSpace = lower.replace(/\\s+/g, '');
                for (const el of document.querySelectorAll('.ant-modal-body div, .ant-modal-body label')) {
                    const text = el.textContent.trim().toLowerCase();
                    const textNoSpace = text.replace(/\\s+/g, '');
                    if ((text.includes(lower) || textNoSpace.includes(lowerNoSpace)) && el.children.length<=5) {
                        const r=el.getBoundingClientRect();
                        if(r.width>100&&r.height>15&&r.height<80&&r.y>200) {
                            return {x:r.left+20, y:r.top+r.height/2, text: el.textContent.trim().substring(0,60)};
                        }
                    }
                }
                return null;
            }""", merchant_name)

            if r:
                logger.info(f"Found merchant '{merchant_name}' via '{term}': {r.get('text', '')}")
                await page.mouse.click(r['x'], r['y'])
                await asyncio.sleep(2)
                clicked = True
                break
        except Exception as e:
            logger.warning(f"Search '{term}' failed: {e}")
            continue

    if not clicked:
        logger.warning(f"No search result found for {merchant_name} (tried: {search_terms})")
        # Close modal
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
    """Login to EB Partner Portal."""
    await page.goto(EB_LOGIN_URL, wait_until="domcontentloaded")
    await asyncio.sleep(5)
    await (await page.wait_for_selector('input[name="email"]')).fill(EB_EMAIL)
    await (await page.wait_for_selector('input[name="password"]')).fill(EB_PASS)
    await page.click('button:has-text("Login")')
    await asyncio.sleep(12)
    return "partners.easebuzz.in" in page.url


async def _eb_generate_settlement_report(page):
    """Generate and download settlement report. Returns DataFrame."""
    await page.goto(f"{EB_PARTNER_URL}/custom-reports", wait_until="domcontentloaded")
    await asyncio.sleep(8)

    # Click Merchant Settlements tab
    await page.evaluate("""() => {
        document.querySelectorAll('div').forEach(el => {
            if (el.textContent.trim() === 'Merchant Settlements' && el.children.length === 0) {
                const r = el.getBoundingClientRect();
                if (r.width > 0 && r.height < 50) el.click();
            }
        });
    }""")
    await asyncio.sleep(5)

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
        await name_input.fill(f"sanity_{datetime.now().strftime('%Y%m%d_%H%M')}")

    # Select All Merchants
    await page.evaluate("""() => {
        const phs = document.querySelectorAll('.ant-select-selection-placeholder');
        for (const ph of phs) {
            if (ph.textContent.includes('Search Merchant')) {
                const sel = ph.closest('.ant-select');
                if (sel) { sel.dispatchEvent(new MouseEvent('mousedown', {bubbles: true}));
                           sel.dispatchEvent(new MouseEvent('click', {bubbles: true})); }
                const selector = ph.closest('.ant-select-selector');
                if (selector) selector.dispatchEvent(new MouseEvent('mousedown', {bubbles: true}));
            }
        }
    }""")
    await asyncio.sleep(5)
    await page.evaluate("""() => {
        for (const el of document.querySelectorAll('*')) {
            if (el.textContent.trim() === 'Select all' && el.children.length === 0) {
                const r = el.getBoundingClientRect();
                if (r.width > 0) { el.click(); return; }
            }
        }
    }""")
    await asyncio.sleep(3)
    await page.mouse.click(770, 280)
    await asyncio.sleep(2)

    # Date range — last 30 days
    today = datetime.now()
    start = today - timedelta(days=30)
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
    await asyncio.sleep(10)

    # Wait for report
    for i in range(int(REPORT_WAIT_TIMEOUT / 5)):
        await asyncio.sleep(5)
        body = await page.inner_text('body')
        if 'sanity_' in body.lower() and 'Success' in body:
            break
        if i == 6:
            await page.goto(f"{EB_PARTNER_URL}/custom-reports", wait_until="domcontentloaded")
            await asyncio.sleep(5)
            await page.evaluate("""() => {
                document.querySelectorAll('div').forEach(el => {
                    if (el.textContent.trim() === 'Merchant Settlements' && el.children.length === 0) {
                        const r = el.getBoundingClientRect();
                        if (r.width > 0 && r.height < 50) el.click();
                    }
                });
            }""")
            await asyncio.sleep(3)

    # Download
    try:
        async with page.expect_download(timeout=60000) as dl:
            await page.evaluate("""() => {
                const icons = document.querySelectorAll('[class*="download-icon"], [class*="download"]');
                for (const icon of icons) {
                    const r = icon.getBoundingClientRect();
                    if (r.width > 0 && r.y > 200 && r.y < 400) { icon.click(); return; }
                }
            }""")
        download = await dl.value
        path = 'config/settlement_report.csv'
        await download.save_as(path)
        return pd.read_csv(path, low_memory=False)
    except Exception as e:
        logger.error(f"Settlement download failed: {e}")
        # Try loading existing report
        if os.path.exists('config/settlement_report.csv'):
            return pd.read_csv('config/settlement_report.csv', low_memory=False)
        return pd.DataFrame()


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

    tracker = get_sanity_tracker()
    if tracker.empty:
        return {"error": "Tracking sheet is empty", "results": []}

    # Check columns in tracker
    check_cols = []
    for col in tracker.columns:
        cl = col.strip().lower()
        if cl in ('settlement report triggered', 'commercial validation', 'bank accont',
                  'bank accont ', 'web hook - vpa', 'salt and key validation'):
            check_cols.append(col)

    # Find merchant name column
    name_col = None
    for col in tracker.columns:
        if col.strip().lower() == 'merchant name':
            name_col = col
            break
    if not name_col:
        return {"error": "No 'Merchant Name' column in tracking sheet", "results": []}

    # Category A: Previous day merchants
    prev_day_merchants = pd.DataFrame()
    if 'Date' in tracker.columns:
        tracker_dates = tracker.copy()
        tracker_dates['_parsed_date'] = pd.to_datetime(tracker_dates['Date'], format='mixed', dayfirst=True, errors='coerce')

        if selected_date:
            target_date = pd.to_datetime(selected_date, format='mixed', dayfirst=True, errors='coerce')
        else:
            target_date = pd.to_datetime(datetime.now().strftime('%Y-%m-%d')) - timedelta(days=1)

        if pd.notna(target_date):
            prev_day_merchants = tracker_dates[tracker_dates['_parsed_date'].dt.date == target_date.date()]
            prev_day_merchants = prev_day_merchants[prev_day_merchants[name_col].astype(str).str.strip() != '']

    # Category B: All merchants with any check column empty or "No"
    incomplete_merchants = pd.DataFrame()
    if check_cols:
        def is_incomplete(row):
            for col in check_cols:
                val = str(row.get(col, '')).strip().lower()
                if val in ('', 'no', 'warn', 'nan', 'none'):
                    return True
            return False

        mask = tracker.apply(is_incomplete, axis=1)
        incomplete_merchants = tracker[mask]
        incomplete_merchants = incomplete_merchants[incomplete_merchants[name_col].astype(str).str.strip() != '']

    # Merge both categories — remove duplicates by merchant name
    if not prev_day_merchants.empty and not incomplete_merchants.empty:
        combined = pd.concat([prev_day_merchants, incomplete_merchants]).drop_duplicates(subset=[name_col], keep='first')
    elif not prev_day_merchants.empty:
        combined = prev_day_merchants
    elif not incomplete_merchants.empty:
        combined = incomplete_merchants
    else:
        return {"error": "No merchants to check (no previous day entries and no incomplete checks)", "results": []}

    combined = combined.reset_index(drop=True)
    # Drop internal columns
    if '_parsed_date' in combined.columns:
        combined = combined.drop(columns=['_parsed_date'])

    logger.info(f"Previous day merchants: {len(prev_day_merchants)}, Incomplete: {len(incomplete_merchants)}, Total unique: {len(combined)}")

    # Now get commercial sheet for MDR rates
    sample = get_sanity_sample()
    sample = sample[sample['Merchant Name'].astype(str).str.strip() != ''].reset_index(drop=True)

    # Use combined as merchant list — merge with commercial for rates
    merchants_to_check = combined

    # 2. Get SALT & KEY sheet
    sk_df = get_salt_key()

    latest_date = selected_date or datetime.now().strftime('%Y-%m-%d')
    logger.info(f"Running checks for {len(merchants_to_check)} merchants")

    pw = await async_playwright().start()
    batch_results = []

    # ═══ PHASE 1: EB Partner Portal (Settlement + MDR + Account) ═══
    update_progress('eb-login')
    logger.info("Phase 1: EB Partner Portal")
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

        if await _eb_login(eb_page):
            logger.info("EB login OK")
            update_progress('settlement')
            sr_df = await _eb_generate_settlement_report(eb_page)
            logger.info(f"Settlement report: {len(sr_df)} rows")
        else:
            logger.error("EB login failed")

        await eb_browser.close()
    except Exception as e:
        logger.exception(f"EB phase failed: {e}")

    # ═══ PHASE 2: GK Dashboard (SALT & KEY + VPA) ═══
    update_progress('gk-login')
    logger.info("Phase 2: GK Dashboard")
    gk_page = None
    gk_browser = None
    gk_logged_in = False
    if True:  # GK Dashboard checks enabled
        try:
            gk_browser = await pw.chromium.launch(
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
            gk_page = await (await gk_browser.new_context(
                viewport={"width": 1366, "height": 768}
            )).new_page()
            gk_page.set_default_timeout(BROWSER_TIMEOUT)

            gk_logged_in = await _gk_login(gk_page)
            if gk_logged_in:
                logger.info("GK login OK")
                await _gk_navigate_terminals(gk_page)
            else:
                logger.error("GK login failed")
        except Exception as e:
            logger.exception(f"GK login failed: {e}")

    # ═══ PHASE 3: Run checks per merchant ═══
    for idx, row in merchants_to_check.iterrows():
        merchant_name = _clean(row.get('Merchant Name', row.get(name_col, '')))
        eb_mid = _clean(row.get('EB MID', row.get('Mid', '')))
        mid = _clean(row.get('MID', row.get('Mid', '')))
        if not merchant_name:
            continue

        logger.info(f"Checking {merchant_name} ({idx+1}/{len(merchants_to_check)})")
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

        # Get SALT & KEY from sheet
        sk = sk_df[sk_df['Merchant Name'].astype(str).str.lower().str.strip() == merchant_name.lower()]
        if sk.empty and mid:
            sk = sk_df[sk_df['MID'].astype(str).str.strip() == mid]
        sheet_key = str(sk.iloc[0].get('KEY', '')).strip() if not sk.empty else ''
        sheet_salt = str(sk.iloc[0].get('SALT', '')).strip() if not sk.empty else ''
        if not eb_mid and not sk.empty:
            eb_mid = _clean(sk.iloc[0].get('MID', ''))

        # Check 1: Settlement
        try:
            c1 = await asyncio.wait_for(check_settlement(sr_df, eb_mid, merchant_name), timeout=CHECK_TIMEOUT)
        except asyncio.TimeoutError:
            c1 = _make_check("Settlement", "FAIL", "Timed out")
        except Exception as e:
            c1 = _make_check("Settlement", "FAIL", str(e)[:80])
        checks.append(c1)

        # Check 2: MDR
        try:
            c2 = await asyncio.wait_for(check_mdr(sr_df, eb_mid, expected_rates, merchant_name), timeout=CHECK_TIMEOUT)
        except asyncio.TimeoutError:
            c2 = _make_check("MDR", "FAIL", "Timed out")
        except Exception as e:
            c2 = _make_check("MDR", "FAIL", str(e)[:80])
        checks.append(c2)

        # Check 3: Account Number
        update_progress('account', merchant_name, idx+1, len(sample))
        try:
            c3 = await asyncio.wait_for(check_account_number(sr_df, eb_mid, merchant_name), timeout=CHECK_TIMEOUT)
        except asyncio.TimeoutError:
            c3 = _make_check("Account Number", "FAIL", "Timed out")
        except Exception as e:
            c3 = _make_check("Account Number", "FAIL", str(e)[:80])
        checks.append(c3)

        # Check 4: SALT & KEY (GK Dashboard)
        update_progress('saltkey', merchant_name, idx+1, len(sample))
        if gk_logged_in and gk_page:
            try:
                switched = await _gk_switch_merchant(gk_page, merchant_name)
                if switched:
                    await _gk_navigate_terminals(gk_page)
                    c4 = await asyncio.wait_for(check_salt_key(gk_page, merchant_name, sheet_key, sheet_salt), timeout=CHECK_TIMEOUT)
                else:
                    c4 = _make_check("SALT & KEY", "WARN",
                                   f"Reason: Could not switch to merchant '{merchant_name}' in GK Dashboard. The merchant name may be different in production.")
            except asyncio.TimeoutError:
                c4 = _make_check("SALT & KEY", "FAIL",
                               "Reason: Request timed out. GK Dashboard is responding slowly.")
            except Exception as e:
                c4 = _make_check("SALT & KEY", "FAIL", f"Reason: An error occurred — {str(e)[:80]}")
        else:
            c4 = _make_check("SALT & KEY", "WARN",
                           "Reason: GK Dashboard login failed or there is a connection issue.")
        checks.append(c4)

        # Check 5: VPA (GK Dashboard) — only if merchant was switched successfully
        update_progress('vpa', merchant_name, idx+1, len(sample))
        switched_ok = c4.get('status') == 'PASS' if c4 else False
        if gk_logged_in and gk_page and switched_ok:
            try:
                c5 = await asyncio.wait_for(check_vpa(gk_page, merchant_name), timeout=CHECK_TIMEOUT)
            except asyncio.TimeoutError:
                c5 = _make_check("VPA / Webhook", "FAIL", "Timed out")
            except Exception as e:
                c5 = _make_check("VPA / Webhook", "FAIL", str(e)[:80])
        else:
            c5 = _make_check("VPA / Webhook", "WARN", "GK Dashboard not available")
        checks.append(c5)

        # Overall
        statuses = [c["status"] for c in checks]
        pass_count = sum(1 for s in statuses if s == "PASS")
        fail_count = sum(1 for s in statuses if s == "FAIL")
        overall = "FAIL" if fail_count > 0 else "PASS" if pass_count == len(checks) else "WARN"

        batch_results.append({
            "merchant_name": merchant_name,
            "eb_mid": eb_mid,
            "date": latest_date,
            "overall_status": overall,
            "pass_count": pass_count,
            "total_checks": len(checks),
            "checks": checks,
        })

    # Cleanup
    if gk_browser:
        try:
            await gk_browser.close()
        except Exception:
            pass
    await pw.stop()

    total = len(batch_results)
    passed = sum(1 for r in batch_results if r["overall_status"] == "PASS")
    failed = sum(1 for r in batch_results if r["overall_status"] == "FAIL")
    warned = total - passed - failed

    # Auto-write results to sheet
    try:
        from modules.sheets_writer import write_results
        write_result = write_results(batch_results)
        logger.info(f"Auto-write to sheet: {write_result}")
    except Exception as e:
        logger.error(f"Auto-write failed: {e}")

    return {
        "date": latest_date,
        "total_merchants": total,
        "passed": passed,
        "failed": failed,
        "warned": warned,
        "results": batch_results,
    }
