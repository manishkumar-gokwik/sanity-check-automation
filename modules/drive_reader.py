"""
Google Drive Reader Module
Reads merchant documents from Google Drive:
1. Cancelled Cheque - for bank account verification
2. MSA/PSA Agreement - for TDR rate verification
"""

from googleapiclient.discovery import build
from google.oauth2.service_account import Credentials
from config.settings import SERVICE_ACCOUNT_FILE

SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
]


def get_drive_service():
    """Authenticate and return Google Drive service."""
    creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
    return build("drive", "v3", credentials=creds)


def search_merchant_folder(merchant_name):
    """
    Search for a merchant's folder in Google Drive.
    Returns folder ID and list of files.
    """
    service = get_drive_service()

    # Search for folder with merchant name
    query = f"name contains '{merchant_name}' and mimeType = 'application/vnd.google-apps.folder'"
    results = service.files().list(
        q=query,
        spaces="drive",
        fields="files(id, name, mimeType, modifiedTime)",
        orderBy="modifiedTime desc",
    ).execute()

    folders = results.get("files", [])

    if not folders:
        return {"success": False, "error": f"No folder found for merchant: {merchant_name}"}

    folder = folders[0]
    folder_id = folder["id"]

    # List files in the folder
    file_query = f"'{folder_id}' in parents"
    file_results = service.files().list(
        q=file_query,
        spaces="drive",
        fields="files(id, name, mimeType, modifiedTime)",
    ).execute()

    files = file_results.get("files", [])

    return {
        "success": True,
        "folder_name": folder["name"],
        "folder_id": folder_id,
        "files": files,
    }


def find_cancelled_cheque(merchant_name):
    """
    Find cancelled cheque document for a merchant.
    Returns file info if found.
    """
    folder_result = search_merchant_folder(merchant_name)
    if not folder_result["success"]:
        return folder_result

    files = folder_result["files"]

    # Look for cancelled cheque
    cheque_keywords = ["cancel", "cheque", "check", "chq"]
    for f in files:
        name_lower = f["name"].lower()
        if any(kw in name_lower for kw in cheque_keywords):
            return {
                "success": True,
                "file": f,
                "folder_name": folder_result["folder_name"],
            }

    return {
        "success": False,
        "error": "No cancelled cheque found",
        "available_files": [f["name"] for f in files],
    }


def find_agreement_doc(merchant_name):
    """
    Find MSA/PSA agreement document for a merchant.
    Returns file info if found.
    """
    folder_result = search_merchant_folder(merchant_name)
    if not folder_result["success"]:
        return folder_result

    files = folder_result["files"]

    # Look for agreement document
    agreement_keywords = ["agreement", "msa", "psa", "indicative", "terms", "contract"]
    for f in files:
        name_lower = f["name"].lower()
        if any(kw in name_lower for kw in agreement_keywords):
            return {
                "success": True,
                "file": f,
                "folder_name": folder_result["folder_name"],
            }

    return {
        "success": False,
        "error": "No agreement document found",
        "available_files": [f["name"] for f in files],
    }


def get_merchant_documents(merchant_name):
    """
    Get all relevant documents for a merchant.
    Returns dict with cancelled cheque and agreement info.
    """
    return {
        "cancelled_cheque": find_cancelled_cheque(merchant_name),
        "agreement": find_agreement_doc(merchant_name),
    }
