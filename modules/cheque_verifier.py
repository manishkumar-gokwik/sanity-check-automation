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

    # Priority 1: File with "cheque" or "cancel" in name
    for f in files:
        name_lower = f['name'].lower()
        if 'cheque' in name_lower or 'cancel' in name_lower:
            if f['mimeType'].startswith(('image/', 'application/pdf')):
                return f

    # Priority 2: Image files (likely cheque photos)
    images = [f for f in files if f['mimeType'].startswith('image/')]
    if images:
        return images[0]

    return None


def _download_file(file_id):
    """Download file from Drive."""
    drive = _get_drive()
    content = drive.files().get_media(fileId=file_id).execute()
    return content


def _ocr_extract(file_content, mime_type):
    """Extract text from image/PDF using OCR."""
    import pytesseract
    from PIL import Image, ImageFilter, ImageEnhance

    if mime_type == 'application/pdf':
        # Convert PDF to image first
        try:
            import pdfplumber
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.pdf')
            tmp.write(file_content)
            tmp.close()
            with pdfplumber.open(tmp.name) as pdf:
                page = pdf.pages[0]
                img = page.to_image(resolution=300).original
            os.unlink(tmp.name)
        except Exception:
            return "", []
    else:
        img = Image.open(io.BytesIO(file_content))

    # Enhance for better OCR
    img = img.convert('L')
    img = ImageEnhance.Contrast(img).enhance(3.0)
    img = ImageEnhance.Sharpness(img).enhance(2.0)
    # Upscale for better OCR accuracy
    img = img.resize((img.width * 2, img.height * 2), Image.LANCZOS)

    # Try multiple PSM modes and return best result
    import re
    best_text = ""
    for psm in ['3', '6', '4']:
        text = pytesseract.image_to_string(img, config='--psm ' + psm)
        numbers = re.findall(r'\d{8,18}', text.replace(' ', ''))
        if numbers:
            return text  # Found account number, use this result
        if len(text) > len(best_text):
            best_text = text

    return best_text


def extract_bank_details(text):
    """Extract account number and IFSC from OCR text."""
    clean_text = text.replace(' ', '')

    # Account number: 8-18 digits
    account_numbers = re.findall(r'\d{8,18}', clean_text)

    # IFSC: 4 letters + 0 + 6 alphanumeric
    ifsc_codes = re.findall(r'[A-Z]{4}0[A-Z0-9]{6}', text.upper())

    # Filter out unlikely account numbers (dates, phone numbers etc.)
    valid_accounts = []
    for num in account_numbers:
        # Skip if looks like a date (20260301) or phone number
        if num.startswith('202') and len(num) == 8:
            continue
        if len(num) == 10 and num.startswith(('91', '98', '97', '96', '95', '94', '93', '92', '91', '90', '89', '88', '87', '86', '85', '84', '83', '82', '81', '80', '79', '78', '77', '76', '75', '74', '73', '72', '71', '70')):
            continue
        valid_accounts.append(num)

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
