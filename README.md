# Power BI Exporter

Automates daily Power BI report exports: login, navigate report pages, capture screenshots per location filter, merge to PDF, and upload to SharePoint.

## Local setup

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt
playwright install chromium
copy .env.example .env            # fill in credentials
```

Run all daily reports:

```bash
cd src
python main.py
```

Run one report:

```bash
python main.py --report skidata
python main.py --config config/daily.yaml --report pass
```

Test one mall (no SharePoint upload):

```bash
python main.py --report skidata --location "Alnoor Mall" --skip-sharepoint
```

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

## GitHub Actions (cron-job.org)

### 1. Push this repo to GitHub

```bash
git init
git add .
git commit -m "Initial commit"
git remote add origin https://github.com/YOUR_ORG/power-bi-exporter.git
git push -u origin main
```

### 2. Add repository secrets

In **Settings → Secrets and variables → Actions**, add:

| Secret | Description |
|--------|-------------|
| `PBI_EMAIL` | Power BI login email |
| `PBI_PASSWORD` | Power BI login password |
| `TENANT_ID` | Azure AD tenant ID |
| `CLIENT_ID` | App registration client ID |
| `CLIENT_SECRET` | App registration client secret |
| `SHAREPOINT_SITE_NAME` | e.g. `https://tenant.sharepoint.com/sites/YourSite` |
| `SHAREPOINT_DOC_LIB` | Document library name (default: `Documents`) |
| `TARGET_FOLDER_PATH` | SharePoint folder, e.g. `Daily_Reports` |
| `PLAYWRIGHT_STORAGE_STATE` | Base64 session from `encode_session.py` (required for cloud runners) |

### 3. Create a GitHub personal access token

Create a fine-grained or classic PAT with **repo** scope (needed for `repository_dispatch`).

### 4. Configure cron-job.org

Create a cron job with:

- **URL:** `https://api.github.com/repos/YOUR_ORG/power-bi-exporter/dispatches`
- **Method:** `POST`
- **Schedule:** your daily time (cron-job.org uses your timezone)
- **Headers:**
  - `Accept: application/vnd.github+json`
  - `Authorization: Bearer YOUR_GITHUB_PAT`
  - `X-GitHub-Api-Version: 2022-11-28`
- **Body (JSON):**

```json
{"event_type": "export-daily"}
```

You can also trigger manually from GitHub: **Actions → Export Daily Reports → Run workflow**.

### 5. Verify

After the job runs, check **Actions** for logs and download PDFs from the workflow artifacts if SharePoint upload fails.

## Config layout

| File | Purpose |
|------|---------|
| `config/daily.yaml` | Daily SKIDATA + Pass reports (active) |
| `config/weekly.yaml` | Planned — weekly cadence |
| `config/monthly.yaml` | Planned — monthly cadence |

## Project structure

```
config/daily.yaml       Report definitions and timing
src/main.py             Entry point
src/auth.py             Power BI login
src/save_session.py     Save browser session locally
src/encode_session.py   Encode session for GitHub secret
src/session_state.py    Load session in main.py / CI
src/browser.py          Navigation and screenshots
src/load_detection.py   Wait for visuals to finish loading
src/export.py           PDF merge
src/sharepoint.py       SharePoint upload via Microsoft Graph
```
