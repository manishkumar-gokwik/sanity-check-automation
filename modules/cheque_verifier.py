"""
Cancelled Cheque Verifier
Downloads cheque images from Google Drive, OCR extracts account number & IFSC.
Matches against Easebuzz settlement data.
"""

import re
import io
import os
import tempfile
from googleapiclient.discovery import build
from google.oauth2.service_account import Credentials
from config.settings import SERVICE_ACCOUNT_FILE

from config.settings import CHEQUE_DRIVE_FOLDER_ID
DRIVE_FOLDER_ID = CHEQUE_DRIVE_FOLDER_ID

_drive = None


def _get_drive():
    """Get Google Drive API client."""
    global _drive
    if _drive is None:
        creds = Credentials.from_service_account_file(
            SERVICE_ACCOUNT_FILE,
            scopes=['https://www.googleapis.com/auth/drive.readonly']
        )
        _drive = build('drive', 'v3', credentials=creds)
    return _drive


def _find_merchant_folder(merchant_name):
    """Find merchant's folder in Drive by name (fuzzy match + Drive-wide search)."""
    drive = _get_drive()
    name_lower = merchant_name.lower().strip()

    # Method 1: Search in the specific cancelled cheque folder
    results = drive.files().list(
        q="'{}' in parents and mimeType = 'application/vnd.google-apps.folder'".format(DRIVE_FOLDER_ID),
        fields='files(id, name)',
        pageSize=200
    ).execute()
    folders = results.get('files', [])

    # Exact match
    for f in folders:
        if f['name'].lower().strip() == name_lower:
            return f

    # Partial match
    for f in folders:
        if name_lower in f['name'].lower() or f['name'].lower() in name_lower:
            return f

    # Method 2: Drive-wide search by name (handles different folder names)
    # Take first 6 chars for Drive search (handles name variations)
    search_term = name_lower[:6] if len(name_lower) >= 6 else name_lower
    try:
        results = drive.files().list(
            q="name contains '{}' and mimeType = 'application/vnd.google-apps.folder'".format(search_term),
            fields='files(id, name)',
            pageSize=20
        ).execute()
        search_folders = results.get('files', [])

        for f in search_folders:
            fname = f['name'].lower()
            if name_lower in fname or fname in name_lower:
                return f
            # Also match without spaces/special chars
            clean_name = name_lower.replace(' ', '')
            clean_fname = fname.replace(' ', '')
            if clean_name in clean_fname or clean_fname in clean_name:
                return f
    except Exception:
        pass

    return None


def _find_cheque_file(folder_id):
    """Find cancelled cheque file inside merchant folder."""
    drive = _get_drive()
    results = drive.files().list(
        q="'{}' in parents".format(folder_id),
        fields='files(id, name, mimeType)',
        pageSize=50
    ).execute()

    files = results.get('files', [])

    # ONLY pick files with "cheque" in the name (prefix, middle, or suffix)
    for f in files:
        name_lower = f['name'].lower()
        if 'cheque' in name_lower:
            if f['mimeType'].startswith(('image/', 'application/pdf')):
                return f

    return None


def _download_file(file_id):
    """Download file from Drive."""
    drive = _get_drive()
    content = drive.files().get_media(fileId=file_id).execute()
    return content


def _gemini_extract(file_content, mime_type):
    """Extract account number from cheque using Gemini AI (95%+ accurate)."""
    try:
        import google.generativeai as genai
        api_key = os.getenv('GEMINI_API_KEY', '')
        if not api_key:
            return None

        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('gemini-2.5-flash')

        # Convert PDF to image if needed
        if mime_type == 'application/pdf':
            try:
                import fitz
                tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.pdf')
                tmp.write(file_content)
                tmp.close()
                doc = fitz.open(tmp.name)
                page = doc[0]
                pix = page.get_pixmap(matrix=fitz.Matrix(300/72, 300/72))
                img_data = pix.tobytes("png")
                doc.close()
                os.unlink(tmp.name)
                mime_for_gemini = "image/png"
                content_for_gemini = img_data
            except Exception:
                return None
        else:
            mime_for_gemini = mime_type
            content_for_gemini = file_content

        import base64
        b64 = base64.b64encode(content_for_gemini).decode('utf-8')

        import time
        # Retry with delay for rate limiting
        response = None
        for attempt in range(3):
            try:
                response = model.generate_content([
            "Extract ONLY the bank account number from this cancelled cheque image. "
            "The account number is usually printed near 'A/C No' or 'Account Number' text. "
            "Do NOT return the MICR code (numbers at bottom of cheque), phone numbers, or IFSC code. "
            "Return ONLY the account number digits, nothing else. "
            "If you cannot find the account number, return 'NOT_FOUND'.",
                {"mime_type": mime_for_gemini, "data": b64}
                ])
                break
            except Exception as retry_err:
                if '429' in str(retry_err) and attempt < 2:
                    time.sleep(40)  # Wait for rate limit reset
                    continue
                raise

        if not response:
            return None

        result = response.text.strip()
        # Clean: remove spaces, newlines, non-digits
        clean = re.sub(r'[^0-9]', '', result)
        if clean and len(clean) >= 8 and 'NOT_FOUND' not in result:
            return clean
        return None
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Gemini extract failed: {e}")
        return None


def _ocr_extract(file_content, mime_type):
    """Extract text from image/PDF.
    Priority: Gemini AI → pdfplumber direct text → Tesseract OCR fallback
    """
    from PIL import Image, ImageEnhance

    # Method 1: Gemini AI (95%+ accurate — best for cheques)
    gemini_result = _gemini_extract(file_content, mime_type)
    if gemini_result:
        return f"A/C No. {gemini_result}"  # Format so extract_bank_details picks it up

    # Method 2: PDF direct text extraction (100% accurate if text-based)
    if mime_type == 'application/pdf':
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.pdf')
        tmp.write(file_content)
        tmp.close()
        try:
            import pdfplumber
            with pdfplumber.open(tmp.name) as pdf:
                text = pdf.pages[0].extract_text()
                if text and len(text.strip()) > 20:
                    os.unlink(tmp.name)
                    return text
        except Exception:
            pass
        try:
            os.unlink(tmp.name)
        except Exception:
            pass

    # Method 3: Tesseract OCR fallback
    try:
        import pytesseract
        if mime_type == 'application/pdf':
            try:
                import fitz
                tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.pdf')
                tmp.write(file_content)
                tmp.close()
                doc = fitz.open(tmp.name)
                pix = doc[0].get_pixmap(matrix=fitz.Matrix(300/72, 300/72))
                img = Image.open(io.BytesIO(pix.tobytes("png")))
                doc.close()
                os.unlink(tmp.name)
            except Exception:
                return ""
        else:
            img = Image.open(io.BytesIO(file_content))

        img = img.convert('L')
        img = ImageEnhance.Contrast(img).enhance(2.5)
        if img.width < 2000:
            scale = 2000 / img.width
            img = img.resize((int(img.width * scale), int(img.height * scale)), Image.LANCZOS)

        text = pytesseract.image_to_string(img, config='--psm 3 --oem 3')
        return text
    except Exception:
        return ""


def extract_bank_details(text):
    """Extract account number and IFSC from OCR text.
    Priority: number near 'A/C', 'Account', 'No' keywords.
    Excludes MICR codes (9-digit codes at bottom of cheque).
    """
    # IFSC: 4 letters + 0 + 6 alphanumeric
    ifsc_codes = re.findall(r'[A-Z]{4}0[A-Z0-9]{6}', text.upper())

    # Find all candidates with context
    candidates = []  # list of (number, priority, position)

    # Priority 1: Numbers near account-related keywords
    # Pattern: "A/C No.: 12345678" or "Account Number: 12345678"
    patterns_with_context = [
        (r'(?:A/?[Cc]\s*(?:No\.?)?\s*[:.]?\s*)(\d{8,18})', 100),
        (r'(?:Account\s*(?:No\.?|Number)?\s*[:.]?\s*)(\d{8,18})', 100),
        (r'(?:CURRENT\s*A/?C[\s\S]{0,50}?)(\d{8,18})', 90),
        (r'(?:SAVING\s*A/?C[\s\S]{0,50}?)(\d{8,18})', 90),
    ]

    for pattern, priority in patterns_with_context:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            num = match.group(1)
            candidates.append((num, priority, match.start()))

    # Priority 2: All 8-18 digit numbers (filtered)
    clean_text = text.replace(' ', '')
    all_numbers = re.findall(r'\d{8,18}', clean_text)

    # Collect phone/fax numbers to exclude
    phone_patterns = re.findall(r'(?:Tel|Fax|Phone|Mobile|Contact)\s*[:.]?\s*(\d{8,15})', text, re.IGNORECASE)
    phone_set = set(phone_patterns)

    for num in all_numbers:
        # Skip dates (20260301)
        if num.startswith('202') and len(num) == 8:
            continue
        # Skip phone numbers (starts with 2-9, exactly 10 digits)
        if len(num) == 10 and num[0] in '23456789':
            continue
        # Skip numbers identified as phone/fax
        if num in phone_set:
            continue
        # Skip MICR-like codes (9 digits)
        if len(num) == 9:
            continue
        # Skip short numbers
        if len(num) < 10:
            continue
        # Skip SWIFT codes or PIN codes
        if len(num) == 11 and num.startswith('0'):
            continue
        candidates.append((num, 10, 0))

    # Dedupe keeping highest priority
    seen = {}
    for num, priority, pos in candidates:
        if num not in seen or seen[num][0] < priority:
            seen[num] = (priority, pos)

    # Sort by priority (highest first)
    sorted_nums = sorted(seen.items(), key=lambda x: -x[1][0])
    valid_accounts = [n for n, _ in sorted_nums]

    return {
        "account_numbers": valid_accounts,
        "ifsc_codes": ifsc_codes,
        "primary_account": valid_accounts[0] if valid_accounts else None,
        "primary_ifsc": ifsc_codes[0] if ifsc_codes else None,
    }


def verify_cheque(merchant_name):
    """
    Full flow: Find merchant folder → Find cheque → OCR → Extract account number.
    Returns dict with account details or error.
    """
    try:
        # Find folder
        folder = _find_merchant_folder(merchant_name)
        if not folder:
            return {
                "success": False,
                "status": "WARN",
                "message": "No folder found in Drive for '{}'".format(merchant_name),
            }

        # Find cheque file
        cheque = _find_cheque_file(folder['id'])
        if not cheque:
            return {
                "success": False,
                "status": "WARN",
                "message": "No cancelled cheque found in '{}' folder".format(folder['name']),
            }

        # Download
        content = _download_file(cheque['id'])

        # OCR
        text = _ocr_extract(content, cheque['mimeType'])
        if not text:
            return {
                "success": False,
                "status": "WARN",
                "message": "Could not read cheque image: {}".format(cheque['name']),
            }

        # Extract bank details
        details = extract_bank_details(text)

        if details['primary_account']:
            return {
                "success": True,
                "status": "PASS",
                "message": "Account: {} | IFSC: {}".format(
                    details['primary_account'],
                    details['primary_ifsc'] or 'N/A'
                ),
                "account_number": details['primary_account'],
                "account_numbers": details['account_numbers'],
                "ifsc": details['primary_ifsc'],
                "file_name": cheque['name'],
                "folder_name": folder['name'],
            }
        else:
            return {
                "success": False,
                "status": "WARN",
                "message": "Could not extract account number from cheque: {}".format(cheque['name']),
            }

    except Exception as e:
        return {
            "success": False,
            "status": "WARN",
            "message": "Cheque verification error: {}".format(str(e)),
        }
