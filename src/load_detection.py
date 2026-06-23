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

# Shared visual readiness helpers (used by page-level and per-visual checks).
_VISUAL_EVAL_HELPERS = """
  function isExcluded(visual) {
    const cls = visual.className || '';
    if (/visual-slicer|visual-actionButton|visual-image|visual-shape|visual-textbox|visual-pageNavigator/.test(cls)) {
      return true;
    }
    return !!visual.querySelector('.slicer-header, .slicerBody, .slicer-restatement');
  }

  function isKpiCardVisual(visual) {
    const cls = visual.className || '';
    return /visual-(card|multiRowCard|kpi)/i.test(cls);
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

  function inspectVisualContainer(container) {
    const visual = container?.querySelector('[data-testid="visual"]');
    const rect = container?.getBoundingClientRect() || { width: 0, height: 0 };
    const layoutW = container?.offsetWidth || 0;
    const layoutH = container?.offsetHeight || 0;
    const width = Math.max(rect.width, layoutW);
    const height = Math.max(rect.height, layoutH);
    const title = getTitle(container, visual);
    const base = {
      title,
      h: Math.round(height),
      w: Math.round(width),
      skipped: false,
      ready: true,
      reason: '',
    };

    if (width < 40 || height < 40) {
      const chart = isChartVisual(visual);
      const tableLike = !!visual.querySelector('table, .treemap');
      if (!chart && !tableLike) {
        return { ...base, skipped: true };
      }
      return { ...base, ready: false, reason: 'deferred-not-in-view' };
    }

    if (!visual) {
      return { ...base, ready: false, reason: 'missing-visual-node' };
    }

    if (isExcluded(visual)) {
      return { ...base, skipped: true };
    }

    const spinner = visual.querySelector('[data-testid="visual-loading-spinner"]');
    if (spinner) {
      const style = window.getComputedStyle(spinner);
      if (style.display !== 'none' && style.visibility !== 'hidden') {
        return { ...base, ready: false, reason: 'spinner' };
      }
    }

    if (visual.querySelector('[aria-busy="true"]')) {
      return { ...base, ready: false, reason: 'aria-busy' };
    }

    if (isKpiCardVisual(visual)) {
      return { ...base, skipped: true };
    }

    if (!visual.hasAttribute('initialized')) {
      return { ...base, ready: false, reason: 'not-initialized' };
    }

    const hasData = visualHasRenderedData(visual);
    const chart = isChartVisual(visual);
    const largeSlot = height >= 80;

    if ((chart || largeSlot) && !hasData) {
      return {
        ...base,
        ready: false,
        reason: chart ? 'chart-no-data' : 'large-no-data',
        classes: (visual.className || '').slice(0, 80),
      };
    }

    return base;
  }
"""

# Inspect every visual-container: initialized, real SVG/canvas data, not just spinners.
EVALUATE_VISUAL_LOAD_STATE_SCRIPT = (
    """
() => {
"""
    + _VISUAL_EVAL_HELPERS
    + """
  const items = [];
  let pending = 0;
  const containers = Array.from(document.querySelectorAll('visual-container'));

  for (const container of containers) {
    const state = inspectVisualContainer(container);
    if (state.skipped) continue;
    if (!state.ready) {
      pending++;
      items.push({
        title: state.title,
        h: state.h,
        w: state.w,
        reason: state.reason,
        classes: state.classes,
      });
    }
  }

  return { pending, items };
}
"""
)

VISUAL_ORDINAL_SCRIPT = (
    """
(args) => {
  const mode = args.mode;
  const ordinal = args.ordinal;
"""
    + _VISUAL_EVAL_HELPERS
    + """
  function sortedDataVisuals() {
    const containers = Array.from(document.querySelectorAll('visual-container'));
    const out = [];
    containers.forEach((container, index) => {
      const visual = container.querySelector('[data-testid="visual"]');
      if (!visual) return;
      if (isExcluded(visual)) return;
      if (isKpiCardVisual(visual)) return;
      const rect = container.getBoundingClientRect();
      out.push({
        container,
        index,
        top: Math.round(rect.top + window.scrollY),
        left: Math.round(rect.left + window.scrollX),
        title: getTitle(container, visual) || 'untitled',
      });
    });
    out.sort((a, b) => a.top - b.top || a.left - b.left);
    return out;
  }

  if (mode === 'list') {
    return sortedDataVisuals().map((item, itemOrdinal) => ({
      ordinal: itemOrdinal,
      index: item.index,
      top: item.top,
      left: item.left,
      title: item.title,
    }));
  }

  if (mode === 'scroll') {
    const item = sortedDataVisuals()[ordinal];
    if (!item) return false;
    item.container.scrollIntoView({ block: 'center', behavior: 'instant' });
    return true;
  }

  const item = sortedDataVisuals()[ordinal];
  if (!item) {
    return { ready: false, skipped: true, reason: 'missing-container', title: '' };
  }
  const state = inspectVisualContainer(item.container);
  return {
    ordinal,
    index: item.index,
    title: state.title,
    ready: state.skipped || state.ready,
    skipped: state.skipped,
    reason: state.reason,
    h: state.h,
    w: state.w,
    classes: state.classes,
  };
}
"""
)

SCROLL_REPORT_TOP_SCRIPT = """
() => {
  for (const selector of ['div.reportContainer', 'div.visualContainerHost']) {
    const el = document.querySelector(selector);
    if (el) {
      el.scrollTop = 0;
    }
  }
  window.scrollTo(0, 0);
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


def _as_visual_list(result) -> list[dict]:
    if not result:
        return []
    if isinstance(result, list):
        return result
    if isinstance(result, dict) and "ordinal" in result:
        return [result]
    return []


def _visual_ordinal(report_frame: ReportHost, mode: str, ordinal: int = 0):
    return report_frame.locator("body").evaluate(
        VISUAL_ORDINAL_SCRIPT, {"mode": mode, "ordinal": ordinal}
    )


def list_monitored_visuals(report_frame: ReportHost) -> list[dict]:
    return _as_visual_list(_visual_ordinal(report_frame, "list"))


def scroll_visual_by_ordinal(report_frame: ReportHost, ordinal: int) -> None:
    _visual_ordinal(report_frame, "scroll", ordinal)


def evaluate_visual_by_ordinal(report_frame: ReportHost, ordinal: int) -> dict:
    return _visual_ordinal(report_frame, "eval", ordinal)


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


def _remaining_ms(deadline: float) -> float:
    return max(0.0, (deadline - time.monotonic()) * 1000)


def _wait_for_visuals_sequential(
    page: Page,
    report_frame: ReportHost,
    *,
    page_label: str,
    deadline: float,
    poll_interval_ms: int,
    per_visual_max_ms: int = 15000,
    scroll_settle_ms: int = 400,
) -> int:
    """Scroll each data visual into view (top-to-bottom) and wait until it is ready."""
    initial = list_monitored_visuals(report_frame)
    if not initial:
        logger.info(
            "%s sequential visual load: no chart visuals indexed; using bulk deferred scroll",
            page_label,
        )
        _scroll_for_deferred_rendering(page, report_frame)
        return count_pending_charts(report_frame)

    logger.info(
        "%s sequential visual load: preparing %s visual(s)",
        page_label,
        len(initial),
    )

    for ordinal in range(len(initial)):
        if _remaining_ms(deadline) <= 0:
            break

        visuals = list_monitored_visuals(report_frame)
        if ordinal >= len(visuals):
            break

        title = visuals[ordinal].get("title") or "untitled"
        visual_deadline = min(
            deadline,
            time.monotonic() + min(per_visual_max_ms, _remaining_ms(deadline)) / 1000,
        )

        try:
            scroll_visual_by_ordinal(report_frame, ordinal)
        except Exception:
            logger.debug("%s could not scroll visual %s", page_label, title)
            continue

        page.wait_for_timeout(min(scroll_settle_ms, int(_remaining_ms(deadline))))

        while time.monotonic() < visual_deadline:
            state = evaluate_visual_by_ordinal(report_frame, ordinal)
            if state.get("ready"):
                logger.info("%s visual ready: %s", page_label, title)
                break
            if state.get("reason") == "missing-container":
                break
            page.wait_for_timeout(min(poll_interval_ms, int(_remaining_ms(visual_deadline))))
        else:
            state = evaluate_visual_by_ordinal(report_frame, ordinal)
            if not state.get("ready"):
                logger.info(
                    "%s visual still pending: %s (%sx%s) — %s",
                    page_label,
                    title,
                    state.get("w"),
                    state.get("h"),
                    state.get("reason") or "unknown",
                )

    try:
        _evaluate_on_report(report_frame, SCROLL_REPORT_TOP_SCRIPT)
        page.wait_for_timeout(200)
    except Exception:
        logger.debug("Report scroll-to-top skipped")

    return count_pending_charts(report_frame)


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
    deadline = started + max_wait_ms / 1000

    while True:
        _scroll_for_deferred_rendering(page, report_frame)
        pending = _wait_for_visuals_sequential(
            page,
            report_frame,
            page_label=page_label,
            deadline=deadline,
            poll_interval_ms=poll_interval_ms,
        )
        if pending == 0:
            logger.info("%s pre-screenshot check passed", page_label)
            return

        elapsed_ms = (time.monotonic() - started) * 1000
        if elapsed_ms >= max_wait_ms:
            state = evaluate_visual_load_state(report_frame)
            logger.warning(
                "%s pre-screenshot timed out (%s visual(s) still pending)",
                page_label,
                state.get("pending", pending),
            )
            _log_pending_visuals(page_label, state)
            return

        logger.info(
            "%s pre-screenshot: %s visual(s) still loading — retrying sequential pass",
            page_label,
            pending,
        )
        state = evaluate_visual_load_state(report_frame)
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
