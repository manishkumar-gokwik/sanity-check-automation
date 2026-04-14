"""
Easebuzz Partner Portal Automation
Single login → access all merchants data → settlements, transactions, reports
URL: https://partners.easebuzz.in
"""

import asyncio
import re
from datetime import datetime, timedelta
from playwright.async_api import async_playwright

PARTNER_LOGIN_URL = "https://auth.easebuzz.in/easebuzz/login?ep=y"
PARTNER_PORTAL_URL = "https://partners.easebuzz.in"


class PartnerPortal:
    def __init__(self, email="easebuzzpg@gokwik.co", password="GoKwik@124"):
        self.email = email
        self.password = password
        self.browser = None
        self.page = None
        self.context = None
        self.playwright = None

    async def start(self, headless=True):
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(
            headless=headless,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        self.context = await self.browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1366, "height": 768},
        )
        await self.context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"
        )
        self.page = await self.context.new_page()
        self.page.set_default_timeout(60000)

    async def login(self):
        """Login to Partner Portal."""
        try:
            await self.page.goto(PARTNER_LOGIN_URL)
            await self.page.wait_for_load_state("networkidle")
            await asyncio.sleep(3)

            email_input = await self.page.wait_for_selector('input[name="email"]')
            await email_input.click()
            await self.page.keyboard.type(self.email, delay=50)

            pass_input = await self.page.wait_for_selector('input[name="password"]')
            await pass_input.click()
            await self.page.keyboard.type(self.password, delay=50)

            await self.page.click('button:has-text("Login")')
            await asyncio.sleep(8)

            if "partners.easebuzz.in" in self.page.url:
                return {"success": True}
            return {"success": False, "error": "Login failed"}

        except Exception as e:
            return {"success": False, "error": str(e)}

    async def get_merchants(self, filter_date=None):
        """
        Get merchants list from Partner Portal.
        If filter_date provided, filters by sign up date.
        """
        try:
            await self.page.click('text=Merchants')
            await asyncio.sleep(3)
            # Click on Referral sub-tab (merchants are under referrals)
            try:
                await self.page.click('text=Referral')
                await asyncio.sleep(3)
            except Exception:
                pass

            await asyncio.sleep(5)

            # Extract merchant table
            merchants = await self.page.evaluate("""
                () => {
                    const rows = document.querySelectorAll('table tbody tr');
                    const data = [];
                    for (const row of rows) {
                        const cells = Array.from(row.querySelectorAll('td')).map(td => td.textContent.trim());
                        if (cells.length >= 9 && cells[0]) {
                            data.push({
                                name: cells[0],
                                referral_id: cells[1],
                                referral_type: cells[2],
                                phone: cells[3],
                                email: cells[4],
                                sign_up_date: cells[5],
                                state: cells[6],
                                referral_status: cells[7],
                                kyc_status: cells[8],
                            });
                        }
                    }
                    return data;
                }
            """)

            # Filter by date if provided
            if filter_date and merchants:
                filtered = []
                for m in merchants:
                    if filter_date.lower() in m.get("sign_up_date", "").lower():
                        filtered.append(m)
                return {"success": True, "merchants": filtered, "total": len(filtered)}

            return {"success": True, "merchants": merchants, "total": len(merchants)}

        except Exception as e:
            return {"success": False, "error": str(e)}

    async def get_transactions(self, merchant_name=None):
        """
        Get transactions from Partner Portal.
        Can filter by merchant name.
        """
        try:
            await self.page.click('text=Transactions')
            await asyncio.sleep(5)

            # If merchant name filter, use search
            if merchant_name:
                # Look for search/filter
                try:
                    search_btn = await self.page.query_selector('text=Search & Filter')
                    if search_btn:
                        await search_btn.click()
                        await asyncio.sleep(2)
                except Exception:
                    pass

            # Extract transactions table
            transactions = await self.page.evaluate("""
                () => {
                    const rows = document.querySelectorAll('table tbody tr');
                    const data = [];
                    for (const row of rows) {
                        const cells = Array.from(row.querySelectorAll('td')).map(td => td.textContent.trim());
                        if (cells.length >= 10 && cells[0]) {
                            data.push({
                                easebuzz_id: cells[0],
                                referral_id: cells[1],
                                referral_name: cells[2],
                                referral_type: cells[3],
                                transaction_date: cells[4],
                                transaction_amount: cells[5],
                                commission_amount: cells[6],
                                payment_mode: cells[7],
                                transaction_ref: cells[8],
                                bank_ref: cells[9],
                            });
                        }
                    }
                    return data;
                }
            """)

            # Filter by merchant name if provided
            if merchant_name and transactions:
                filtered = [t for t in transactions
                           if merchant_name.lower() in t.get("referral_name", "").lower()]
                return {"success": True, "transactions": filtered}

            return {"success": True, "transactions": transactions}

        except Exception as e:
            return {"success": False, "error": str(e)}

    async def get_merchant_details(self, merchant_mid):
        """
        Get merchant details (VPA, Webhook, Settlement Account) from Partner Portal.
        Navigate to merchant's detail page using MID.
        """
        try:
            # Go to Merchants section
            await self.page.click('text=Merchants')
            await asyncio.sleep(3)

            # Search for merchant by MID
            try:
                search_input = await self.page.query_selector('input[placeholder*="Search"], input[placeholder*="search"]')
                if search_input:
                    await search_input.fill(str(merchant_mid))
                    await asyncio.sleep(3)
            except Exception:
                pass

            # Try to find and click on the merchant row
            try:
                row = await self.page.query_selector('table tbody tr')
                if row:
                    await row.click()
                    await asyncio.sleep(5)
            except Exception:
                pass

            # Extract page content for VPA/Webhook/Account info
            body_text = await self.page.inner_text('body')
            body_lower = body_text.lower()

            result = {
                "success": True,
                "vpa": None,
                "webhook_url": None,
                "account_number": None,
                "ifsc": None,
            }

            # Look for VPA pattern (xxx@xxx)
            import re
            vpa_matches = re.findall(r'[\w.]+@[\w]+', body_text)
            # Filter out email addresses
            vpas = [v for v in vpa_matches if not v.endswith(('.com', '.in', '.co', '.org', '.net'))]
            if vpas:
                result['vpa'] = vpas[0]

            # Check for webhook
            if 'webhook' in body_lower:
                webhook_matches = re.findall(r'https?://[^\s<>"]+webhook[^\s<>"]*', body_text, re.IGNORECASE)
                if webhook_matches:
                    result['webhook_url'] = webhook_matches[0]
                elif 'configured' in body_lower or 'active' in body_lower:
                    result['webhook_url'] = 'configured'

            # Account number
            acct_matches = re.findall(r'\d{9,18}', body_text)
            if acct_matches:
                result['account_number'] = acct_matches[0]

            # IFSC
            ifsc_matches = re.findall(r'[A-Z]{4}0[A-Z0-9]{6}', body_text)
            if ifsc_matches:
                result['ifsc'] = ifsc_matches[0]

            return result

        except Exception as e:
            return {"success": False, "error": str(e)}

    async def generate_settlement_report(self, start_date, end_date):
        """
        Generate Merchant Settlements report via Custom Reports.
        start_date, end_date format: 'DD/MM/YYYY'
        """
        try:
            await self.page.click('text=Custom Reports')
            await asyncio.sleep(5)

            # Click Merchant Settlements tab
            await self.page.click('text=Merchant Settlements')
            await asyncio.sleep(3)

            # Click Generate New Report
            await self.page.click('text=Generate New Report')
            await asyncio.sleep(3)

            # Fill date range and report name
            # This will depend on the modal/form structure
            await self.page.screenshot(path='/home/vidit/Desktop/Automation/debug_report_form.png')

            body = await self.page.inner_text('body')
            return {"success": True, "message": "Report generation UI loaded", "page_text": body[:2000]}

        except Exception as e:
            return {"success": False, "error": str(e)}

    async def get_payout_reports(self):
        """Get payout reports summary."""
        try:
            await self.page.click('text=Payout Reports')
            await asyncio.sleep(5)

            data = await self.page.evaluate("""
                () => {
                    const rows = document.querySelectorAll('table tbody tr');
                    const reports = [];
                    for (const row of rows) {
                        const cells = Array.from(row.querySelectorAll('td')).map(td => td.textContent.trim());
                        if (cells.length >= 6 && cells[0]) {
                            reports.push({
                                month: cells[0],
                                commission: cells[1],
                                tds: cells[2],
                                gst: cells[3],
                                pending_gst: cells[4],
                                status: cells[5],
                            });
                        }
                    }
                    return reports;
                }
            """)

            return {"success": True, "reports": data}

        except Exception as e:
            return {"success": False, "error": str(e)}

    async def run_sanity_check(self, previous_day_merchants):
        """
        Run sanity check for a list of merchant names.
        Gets transaction data from Partner Portal for each merchant.
        """
        results = []
        transactions_result = await self.get_transactions()

        if not transactions_result["success"]:
            return {"success": False, "error": "Could not fetch transactions"}

        all_transactions = transactions_result["transactions"]

        for merchant in previous_day_merchants:
            merchant_name = merchant.get("name") or merchant.get("Merchant Name", "")
            if not merchant_name:
                continue

            # Find transactions for this merchant
            merchant_txns = [t for t in all_transactions
                           if merchant_name.lower() in t.get("referral_name", "").lower()]

            # Build result
            check = {
                "merchant_name": merchant_name,
                "sign_up_date": merchant.get("sign_up_date", merchant.get("Date", "")),
                "email": merchant.get("email", ""),
                "status": merchant.get("referral_status", ""),
                "transaction_count": len(merchant_txns),
                "has_transactions": len(merchant_txns) > 0,
                "transactions": merchant_txns[:5],  # First 5
                "checks": [],
            }

            # Check 1: Is merchant live?
            if merchant.get("referral_status", "").lower() in ["active", "live"]:
                check["checks"].append({"name": "Merchant Status", "status": "PASS", "message": f"Status: {merchant.get('referral_status')}"})
            else:
                check["checks"].append({"name": "Merchant Status", "status": "WARN", "message": f"Status: {merchant.get('referral_status', 'Unknown')}"})

            # Check 2: Has transactions?
            if merchant_txns:
                total_amount = 0
                for t in merchant_txns:
                    try:
                        amt = float(t["transaction_amount"].replace("₹", "").replace(",", "").strip())
                        total_amount += amt
                    except (ValueError, KeyError):
                        pass
                check["checks"].append({
                    "name": "Transaction Activity",
                    "status": "PASS",
                    "message": f"{len(merchant_txns)} transactions found, Total: ₹{total_amount:,.2f}"
                })
            else:
                check["checks"].append({
                    "name": "Transaction Activity",
                    "status": "FAIL",
                    "message": "No transactions found"
                })

            # Overall status
            statuses = [c["status"] for c in check["checks"]]
            if "FAIL" in statuses:
                check["overall_status"] = "FAIL"
            elif "WARN" in statuses:
                check["overall_status"] = "WARN"
            else:
                check["overall_status"] = "PASS"

            check["pass_count"] = sum(1 for s in statuses if s == "PASS")
            check["total_checks"] = len(statuses)

            results.append(check)

        return {"success": True, "results": results}

    async def close(self):
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()
