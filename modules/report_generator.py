"""
PDF Report Generator for Sanity Check Results
Generates professional reports with GoKwik branding.
"""

from fpdf import FPDF
from datetime import datetime
import os


class SanityReport(FPDF):
    def header(self):
        self.set_font('Helvetica', 'B', 16)
        self.set_text_color(26, 42, 64)
        self.cell(0, 10, 'GoKwik - Sanity Check Report', align='C', new_x="LMARGIN", new_y="NEXT")
        self.set_font('Helvetica', '', 9)
        self.set_text_color(100, 116, 139)
        self.cell(0, 6, f'Generated: {datetime.now().strftime("%d %b %Y, %I:%M %p")}', align='C', new_x="LMARGIN", new_y="NEXT")
        self.set_draw_color(59, 130, 246)
        self.set_line_width(0.8)
        self.line(10, self.get_y() + 2, 200, self.get_y() + 2)
        self.ln(8)

    def footer(self):
        self.set_y(-15)
        self.set_font('Helvetica', 'I', 8)
        self.set_text_color(148, 163, 184)
        self.cell(0, 10, f'Sanity Check Automation v1.0 | GoKwik Merchant Onboarding | Page {self.page_no()}', align='C')


def generate_report(batch_result, output_path=None):
    """
    Generate PDF report from batch sanity check results.
    Returns the file path of the generated PDF.
    """
    if output_path is None:
        os.makedirs('reports', exist_ok=True)
        output_path = f'reports/sanity_report_{datetime.now().strftime("%Y%m%d_%H%M")}.pdf'

    pdf = SanityReport()
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.add_page()

    # Summary Section
    date = batch_result.get('date', 'N/A')
    total = batch_result.get('total_merchants', 0)
    passed = batch_result.get('passed', 0)
    failed = batch_result.get('failed', 0)
    warned = batch_result.get('warned', 0)

    pdf.set_font('Helvetica', 'B', 12)
    pdf.set_text_color(26, 42, 64)
    pdf.cell(0, 8, f'Summary - Merchants Added on {date}', new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)

    # Summary boxes
    _draw_summary_box(pdf, 10, pdf.get_y(), 'Total', str(total), (59, 130, 246))
    _draw_summary_box(pdf, 57, pdf.get_y(), 'Passed', str(passed), (16, 185, 129))
    _draw_summary_box(pdf, 104, pdf.get_y(), 'Failed', str(failed), (239, 68, 68))
    _draw_summary_box(pdf, 151, pdf.get_y(), 'Warnings', str(warned), (245, 158, 11))
    pdf.ln(28)

    # Results Table
    results = batch_result.get('results', [])

    for i, result in enumerate(results):
        # Check if we need a new page
        if pdf.get_y() > 230:
            pdf.add_page()

        merchant_name = result.get('merchant_name', 'Unknown')
        overall = result.get('overall_status', 'UNKNOWN')
        pass_count = result.get('pass_count', 0)
        total_checks = result.get('total_checks', 0)
        poc = result.get('poc', '')

        # Merchant header
        if overall == 'PASS':
            bg_color = (209, 250, 229)
            text_color = (6, 95, 70)
            icon = 'PASS'
        elif overall == 'FAIL':
            bg_color = (254, 226, 226)
            text_color = (153, 27, 27)
            icon = 'FAIL'
        else:
            bg_color = (254, 243, 199)
            text_color = (146, 64, 14)
            icon = 'WARN'

        pdf.set_fill_color(*bg_color)
        pdf.set_text_color(*text_color)
        pdf.set_font('Helvetica', 'B', 10)
        pdf.cell(0, 8, f'  {icon}  {merchant_name} - {overall} ({pass_count}/{total_checks} passed)' +
                 (f'  |  POC: {poc}' if poc else ''),
                 fill=True, new_x="LMARGIN", new_y="NEXT")

        # Check details
        checks = result.get('checks', [])
        for check in checks:
            if pdf.get_y() > 270:
                pdf.add_page()

            check_name = check.get('check_name', '')
            status = check.get('status', '')
            message = check.get('message', '')
            actual = check.get('actual', '')

            # Status color
            if status == 'PASS':
                pdf.set_text_color(16, 185, 129)
                status_text = 'PASS'
            elif status == 'FAIL':
                pdf.set_text_color(239, 68, 68)
                status_text = 'FAIL'
            elif status == 'PENDING':
                pdf.set_text_color(148, 163, 184)
                status_text = 'PENDING'
            else:
                pdf.set_text_color(245, 158, 11)
                status_text = status

            pdf.set_font('Helvetica', '', 8)
            pdf.set_text_color(71, 85, 105)
            pdf.cell(55, 6, f'    {check_name}', new_x="RIGHT")
            pdf.set_font('Helvetica', 'B', 8)

            if status == 'PASS':
                pdf.set_text_color(16, 185, 129)
            elif status == 'FAIL':
                pdf.set_text_color(239, 68, 68)
            elif status == 'PENDING':
                pdf.set_text_color(148, 163, 184)
            else:
                pdf.set_text_color(245, 158, 11)

            pdf.cell(15, 6, status_text, new_x="RIGHT")
            pdf.set_font('Helvetica', '', 8)
            pdf.set_text_color(100, 116, 139)
            # Truncate message if too long
            msg = message[:80] + ('...' if len(message) > 80 else '')
            pdf.cell(0, 6, msg, new_x="LMARGIN", new_y="NEXT")

        pdf.ln(3)

    pdf.output(output_path)
    return output_path


def _draw_summary_box(pdf, x, y, label, value, color):
    """Draw a summary metric box."""
    pdf.set_fill_color(248, 250, 252)
    pdf.set_draw_color(226, 232, 240)
    pdf.rect(x, y, 44, 22, style='DF')

    pdf.set_xy(x, y + 3)
    pdf.set_font('Helvetica', '', 7)
    pdf.set_text_color(107, 123, 141)
    pdf.cell(44, 4, label.upper(), align='C')

    pdf.set_xy(x, y + 9)
    pdf.set_font('Helvetica', 'B', 14)
    pdf.set_text_color(*color)
    pdf.cell(44, 10, value, align='C')
