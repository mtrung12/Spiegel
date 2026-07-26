"""
Polite HTTP client for corpus fetching.

Every outbound request goes through here so the rate limiting, backoff and
robots.txt behaviour are enforced in one place rather than per adapter.
"""

import threading
import time
import urllib.robotparser
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import httpx

from ...config import Config
from ...utils.logger import get_logger

logger = get_logger('mirofish.corpus.http')

# Statuses worth retrying. 429 is handled separately so Retry-After is honoured.
RETRYABLE_STATUSES = {429, 500, 502, 503, 504}


class RateLimitedError(RuntimeError):
    """Raised when a host keeps returning 429 after the retry budget."""


class _TokenBucket:
    """Simple thread-safe token bucket, one per host."""

    def __init__(self, rate_per_second: float, burst: int):
        self.rate = max(rate_per_second, 0.01)
        self.capacity = max(burst, 1)
        self._tokens = float(self.capacity)
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        """Block until a token is available."""
        while True:
            with self._lock:
                now = time.monotonic()
                self._tokens = min(
                    self.capacity,
                    self._tokens + (now - self._last) * self.rate,
                )
                self._last = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                wait = (1.0 - self._tokens) / self.rate
            time.sleep(min(wait, 5.0))


class PoliteClient:
    """
    An httpx wrapper that behaves itself.

    - one token bucket per host, so a slow source cannot starve the others
    - honours Retry-After on 429 and backs off exponentially otherwise
    - identifies itself with a contactable User-Agent
    - optional robots.txt check for non-API hosts
    """

    def __init__(
        self,
        user_agent: Optional[str] = None,
        timeout: Optional[float] = None,
        rate_per_second: Optional[float] = None,
        burst: int = 3,
        max_retries: int = 3,
        max_response_bytes: Optional[int] = None,
    ):
        self.user_agent = user_agent or Config.CORPUS_USER_AGENT
        self.timeout = timeout if timeout is not None else Config.CORPUS_HTTP_TIMEOUT
        self.rate_per_second = (
            rate_per_second if rate_per_second is not None else Config.CORPUS_RATE_PER_SECOND
        )
        self.burst = burst
        self.max_retries = max_retries
        self.max_response_bytes = max_response_bytes or Config.CORPUS_MAX_RESPONSE_BYTES

        self._buckets: Dict[str, _TokenBucket] = {}
        self._buckets_lock = threading.Lock()
        self._robots: Dict[str, Optional[urllib.robotparser.RobotFileParser]] = {}
        self._robots_lock = threading.Lock()

        self._client = httpx.Client(
            timeout=self.timeout,
            follow_redirects=True,
            headers={'User-Agent': self.user_agent, 'Accept-Encoding': 'gzip, deflate'},
        )

    # -- lifecycle ---------------------------------------------------------

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> 'PoliteClient':
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    # -- internals ---------------------------------------------------------

    def _bucket_for(self, url: str) -> _TokenBucket:
        host = urlparse(url).netloc
        with self._buckets_lock:
            bucket = self._buckets.get(host)
            if bucket is None:
                bucket = _TokenBucket(self.rate_per_second, self.burst)
                self._buckets[host] = bucket
            return bucket

    def _robots_allows(self, url: str) -> bool:
        """Check robots.txt for the host, caching the parsed result."""
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        with self._robots_lock:
            if origin not in self._robots:
                parser = urllib.robotparser.RobotFileParser()
                try:
                    resp = self._client.get(f"{origin}/robots.txt", timeout=10.0)
                    if resp.status_code == 200:
                        parser.parse(resp.text.splitlines())
                    else:
                        # No robots.txt published means no restrictions declared
                        parser = None
                except httpx.HTTPError:
                    parser = None
                self._robots[origin] = parser
            parser = self._robots[origin]
        if parser is None:
            return True
        return parser.can_fetch(self.user_agent, url)

    # -- requests ----------------------------------------------------------

    def request(
        self,
        method: str,
        url: str,
        *,
        check_robots: bool = False,
        **kwargs: Any,
    ) -> httpx.Response:
        """
        Perform one rate-limited request with retries.

        Args:
            method: HTTP verb
            url: Absolute URL
            check_robots: Consult robots.txt first. Leave off for documented
                APIs, where robots.txt governs crawlers rather than API clients.
            **kwargs: Passed through to httpx

        Returns:
            The successful response

        Raises:
            PermissionError: robots.txt disallows the path
            RateLimitedError: the host kept returning 429
            httpx.HTTPError: transport failure after retries
        """
        if check_robots and not self._robots_allows(url):
            raise PermissionError(f"robots.txt disallows {url}")

        bucket = self._bucket_for(url)
        last_error: Optional[Exception] = None

        for attempt in range(self.max_retries + 1):
            bucket.acquire()
            try:
                response = self._client.request(method, url, **kwargs)
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    raise
                time.sleep(min(2 ** attempt, 15))
                continue

            if response.status_code not in RETRYABLE_STATUSES:
                self._guard_response_size(response, url)
                return response

            if attempt >= self.max_retries:
                if response.status_code == 429:
                    raise RateLimitedError(f"{url} still rate limited after {attempt + 1} attempts")
                response.raise_for_status()

            delay = self._retry_delay(response, attempt)
            logger.warning(
                "corpus http %s -> %s, retrying in %.1fs (attempt %d/%d)",
                url, response.status_code, delay, attempt + 1, self.max_retries,
            )
            time.sleep(delay)

        # Only reachable if the loop exhausted without returning or raising
        raise last_error or httpx.HTTPError(f"request to {url} failed")

    @staticmethod
    def _retry_delay(response: httpx.Response, attempt: int) -> float:
        """Prefer the server's Retry-After, otherwise exponential backoff."""
        retry_after = response.headers.get('Retry-After')
        if retry_after:
            try:
                return min(float(retry_after), 60.0)
            except ValueError:
                pass
        return min(2 ** attempt, 15)

    def _guard_response_size(self, response: httpx.Response, url: str) -> None:
        """Refuse oversized payloads so one bad feed cannot exhaust memory."""
        if len(response.content) > self.max_response_bytes:
            raise ValueError(
                f"response from {url} is {len(response.content)} bytes, "
                f"over the {self.max_response_bytes} byte cap"
            )

    def get_json(self, url: str, **kwargs: Any) -> Any:
        """GET and parse JSON."""
        response = self.request('GET', url, **kwargs)
        response.raise_for_status()
        return response.json()

    def get_text(self, url: str, **kwargs: Any) -> str:
        """GET and return decoded text."""
        response = self.request('GET', url, **kwargs)
        response.raise_for_status()
        return response.text
