import os
from pathlib import Path

import yaml
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

from auth import login_to_power_bi

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    load_dotenv(ROOT / ".env")
    with open(ROOT / "config" / "daily.yaml", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    out = ROOT / "output" / "debug"
    out.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=50)
        page = browser.new_page(viewport={"width": 1920, "height": 1080})

        page.goto(config["report_url"], wait_until="domcontentloaded", timeout=120000)
        page.screenshot(path=str(out / "01_initial.png"), full_page=True)
        print("URL after goto:", page.url)

        login_to_power_bi(page, os.environ["PBI_EMAIL"], os.environ["PBI_PASSWORD"])
        print("URL after login:", page.url)

        for wait_s in (5, 15, 30, 60, 90):
            page.wait_for_timeout(5000)
            iframe_count = page.locator("iframe").count()
            report_count = page.locator("div.reportContainer").count()
            slicer_count = page.locator('[data-testid="slicer-dropdown"]').count()
            path_count = page.locator("path.ui-role-button-fill").count()
            print(
                f"@{wait_s}s iframe={iframe_count} reportContainer={report_count} "
                f"slicer={slicer_count} nav_paths={path_count}"
            )
            if report_count or slicer_count or path_count:
                page.screenshot(path=str(out / f"ready_{wait_s}s.png"), full_page=True)
                break

        print("Frames:")
        for i, frame in enumerate(page.frames):
            print(f"  [{i}] {frame.url[:200]}")

        page.screenshot(path=str(out / "03_final.png"), full_page=True)
        browser.close()


if __name__ == "__main__":
    main()
