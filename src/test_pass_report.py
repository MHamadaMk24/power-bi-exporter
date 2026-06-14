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

from browser import (
    click_navigation_button,
    get_report_frame,
    list_slicer_options,
    open_slicer_dropdown,
    wait_for_slicer_ready,
)
from load_detection import build_page_waits
from main import (
    _report_content_ready,
    _wait_cfg,
    chromium_launch_kwargs,
    load_config,
    merge_load_detection,
    navigate_to_report,
    open_report_entry,
    resolve_filter_values,
    resolve_reports,
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
    config = load_config()
    email = os.environ["PBI_EMAIL"]
    password = os.environ["PBI_PASSWORD"]

    ski_report, pass_report = resolve_reports(config, ["skidata", "pass"])
    ski_cfg = merge_load_detection(config, ski_report)
    pass_cfg = merge_load_detection(config, pass_report)
    pass_filter = pass_report["filters"]
    slicer_label = pass_filter["slicer_label"]

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(**chromium_launch_kwargs(config))
        context_kwargs = {
            "viewport": {"width": 1920, "height": 1080},
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

        # Start Pass report (same as run_report_exports)
        logger.info("=== Starting Pass report ===")
        nav_cfg = pass_report["navigation"]
        page_waits = build_page_waits(pass_cfg)

        if not _report_content_ready(page, pass_report["report_url"]):
            navigate_to_report(page, pass_report["report_url"], email, password)
        report_frame = get_report_frame(page)
        page.wait_for_timeout(3000)

        click_navigation_button(report_frame, nav_cfg["entry_button"])
        from load_detection import wait_for_report_ready

        wait_for_report_ready(
            page,
            report_frame,
            **_wait_cfg(pass_cfg, page_waits, 0),
        )

        logger.info("Listing slicers on page...")
        slicers = report_frame.locator('[data-testid="slicer-dropdown"]')
        for i in range(slicers.count()):
            label = slicers.nth(i).get_attribute("aria-label")
            logger.info("  slicer[%s] aria-label=%r", i, label)

        locations = resolve_filter_values(report_frame, page, pass_filter)
        logger.info("PASS — found %s locations: %s", len(locations), locations)

        report_frame = open_report_entry(
            page,
            pass_report["report_url"],
            nav_cfg,
            pass_cfg,
            page_waits,
            email=email,
            password=password,
            slicer_label=slicer_label,
        )
        wait_for_slicer_ready(report_frame, slicer_label)
        open_slicer_dropdown(report_frame, slicer_label, page=page)
        logger.info("PASS — slicer opened after reload for %s", locations[0])

        browser.close()

    logger.info("Pass report flow test completed successfully")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        logger.exception("Pass report test failed")
        sys.exit(1)
