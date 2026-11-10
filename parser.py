"""Pure HTML → structured rows extraction.

The ``field_map`` is applied *in document order* per selector; rows are formed
by aligning the i-th match of every field.  This keeps the parser pure and
config-driven, at the cost of requiring consistent per-page structure.

# FUTURE EXTENSION: JS rendering (Playwright/Selenium) for client-rendered
# pages; per-item container selector (``item_selector``) for scoped parsing;
# value transforms (strip prefixes, regex, value maps) on FieldSpec.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from bs4 import BeautifulSoup, Tag

from config_loader import FieldSpec

logger = logging.getLogger(__name__)


@dataclass
class ParseResult:
    """Items extracted plus a count of rows dropped as malformed."""
    items: list[dict[str, str]] = field(default_factory=list)
    skipped: int = 0


def _extract_value(node: Tag, spec: FieldSpec) -> Optional[str]:
    if spec.attr is not None:
        value = node.get(spec.attr)
        if value is None:
            return None
        if isinstance(value, list):
            # e.g. class="star-rating Three" -> ["star-rating", "Three"]
            value = " ".join(value)
        value = value.strip()
        return value or None

    # Text extraction; collapse internal whitespace so CSV stays clean.
    text = node.get_text(" ", strip=True)
    text = " ".join(text.split())
    return text or None


def parse_page(html: str, field_map: dict[str, FieldSpec]) -> ParseResult:
    """Extract rows from a single HTML page.

    Fields whose selectors match nothing simply do not contribute values; any
    row where at least one field is missing is logged and skipped.
    """
    result = ParseResult()
    if not field_map:
        return result

    soup = BeautifulSoup(html, "html.parser")

    per_field: dict[str, list[Optional[str]]] = {}
    for name, spec in field_map.items():
        nodes = soup.select(spec.selector)
        per_field[name] = [_extract_value(n, spec) for n in nodes]

    # Use the *longest* column so trailing rows from a truncated column are
    # surfaced as skips instead of silently dropped.
    item_count = max((len(v) for v in per_field.values()), default=0)

    for i in range(item_count):
        row: dict[str, str] = {}
        reason: Optional[str] = None

        for name, values in per_field.items():
            if i >= len(values):
                reason = f"{name!r} has no matching element at row {i}"
                break
            value = values[i]
            if value is None:
                reason = (
                    f"{name!r} matched but had no value at row {i} "
                    f"(selector={field_map[name].selector!r})"
                )
                break
            row[name] = value

        if reason is not None:
            logger.warning("Skipped item: %s", reason)
            result.skipped += 1
            continue

        result.items.append(row)

    return result