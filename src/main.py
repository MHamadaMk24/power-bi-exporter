import argparse
import logging
import os
import sys
from pathlib import Path

import yaml
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

from auth import login_to_power_bi
from browser import (
    click_navigation_button,
    clear_slicer_selection,
    close_slicer_dropdown,
    get_report_frame,
    list_slicer_options,
    open_slicer_dropdown,
    sanitize_filename,
    select_slicer_option,
)

from export import merge_images_to_pdf
from sharepoint import SharePointConfig, SharePointUploader
from load_detection import (
    CONFIG_ONLY_KEYS,
    build_page_waits,
    wait_for_report_ready,
    wait_until_charts_ready,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_CANDIDATES = (
    ROOT / "config" / "daily.yaml",
    ROOT / "config.yaml",
)


def resolve_config_path(config_path: Path | str | None = None) -> Path:
    if config_path is None:
        for candidate in DEFAULT_CONFIG_CANDIDATES:
            if candidate.is_file():
                return candidate
        raise FileNotFoundError(
            "No config file found. Expected config/daily.yaml or config.yaml"
        )

    path = Path(config_path)
    if not path.is_absolute():
        # Config paths are relative to the project root, not the shell cwd.
        path = (ROOT / path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Config file not found: {path}")
    return path


def load_config(config_path: Path | str | None = None) -> dict:
    path = resolve_config_path(config_path)
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def browser_settings(config: dict) -> dict:
    settings = dict(config.get("browser", {}))
    if os.environ.get("GITHUB_ACTIONS", "").lower() == "true":
        settings["headless"] = True
    return settings


def chromium_launch_kwargs(config: dict) -> dict:
    settings = browser_settings(config)
    kwargs: dict = {
        "headless": settings.get("headless", False),
        "slow_mo": settings.get("slow_mo", 0),
    }
    if os.environ.get("GITHUB_ACTIONS", "").lower() == "true":
        kwargs["headless"] = True
        kwargs["args"] = [
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
        ]
    return kwargs


def resolve_reports(config: dict, only_names: list[str] | None = None) -> list[dict]:
    if "reports" in config:
        reports = config["reports"]
    else:
        reports = [
            {
                "name": "default",
                "enabled": True,
                "report_url": config["report_url"],
                "navigation": config["navigation"],
                "filters": config["filters"],
                "load_detection": config.get("load_detection", {}),
            }
        ]

    selected: list[dict] = []
    for report in reports:
        if not report.get("enabled", True):
            continue
        name = report.get("name", "report")
        if only_names and name not in only_names:
            continue
        selected.append(report)

    if only_names and not selected:
        available = [r.get("name", "report") for r in reports]
        raise ValueError(f"No matching reports for {only_names}. Available: {available}")

    return selected


def merge_load_detection(config: dict, report: dict) -> dict:
    global_cfg = config.get("load_detection", {})
    report_cfg = report.get("load_detection", {})
    return {**global_cfg, **report_cfg}


def report_label(report: dict) -> str:
    return (report.get("label") or report.get("name") or "report").strip()


def report_work_dir(base_output: Path, report: dict, location_name: str) -> Path:
    safe_report = sanitize_filename(report.get("name", "report"))
    safe_location = sanitize_filename(location_name)
    return base_output / "_work" / safe_report / safe_location / "pages"


def _runtime_load_cfg(load_cfg: dict) -> dict:
    return {k: v for k, v in load_cfg.items() if k not in CONFIG_ONLY_KEYS}


def _wait_cfg(load_cfg: dict, page_waits: dict[int, int], page_num: int) -> dict:
    cfg = _runtime_load_cfg(load_cfg)
    cfg["minimum_page_wait_ms"] = page_waits.get(page_num, 0)
    cfg["page_label"] = f"Page {page_num}" if page_num else "Entry"
    return cfg


def screenshot_page(
    page,
    report_frame,
    output_path: Path,
    load_cfg: dict,
    page_label: str,
) -> None:
    runtime = _runtime_load_cfg(load_cfg)
    wait_until_charts_ready(
        page,
        report_frame,
        page_label=page_label,
        max_wait_ms=int(runtime.get("pre_screenshot_max_wait_ms", 90000)),
        poll_interval_ms=int(runtime.get("poll_interval_ms", 400)),
    )
    screenshot_report_area(page, report_frame, output_path)


def screenshot_report_area(page, report_frame, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    for selector in (
        "div.reportContainer",
        "div[data-testid='report-embed-host']",
        "div.visualContainerHost",
    ):
        loc = report_frame.locator(selector)
        if loc.count() > 0:
            try:
                loc.first.screenshot(path=str(output_path), timeout=10000)
                logger.info("Screenshot saved: %s", output_path)
                return
            except Exception:
                logger.warning("Could not screenshot %s; trying next selector", selector)

    page.screenshot(path=str(output_path), full_page=False)
    logger.info("Screenshot saved (viewport fallback): %s", output_path)


def resolve_filter_values(report_frame, page, filter_cfg: dict) -> list[str]:
    configured = filter_cfg.get("values") or []
    if configured:
        return [str(value).strip() for value in configured if str(value).strip()]

    test_value = (filter_cfg.get("test_value") or "").strip()
    if test_value and not filter_cfg.get("export_all", False):
        return [test_value]

    skip_values = filter_cfg.get("skip_values") or ["All"]
    options = list_slicer_options(
        report_frame,
        filter_cfg["slicer_label"],
        skip_values=skip_values,
    )
    close_slicer_dropdown(page, report_frame, filter_cfg["slicer_label"])
    return options


def open_report_entry(
    page,
    report_url: str,
    nav_cfg: dict,
    load_cfg: dict,
    page_waits: dict[int, int],
):
    logger.info("Reloading report and opening entry view")
    page.goto(report_url, wait_until="domcontentloaded", timeout=120000)
    report_frame = get_report_frame(page)
    page.wait_for_timeout(3000)
    click_navigation_button(report_frame, nav_cfg["entry_button"])
    wait_for_report_ready(
        page,
        report_frame,
        **_wait_cfg(load_cfg, page_waits, 0),
    )
    return report_frame


def apply_filter(
    page,
    report_frame,
    filter_cfg: dict,
    load_cfg: dict,
    page_waits: dict[int, int],
    filter_value: str,
) -> str:
    open_slicer_dropdown(report_frame, filter_cfg["slicer_label"])
    clear_slicer_selection(page, report_frame, filter_cfg["slicer_label"])
    open_slicer_dropdown(report_frame, filter_cfg["slicer_label"])
    filter_name = select_slicer_option(report_frame, filter_value)
    close_slicer_dropdown(page, report_frame, filter_cfg["slicer_label"])

    wait_for_report_ready(
        page,
        report_frame,
        pending_selector=".slicer-header-pending-text",
        **_wait_cfg(load_cfg, page_waits, 1),
    )
    close_slicer_dropdown(page, report_frame, filter_cfg["slicer_label"])
    return filter_name


def capture_page_screenshots(
    page,
    report_frame,
    output_dir: Path,
    nav_cfg: dict,
    load_cfg: dict,
    page_waits: dict[int, int],
) -> list[Path]:
    screenshots: list[Path] = []

    first_path = output_dir / "01_daily_summary.png"
    screenshot_page(page, report_frame, first_path, load_cfg, "Page 1")
    screenshots.append(first_path)

    page_btn_cfg = nav_cfg.get("page_buttons", {})
    forward_index = int(page_btn_cfg.get("forward_index", page_btn_cfg.get("index", 0)))
    additional_pages = int(page_btn_cfg.get("additional_pages", 0))
    button_cfg = {"path_width": page_btn_cfg["path_width"], "index": forward_index}

    for page_num in range(2, additional_pages + 2):
        logger.info("Navigating to next page (%s/%s)", page_num - 1, additional_pages + 1)
        click_navigation_button(report_frame, button_cfg)
        wait_for_report_ready(
            page,
            report_frame,
            pending_selector=".slicer-header-pending-text",
            **_wait_cfg(load_cfg, page_waits, page_num),
        )
        shot_path = output_dir / f"{page_num:02d}_page.png"
        screenshot_page(page, report_frame, shot_path, load_cfg, f"Page {page_num}")
        screenshots.append(shot_path)

    return screenshots


def export_filter_pdf(
    page,
    report_frame,
    filter_value: str,
    *,
    output_dir: Path,
    report: dict,
    filter_cfg: dict,
    nav_cfg: dict,
    load_cfg: dict,
    page_waits: dict[int, int],
) -> Path:
    filter_name = apply_filter(
        page, report_frame, filter_cfg, load_cfg, page_waits, filter_value
    )
    safe_name = sanitize_filename(filter_name)

    work_dir = report_work_dir(output_dir, report, filter_name)
    screenshots = capture_page_screenshots(
        page, report_frame, work_dir, nav_cfg, load_cfg, page_waits
    )

    pdf_path = output_dir / f"{safe_name}.pdf"
    merge_images_to_pdf(screenshots, pdf_path)
    return pdf_path


def _upload_pdf(
    uploader: SharePointUploader, pdf_path: Path, folder: str
) -> bool:
    try:
        uploader.upload_file(pdf_path, folder=folder)
        return True
    except Exception:
        logger.exception("SharePoint upload failed for %s", pdf_path.name)
        return False


def _build_sharepoint_uploader(config: dict) -> SharePointUploader | None:
    sp_cfg = config.get("sharepoint", {})
    if not sp_cfg.get("enabled", True):
        logger.info("SharePoint upload disabled in config")
        return None

    sharepoint_config = SharePointConfig.from_env()
    if sharepoint_config is None:
        logger.warning(
            "SharePoint upload skipped: set TENANT_ID, CLIENT_ID, CLIENT_SECRET, "
            "SHAREPOINT_SITE_NAME, and TARGET_FOLDER_PATH in .env"
        )
        return None

    return SharePointUploader(sharepoint_config)


def run_report_exports(
    page,
    report: dict,
    *,
    output_dir: Path,
    load_cfg: dict,
    uploader: SharePointUploader | None,
    upload_after_each: bool,
    default_sharepoint_folder: str,
) -> list[Path]:
    report_name = report.get("name", "report")
    report_title = report_label(report)
    nav_cfg = report["navigation"]
    filter_cfg = report["filters"]
    page_waits = build_page_waits(load_cfg)
    output_dir.mkdir(parents=True, exist_ok=True)
    sp_folder = default_sharepoint_folder

    logger.info("=== %s (%s) ===", report_title, report_name)
    logger.info(
        "Page wait budget (seconds): entry=%s, pages=%s",
        page_waits[0] / 1000,
        {p: page_waits[p] / 1000 for p in sorted(page_waits) if p > 0},
    )

    logger.info("Opening report URL")
    page.goto(report["report_url"], wait_until="domcontentloaded", timeout=120000)
    report_frame = get_report_frame(page)
    page.wait_for_timeout(3000)

    logger.info("Clicking entry button")
    click_navigation_button(report_frame, nav_cfg["entry_button"])
    wait_for_report_ready(
        page,
        report_frame,
        **_wait_cfg(load_cfg, page_waits, 0),
    )

    filter_values = resolve_filter_values(report_frame, page, filter_cfg)
    logger.info(
        "Exporting %s location(s) for '%s'", len(filter_values), report_title
    )

    report_frame = open_report_entry(
        page,
        report["report_url"],
        nav_cfg,
        load_cfg,
        page_waits,
    )

    pdf_paths: list[Path] = []
    for index, filter_value in enumerate(filter_values):
        logger.info(
            "=== %s | Export %s/%s: %s ===",
            report_title,
            index + 1,
            len(filter_values),
            filter_value,
        )
        if index > 0:
            report_frame = open_report_entry(
                page,
                report["report_url"],
                nav_cfg,
                load_cfg,
                page_waits,
            )
        pdf_path = export_filter_pdf(
            page,
            report_frame,
            filter_value,
            output_dir=output_dir,
            report=report,
            filter_cfg=filter_cfg,
            nav_cfg=nav_cfg,
            load_cfg=load_cfg,
            page_waits=page_waits,
        )
        pdf_paths.append(pdf_path)
        if uploader and upload_after_each:
            _upload_pdf(uploader, pdf_path, sp_folder)

    if uploader and not upload_after_each and pdf_paths:
        for pdf_path in pdf_paths:
            _upload_pdf(uploader, pdf_path, sp_folder)

    return pdf_paths


def run_export(
    only_reports: list[str] | None = None,
    config_path: Path | str | None = None,
) -> list[Path]:
    load_dotenv(ROOT / ".env")
    config = load_config(config_path)

    email = os.environ.get("PBI_EMAIL")
    password = os.environ.get("PBI_PASSWORD")
    if not email or not password:
        raise RuntimeError("PBI_EMAIL and PBI_PASSWORD must be set in .env")

    reports = resolve_reports(config, only_reports)
    output_dir = ROOT / config.get("output_dir", "output")
    output_dir.mkdir(parents=True, exist_ok=True)

    browser_cfg = browser_settings(config)
    uploader = _build_sharepoint_uploader(config)
    upload_after_each = config.get("sharepoint", {}).get("upload_after_each", True)
    default_sp_folder = (
        SharePointConfig.from_env().target_folder
        if SharePointConfig.from_env()
        else os.environ.get("TARGET_FOLDER_PATH", "Daily_Reports").strip().strip("/")
    )

    pdf_paths: list[Path] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(**chromium_launch_kwargs(config))
        context = browser.new_context(
            viewport={
                "width": browser_cfg.get("viewport_width", 1920),
                "height": browser_cfg.get("viewport_height", 1080),
            }
        )
        page = context.new_page()

        page.goto(reports[0]["report_url"], wait_until="domcontentloaded", timeout=120000)
        login_to_power_bi(page, email, password)

        for report in reports:
            load_cfg = merge_load_detection(config, report)
            report_pdfs = run_report_exports(
                page,
                report,
                output_dir=output_dir,
                load_cfg=load_cfg,
                uploader=uploader,
                upload_after_each=upload_after_each,
                default_sharepoint_folder=default_sp_folder,
            )
            pdf_paths.extend(report_pdfs)

        browser.close()

    return pdf_paths


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export Power BI reports to PDF")
    parser.add_argument(
        "--config",
        default=None,
        metavar="PATH",
        help="Config YAML path (default: config/daily.yaml)",
    )
    parser.add_argument(
        "--report",
        action="append",
        dest="reports",
        metavar="NAME",
        help="Run only named report(s), e.g. skidata or pass (repeatable)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    try:
        args = parse_args()
        results = run_export(
            only_reports=args.reports,
            config_path=args.config,
        )
        print(f"Export complete. {len(results)} PDF(s):")
        for path in results:
            print(f"  - {path}")
    except Exception:
        logger.exception("Export failed")
        sys.exit(1)
