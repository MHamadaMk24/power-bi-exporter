import logging
import time

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeout

logger = logging.getLogger(__name__)

LOGIN_URL_MARKERS = ("login.microsoftonline.com", "login.live.com")
LOGIN_TIMEOUT_SECONDS = 240

REPORT_DOM_SELECTORS = (
    "div.reportContainer",
    "path.ui-role-button-fill",
    '[data-testid="slicer-dropdown"]',
    "visual-modern",
)


def navigate_to_report(page: Page, url: str, email: str, password: str) -> None:
    """Open a report URL and complete Microsoft sign-in if prompted."""
    page.goto(url, wait_until="domcontentloaded", timeout=120000)
    page.wait_for_timeout(2000)
    login_to_power_bi(page, email, password)
    _wait_for_report_page(page, timeout_seconds=120)


def login_to_power_bi(page: Page, email: str, password: str) -> None:
    """Sign in to Microsoft / Power BI when the login form appears."""
    deadline = time.time() + LOGIN_TIMEOUT_SECONDS

    while time.time() < deadline:
        if _on_report_page(page):
            logger.info("Authenticated — report content visible")
            return

        if not _on_auth_flow_page(page):
            page.wait_for_timeout(2000)
            continue

        logger.info("Microsoft sign-in step at %s", page.url[:120])

        if _on_powerbi_sso_page(page):
            _advance_from_powerbi_sso(page)

        if _on_account_picker(page):
            _select_account_from_picker(page, email)

        if _has_email_field(page):
            _submit_email(page, email)

        if _has_password_field(page):
            _submit_password(page, password)

        if _on_kmsi_page(page):
            _accept_stay_signed_in(page)

        _wait_through_oauth_redirect(page, timeout_seconds=20)
        page.wait_for_timeout(1500)

        if _on_report_page(page):
            logger.info("Authenticated — report content visible")
            return

    raise RuntimeError(
        f"Power BI login timed out after {LOGIN_TIMEOUT_SECONDS}s. "
        f"Current URL: {page.url}"
    )


def _on_powerbi_sso_page(page: Page) -> bool:
    url = (page.url or "").lower()
    return "app.powerbi.com" in url and (
        "singlesignon" in url or "nosignupcheck" in url
    )


def _advance_from_powerbi_sso(page: Page) -> None:
    logger.info("On Power BI SSO page — waiting for Microsoft redirect")
    try:
        page.wait_for_url("**://login.microsoftonline.com/**", timeout=45000)
        logger.info("Redirected to Microsoft login")
        return
    except PlaywrightTimeout:
        pass

    for selector in (
        'a:has-text("Sign in")',
        'button:has-text("Sign in")',
        'a[href*="login.microsoftonline.com"]',
    ):
        try:
            control = page.locator(selector).first
            if control.count() > 0 and control.is_visible(timeout=3000):
                control.click()
                page.wait_for_timeout(3000)
                logger.info("Clicked Power BI sign-in control")
                try:
                    page.wait_for_url("**://login.microsoftonline.com/**", timeout=30000)
                    logger.info("Redirected to Microsoft login")
                except PlaywrightTimeout:
                    pass
                return
        except PlaywrightTimeout:
            continue

    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except PlaywrightTimeout:
        pass


def _on_auth_flow_page(page: Page) -> bool:
    url = (page.url or "").lower()
    if _on_powerbi_sso_page(page):
        return True
    if any(marker in url for marker in LOGIN_URL_MARKERS):
        return True
    if _on_account_picker(page):
        return True
    return _has_email_field(page) or _has_password_field(page) or _on_kmsi_page(page)


def _on_account_picker(page: Page) -> bool:
    url = (page.url or "").lower()
    if "select_account" in url or "/reprocess" in url:
        return True
    if page.locator("#tilesHolder, #credentialPickerTitle").count() > 0:
        return True
    return page.locator('div[role="heading"]:has-text("Pick an account")').count() > 0


def _has_email_field(page: Page) -> bool:
    return page.locator('input[name="loginfmt"], input[type="email"], #i0116').count() > 0


def _has_password_field(page: Page) -> bool:
    return page.locator('input[name="passwd"], input[type="password"], #i0118').count() > 0


def _on_kmsi_page(page: Page) -> bool:
    return page.locator("#KmsiDescription, #KmsiCheckbox, #KmsiTitle").count() > 0


def _select_account_from_picker(page: Page, email: str) -> bool:
    email_lower = email.lower()

    rows = page.locator("div.table-row")
    for index in range(rows.count()):
        row = rows.nth(index)
        try:
            row_text = row.inner_text(timeout=2000).lower()
        except PlaywrightTimeout:
            continue
        if email_lower in row_text:
            row.click()
            page.wait_for_timeout(3000)
            logger.info("Selected account from picker: %s", email)
            return True

    for locator in (
        page.locator(f'small:text-is("{email}")'),
        page.get_by_text(email, exact=True),
        page.locator(f'div[data-test-id="{email}"]'),
    ):
        if locator.count() == 0:
            continue
        try:
            target = locator.first
            if target.is_visible(timeout=2000):
                target.click()
                page.wait_for_timeout(3000)
                logger.info("Selected account tile: %s", email)
                return True
        except PlaywrightTimeout:
            continue

    if not _has_email_field(page):
        other_account = page.locator(
            "#otherTileText, div.table-cell:has-text('Use another account')"
        )
        if other_account.count() > 0:
            try:
                other_account.first.click(timeout=3000)
                page.wait_for_timeout(2000)
                logger.info('Opened "Use another account"')
            except PlaywrightTimeout:
                pass

    return False


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


def _accept_stay_signed_in(page: Page) -> None:
    for selector in (
        "#idSIButton9",
        'button:has-text("Yes")',
        'input[id="idBtn_Back"]',
        'button:has-text("No")',
    ):
        try:
            button = page.locator(selector).first
            if button.is_visible(timeout=4000):
                button.click()
                page.wait_for_timeout(1500)
                logger.info("Accepted stay-signed-in prompt")
                return
        except PlaywrightTimeout:
            continue


def _wait_through_oauth_redirect(page: Page, *, timeout_seconds: int) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if _on_report_page(page):
            return
        if not _on_auth_flow_page(page):
            return
        page.wait_for_timeout(1000)


def _wait_for_report_page(page: Page, *, timeout_seconds: int) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if _on_report_page(page):
            return
        page.wait_for_timeout(1000)
    raise RuntimeError(
        f"Report did not load after login. Current URL: {page.url}"
    )


def _has_visible_report_content(page: Page) -> bool:
    for selector in REPORT_DOM_SELECTORS:
        locator = page.locator(selector)
        if locator.count() > 0:
            try:
                if locator.first.is_visible(timeout=1000):
                    return True
            except PlaywrightTimeout:
                continue

    for frame in page.frames:
        frame_url = (frame.url or "").lower()
        if "powerbi" not in frame_url and "reportembed" not in frame_url:
            continue
        for selector in REPORT_DOM_SELECTORS:
            if frame.locator(selector).count() > 0:
                return True
    return False


def is_report_ready(page: Page) -> bool:
    return _on_report_page(page)


def _on_report_page(page: Page) -> bool:
    url = (page.url or "").lower()
    if any(marker in url for marker in LOGIN_URL_MARKERS):
        return False
    if "oauth2" in url and "authorize" in url:
        return False
    if "select_account" in url or "/reprocess" in url:
        return False
    if _on_powerbi_sso_page(page):
        return False
    return _has_visible_report_content(page)
