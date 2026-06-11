from playwright.sync_api import Page, TimeoutError as PlaywrightTimeout


def login_to_power_bi(page: Page, email: str, password: str) -> None:
    """Sign in to Microsoft / Power BI when the login form appears."""
    page.wait_for_timeout(2000)

    for _ in range(3):
        if _on_report_page(page):
            return

        email_input = page.locator('input[type="email"]')
        if email_input.count() > 0 and email_input.first.is_visible():
            email_input.first.fill(email)
            page.locator('input[type="submit"], button[type="submit"]').first.click()
            page.wait_for_timeout(2000)

        password_input = page.locator('input[type="password"]')
        if password_input.count() > 0:
            try:
                password_input.first.wait_for(state="visible", timeout=15000)
                password_input.first.fill(password)
                page.locator('input[type="submit"], button[type="submit"]').first.click()
                page.wait_for_timeout(3000)
            except PlaywrightTimeout:
                pass

        _dismiss_stay_signed_in(page)
        page.wait_for_timeout(5000)

        if _on_report_page(page):
            return


def _on_report_page(page: Page) -> bool:
    url = (page.url or "").lower()
    if "reportembed" in url or "/reports/" in url:
        return True
    if page.locator("iframe").count() > 0:
        return True
    if page.locator("div.reportContainer").count() > 0:
        return True
    return False


def _dismiss_stay_signed_in(page: Page) -> None:
    for selector in (
        'input[id="idBtn_Back"]',
        'input[id="idSIButton9"]',
        'button:has-text("No")',
        'button:has-text("Yes")',
    ):
        try:
            button = page.locator(selector).first
            if button.is_visible(timeout=3000):
                button.click()
                page.wait_for_timeout(1000)
                return
        except PlaywrightTimeout:
            continue
