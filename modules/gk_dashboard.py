"""
GoKwik Dashboard Automation Module
Login via email/password/OTP → Switch merchant → Check Orders, Payment, Webhook
URL: https://sandbox-mdashboard.dev.gokwik.in
"""

import asyncio
from playwright.async_api import async_playwright

GK_LOGIN_URL = "https://sandbox-mdashboard.dev.gokwik.in/login"
GK_BASE_URL = "https://sandbox-mdashboard.dev.gokwik.in"


class GKDashboard:
    def __init__(self, email="sandboxuser1@gokwik.co", password="Wb7y,=e.9NX9", otp="123456"):
        self.email = email
        self.password = password
        self.otp = otp
        self.browser = None
        self.page = None
        self.playwright = None

    async def start(self, headless=True):
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(
            headless=headless,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        context = await self.browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1366, "height": 768},
        )
        self.page = await context.new_page()
        self.page.set_default_timeout(30000)

    async def login(self):
        """Login: Email → Next → Password → Next → OTP → Next"""
        try:
            await self.page.goto(GK_LOGIN_URL)
            await asyncio.sleep(5)

            # Email
            email_input = await self.page.query_selector('input[type="email"]')
            if not email_input:
                return {"success": False, "error": "Email field not found"}
            await email_input.click()
            await self.page.keyboard.type(self.email, delay=30)
            await self.page.click('button:has-text("Next")')
            await asyncio.sleep(3)

            # Password
            pass_input = await self.page.query_selector('input[type="password"]')
            if pass_input and await pass_input.is_visible():
                await pass_input.click()
                await self.page.keyboard.type(self.password, delay=30)
                await self.page.click('button:has-text("Next")')
                await asyncio.sleep(5)

            # OTP
            if "verify-otp" in self.page.url:
                for inp in await self.page.query_selector_all('input'):
                    if await inp.is_visible():
                        t = await inp.get_attribute('type')
                        if t in ('text', 'tel', 'number', None):
                            await inp.click()
                            await inp.fill(self.otp)
                            break
                await self.page.click('button:has-text("Next")')
                await asyncio.sleep(8)

            if "login" not in self.page.url:
                return {"success": True}
            return {"success": False, "error": "Still on login page"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def get_orders(self):
        """Get orders list from Orders section."""
        try:
            await self.page.goto(f"{GK_BASE_URL}/checkout/orders")
            await asyncio.sleep(5)

            orders = await self.page.evaluate("""
                () => {
                    const rows = document.querySelectorAll('table tbody tr');
                    const data = [];
                    for (const row of rows) {
                        const cells = Array.from(row.querySelectorAll('td')).map(td => td.textContent.trim());
                        if (cells.length >= 7) {
                            data.push({
                                order_number: cells[0],
                                order_status: cells[1],
                                platform_order: cells[2],
                                created_at: cells[3],
                                payment_mode: cells[4],
                                payment_status: cells[5],
                                grand_total: cells[6],
                            });
                        }
                    }
                    return data;
                }
            """)
            return {"success": True, "orders": orders, "count": len(orders)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def get_order_detail(self, order_number):
        """Get detailed order info including payment, webhook status."""
        try:
            await self.page.goto(f"{GK_BASE_URL}/checkout/orders/{order_number}")
            await asyncio.sleep(5)

            body = await self.page.inner_text('body')

            # Extract key-value pairs
            detail = {}
            lines = body.split('\n')
            for i, line in enumerate(lines):
                line = line.strip()
                if ':' in line and len(line) < 200:
                    parts = line.split(':', 1)
                    if len(parts) == 2:
                        key = parts[0].strip()
                        val = parts[1].strip()
                        if key and val:
                            detail[key] = val

            # Specific checks
            payment_status = detail.get("Payment Status", "")
            payment_type = detail.get("Payment Type", "")
            c2p_order = detail.get("C2P Order", "")
            onpl_order = detail.get("ONPL Order", "")
            order_status = detail.get("Order Status", "")

            # Check for VPN - payment link present means VPN working
            has_payment_link = "upi://pay" in body or "razorpay" in body.lower() or "payu" in body.lower()

            return {
                "success": True,
                "order_number": order_number,
                "order_status": order_status,
                "payment_status": payment_status,
                "payment_type": payment_type,
                "c2p_order": c2p_order,
                "onpl_order": onpl_order,
                "has_payment_link": has_payment_link,
                "detail": detail,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def get_global_settings(self):
        """Check Global Settings - Payment terminals configured."""
        try:
            await self.page.goto(f"{GK_BASE_URL}/global-settings/payments")
            await asyncio.sleep(5)

            terminals = await self.page.evaluate("""
                () => {
                    const rows = document.querySelectorAll('table tbody tr');
                    const data = [];
                    for (const row of rows) {
                        const cells = Array.from(row.querySelectorAll('td')).map(td => td.textContent.trim());
                        if (cells.length >= 4) {
                            data.push({
                                provider: cells[0],
                                status: cells[1],
                                created_at: cells[2],
                                updated_at: cells[3],
                            });
                        }
                    }
                    return data;
                }
            """)

            active_terminals = [t for t in terminals if t.get("status", "").lower() == "active"]

            return {
                "success": True,
                "terminals": terminals,
                "active_count": len(active_terminals),
                "configured": len(terminals) > 0,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def run_merchant_check(self, merchant_name=None):
        """Run complete GK check: orders + payment + settings."""
        result = {
            "login": None,
            "orders": None,
            "order_detail": None,
            "global_settings": None,
            "checks": [],
        }

        # Check 1: Orders exist
        orders_result = await self.get_orders()
        result["orders"] = orders_result

        if orders_result.get("success") and orders_result.get("orders"):
            result["checks"].append({
                "check_name": "GK Orders",
                "status": "PASS",
                "message": f"{orders_result['count']} orders found",
                "actual": f"Latest: {orders_result['orders'][0].get('order_number', 'N/A')}",
            })

            # Check 2: Order detail - payment working
            first_order = orders_result["orders"][0]["order_number"]
            detail = await self.get_order_detail(first_order)
            result["order_detail"] = detail

            if detail.get("success"):
                # Payment status check
                if detail.get("payment_status", "").lower() in ("true", "paid", "success"):
                    result["checks"].append({
                        "check_name": "Payment Status",
                        "status": "PASS",
                        "message": f"Payment working - Type: {detail.get('payment_type', 'N/A')}",
                        "actual": f"Status: {detail.get('payment_status')}",
                    })
                else:
                    result["checks"].append({
                        "check_name": "Payment Status",
                        "status": "WARN",
                        "message": f"Payment Status: {detail.get('payment_status', 'Unknown')}",
                        "actual": "",
                    })

                # VPN check - payment link present
                if detail.get("has_payment_link"):
                    result["checks"].append({
                        "check_name": "VPN (Payment Notification)",
                        "status": "PASS",
                        "message": "Payment link/notification configured",
                        "actual": f"Type: {detail.get('payment_type', 'N/A')}",
                    })
                else:
                    result["checks"].append({
                        "check_name": "VPN (Payment Notification)",
                        "status": "WARN",
                        "message": "No payment link found in order",
                        "actual": "",
                    })
        else:
            result["checks"].append({
                "check_name": "GK Orders",
                "status": "WARN",
                "message": "No orders found or could not fetch",
                "actual": orders_result.get("error", ""),
            })

        # Check 3: Global Settings - terminals
        settings = await self.get_global_settings()
        result["global_settings"] = settings

        if settings.get("success") and settings.get("configured"):
            result["checks"].append({
                "check_name": "Payment Gateway Config",
                "status": "PASS",
                "message": f"{settings['active_count']} active payment terminal(s)",
                "actual": ", ".join([t["provider"] for t in settings.get("terminals", [])]),
            })
        else:
            result["checks"].append({
                "check_name": "Payment Gateway Config",
                "status": "WARN",
                "message": "No payment terminals configured",
                "actual": "",
            })

        return result

    async def close(self):
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()
