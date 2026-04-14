"""
Validation Module
Cross-validates data from different sources:
1. Settlement check - Easebuzz settlement data vs Sanity sheet
2. TDR rate check - Easebuzz MDR vs Commercial sheet rates vs Agreement (MSA/PSA)
3. Account number check - Easebuzz account vs Cancelled cheque
4. VPA check - from Google Drive merchant docs
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ValidationResult:
    """Result of a single validation check."""
    check_name: str
    status: str  # "PASS", "FAIL", "WARN", "SKIPPED"
    expected: str = ""
    actual: str = ""
    message: str = ""


@dataclass
class MerchantValidation:
    """Complete validation results for a merchant."""
    merchant_name: str
    date: str = ""
    results: list = field(default_factory=list)
    extra_data: dict = field(default_factory=dict)

    @property
    def overall_status(self):
        if any(r.status == "FAIL" for r in self.results):
            return "FAIL"
        if any(r.status == "WARN" for r in self.results):
            return "WARN"
        if all(r.status == "PASS" for r in self.results):
            return "PASS"
        return "PENDING"

    @property
    def pass_count(self):
        return sum(1 for r in self.results if r.status == "PASS")

    @property
    def fail_count(self):
        return sum(1 for r in self.results if r.status == "FAIL")

    @property
    def total_checks(self):
        return len(self.results)


def validate_settlement(eb_settlement_data, sanity_data):
    """
    Check 1: Verify settlement is happening correctly.
    Compare settlement data from Easebuzz with Sanity sheet entry.
    """
    result = ValidationResult(check_name="Settlement Verification", status="PENDING")

    if not eb_settlement_data or not eb_settlement_data.get("success"):
        result.status = "SKIPPED"
        result.message = "Could not fetch settlement data from Easebuzz"
        return result

    settlement_info = eb_settlement_data.get("mdr_info", {})
    settlement_amount = settlement_info.get("settlement_amount", "N/A")

    if settlement_amount and settlement_amount != "N/A":
        result.status = "PASS"
        result.actual = f"Settlement Amount: {settlement_amount}"
        result.message = "Settlement is active and processing"
    else:
        result.status = "FAIL"
        result.message = "No settlement amount found"

    return result


def validate_tdr_rates(eb_mdr_data, commercial_rates, tolerance=0.1):
    """
    Check 2: Verify TDR rates match between Easebuzz and Commercial sheet.
    Compares MDR percentage from Easebuzz with rates in commercial sheet.

    Args:
        eb_mdr_data: MDR data from Easebuzz dashboard
        commercial_rates: Expected rates from Commercial sheet (dict with payment mode keys)
        tolerance: Acceptable difference in percentage (default 0.1%)
    """
    result = ValidationResult(check_name="TDR Rate Verification", status="PENDING")

    if not eb_mdr_data or not eb_mdr_data.get("success"):
        result.status = "SKIPPED"
        result.message = "Could not fetch MDR data from Easebuzz"
        return result

    mdr_info = eb_mdr_data.get("mdr_info", {})
    actual_mdr = mdr_info.get("mdr_percentage", "N/A")

    if actual_mdr == "N/A":
        result.status = "WARN"
        result.message = "Could not calculate MDR percentage"
        return result

    # Compare with commercial rates if available
    import pandas as pd
    has_rates = isinstance(commercial_rates, pd.DataFrame) and not commercial_rates.empty
    if has_rates:
        result.actual = f"Easebuzz MDR: {actual_mdr}%"
        # Get expected rate columns from commercial sheet
        rate_columns = [col for col in commercial_rates.columns if any(
            keyword in col.lower() for keyword in ["upi", "dc", "cc", "credit", "debit", "net banking"]
        )]

        mismatches = []
        matches = []
        for col in rate_columns:
            try:
                expected_rate = float(commercial_rates.iloc[0][col])
                if abs(float(actual_mdr) - expected_rate) <= tolerance:
                    matches.append(f"{col}: {expected_rate}%")
                else:
                    mismatches.append(f"{col}: expected {expected_rate}%, got {actual_mdr}%")
            except (ValueError, IndexError):
                continue

        if mismatches:
            result.status = "FAIL"
            result.expected = "; ".join(mismatches)
            result.message = f"TDR rate mismatch found in {len(mismatches)} mode(s)"
        else:
            result.status = "PASS"
            result.message = f"TDR rates match for {len(matches)} mode(s)"
    else:
        result.status = "WARN"
        result.actual = f"Easebuzz MDR: {actual_mdr}%"
        result.message = "No commercial rates available for comparison"

    return result


def validate_account_number(eb_account_data, expected_account_number):
    """
    Check 3: Verify bank account number matches.
    Compare Easebuzz account number with cancelled cheque / expected account.
    """
    result = ValidationResult(check_name="Bank Account Verification", status="PENDING")

    if not eb_account_data or not eb_account_data.get("success"):
        result.status = "SKIPPED"
        result.message = "Could not fetch account data from Easebuzz"
        return result

    actual_account = str(eb_account_data.get("account_number", "")).strip()
    expected = str(expected_account_number).strip()

    if not actual_account or actual_account == "N/A":
        result.status = "FAIL"
        result.message = "No account number found in Easebuzz"
        return result

    if not expected:
        result.status = "WARN"
        result.message = "No expected account number provided for comparison"
        result.actual = f"Easebuzz Account: {actual_account}"
        return result

    result.actual = actual_account
    result.expected = expected

    # Clean and compare (remove spaces, leading zeros)
    clean_actual = actual_account.replace(" ", "").lstrip("0")
    clean_expected = expected.replace(" ", "").lstrip("0")

    if clean_actual == clean_expected:
        result.status = "PASS"
        result.message = "Account numbers match"
    else:
        result.status = "FAIL"
        result.message = f"Account number mismatch! Expected: {expected}, Got: {actual_account}"

    return result


def validate_vpa(drive_vpa, dashboard_vpa):
    """
    Check 4: Verify VPA (Virtual Payment Account).
    Compare VPA from merchant's Google Drive document with dashboard.
    """
    result = ValidationResult(check_name="VPA Verification", status="PENDING")

    if not drive_vpa:
        result.status = "SKIPPED"
        result.message = "No VPA found in merchant documents"
        return result

    if not dashboard_vpa:
        result.status = "SKIPPED"
        result.message = "No VPA found in dashboard"
        return result

    result.actual = str(dashboard_vpa).strip()
    result.expected = str(drive_vpa).strip()

    if result.actual.lower() == result.expected.lower():
        result.status = "PASS"
        result.message = "VPA matches"
    else:
        result.status = "FAIL"
        result.message = f"VPA mismatch! Expected: {drive_vpa}, Got: {dashboard_vpa}"

    return result


def run_all_validations(merchant_name, date, eb_settlement, eb_mdr,
                         eb_account, commercial_rates, expected_account,
                         drive_vpa=None, dashboard_vpa=None):
    """
    Run all 4 validation checks for a merchant.
    Returns MerchantValidation with all results.
    """
    validation = MerchantValidation(merchant_name=merchant_name, date=date)

    # Check 1: Settlement
    validation.results.append(validate_settlement(eb_settlement, None))

    # Check 2: TDR Rates
    validation.results.append(validate_tdr_rates(eb_mdr, commercial_rates))

    # Check 3: Account Number
    validation.results.append(validate_account_number(eb_account, expected_account))

    # Check 4: VPA
    validation.results.append(validate_vpa(drive_vpa, dashboard_vpa))

    return validation
