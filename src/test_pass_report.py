"""
Test Pass report flow after SKIDATA (simulates CI handoff).

Usage (from src/):
    python test_pass_report.py
"""

import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

from load_detection import build_page_waits
from main import (
    browser_settings,
    chromium_launch_kwargs,
    load_config,
    merge_load_detection,
    open_report_entry,
    resolve_reports,
    run_report_exports,
)
from session_state import CHROMIUM_USER_AGENT, resolve_storage_state_path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    load_dotenv(ROOT / ".env")
    os.environ.setdefault("GITHUB_ACTIONS", "true")
    config = load_config("config/weekly.yaml")
    email = os.environ["PBI_EMAIL"]
    password = os.environ["PBI_PASSWORD"]

    ski_report, pass_report = resolve_reports(config, ["skidata", "pass"])
    ski_cfg = merge_load_detection(config, ski_report)
    pass_cfg = merge_load_detection(config, pass_report)

    browser_cfg = browser_settings(config)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(**chromium_launch_kwargs(config))
        context_kwargs = {
            "viewport": {
                "width": browser_cfg.get("viewport_width", 1920),
                "height": browser_cfg.get("viewport_height", 1080),
            },
            "user_agent": CHROMIUM_USER_AGENT,
        }
        storage_state = resolve_storage_state_path()
        if storage_state:
            context_kwargs["storage_state"] = str(storage_state)
        page = browser.new_context(**context_kwargs).new_page()

        # Simulate end of SKIDATA run
        logger.info("=== Simulating end of SKIDATA ===")
        open_report_entry(
            page,
            ski_report["report_url"],
            ski_report["navigation"],
            ski_cfg,
            build_page_waits(ski_cfg),
            email=email,
            password=password,
            slicer_label="Location",
        )

        # Start Pass report (same as run_export after SKIDATA)
        logger.info("=== Starting Pass report (CI-style handoff) ===")
        output_dir = ROOT / config.get("output_dir", "output")
        report_pdfs = run_report_exports(
            page,
            pass_report,
            output_dir=output_dir,
            load_cfg=pass_cfg,
            uploader=None,
            upload_after_each=False,
            default_sharepoint_folder="Weekly_Reports",
            email=email,
            password=password,
            only_locations=["Al Hamra Mall"],
            force_navigate=True,
        )
        logger.info("PASS — exported %s PDF(s): %s", len(report_pdfs), report_pdfs)

        browser.close()

    logger.info("Pass report flow test completed successfully")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        logger.exception("Pass report test failed")
        sys.exit(1)
