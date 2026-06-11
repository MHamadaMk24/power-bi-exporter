import logging
import time

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeout

logger = logging.getLogger(__name__)

LOGIN_URL_MARKERS = ("login.microsoftonline.com", "login.live.com")
LOGIN_TIMEOUT_SECONDS = 180


def navigate_to_report(page: Page, url: str, email: str, password: str) -> None:
    """Open a report URL and complete Microsoft sign-in if prompted."""
    page.goto(url, wait_until="domcontentloaded", timeout=120000)
    login_to_power_bi(page, email, password)
    _wait_for_report_page(page, timeout_seconds=90)


def login_to_power_bi(page: Page, email: str, password: str) -> None:
    """Sign in to Microsoft / Power BI when the login form appears."""
    deadline = time.time() + LOGIN_TIMEOUT_SECONDS

    while time.time() < deadline:
        if _on_report_page(page):
            logger.info("Authenticated — report page ready")
            return

        if _needs_login(page):
            logger.info("Microsoft sign-in required at %s", page.url[:100])
            _submit_email(page, email)
            _submit_password(page, password)
            _handle_post_password_prompts(page)

        page.wait_for_timeout(2000)

        if _on_report_page(page):
            logger.info("Authenticated — report page ready")
            return

    raise RuntimeError(
        f"Power BI login timed out after {LOGIN_TIMEOUT_SECONDS}s. "
        f"Current URL: {page.url}"
    )


def _needs_login(page: Page) -> bool:
    if any(marker in (page.url or "").lower() for marker in LOGIN_URL_MARKERS):
        return True
    return page.locator(
        'input[name="loginfmt"], input[type="email"], input[name="passwd"], input[type="password"]'
    ).count() > 0


def _submit_email(page: Page, email: str) -> None:
    for selector in ('input[name="loginfmt"]', 'input[type="email"]', "#i0116"):
        field = page.locator(selector).first
        if field.count() == 0:
            continue
        try:
            field.wait_for(state="visible", timeout=8000)
            field.fill(email)
            page.locator('#idSIButton9, input[type="submit"], button[type="submit"]').first.click()
            page.wait_for_timeout(2000)
            logger.info("Submitted sign-in email")
            return
        except PlaywrightTimeout:
            continue


def _submit_password(page: Page, password: str) -> None:
    for selector in ('input[name="passwd"]', 'input[type="password"]', "#i0118"):
        field = page.locator(selector).first
        if field.count() == 0:
            continue
        try:
            field.wait_for(state="visible", timeout=20000)
            field.fill(password)
            page.locator('#idSIButton9, input[type="submit"], button[type="submit"]').first.click()
            page.wait_for_timeout(3000)
            logger.info("Submitted sign-in password")
            return
        except PlaywrightTimeout:
            continue


def _handle_post_password_prompts(page: Page) -> None:
    _dismiss_stay_signed_in(page)
    page.wait_for_timeout(2000)


def _dismiss_stay_signed_in(page: Page) -> None:
    # Prefer "Yes" so later navigations reuse the session in CI.
    for selector in (
        "#idSIButton9",
        'input[id="idBtn_Back"]',
        'button:has-text("Yes")',
        'button:has-text("No")',
    ):
        try:
            button = page.locator(selector).first
            if button.is_visible(timeout=4000):
                button.click()
                page.wait_for_timeout(1500)
                logger.info("Dismissed post-login prompt")
                return
        except PlaywrightTimeout:
            continue


def _wait_for_report_page(page: Page, *, timeout_seconds: int) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if _on_report_page(page):
            return
        page.wait_for_timeout(1000)
    raise RuntimeError(
        f"Report did not load after login. Current URL: {page.url}"
    )


def _on_report_page(page: Page) -> bool:
    url = (page.url or "").lower()
    if any(marker in url for marker in LOGIN_URL_MARKERS):
        return False
    if "reportembed" in url or "/reports/" in url:
        return True
    if page.locator("div.reportContainer").count() > 0:
        return True
    if page.locator("path.ui-role-button-fill").count() > 0:
        return True
    if page.locator('[data-testid="slicer-dropdown"]').count() > 0:
        return True
    if page.locator("iframe").count() > 0 and "powerbi" in url:
        return True
    return False
