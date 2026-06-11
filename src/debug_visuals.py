import os
from pathlib import Path

import yaml
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

from auth import login_to_power_bi
from browser import click_navigation_button, get_report_frame, open_slicer_dropdown, select_slicer_option
from load_detection import evaluate_visual_load_state

ROOT = Path(__file__).resolve().parent.parent

INSPECT_SCRIPT = """
() => {
  const visuals = Array.from(document.querySelectorAll('[data-testid="visual"]'));
  return visuals.map((visual, idx) => {
    const container = visual.closest('visual-container');
    const rect = (container || visual).getBoundingClientRect();
    const title = (
      container?.querySelector('.visual-title, .tableExTitle')?.textContent ||
      visual.querySelector('h3, .slicer-header-text')?.textContent ||
      ''
    ).trim().slice(0, 50);
    return {
      idx,
      title,
      h: Math.round(rect.height),
      w: Math.round(rect.width),
      classes: (visual.className || '').slice(0, 80),
      initialized: visual.hasAttribute('initialized'),
      hasCard: !!visual.querySelector('.kpi, .card, .multiRowCard'),
      svgCount: visual.querySelectorAll('svg').length,
      pathCount: visual.querySelectorAll('svg path').length,
      canvas: !!visual.querySelector('canvas'),
    };
  });
}
"""


def goto_page(host, nav_cfg, page_num: int) -> None:
    click_navigation_button(host, nav_cfg["entry_button"])
    btn = {
        "path_width": nav_cfg["page_buttons"]["path_width"],
        "index": nav_cfg["page_buttons"]["index"],
    }
    for _ in range(page_num - 1):
        click_navigation_button(host, btn)


def main() -> None:
    load_dotenv(ROOT / ".env")
    with open(ROOT / "config" / "daily.yaml", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
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
        page.wait_for_timeout(2000)

        for target in (4, 5, 6):
            btn = {
                "path_width": config["navigation"]["page_buttons"]["path_width"],
                "index": config["navigation"]["page_buttons"]["index"],
            }
            clicks = target - 1
            for _ in range(clicks):
                click_navigation_button(host, btn)
                page.wait_for_timeout(3000)
            page.wait_for_timeout(8000)
            state = evaluate_visual_load_state(host)
            print(f"\n=== PAGE {target} pending={state['pending']} ===")
            for item in state["items"]:
                if item.get("h", 0) >= 60:
                    print(item)
            rows = host.locator("body").evaluate(INSPECT_SCRIPT)
            for row in rows:
                if row["h"] >= 100:
                    print("raw:", row)

        browser.close()


if __name__ == "__main__":
    main()
