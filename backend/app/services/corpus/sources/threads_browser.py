"""
Headless browser transport for Threads.

Threads serves nothing useful to a plain HTTP client: the search results and the
reply trees are hydrated client side, so the page has to actually run. This
module owns that - launching Chromium, injecting a session cookie, scrolling far
enough for the lazy loader to hand over more items, and returning raw HTML for
``threads_parser`` to read.

Two things about this transport are worth being explicit about, because they are
the reason ``ThreadsSource`` ships disabled:

* It reads pages published for browsers rather than a documented API, so
  ``robots.txt`` genuinely applies. The adapter checks it and refuses by default.
* The pacing below is not decoration. Threads throttles clients that page
  through results faster than a person could read them, so the delays are what
  keep the traffic to roughly one page per interaction. Turning them off does not
  make the source faster, it makes it fail.

Playwright is imported lazily so the rest of the corpus package keeps working -
and keeps being testable - on installs that never enable this source.
"""

import asyncio
import json
import math
import os
import random
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from ....utils.logger import get_logger

logger = get_logger('spiegel.corpus.threads.browser')

#: Container Threads puts around every post card. Its absence after a load means
#: either a login wall or a markup change, and the adapter reports both.
POST_CONTAINER_SELECTOR = '[data-pressable-container=true]'


class ThreadsBrowserError(RuntimeError):
    """Raised when the browser transport cannot produce a page."""


def playwright_available() -> bool:
    """Whether the optional Playwright dependency is importable."""
    try:
        import playwright.async_api  # noqa: F401
    except ImportError:
        return False
    return True


def run_sync(make_coro: Callable[[], Any]) -> Any:
    """
    Run one coroutine to completion from synchronous code.

    The corpus harvester runs adapters in a thread pool, and Playwright wants a
    loop of its own. Driving it on a dedicated thread means this works the same
    whether or not the caller already has a running loop, which a bare
    ``asyncio.run`` does not.
    """
    box: Dict[str, Any] = {}

    def runner() -> None:
        try:
            box['value'] = asyncio.run(make_coro())
        except BaseException as exc:  # re-raised on the calling thread below
            box['error'] = exc

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    thread.join()

    if 'error' in box:
        raise box['error']
    return box.get('value')


# -- session ---------------------------------------------------------------


def load_cookies(path: str) -> List[Dict[str, Any]]:
    """
    Read a stored Threads session into Playwright cookie dicts.

    Two shapes are accepted, because both are what people actually have: a list
    of cookie objects exported from a browser, or a flat ``{name: value}`` map.
    An unreadable or empty file yields no cookies rather than an error - the
    adapter's availability check is what reports that, with a fixable message.
    """
    expanded = Path(os.path.expanduser(path))
    if not expanded.is_file():
        return []

    try:
        payload = json.loads(expanded.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("threads cookie file %s unreadable: %s", expanded, exc)
        return []

    # Some exporters wrap the list under a key rather than storing it bare.
    if isinstance(payload, dict) and isinstance(payload.get('cookies'), (dict, list)):
        payload = payload['cookies']

    cookies: List[Dict[str, Any]] = []
    if isinstance(payload, dict):
        for name, value in payload.items():
            if not value:
                continue
            cookies.append({
                'name': name,
                'value': str(value),
                'domain': '.threads.net',
                'path': '/',
                'secure': True,
                'httpOnly': False,
                'sameSite': 'Lax',
            })
    elif isinstance(payload, list):
        for cookie in payload:
            if not isinstance(cookie, dict) or not cookie.get('name'):
                continue
            entry = {
                'name': cookie['name'],
                'value': str(cookie.get('value', '')),
                'domain': cookie.get('domain') or '.threads.net',
                'path': cookie.get('path') or '/',
                'secure': bool(cookie.get('secure', True)),
                'httpOnly': bool(cookie.get('httpOnly', False)),
                'sameSite': cookie.get('sameSite') or 'Lax',
            }
            # Playwright rejects the extension-style "no_restriction" value.
            if entry['sameSite'] not in ('Strict', 'Lax', 'None'):
                entry['sameSite'] = 'Lax'
            cookies.append(entry)

    return cookies


def launch_args(mode: str) -> Dict[str, Any]:
    """
    Chromium launch options for one browser mode.

    ``headless-new`` is the default: Chromium's render-aware headless mode, which
    shares the graphics pipeline with the headed browser, so pages that branch on
    canvas or WebGL support render the same way they would for a person. The
    headed modes exist for debugging a page that only misbehaves headless.
    """
    if mode == 'headed':
        return {'headless': False, 'args': ['--window-size=1280,900']}
    if mode == 'headless':
        return {'headless': True, 'args': []}
    return {
        'headless': False,  # Playwright would add its own legacy --headless flag
        'args': ['--headless=new'],
    }


# -- pacing ----------------------------------------------------------------


@dataclass
class Pacing:
    """
    How long each interaction takes.

    Defaults are drawn from ordinary reading and scrolling speeds. They are
    deliberately variable: a fixed cadence both looks wrong to the server and
    tends to synchronise with the lazy loader in ways that skip content.
    """

    settle_mu: float = 1.8
    settle_sd: float = 0.6
    scroll_burst_mu: float = 5.0
    scroll_burst_sd: float = 1.5
    scroll_gap_mu: float = 0.110
    scroll_gap_sd: float = 0.030
    scroll_delta_mu: float = 420.0
    scroll_delta_sd: float = 140.0
    scroll_rest_mu: float = 2.2
    scroll_rest_sd: float = 0.9
    mouse_step_mu: float = 0.016
    mouse_step_sd: float = 0.004


def _gauss(mean: float, sd: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, random.gauss(mean, sd)))


def _min_jerk(n: int) -> List[float]:
    """Minimum-jerk easing, which is how a hand actually accelerates."""
    if n < 2:
        return [1.0]
    return [10 * t ** 3 - 15 * t ** 4 + 6 * t ** 5 for t in (i / (n - 1) for i in range(n))]


def _bezier(p0, p1, p2, p3, t):
    u = 1 - t
    return (
        u ** 3 * p0[0] + 3 * u ** 2 * t * p1[0] + 3 * u * t ** 2 * p2[0] + t ** 3 * p3[0],
        u ** 3 * p0[1] + 3 * u ** 2 * t * p1[1] + 3 * u * t ** 2 * p2[1] + t ** 3 * p3[1],
    )


def _mouse_path(x0: float, y0: float, x1: float, y1: float) -> List[tuple]:
    """A bowed path between two points rather than a straight line."""
    dx, dy = x1 - x0, y1 - y0
    distance = math.hypot(dx, dy)
    if distance == 0:
        return [(x0, y0)]
    px, py = -dy / distance, dx / distance
    bow = random.gauss(0, distance * 0.15)
    c1 = (x0 + dx * 0.33 + px * bow * 0.5, y0 + dy * 0.33 + py * bow * 0.5)
    c2 = (x0 + dx * 0.66 + px * bow, y0 + dy * 0.66 + py * bow)
    steps = int(_gauss(distance / 8, distance / 24, 10, 120))
    return [_bezier((x0, y0), c1, c2, (x1, y1), t) for t in _min_jerk(steps)]


async def _settle(pacing: Pacing) -> None:
    await asyncio.sleep(_gauss(pacing.settle_mu, pacing.settle_sd, 0.4, 6.0))


async def _drift_mouse(page, pacing: Pacing, x: float, y: float) -> None:
    """Move the pointer somewhere. Threads only loads media the pointer nears."""
    start = await page.evaluate('() => [window.__cx ?? 100, window.__cy ?? 100]')
    for px, py in _mouse_path(start[0], start[1], x, y):
        await page.mouse.move(px, py)
        await asyncio.sleep(_gauss(pacing.mouse_step_mu, pacing.mouse_step_sd, 0.008, 0.040))
    await page.evaluate(f'window.__cx = {x}; window.__cy = {y};')


async def _scroll_burst(page, pacing: Pacing) -> None:
    """One burst of wheel events followed by a pause, as when reading down a feed."""
    length = int(_gauss(pacing.scroll_burst_mu, pacing.scroll_burst_sd, 1, 12))
    for _ in range(length):
        delta = _gauss(pacing.scroll_delta_mu, pacing.scroll_delta_sd, 80, 1200)
        await page.mouse.wheel(0, delta)
        await asyncio.sleep(_gauss(pacing.scroll_gap_mu, pacing.scroll_gap_sd, 0.05, 0.4))
    await asyncio.sleep(_gauss(pacing.scroll_rest_mu, pacing.scroll_rest_sd, 0.3, 8.0))


# -- rendering -------------------------------------------------------------


class ThreadsRenderer:
    """
    Renders Threads URLs to HTML, reusing one browser across a harvest.

    One context for the whole run rather than one per URL: a cold Chromium start
    costs seconds, and a harvest opens a page per post it wants replies from.
    """

    def __init__(
        self,
        cookies: Optional[List[Dict[str, Any]]] = None,
        browser_mode: str = 'headless-new',
        viewport: Optional[Dict[str, int]] = None,
        pacing: Optional[Pacing] = None,
        nav_timeout_ms: int = 30_000,
        selector_timeout_ms: int = 15_000,
    ):
        self.cookies = cookies or []
        self.browser_mode = browser_mode
        self.viewport = viewport or {'width': 1280, 'height': 900}
        self.pacing = pacing or Pacing()
        self.nav_timeout_ms = nav_timeout_ms
        self.selector_timeout_ms = selector_timeout_ms
        self.pages_rendered = 0

    async def render_many(
        self,
        urls: List[str],
        scrolls: int = 3,
        on_page: Optional[Callable[[str, str], None]] = None,
    ) -> Dict[str, str]:
        """
        Render several URLs in one browser session, sequentially.

        Sequential on purpose. Parallel pages would finish sooner but would also
        put several simultaneous requests on one host from one session, which is
        both rude and the fastest way to get the session flagged.

        Returns:
            ``{url: html}``, omitting URLs that failed. Failures are logged, not
            raised, so one dead post cannot lose a whole harvest.
        """
        from playwright.async_api import async_playwright

        rendered: Dict[str, str] = {}
        options = launch_args(self.browser_mode)

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=options['headless'], args=options['args']
            )
            try:
                context = await browser.new_context(viewport=self.viewport)
                if self.cookies:
                    await context.add_cookies(self.cookies)
                try:
                    for url in urls:
                        html = await self._render_one(context, url, scrolls)
                        if html is None:
                            continue
                        rendered[url] = html
                        if on_page is not None:
                            on_page(url, html)
                finally:
                    await context.close()
            finally:
                await browser.close()

        return rendered

    async def _render_one(self, context, url: str, scrolls: int) -> Optional[str]:
        page = await context.new_page()
        try:
            await page.goto(url, wait_until='domcontentloaded', timeout=self.nav_timeout_ms)
            try:
                await page.wait_for_selector(
                    POST_CONTAINER_SELECTOR, timeout=self.selector_timeout_ms
                )
            except Exception:
                # No cards is a real outcome - a login wall, or a search that
                # matched nothing. The parser reports zero items either way, and
                # the adapter turns that into a message the operator can act on.
                logger.warning("threads: no post containers on %s", url)

            await _settle(self.pacing)
            await _drift_mouse(
                page,
                self.pacing,
                random.uniform(300, self.viewport['width'] - 200),
                random.uniform(200, self.viewport['height'] - 200),
            )
            for _ in range(max(0, scrolls)):
                await _scroll_burst(page, self.pacing)
            await _settle(self.pacing)

            self.pages_rendered += 1
            return await page.content()
        except Exception as exc:
            logger.warning("threads render failed for %s: %s", url, exc)
            return None
        finally:
            try:
                await page.close()
            except Exception:
                pass
