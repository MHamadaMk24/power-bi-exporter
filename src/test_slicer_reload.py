"""
Test slicer opens after repeated report reloads (reproduces CI export 4/4 failure).

Usage (from src/):
    python test_slicer_reload.py
"""

import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

from auth import navigate_to_report
from browser import (
    get_report_frame,
    list_slicer_options,
    open_slicer_dropdown,
    wait_for_slicer_ready,
)
from main import (
    chromium_launch_kwargs,
    load_config,
    merge_load_detection,
    open_report_entry,
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

    reports = resolve_reports(config, ["skidata"])
    report = reports[0]
    filter_cfg = report["filters"]
    slicer_label = filter_cfg["slicer_label"]
    load_cfg = merge_load_detection(config, report)
    nav_cfg = report["navigation"]

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(**chromium_launch_kwargs(config))
        context_kwargs = {
            "viewport": {"width": 1920, "height": 1080},
            "user_agent": CHROMIUM_USER_AGENT,
        }
        storage_state = resolve_storage_state_path()
        if storage_state:
            context_kwargs["storage_state"] = str(storage_state)
            logger.info("Using saved session: %s", storage_state)
        context = browser.new_context(**context_kwargs)
        page = context.new_page()

        page_waits = __import__("load_detection", fromlist=["build_page_waits"]).build_page_waits(
            load_cfg
        )

        report_frame = open_report_entry(
            page,
            report["report_url"],
            nav_cfg,
            load_cfg,
            page_waits,
            email=email,
            password=password,
            slicer_label=slicer_label,
        )
        locations = list_slicer_options(
            report_frame,
            slicer_label,
            skip_values=filter_cfg.get("skip_values"),
        )
        logger.info("Locations to test (%s): %s", len(locations), locations)

        for index, location in enumerate(locations):
            logger.info(
                "=== Reload test %s/%s: %s ===",
                index + 1,
                len(locations),
                location,
            )
            report_frame = open_report_entry(
                page,
                report["report_url"],
                nav_cfg,
                load_cfg,
                page_waits,
                email=email,
                password=password,
                slicer_label=slicer_label,
            )
            wait_for_slicer_ready(report_frame, slicer_label)
            open_slicer_dropdown(report_frame, slicer_label, page=page)
            logger.info("PASS — slicer opened for %s", location)

        browser.close()

    logger.info("All %s reload + slicer open tests passed", len(locations))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        logger.exception("Slicer reload test failed")
        sys.exit(1)
