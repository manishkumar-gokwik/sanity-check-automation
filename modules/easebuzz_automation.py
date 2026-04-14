"""
Easebuzz Dashboard Browser Automation Module
Uses Playwright to:
1. Login to Easebuzz Dashboard
2. Get Settlement History from /payouts/history
3. Get Transaction details (Split Info, MDR, Account)
"""

import asyncio
import re
from playwright.async_api import async_playwright
from config.settings import EASEBUZZ_LOGIN_URL, HEADLESS, BROWSER_TIMEOUT


class EasebuzzAutomation:
    def __init__(self, email, password):
        self.email = email
        self.password = password
        self.browser = None
        self.page = None
        self.context = None

    async def start(self):
        """Launch browser."""
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(
            headless=HEADLESS,
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
        self.page.set_default_timeout(BROWSER_TIMEOUT)

    async def _close_popups(self):
        """Remove all overlays and popups."""
        try:
            await self.page.evaluate("""
                document.querySelectorAll(
                    '.react-joyride__overlay, #react-joyride-portal, [class*="joyride"]'
                ).forEach(el => el.remove());
            """)
            for sel in ['text=Skip', 'text=End Guide']:
                try:
                    el = await self.page.query_selector(sel)
                    if el and await el.is_visible():
                        await el.click(force=True)
                except Exception:
                    pass
        except Exception:
            pass
        await self.page.wait_for_timeout(500)

    async def login(self):
        """Login to Easebuzz Dashboard."""
        try:
            await self.page.goto(EASEBUZZ_LOGIN_URL)
            await self.page.wait_for_load_state("networkidle")
            await self.page.wait_for_timeout(2000)

            email_input = await self.page.wait_for_selector('input[name="email"]', timeout=30000)
            await email_input.click()
            await self.page.keyboard.type(self.email, delay=50)

            password_input = await self.page.wait_for_selector('input[name="password"]', timeout=5000)
            await password_input.click()
            await self.page.keyboard.type(self.password, delay=50)

            await self.page.click('button:has-text("Login")')
            await self.page.wait_for_timeout(5000)
            await self._close_popups()

            if "dashboard" in self.page.url.lower() and "login" not in self.page.url.lower():
                return {"success": True}
            return {"success": False, "error": "Login failed - check credentials"}

        except Exception as e:
            return {"success": False, "error": str(e)}

    async def get_settlement_history(self):
        """Get settlement history from /payouts/history."""
        try:
            await self.page.goto("https://dashboard.easebuzz.in/payouts/history")
            await self.page.wait_for_timeout(5000)
            await self._close_popups()

            # Try table first
            data = await self.page.evaluate("""
                () => {
                    const rows = document.querySelectorAll('table tbody tr');
                    const settlements = [];
                    for (const row of rows) {
                        const text = row.textContent.trim();
                        if (text.includes('No data') || text === '') continue;
                        const cells = Array.from(row.querySelectorAll('td')).map(td => td.textContent.trim());
                        if (cells.length >= 6) {
                            settlements.push({
                                date: cells[1] || cells[0],
                                settlement_id: cells[2] || cells[1],
                                settlement_amount: cells[3] || cells[2],
                                service_charge: cells[4] || cells[3],
                                gst: cells[5] || cells[4],
                            });
                        }
                    }
                    return settlements;
                }
            """)

            # If table is empty, extract from summary stats on page
            if not data:
                summary = await self.page.evaluate("""
                    () => {
                        const body = document.body.innerText;
                        const result = {};
                        // Look for Settlement Amount in summary
                        const amtMatch = body.match(/Settlement Amount[\\s\\n]*₹?([\\d,.]+)/);
                        if (amtMatch) result.settlement_amount = amtMatch[1].replace(/,/g, '');
                        // Transaction count
                        const countMatch = body.match(/Transaction count[\\s\\n]*(\\d+)/);
                        if (countMatch) result.transaction_count = countMatch[1];
                        return result;
                    }
                """)
                if summary.get("settlement_amount"):
                    data = [{
                        "date": "Latest",
                        "settlement_id": "N/A (from summary)",
                        "settlement_amount": summary["settlement_amount"],
                        "service_charge": "N/A",
                        "gst": "N/A",
                    }]

            return {"success": True, "settlements": data} if data else {"success": False, "error": "No settlement data found"}

        except Exception as e:
            return {"success": False, "error": str(e)}

    async def get_transaction_detail(self, settlement_id=None):
        """
        Go to Transactions page, find a Success transaction, click it,
        and extract Split Transaction Info (Account, MDR, etc.)
        """
        try:
            await self.page.goto("https://dashboard.easebuzz.in/transaction")
            await self.page.wait_for_timeout(5000)
            await self._close_popups()

            # Find and click a Success transaction
            rows = await self.page.query_selector_all('table:first-of-type tbody tr')
            clicked = False
            for row in rows:
                text = await row.inner_text()
                if 'Success' in text:
                    await row.click(force=True)
                    clicked = True
                    break

            if not clicked:
                return {"success": False, "error": "No successful transaction found"}

            await self.page.wait_for_timeout(5000)
            await self._close_popups()

            # Extract Split Transaction Info table (Table with Account Label, Amount, GST, Service Charge, Settlement Amount)
            split_info = await self.page.evaluate("""
                () => {
                    const tables = document.querySelectorAll('table');
                    for (const table of tables) {
                        const headers = Array.from(table.querySelectorAll('th')).map(th => th.textContent.trim());
                        if (headers.includes('Account Label') || headers.some(h => h.includes('Account'))) {
                            const rows = table.querySelectorAll('tbody tr');
                            const data = [];
                            for (const row of rows) {
                                const cells = Array.from(row.querySelectorAll('td')).map(td => td.textContent.trim());
                                if (cells.length >= 5 && cells[0] !== '') {
                                    data.push({
                                        account_label: cells[0],
                                        amount: cells[1],
                                        gst: cells[2],
                                        service_charge: cells[3],
                                        settlement_amount: cells[4],
                                    });
                                    // Check if there's an account number column
                                    if (cells.length >= 6) {
                                        data[data.length-1].account_number = cells[5];
                                    }
                                }
                            }
                            return data;
                        }
                    }
                    return [];
                }
            """)

            # Extract transaction details (Settlement Amount, Service Charge, etc.)
            tx_details = await self.page.evaluate("""
                () => {
                    const body = document.body.innerText;
                    const result = {};

                    // Extract key-value pairs
                    const patterns = [
                        ['settlement_amount', /Settlement Amount[\\s\\t]+(\\d[\\d,.]+)/],
                        ['service_charge', /Service Charge[\\s\\t]+(\\d[\\d,.]+)/],
                        ['gst', /GST[\\s\\t]+(\\d[\\d,.]+)/],
                        ['amount', /Amount[\\s\\t]+(\\d[\\d,.]+)/],
                        ['payment_mode', /Payment Mode[\\s\\t]+(\\w+)/],
                        ['settlement_status', /Settlement Status[\\s\\t]+(\\w+)/],
                    ];

                    for (const [key, regex] of patterns) {
                        const match = body.match(regex);
                        if (match) result[key] = match[1];
                    }

                    return result;
                }
            """)

            # Extract account number - must be 8+ digits, from "Account Number" label
            account_number = await self.page.evaluate("""
                () => {
                    const body = document.body.innerText;
                    // Look for Account Number field with 8+ digit number
                    const match = body.match(/Account\\s*Number[\\s\\t:]+([\\d]{8,18})/i);
                    if (match) return match[1];
                    return '';
                }
            """)

            result = {
                "split_info": split_info,
                "tx_details": tx_details,
                "account_number": account_number or "",
            }

            # If split info has account number column
            if not account_number and split_info and split_info[0].get("account_number"):
                result["account_number"] = split_info[0]["account_number"]

            return {"success": True, "data": result}

        except Exception as e:
            return {"success": False, "error": str(e)}

    async def run_full_check(self, settlement_id=None):
        """Run complete check: login → settlements → transaction detail."""
        results = {
            "login": None,
            "settlement_history": None,
            "transaction_detail": None,
            "mdr_info": None,
        }

        # Login
        results["login"] = await self.login()
        if not results["login"]["success"]:
            return results

        # Settlement History
        results["settlement_history"] = await self.get_settlement_history()

        # Find target settlement
        target = None
        if results["settlement_history"]["success"]:
            settlements = results["settlement_history"]["settlements"]
            if settlement_id:
                for s in settlements:
                    if s["settlement_id"] == settlement_id:
                        target = s
                        break
            if not target and settlements:
                target = settlements[0]

        # Transaction Detail
        results["transaction_detail"] = await self.get_transaction_detail(settlement_id)

        # Build MDR info
        if target:
            try:
                amt = float(target["settlement_amount"].replace(",", ""))
                sc = float(target["service_charge"].replace(",", ""))
                total = amt + sc
                mdr_pct = round((sc / total) * 100, 2) if total > 0 else "N/A"
            except (ValueError, ZeroDivisionError):
                amt = target["settlement_amount"]
                sc = target["service_charge"]
                mdr_pct = "N/A"

            account_number = ""
            if results["transaction_detail"] and results["transaction_detail"].get("success"):
                raw_account = results["transaction_detail"]["data"].get("account_number", "")
                # Only use if it looks like a real account number (8+ digits, no decimals)
                if raw_account and len(raw_account) >= 8 and '.' not in raw_account:
                    account_number = raw_account

            results["mdr_info"] = {
                "success": True,
                "mdr_info": {
                    "settlement_id": target["settlement_id"],
                    "date": target["date"],
                    "amount": target["settlement_amount"],
                    "service_charge": target["service_charge"],
                    "gst": target["gst"],
                    "settlement_amount": target["settlement_amount"],
                    "account_number": account_number,
                    "mdr_percentage": mdr_pct,
                },
            }

        return results

    async def close(self):
        """Close browser."""
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()
