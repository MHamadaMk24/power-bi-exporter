import os
from pathlib import Path

import yaml
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

from auth import login_to_power_bi
from browser import REPORT_READY_SELECTORS, get_report_frame

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

        paths = host.locator("path.ui-role-button-fill")
        print("path count:", paths.count())
        for i in range(min(paths.count(), 20)):
            d = paths.nth(i).get_attribute("d") or ""
            clip = paths.nth(i).get_attribute("clip-path") or ""
            print(f"[{i}] clip={clip[-40:]} d_width={d.split('L')[1].split()[0] if 'L' in d else '?'}")

        for label in ("Daily", "Weekly", "Monthly"):
            loc = host.get_by_text(label, exact=True)
            print(f"text '{label}' count:", loc.count())

        clip = config["navigation"]["entry_button_clip_path"]
        print("configured clip-path match:", host.locator(f'path[clip-path="url(#{clip})"]').count())
        print("wide path match:", host.locator('path.ui-role-button-fill[d*="184.275"]').count())

        browser.close()


if __name__ == "__main__":
    main()
