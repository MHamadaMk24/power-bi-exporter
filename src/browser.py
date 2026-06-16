import logging
import os
import re
import time

from playwright.sync_api import FrameLocator, Page

ReportHost = Page | FrameLocator

logger = logging.getLogger(__name__)


REPORT_READY_SELECTORS = (
    "path.ui-role-button-fill",
    '[data-testid="slicer-dropdown"]',
    "visual-modern",
    "div.reportContainer",
    "iframe",
)


def _report_hosted_on_page(page: Page) -> bool:
    for selector in (
        "path.ui-role-button-fill",
        '[data-testid="slicer-dropdown"]',
        "div.reportContainer",
        "visual-modern",
    ):
        if page.locator(selector).count() > 0:
            return True
    return False


def get_report_frame(page: Page, timeout_ms: int = 120000) -> FrameLocator | Page:
    """Return the frame or page that hosts the Power BI report."""
    url = (page.url or "").lower()
    if "login.microsoftonline.com" in url or "login.live.com" in url:
        raise RuntimeError(
            "Still on Microsoft login page — sign-in did not complete. "
            f"URL: {page.url}"
        )
    if "oauth2" in url and "authorize" in url:
        raise RuntimeError(
            "Still on Microsoft OAuth redirect — sign-in did not complete. "
            f"URL: {page.url}"
        )

    selector_union = ", ".join(REPORT_READY_SELECTORS)
    page.wait_for_selector(selector_union, state="visible", timeout=timeout_ms)
    page.wait_for_timeout(3000)

    if _report_hosted_on_page(page):
        logger.info("Report rendered directly on page (no iframe)")
        return page

    if page.locator("iframe").count() > 0:
        for frame in page.frames:
            url = frame.url or ""
            if "powerbi" in url.lower() or "reportembed" in url.lower():
                fragment = _iframe_src_fragment(url)
                loc = page.locator(f'iframe[src*="{fragment}"]')
                if loc.count() > 0:
                    logger.info("Using report iframe: %s", url[:120])
                    return page.frame_locator(f'iframe[src*="{fragment}"]')
        logger.info("Falling back to first iframe")
        return page.frame_locator("iframe").first

    logger.info("Report rendered directly on page (fallback)")
    return page


def _iframe_src_fragment(url: str) -> str:
    if "reportEmbed" in url:
        return "reportEmbed"
    if "powerbi" in url.lower():
        return "powerbi"
    return url.split("/")[2] if "/" in url else "iframe"


def click_navigation_button(report_frame: ReportHost, button_cfg: dict) -> None:
    """Click a Power BI navigation button using stable selectors."""
    if text := button_cfg.get("text"):
        target = report_frame.get_by_text(text, exact=True)
        target.wait_for(state="visible", timeout=60000)
        target.click(force=True)
        logger.info("Clicked navigation button text: %s", text)
        return

    if path_width := button_cfg.get("path_width"):
        paths = report_frame.locator(f'path.ui-role-button-fill[d*="{path_width}"]')
        index = int(button_cfg.get("index", 0))
        button = paths.nth(index)
        button.wait_for(state="visible", timeout=60000)
        button.click(force=True)
        logger.info("Clicked navigation path width=%s index=%s", path_width, index)
        return

    if clip_path_id := button_cfg.get("clip_path"):
        selector = f'path[clip-path="url(#{clip_path_id})"]'
        button = report_frame.locator(selector)
        button.wait_for(state="visible", timeout=60000)
        button.click(force=True)
        logger.info("Clicked navigation clip-path: %s", clip_path_id)
        return

    raise ValueError("Navigation button config must include text, path_width, or clip_path")


def _slicer_dropdown(report_frame: ReportHost, slicer_label: str):
    exact = report_frame.locator(
        f'[data-testid="slicer-dropdown"][aria-label="{slicer_label}"]'
    )
    if exact.count() > 0:
        return exact

    fuzzy = report_frame.locator('[data-testid="slicer-dropdown"]').filter(
        has=report_frame.locator(f'[aria-label*="{slicer_label}"]')
    )
    if fuzzy.count() > 0:
        logger.info('Using fuzzy slicer match for "%s"', slicer_label)
        return fuzzy

    all_slicers = report_frame.locator('[data-testid="slicer-dropdown"]')
    if all_slicers.count() == 1:
        logger.info('Using only slicer on page for "%s"', slicer_label)
        return all_slicers

    return exact


def wait_for_slicer_ready(
    report_frame: ReportHost,
    slicer_label: str,
    *,
    page: Page | None = None,
    max_wait_ms: int = 120000,
) -> None:
    """Wait until the slicer header is not pending and values have settled."""
    dropdown = _slicer_dropdown(report_frame, slicer_label)
    dropdown.wait_for(state="visible", timeout=90000)
    dropdown.scroll_into_view_if_needed(timeout=10000)

    deadline = time.monotonic() + max_wait_ms / 1000
    last_reason = "unknown"
    stable_reads = 0
    required_stable = 3 if os.environ.get("GITHUB_ACTIONS") else 2

    while time.monotonic() < deadline:
        if dropdown.count() == 0:
            last_reason = "not-found"
            stable_reads = 0
        else:
            state = dropdown.first.evaluate(
                """(el) => {
                    if (el.querySelector('.slicer-header-pending-text, .slicer-header-pending-icon'))
                        return { ready: false, reason: 'pending-header' };
                    const container = el.closest('visual-container');
                    if (container?.querySelector('[data-testid="visual-loading-spinner"]'))
                        return { ready: false, reason: 'spinner' };
                    return { ready: true, reason: 'ok' };
                }"""
            )
            if not state.get("ready"):
                last_reason = state.get("reason", "unknown")
                stable_reads = 0
            else:
                stable_reads += 1
                if stable_reads >= required_stable:
                    settle_ms = 2500 if os.environ.get("GITHUB_ACTIONS") else 800
                    if page is not None:
                        page.wait_for_timeout(settle_ms)
                    else:
                        time.sleep(settle_ms / 1000)
                    logger.info("Slicer data ready: %s", slicer_label)
                    return

        if stable_reads > 0:
            logger.info(
                'Slicer "%s" settling (%s/%s)', slicer_label, stable_reads, required_stable
            )
        elif last_reason != "unknown":
            logger.info('Slicer "%s" still loading (%s)', slicer_label, last_reason)

        if page is not None:
            page.wait_for_timeout(500)
        else:
            time.sleep(0.5)

    raise RuntimeError(
        f'Slicer "{slicer_label}" data not ready after {max_wait_ms}ms ({last_reason})'
    )


def _try_open_slicer_popup(
    report_frame: ReportHost,
    dropdown,
    *,
    page: Page | None,
    popup_wait_ms: int,
) -> bool:
    click_targets = (
        dropdown,
        dropdown.locator(".slicer-head-container"),
        dropdown.locator(".slicer-header"),
        dropdown.locator(".slicer-text"),
        dropdown.locator(".slicer-restatement"),
        dropdown.locator('[role="combobox"]'),
        dropdown.locator(".glyphicon-chevron-down"),
    )

    for target in click_targets:
        if target.count() == 0:
            continue
        try:
            target.first.scroll_into_view_if_needed(timeout=10000)
            target.first.click(force=True)
        except Exception:
            logger.debug("Slicer click failed on %s", target)
        if _slicer_popup_visible(report_frame):
            return True
        popup = report_frame.locator('[role="listbox"]').last
        try:
            popup.wait_for(state="visible", timeout=popup_wait_ms)
            return True
        except Exception:
            continue

    if page is not None and dropdown.count() > 0:
        try:
            dropdown.first.focus()
            page.keyboard.press("Enter")
            page.wait_for_timeout(400)
            if _slicer_popup_visible(report_frame):
                return True
            page.keyboard.press("Space")
            page.wait_for_timeout(400)
            if _slicer_popup_visible(report_frame):
                return True
            page.keyboard.press("ArrowDown")
            page.wait_for_timeout(400)
            if _slicer_popup_visible(report_frame):
                return True
        except Exception:
            logger.debug("Keyboard slicer open failed")

    return _slicer_popup_visible(report_frame)


def open_slicer_dropdown(
    report_frame: ReportHost,
    slicer_label: str,
    *,
    page: Page | None = None,
) -> None:
    wait_for_slicer_ready(report_frame, slicer_label, page=page)

    dropdown = _slicer_dropdown(report_frame, slicer_label)

    if page is not None:
        for _ in range(5):
            if not _slicer_popup_visible(report_frame):
                break
            page.keyboard.press("Escape")
            page.wait_for_timeout(400)

    if page is not None and os.environ.get("GITHUB_ACTIONS"):
        page.wait_for_timeout(1500)

    popup_wait_ms = 10000 if os.environ.get("GITHUB_ACTIONS") else 5000
    max_attempts = 12 if os.environ.get("GITHUB_ACTIONS") else 6

    for attempt in range(max_attempts):
        if _slicer_popup_visible(report_frame):
            logger.info("Opened slicer dropdown: %s", slicer_label)
            return

        if _try_open_slicer_popup(
            report_frame, dropdown, page=page, popup_wait_ms=popup_wait_ms
        ):
            logger.info("Opened slicer dropdown: %s", slicer_label)
            return

        if page is not None:
            page.wait_for_timeout(1000)
            if attempt == max_attempts // 2:
                wait_for_slicer_ready(report_frame, slicer_label, page=page)

    raise RuntimeError(f'Could not open slicer dropdown: "{slicer_label}"')


def clear_slicer_selection(
    page: Page, report_frame: ReportHost, slicer_label: str
) -> None:
    """Clear active slicer selections before choosing a single location."""
    dropdown = _slicer_dropdown(report_frame, slicer_label)
    current = dropdown.locator(".slicer-restatement").inner_text().strip()
    if not current:
        return

    clear_btn = report_frame.locator('[aria-label="Clear selections"]')
    if clear_btn.count() > 0:
        try:
            clear_btn.first.click(force=True)
            page.wait_for_timeout(500)
            logger.info("Cleared slicer selection")
            return
        except Exception:
            logger.debug("Clear selections button not clickable")

    if current.lower() == "all":
        popup = report_frame.locator('[role="listbox"]').last
        all_option = popup.locator('[role="option"]:has-text("All")')
        if all_option.count() > 0:
            all_option.first.click()
            page.wait_for_timeout(500)
            logger.info("Deselected slicer 'All' option")


def _find_slicer_option(popup, option_text: str):
    target = option_text.strip()
    options = popup.locator('[role="option"]')
    for i in range(options.count()):
        text = options.nth(i).inner_text().strip()
        if text == target or text.lower() == target.lower():
            return options.nth(i), text
    return None, None


def select_slicer_option(report_frame: ReportHost, option_text: str) -> str:
    deadline = time.monotonic() + 30
    last_count = 0

    while time.monotonic() < deadline:
        popup = report_frame.locator('[role="listbox"]').last
        if popup.count() == 0 or not popup.is_visible():
            time.sleep(0.4)
            continue

        option, matched_text = _find_slicer_option(popup, option_text)
        if option is not None:
            option.click()
            logger.info("Selected slicer option: %s", matched_text)
            return matched_text

        count = popup.locator('[role="option"]').count()
        if count != last_count:
            last_count = count
        elif count > 0:
            try:
                popup.evaluate("el => { el.scrollTop += 250; }")
            except Exception:
                pass

        time.sleep(0.4)

    raise RuntimeError(f'Slicer option not found: "{option_text}"')


def _slicer_popup_visible(report_frame: ReportHost) -> bool:
    popup = report_frame.locator('[role="listbox"]')
    return popup.count() > 0 and popup.last.is_visible()


def close_slicer_dropdown(
    page: Page, report_frame: ReportHost, slicer_label: str
) -> None:
    """Dismiss the Location slicer popup before screenshots."""
    dropdown = _slicer_dropdown(report_frame, slicer_label)

    for attempt in range(5):
        if not _slicer_popup_visible(report_frame):
            return

        page.keyboard.press("Escape")
        page.wait_for_timeout(400)

        if not _slicer_popup_visible(report_frame):
            return

        try:
            dropdown.click(force=True)
            page.wait_for_timeout(400)
        except Exception:
            logger.debug("Could not toggle slicer dropdown (attempt %s)", attempt + 1)

    if _slicer_popup_visible(report_frame):
        for selector in ("div.reportContainer", "div.visualContainerHost"):
            loc = report_frame.locator(selector)
            if loc.count() > 0:
                loc.first.click(position={"x": 10, "y": 10}, force=True)
                page.wait_for_timeout(400)
                break

    if _slicer_popup_visible(report_frame):
        raise RuntimeError(f'Could not close slicer dropdown: "{slicer_label}"')

    logger.info("Closed slicer dropdown: %s", slicer_label)


def list_slicer_options(
    report_frame: ReportHost,
    slicer_label: str,
    *,
    skip_values: list[str] | None = None,
    page: Page | None = None,
) -> list[str]:
    skip = {value.strip().lower() for value in (skip_values or ["All"])}
    open_slicer_dropdown(report_frame, slicer_label, page=page)

    popup = report_frame.locator('[role="listbox"]').last
    popup.wait_for(state="visible", timeout=30000)
    options = popup.locator('[role="option"]')

    values: list[str] = []
    for i in range(options.count()):
        text = options.nth(i).inner_text().strip()
        if not text or text.lower() in skip:
            continue
        values.append(text)

    if not values:
        raise RuntimeError(f'No slicer options found for "{slicer_label}"')

    logger.info("Found %s slicer option(s): %s", len(values), values)
    return values


def click_back_button(report_frame: ReportHost, page_btn_cfg: dict) -> None:
    """Click the back arrow (on the last page only one arrow may be visible at index 0)."""
    path_width = page_btn_cfg["path_width"]
    preferred_index = int(page_btn_cfg.get("back_index", 1))
    paths = report_frame.locator(f'path.ui-role-button-fill[d*="{path_width}"]')
    paths.first.wait_for(state="visible", timeout=15000)

    if paths.count() > preferred_index:
        paths.nth(preferred_index).click(force=True)
    else:
        paths.nth(0).click(force=True)

    logger.info("Clicked back navigation button")


def return_to_first_report_page(
    report_frame: ReportHost, page_btn_cfg: dict, *, settle_ms: int = 800
) -> None:
    """Navigate back to the first daily page using the back arrow."""
    steps = int(page_btn_cfg.get("additional_pages", 0))

    for step in range(steps):
        click_back_button(report_frame, page_btn_cfg)
        if step < steps - 1:
            time.sleep(settle_ms / 1000)

    logger.info("Returned to first report page (%s back clicks)", steps)


def get_first_slicer_option(report_frame: ReportHost, *, skip_all: bool = True) -> str:
    popup = report_frame.locator('[role="listbox"]').last
    popup.wait_for(state="visible", timeout=15000)

    options = popup.locator('[role="option"]')
    count = options.count()
    for i in range(count):
        text = options.nth(i).inner_text().strip()
        if skip_all and text.lower() == "all":
            continue
        if text:
            options.nth(i).click()
            logger.info("Selected first slicer option: %s", text)
            return text

    raise RuntimeError("No usable slicer options found in dropdown")


def sanitize_filename(name: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*]', "_", name.strip())
    return cleaned or "export"
