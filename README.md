# Power BI Exporter

Automates Power BI report exports (daily and weekly): login, navigate report pages, capture screenshots per location filter, merge to PDF, and upload to SharePoint.

## Local setup

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt
playwright install chromium
copy .env.example .env            # fill in credentials
```

### Daily reports

```bash
cd src
python main.py
```

### Weekly reports

```bash
cd src
python main.py --config config/weekly.yaml
```

Or from the project root: `run-weekly.bat`

### Run one report or one mall

```bash
python main.py --report skidata
python main.py --config config/weekly.yaml --report pass
python main.py --config config/weekly.yaml --report skidata --location "Alnoor Mall" --skip-sharepoint
```

## Config layout

| File | Purpose | SharePoint folder |
|------|---------|-------------------|
| `config/daily.yaml` | Daily SKIDATA + Pass | `Daily_Reports` (from `.env` / secret) |
| `config/weekly.yaml` | Weekly SKIDATA + Pass | `Weekly_Reports` (in config) |
| `config/monthly.yaml` | Planned — monthly cadence | — |

## Saved browser session (GitHub cloud runners)

Microsoft login often fails on GitHub-hosted runners. Save a session locally and store it as a secret.

### 1. Save session on your PC

```bash
cd src
python save_session.py
```

A browser opens, signs in to Power BI, and writes `playwright-state/session.json`.

### 2. Encode for GitHub

```bash
python encode_session.py
```

Copy the long base64 output.

### 3. Add GitHub secret

**Settings → Secrets → Actions → New secret**

| Secret | Value |
|--------|-------|
| `PLAYWRIGHT_STORAGE_STATE` | Paste the base64 string from step 2 |

Keep `PBI_EMAIL` and `PBI_PASSWORD` as fallback if the session expires.

### 4. Refresh the session

Sessions expire (often every 2–4 weeks). When GitHub Actions fails on login, repeat steps 1–3.

## Push to GitHub

Your repo: `https://github.com/MHamadaMk24/power-bi-exporter`

From the project folder in PowerShell:

```powershell
cd "c:\Users\Mohammed\Desktop\Projects\Power BI Exporter"

git add config/weekly.yaml .github/workflows/export-weekly.yml src/ README.md run-weekly.bat
git status

git commit -m "Add weekly report export config and GitHub Actions workflow"

git push origin main
```

If this is a **new machine** and `origin` is not set yet:

```powershell
git remote add origin https://github.com/MHamadaMk24/power-bi-exporter.git
git push -u origin main
```

## GitHub repository secrets

In **Settings → Secrets and variables → Actions**, ensure these secrets exist (same for daily and weekly):

| Secret | Description |
|--------|-------------|
| `PBI_EMAIL` | Power BI login email |
| `PBI_PASSWORD` | Power BI login password |
| `TENANT_ID` | Azure AD tenant ID |
| `CLIENT_ID` | App registration client ID |
| `CLIENT_SECRET` | App registration client secret |
| `SHAREPOINT_SITE_NAME` | e.g. `https://tenant.sharepoint.com/sites/YourSite` |
| `SHAREPOINT_DOC_LIB` | Document library name (default: `Documents`) |
| `TARGET_FOLDER_PATH` | Default folder for daily exports, e.g. `Daily_Reports` |
| `PLAYWRIGHT_STORAGE_STATE` | Base64 session from `encode_session.py` |

Weekly uploads use `Weekly_Reports` from `config/weekly.yaml` — no extra secret needed.

## GitHub personal access token (for cron-job.org)

1. GitHub → **Settings** → **Developer settings** → **Personal access tokens**
2. Create a token with **repo** scope (classic) or repository **Contents** + **Metadata** access (fine-grained)
3. Copy the token — you will use it in cron-job.org as `Bearer YOUR_GITHUB_PAT`

## cron-job.org setup

Create **one cron job per cadence** (daily and weekly). Both hit the same API URL; only the JSON body differs.

**Shared settings**

| Field | Value |
|-------|-------|
| **URL** | `https://api.github.com/repos/MHamadaMk24/power-bi-exporter/dispatches` |
| **Method** | `POST` |
| **Header** | `Accept: application/vnd.github+json` |
| **Header** | `Authorization: Bearer YOUR_GITHUB_PAT` |
| **Header** | `X-GitHub-Api-Version: 2022-11-28` |
| **Content-Type** | `application/json` |

### Daily cron job

| Field | Value |
|-------|-------|
| **Title** | Power BI Daily Export |
| **Schedule** | e.g. every day at 8:00 AM (your timezone) |
| **Body** | `{"event_type":"export-daily"}` |

### Weekly cron job

| Field | Value |
|-------|-------|
| **Title** | Power BI Weekly Export |
| **Schedule** | e.g. every Monday at 8:00 AM (your timezone) |
| **Body** | `{"event_type":"export-weekly"}` |

### Manual test (before cron)

After pushing, open GitHub → **Actions**:

- **Export Daily Reports** → Run workflow
- **Export Weekly Reports** → Run workflow

Check logs and PDF artifacts if SharePoint upload fails.

## Project structure

```
config/daily.yaml           Daily SKIDATA + Pass
config/weekly.yaml          Weekly SKIDATA + Pass
.github/workflows/
  export-daily.yml          Triggered by export-daily
  export-weekly.yml         Triggered by export-weekly
src/main.py                 Entry point
src/auth.py                 Power BI login
src/save_session.py         Save browser session locally
src/encode_session.py         Encode session for GitHub secret
src/session_state.py        Load session in main.py / CI
src/browser.py              Navigation and screenshots
src/load_detection.py       Wait for visuals to finish loading
src/export.py               PDF merge
src/sharepoint.py           SharePoint upload via Microsoft Graph
run.bat                     Run daily export locally
run-weekly.bat              Run weekly export locally
```
