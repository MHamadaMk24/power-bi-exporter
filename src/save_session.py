"""
Log in to Power BI once and save the browser session for GitHub Actions.

Usage (from src/):
    python save_session.py

Then encode for GitHub:
    python encode_session.py
"""

import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

from auth import is_report_ready, navigate_to_report
from main import load_config, resolve_reports
from session_state import CHROMIUM_USER_AGENT, DEFAULT_SESSION_PATH

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    load_dotenv(ROOT / ".env")
    config = load_config()

    email = os.environ.get("PBI_EMAIL")
    password = os.environ.get("PBI_PASSWORD")
    if not email or not password:
        raise RuntimeError("PBI_EMAIL and PBI_PASSWORD must be set in .env")

    reports = resolve_reports(config)
    browser_cfg = config.get("browser", {})

    DEFAULT_SESSION_PATH.parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=browser_cfg.get("headless", False),
            slow_mo=browser_cfg.get("slow_mo", 0),
        )
        context = browser.new_context(
            viewport={
                "width": browser_cfg.get("viewport_width", 1920),
                "height": browser_cfg.get("viewport_height", 1080),
            },
            user_agent=CHROMIUM_USER_AGENT,
        )
        page = context.new_page()

        logger.info("Signing in to Power BI — complete any prompts in the browser")
        navigate_to_report(page, reports[0]["report_url"], email, password)

        if not is_report_ready(page):
            browser.close()
            raise RuntimeError("Login did not reach the report page. Session not saved.")

        context.storage_state(path=str(DEFAULT_SESSION_PATH))
        browser.close()

    logger.info("Saved session to %s", DEFAULT_SESSION_PATH)
    logger.info("Next: python encode_session.py")
    print(f"\nSession saved: {DEFAULT_SESSION_PATH}")
    print("Run: python encode_session.py")
    print("Then paste the output into GitHub secret PLAYWRIGHT_STORAGE_STATE")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        logger.exception("Failed to save session")
        sys.exit(1)
