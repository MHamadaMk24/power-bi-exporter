import logging
import time

from playwright.sync_api import Page

from browser import ReportHost

logger = logging.getLogger(__name__)

QUERY_URL_FRAGMENTS = (
    "querydata",
    "executeQueries",
    "explore",
    "conceptualschema",
)

LOADING_SELECTORS = (
    ".slicer-header-pending-text",
    ".slicer-header-pending-icon",
    '[data-testid="visual-loading-spinner"]',
    '[class*="loadingSpinner"]',
    '[class*="loading-overlay"]',
    ".shimmer",
    ".skeleton",
)

# Inspect every visual-container: initialized, real SVG/canvas data, not just spinners.
EVALUATE_VISUAL_LOAD_STATE_SCRIPT = """
() => {
  function isExcluded(visual) {
    const cls = visual.className || '';
    return /visual-slicer|visual-actionButton|visual-image|visual-shape|visual-textbox|visual-pageNavigator/.test(cls);
  }

  function isKpiCardVisual(visual) {
    return !!visual.querySelector('.kpi, .card, .multiRowCard');
  }

  function isChartVisual(visual) {
    const cls = visual.className || '';
    return /visual-(lineChart|clusteredColumnChart|clusteredBarChart|barChart|areaChart|pieChart|donutChart|scatterChart|waterfallChart|funnelChart|gauge|treemap|tableEx|pivotTable|matrix|ribbonChart|stackedAreaChart|hundredPercentStackedColumnChart|stackedColumnChart|map|filledMap|shapeMap|azureMap|keyDriversVisual)/.test(cls);
  }

  function svgHasRealData(svg) {
    if (!svg) return false;
    for (const path of svg.querySelectorAll('path')) {
      if ((path.getAttribute('d') || '').length > 30) return true;
    }
    for (const rect of svg.querySelectorAll('rect')) {
      const w = parseFloat(rect.getAttribute('width') || '0');
      const h = parseFloat(rect.getAttribute('height') || '0');
      if (w > 8 && h > 8) return true;
    }
    if (svg.querySelectorAll('circle').length >= 2) return true;
    for (const text of svg.querySelectorAll('text')) {
      if (/[0-9]/.test((text.textContent || '').trim())) return true;
    }
    return false;
  }

  function visualHasRenderedData(visual) {
    if (visual.querySelector('table tbody tr')) return true;
    if (visual.querySelector('.treemap .node')) return true;
    const canvas = visual.querySelector('canvas');
    if (canvas && canvas.width > 1 && canvas.height > 1) return true;
    for (const svg of visual.querySelectorAll('svg')) {
      if (svgHasRealData(svg)) return true;
    }
    return false;
  }

  function getTitle(container, visual) {
    const titleEl = container?.querySelector('.visual-title, .tableExTitle, .visual-header');
    if (titleEl?.textContent) return titleEl.textContent.trim().slice(0, 60);
    const h3 = visual?.querySelector('h3');
    return h3?.textContent?.trim().slice(0, 60) || '';
  }

  const items = [];
  let pending = 0;
  const containers = Array.from(document.querySelectorAll('visual-container'));

  for (const container of containers) {
    const visual = container.querySelector('[data-testid="visual"]');
    const rect = container.getBoundingClientRect();
    if (rect.width < 40 || rect.height < 40) continue;

    const title = getTitle(container, visual);
    const base = { title, h: Math.round(rect.height), w: Math.round(rect.width) };

    if (!visual) {
      pending++;
      items.push({ ...base, reason: 'missing-visual-node' });
      continue;
    }

    if (isExcluded(visual)) continue;

    const spinner = visual.querySelector('[data-testid="visual-loading-spinner"]');
    if (spinner) {
      const style = window.getComputedStyle(spinner);
      if (style.display !== 'none' && style.visibility !== 'hidden') {
        pending++;
        items.push({ ...base, reason: 'spinner' });
        continue;
      }
    }

    if (visual.querySelector('[aria-busy="true"]')) {
      pending++;
      items.push({ ...base, reason: 'aria-busy' });
      continue;
    }

    if (isKpiCardVisual(visual)) continue;

    if (!visual.hasAttribute('initialized')) {
      pending++;
      items.push({ ...base, reason: 'not-initialized' });
      continue;
    }

    const hasData = visualHasRenderedData(visual);
    const chart = isChartVisual(visual);
    const largeSlot = rect.height >= 80;

    if ((chart || largeSlot) && !hasData) {
      pending++;
      items.push({
        ...base,
        reason: chart ? 'chart-no-data' : 'large-no-data',
        classes: (visual.className || '').slice(0, 80),
      });
    }
  }

  return { pending, items };
}
"""

SCROLL_DEFERRED_SCRIPT = """
() => {
  for (const selector of ['div.reportContainer', 'div.visualContainerHost']) {
    const el = document.querySelector(selector);
    if (el) {
      el.scrollTop = el.scrollHeight;
      el.scrollTop = 0;
    }
  }
  document.querySelectorAll('[data-testid="visual"]').forEach((v) => {
    v.scrollIntoView({ block: 'nearest', behavior: 'instant' });
  });
}
"""


CONFIG_ONLY_KEYS = frozenset(
    {
        "total_budget_ms",
        "page_3_min_ms",
        "entry_wait_ms",
        "default_page_wait_ms",
        "page_waits",
        "pre_screenshot_max_wait_ms",
    }
)


def build_page_waits(load_cfg: dict) -> dict[int, int]:
    """Build per-page minimum waits from defaults and page_waits overrides."""
    entry_ms = int(load_cfg.get("entry_wait_ms", 15000))
    default_ms = int(load_cfg.get("default_page_wait_ms", 30000))
    overrides = dict(load_cfg.get("page_waits") or {})

    if "page_3_min_ms" in load_cfg and 3 not in overrides and "3" not in overrides:
        overrides[3] = load_cfg["page_3_min_ms"]

    waits = {0: entry_ms}
    for page_num in range(1, 8):
        override = overrides.get(page_num, overrides.get(str(page_num), default_ms))
        waits[page_num] = int(override)
    return waits


def evaluate_visual_load_state(report_frame: ReportHost) -> dict:
    return _evaluate_on_report(report_frame, EVALUATE_VISUAL_LOAD_STATE_SCRIPT)


def count_pending_charts(report_frame: ReportHost) -> int:
    return int(evaluate_visual_load_state(report_frame).get("pending", 0))


def _log_pending_visuals(page_label: str, state: dict) -> None:
    pending = state.get("pending", 0)
    if pending <= 0:
        return
    for item in state.get("items", [])[:6]:
        logger.info(
            "%s pending visual: %s (%sx%s) — %s",
            page_label,
            item.get("title") or item.get("classes") or "untitled",
            item.get("w"),
            item.get("h"),
            item.get("reason"),
        )


def wait_until_charts_ready(
    page: Page,
    report_frame: ReportHost,
    *,
    page_label: str,
    max_wait_ms: int = 90000,
    poll_interval_ms: int = 400,
) -> None:
    """Final gate before screenshot — wait until no chart visuals are pending."""
    started = time.monotonic()

    while True:
        _scroll_for_deferred_rendering(page, report_frame)
        state = evaluate_visual_load_state(report_frame)
        pending = state.get("pending", 0)
        if pending == 0:
            logger.info("%s pre-screenshot check passed", page_label)
            return

        elapsed_ms = (time.monotonic() - started) * 1000
        if elapsed_ms >= max_wait_ms:
            logger.warning(
                "%s pre-screenshot timed out (%s visual(s) still pending)",
                page_label,
                pending,
            )
            _log_pending_visuals(page_label, state)
            return

        logger.info("%s pre-screenshot: %s visual(s) still loading", page_label, pending)
        _log_pending_visuals(page_label, state)
        page.wait_for_timeout(poll_interval_ms)


def wait_for_report_ready(
    page: Page,
    report_frame: ReportHost,
    *,
    min_wait_ms: int = 800,
    max_wait_ms: int = 180000,
    network_idle_ms: int = 500,
    poll_interval_ms: int = 400,
    render_stable_reads: int = 2,
    settle_after_action_ms: int = 4000,
    minimum_page_wait_ms: int = 0,
    page_label: str = "page",
    pending_selector: str | None = None,
) -> None:
    """Wait until Power BI charts on the page have finished loading."""
    started = time.monotonic()
    page.wait_for_timeout(min_wait_ms + settle_after_action_ms)

    selectors = list(LOADING_SELECTORS)
    if pending_selector and pending_selector not in selectors:
        selectors.insert(0, pending_selector)

    _scroll_for_deferred_rendering(page, report_frame)
    _wait_for_all_loading_indicators_cleared(
        page, report_frame, selectors, max_wait_ms, poll_interval_ms
    )
    _wait_for_query_network_idle(page, network_idle_ms, max_wait_ms)
    _scroll_for_deferred_rendering(page, report_frame)
    _wait_for_all_visuals_rendered(
        page,
        report_frame,
        max_wait_ms,
        poll_interval_ms,
        render_stable_reads,
    )
    _enforce_minimum_page_wait(
        page,
        report_frame,
        started,
        minimum_page_wait_ms,
        max_wait_ms,
        poll_interval_ms,
        page_label,
    )


def _enforce_minimum_page_wait(
    page: Page,
    report_frame: ReportHost,
    started: float,
    minimum_page_wait_ms: int,
    max_wait_ms: int,
    poll_interval_ms: int,
    page_label: str,
) -> None:
    """Keep polling until the page budget is used and charts stay loaded."""
    if minimum_page_wait_ms <= 0:
        return

    while True:
        elapsed_ms = (time.monotonic() - started) * 1000
        remaining_ms = minimum_page_wait_ms - elapsed_ms

        if remaining_ms <= 0:
            state = evaluate_visual_load_state(report_frame)
            pending = state.get("pending", 0)
            if pending == 0:
                logger.info(
                    "%s minimum wait complete (%.0fs budget used)",
                    page_label,
                    minimum_page_wait_ms / 1000,
                )
                return
            logger.info(
                "%s budget elapsed; waiting for %s remaining visual(s)",
                page_label,
                pending,
            )
            _log_pending_visuals(page_label, state)

        if elapsed_ms >= max_wait_ms:
            logger.warning("%s hit max wait (%ss)", page_label, max_wait_ms / 1000)
            return

        _scroll_for_deferred_rendering(page, report_frame)
        state = evaluate_visual_load_state(report_frame)
        pending = state.get("pending", 0)
        if pending:
            logger.info("%s still loading %s visual(s)", page_label, pending)
            _log_pending_visuals(page_label, state)

        sleep_ms = min(poll_interval_ms, max(200, int(remaining_ms))) if remaining_ms > 0 else poll_interval_ms
        page.wait_for_timeout(sleep_ms)


def _evaluate_on_report(report_frame: ReportHost, script: str):
    return report_frame.locator("body").evaluate(script)


def _scroll_for_deferred_rendering(page: Page, report_frame: ReportHost) -> None:
    try:
        _evaluate_on_report(report_frame, SCROLL_DEFERRED_SCRIPT)
        page.wait_for_timeout(200)
    except Exception:
        logger.debug("Deferred-render scroll skipped")


def _wait_for_all_loading_indicators_cleared(
    page: Page,
    report_frame: ReportHost,
    selectors: list[str],
    max_wait_ms: int,
    poll_interval_ms: int,
) -> None:
    elapsed = 0

    while elapsed < max_wait_ms:
        visible_count = 0
        for selector in selectors:
            locator = report_frame.locator(selector)
            for i in range(locator.count()):
                try:
                    if locator.nth(i).is_visible():
                        visible_count += 1
                except Exception:
                    continue

        if visible_count == 0:
            logger.info("All DOM loading indicators cleared")
            return

        page.wait_for_timeout(poll_interval_ms)
        elapsed += poll_interval_ms

    logger.warning("Timed out waiting for all loading indicators to clear")


def _wait_for_all_visuals_rendered(
    page: Page,
    report_frame: ReportHost,
    max_wait_ms: int,
    poll_interval_ms: int,
    render_stable_reads: int,
) -> None:
    elapsed = 0
    stable_reads = 0

    while elapsed < max_wait_ms:
        if elapsed % (poll_interval_ms * 4) == 0 and elapsed > 0:
            _scroll_for_deferred_rendering(page, report_frame)

        state = evaluate_visual_load_state(report_frame)
        pending = state.get("pending", 0)

        if pending == 0:
            stable_reads += 1
            if stable_reads >= render_stable_reads:
                logger.info("All chart visuals rendered")
                return
        else:
            stable_reads = 0
            logger.info("Waiting for %s visual(s)", pending)
            _log_pending_visuals("Report", state)

        page.wait_for_timeout(poll_interval_ms)
        elapsed += poll_interval_ms

    pending = count_pending_charts(report_frame)
    logger.warning(
        "Timed out waiting for visuals to render (%s still pending)", pending
    )


def _wait_for_query_network_idle(
    page: Page,
    network_idle_ms: int,
    max_wait_ms: int,
) -> None:
    pending = {"count": 0}

    def on_request(request) -> None:
        url = request.url.lower()
        if any(fragment in url for fragment in QUERY_URL_FRAGMENTS):
            pending["count"] += 1

    def on_finished(request) -> None:
        url = request.url.lower()
        if any(fragment in url for fragment in QUERY_URL_FRAGMENTS):
            pending["count"] = max(0, pending["count"] - 1)

    page.on("request", on_request)
    page.on("requestfinished", on_finished)
    page.on("requestfailed", on_finished)

    elapsed = 0
    step = 200
    while elapsed < max_wait_ms:
        if pending["count"] == 0:
            page.wait_for_timeout(network_idle_ms)
            if pending["count"] == 0:
                logger.info("Query network idle")
                return
        page.wait_for_timeout(step)
        elapsed += step

    logger.warning("Timed out waiting for query network idle")
