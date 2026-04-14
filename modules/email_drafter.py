"""
Email Draft Generator for Discrepancy Reports
Generates professional email drafts to send to Easebuzz team.
"""

from datetime import datetime


def generate_discrepancy_email(merchant_results):
    """
    Generate email draft for merchants with discrepancies.
    Returns dict with subject, to, body.
    """
    failed_merchants = [r for r in merchant_results if r.get("overall_status") == "FAIL"]
    warned_merchants = [r for r in merchant_results if r.get("overall_status") == "WARN"]

    if not failed_merchants and not warned_merchants:
        return None  # No discrepancies

    date = datetime.now().strftime("%d %b %Y")
    total_issues = len(failed_merchants) + len(warned_merchants)

    subject = f"Sanity Check Discrepancies - {date} ({total_issues} merchant(s) require attention)"

    body = f"""Hi Easebuzz Team,

During our automated sanity check on {date}, we found discrepancies in the following merchant accounts that require your attention:

"""

    if failed_merchants:
        body += "=" * 60 + "\n"
        body += "CRITICAL ISSUES (FAIL)\n"
        body += "=" * 60 + "\n\n"

        for m in failed_merchants:
            body += f"Merchant: {m['merchant_name']}\n"
            body += f"Date Added: {m.get('date', 'N/A')}\n"
            for check in m.get("checks", []):
                if check["status"] == "FAIL":
                    body += f"  - {check['check_name']}: {check['message']}\n"
                    if check.get("expected"):
                        body += f"    Expected: {check['expected']}\n"
                    if check.get("actual"):
                        body += f"    Actual: {check['actual']}\n"
            body += "\n"

    if warned_merchants:
        body += "-" * 60 + "\n"
        body += "WARNINGS (Need Review)\n"
        body += "-" * 60 + "\n\n"

        for m in warned_merchants:
            body += f"Merchant: {m['merchant_name']}\n"
            for check in m.get("checks", []):
                if check["status"] in ("WARN", "PENDING"):
                    body += f"  - {check['check_name']}: {check['message']}\n"
            body += "\n"

    body += f"""
Please review and rectify the above discrepancies at the earliest.

Thanks & Regards,
GoKwik Merchant Onboarding Team
(Automated Sanity Check System)
"""

    return {
        "subject": subject,
        "to": "Easebuzz Team",
        "body": body,
        "failed_count": len(failed_merchants),
        "warned_count": len(warned_merchants),
    }
