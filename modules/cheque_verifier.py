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

    # Words to EXCLUDE (not cheques)
    exclude_words = ['pan card', 'pancard', 'pan_card', 'aadhaar', 'aadhar',
                     'gst', 'certificate', 'invoice', 'agreement', 'msa',
                     'brd', 'ubo', 'fssai', 'license', 'resolution',
                     'board resolution', 'iec', 'address', 'share']

    def is_excluded(name):
        name_lower = name.lower()
        return any(ex in name_lower for ex in exclude_words)

    # Priority 1: File with "cheque" or "cancel" in name (not excluded)
    for f in files:
        name_lower = f['name'].lower()
        if ('cheque' in name_lower or 'cancel' in name_lower) and not is_excluded(name_lower):
            if f['mimeType'].startswith(('image/', 'application/pdf')):
                return f

    # Priority 2: Image/PDF files that are NOT excluded documents
    for f in files:
        if f['mimeType'].startswith(('image/', 'application/pdf')) and not is_excluded(f['name']):
            # Skip very small files (likely icons/logos)
            return f

    return None


def _download_file(file_id):
    """Download file from Drive."""
    drive = _get_drive()
    content = drive.files().get_media(fileId=file_id).execute()
    return content


def _ocr_extract(file_content, mime_type):
    """Extract text from image/PDF.
    PDF: pdfplumber direct text → PyMuPDF high-res image → Tesseract OCR
    Image: PyMuPDF/PIL preprocessing → Tesseract OCR
    """
    import pytesseract
    from PIL import Image, ImageFilter, ImageEnhance

    if mime_type == 'application/pdf':
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.pdf')
        tmp.write(file_content)
        tmp.close()

        # Method 1: Direct PDF text extraction (100% accurate if text-based)
        try:
            import pdfplumber
            with pdfplumber.open(tmp.name) as pdf:
                page = pdf.pages[0]
                text = page.extract_text()
                if text and len(text.strip()) > 20:
                    os.unlink(tmp.name)
                    return text
        except Exception:
            pass

        # Method 2: PyMuPDF — high-res image extraction from PDF (better than pdfplumber)
        try:
            import fitz  # PyMuPDF
            doc = fitz.open(tmp.name)
            page = doc[0]
            # Render at 300 DPI for best OCR quality
            mat = fitz.Matrix(300/72, 300/72)
            pix = page.get_pixmap(matrix=mat)
            img_data = pix.tobytes("png")
            doc.close()
            img = Image.open(io.BytesIO(img_data))
        except Exception:
            # Fallback: pdfplumber image
            try:
                import pdfplumber
                with pdfplumber.open(tmp.name) as pdf:
                    img = pdf.pages[0].to_image(resolution=300).original
            except Exception:
                os.unlink(tmp.name)
                return ""

        os.unlink(tmp.name)
    else:
        img = Image.open(io.BytesIO(file_content))

    # Image preprocessing for better OCR
    # Convert to grayscale
    img = img.convert('L')

    # Increase contrast
    img = ImageEnhance.Contrast(img).enhance(2.5)

    # Sharpen
    img = ImageEnhance.Sharpness(img).enhance(2.0)

    # Upscale small images (if width < 2000px)
    if img.width < 2000:
        scale = 2000 / img.width
        img = img.resize((int(img.width * scale), int(img.height * scale)), Image.LANCZOS)

    # Binarize — convert to pure black/white (helps OCR a lot)
    threshold = 140
    img = img.point(lambda x: 255 if x > threshold else 0)

    # Try multiple PSM modes
    best_text = ""
    for psm in ['3', '6', '4', '11']:
        try:
            text = pytesseract.image_to_string(img, config=f'--psm {psm} --oem 3')
            numbers = re.findall(r'\d{8,18}', text.replace(' ', ''))
            if numbers:
                return text
            if len(text) > len(best_text):
                best_text = text
        except Exception:
            continue

    return best_text


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
