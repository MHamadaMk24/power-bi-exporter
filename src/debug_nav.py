import os
from pathlib import Path

import yaml
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

from auth import login_to_power_bi
from browser import click_navigation_button, get_report_frame, open_slicer_dropdown, select_slicer_option

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    load_dotenv(ROOT / ".env")
    with open(ROOT / "config" / "daily.yaml", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=50)
        page = browser.new_page(viewport={"width": 1920, "height": 1080})
        page.goto(config["report_url"], wait_until="domcontentloaded", timeout=120000)
        login_to_power_bi(page, os.environ["PBI_EMAIL"], os.environ["PBI_PASSWORD"])
        host = get_report_frame(page)
        page.wait_for_timeout(3000)

        click_navigation_button(host, config["navigation"]["entry_button"])
        page.wait_for_timeout(5000)
        open_slicer_dropdown(host, "Location")
        select_slicer_option(host, "Alnoor Mall")
        page.keyboard.press("Escape")
        page.wait_for_timeout(5000)

        width = config["navigation"]["page_buttons"]["path_width"]
        paths = host.locator(f'path.ui-role-button-fill[d*="{width}"]')
        print("nav path count:", paths.count())
        for i in range(paths.count()):
            d = paths.nth(i).get_attribute("d") or ""
            clip = paths.nth(i).get_attribute("clip-path") or ""
            print(f"  [{i}] clip={clip[-36:]} d={d[:50]}")

        browser.close()


if __name__ == "__main__":
    main()
