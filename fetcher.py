"""HTTP fetching with retry + exponential backoff.

Honest single User-Agent, no proxies, no anti-bot handling.

# FUTURE EXTENSION: proxy rotation, cookie/session persistence, anti-bot
# handling (Cloudflare, JS challenges) — explicitly out of scope.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# A single, honest UA string.  No rotating or spoofed identities.
USER_AGENT = (
    "portfolio-multi-source-scraper/1.0 "
    "(+https://github.com/example/portfolio-scraper)"
)

# Status codes we're willing to retry.  4xx other than 429 is a client
# error — retrying it is pointless and abusive.
_RETRYABLE_STATUSES = {429}


class FetchError(RuntimeError):
    """Raised when a URL cannot be fetched after all retries."""


class Fetcher:
    def __init__(
        self,
        timeout: int = 15,
        max_retries: int = 3,
        session: Optional[requests.Session] = None,
    ) -> None:
        self._timeout = timeout
        self._max_retries = max_retries
        self._session = session or requests.Session()
        self._session.headers.update(
            {"User-Agent": USER_AGENT, "Accept": "text/html,*/*;q=0.8"}
        )

    # ------------------------------------------------------------------ #
    def fetch(self, url: str) -> str:
        """Return the HTML body for ``url`` or raise :class:`FetchError`."""
        last_exc: Optional[BaseException] = None

        for attempt in range(self._max_retries + 1):
            try:
                resp = self._session.get(url, timeout=self._timeout)
                resp.raise_for_status()

                # requests falls back to ISO-8859-1 when the header omits a
                # charset; that is almost always wrong for modern HTML.
                if not resp.encoding or resp.encoding.lower() == "iso-8859-1":
                    resp.encoding = "utf-8"

                return resp.text

            except requests.HTTPError as exc:
                last_exc = exc
                status = exc.response.status_code if exc.response is not None else None
                if (
                    status is not None
                    and 400 <= status < 500
                    and status not in _RETRYABLE_STATUSES
                ):
                    logger.error("Non-retryable HTTP %d for %s", status, url)
                    raise FetchError(
                        f"Non-retryable HTTP {status} for {url}"
                    ) from exc
                # 429 / 5xx / anything else → fall through to the backoff path.

            except requests.RequestException as exc:
                last_exc = exc

            if attempt < self._max_retries:
                backoff = 2 ** attempt  # 1, 2, 4 …
                logger.warning(
                    "Fetch failed for %s (attempt %d/%d): %s. Retrying in %ds.",
                    url,
                    attempt + 1,
                    self._max_retries + 1,
                    last_exc,
                    backoff,
                )
                time.sleep(backoff)
            else:
                logger.error(
                    "Fetch failed for %s after %d attempts: %s",
                    url,
                    self._max_retries + 1,
                    last_exc,
                )

        raise FetchError(
            f"Failed to fetch {url} after {self._max_retries + 1} attempts"
        ) from last_exc

    # ------------------------------------------------------------------ #
    def close(self) -> None:
        self._session.close()

    def __enter__(self) -> "Fetcher":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()