"""Next-page discovery.

Two modes:

* ``css_selector`` — find a ``<a href>`` inside a container element; the
  href is resolved against the current URL with ``urljoin``.
* ``url_pattern`` — format a ``base_url`` containing ``{}`` with the next
  page number.  The caller stops when a fetch fails or a page yields zero
  items, since the pattern itself can't tell us when to stop.

# FUTURE EXTENSION: sitemap-driven pagination, JSON-API "next" cursors,
# infinite-scroll / XHR endpoints.
"""
from __future__ import annotations

import logging
from typing import Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from config_loader import PaginationRule

logger = logging.getLogger(__name__)


def next_page_url(
    html: str,
    current_url: str,
    rule: PaginationRule,
    current_page_num: int,
) -> Optional[str]:
    """Return the absolute URL of the next page, or ``None`` when done."""
    if rule.mode == "css_selector":
        if not rule.selector:
            return None
        soup = BeautifulSoup(html, "html.parser")
        node = soup.select_one(rule.selector)
        if node is None:
            return None
        href = node.get("href")
        if not href or not isinstance(href, str):
            return None
        return urljoin(current_url, href)

    if rule.mode == "url_pattern":
        if not rule.base_url:
            return None
        return rule.base_url.format(current_page_num + 1)

    return None