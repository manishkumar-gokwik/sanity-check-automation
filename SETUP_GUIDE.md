# Sanity Check Automation - Setup Guide

## Step 1: Google Service Account Setup (ZAROORI HAI)

Ye ek baar karna hai, phir sheets automatically read hongi.

### 1.1 Google Cloud Console pe jao
- Open: https://console.cloud.google.com
- GoKwik ka Google account se login karo

### 1.2 New Project banao
- Click "Select Project" → "New Project"
- Name: `sanity-check-automation`
- Click "Create"

### 1.3 APIs Enable karo
- Left menu → "APIs & Services" → "Enable APIs"
- Search aur enable karo ye 2 APIs:
  - **Google Sheets API**
  - **Google Drive API**

### 1.4 Service Account banao
- Left menu → "APIs & Services" → "Credentials"
- Click "Create Credentials" → "Service Account"
- Name: `sanity-bot`
- Click "Done"

### 1.5 Key download karo
- Service account pe click karo (`sanity-bot@...`)
- "Keys" tab → "Add Key" → "Create new key" → JSON
- File download hogi → **rename it to `service_account.json`**
- Move it to: `config/service_account.json`

### 1.6 Sheet access do
- `service_account.json` open karo
- `client_email` copy karo (e.g., `sanity-bot@sanity-check-automation.iam.gserviceaccount.com`)
- Dono Google Sheets open karo:
  - Merchant Onboarding - Sanity
  - Easebuzz - SALT AND KEY
- Har sheet mein: **Share** → paste the email → **Editor** role do

---

## Step 2: Run the App

```bash
cd /home/vidit/Desktop/Automation
source venv/bin/activate
streamlit run app.py
```

Browser mein automatically open hoga: http://localhost:8501

---

## Step 3: Use the Dashboard

1. **Sidebar mein**: Easebuzz ka email aur password daalo
2. **Tab 1**: "Load Merchant Data" click karo → Sheet se data load hoga
3. **Tab 2**: Merchant name aur Settlement ID daalo → "Run Check" click karo
4. **Tab 3**: Results dekhlo → CSV export bhi kar sakte ho

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| "Service account file not found" | `service_account.json` ko `config/` folder mein daalo |
| "Permission denied on sheet" | Sheet ko service account email ke saath share karo |
| "Playwright browser not found" | Run: `source venv/bin/activate && playwright install chromium` |
| "Login failed on Easebuzz" | Check email/password, ya dashboard ka UI change hua ho |
