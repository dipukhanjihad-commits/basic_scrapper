"""Load and validate source configuration files.

A source is described entirely by JSON — no source-specific code lives in the
scraper.  Adding a new target is a matter of adding a file under ``sources/``.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_VALID_PAGINATION_MODES = ("css_selector", "url_pattern")
_VALID_LIMIT_MODES = ("all", "pages", "items")


# --------------------------------------------------------------------------- #
# Data model
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class FieldSpec:
    """How to extract a single logical field from a page."""
    selector: str
    attr: Optional[str] = None  # None => text extraction


@dataclass(frozen=True)
class PaginationRule:
    """How to discover the URL of the next page."""
    mode: str  # "css_selector" | "url_pattern"
    selector: Optional[str] = None
    base_url: Optional[str] = None  # must contain "{}"


@dataclass(frozen=True)
class Limits:
    mode: str  # "all" | "pages" | "items"
    max_pages: Optional[int] = None
    max_items: Optional[int] = None


@dataclass(frozen=True)
class Config:
    source_name: str
    start_url: str
    field_map: dict[str, FieldSpec]
    pagination: PaginationRule
    limits: Limits
    output_file: str
    request_timeout_seconds: int
    max_retries: int
    dedupe_key: str

    @property
    def field_names(self) -> list[str]:
        """Stable field order used for the CSV header."""
        return list(self.field_map.keys())


# --------------------------------------------------------------------------- #
# Validation helpers — every failure raises ValueError with a useful message
# --------------------------------------------------------------------------- #
def _require_mapping(value: Any, ctx: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{ctx}: expected an object, got {type(value).__name__}")
    return value


def _require_nonempty_str(value: Any, key: str, ctx: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{ctx}: '{key}' must be a non-empty string")
    return value


def _require_positive_int(value: Any, key: str, ctx: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{ctx}: '{key}' must be a positive integer")
    return value


def _require_nonnegative_int(value: Any, key: str, ctx: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{ctx}: '{key}' must be a non-negative integer")
    return value


def _parse_field_map(raw: Any, ctx: str) -> dict[str, FieldSpec]:
    raw = _require_mapping(raw, f"{ctx}.field_map")
    if not raw:
        raise ValueError(f"{ctx}.field_map: must contain at least one field")
    out: dict[str, FieldSpec] = {}
    for name, spec in raw.items():
        fctx = f"{ctx}.field_map['{name}']"
        spec = _require_mapping(spec, fctx)
        selector = _require_nonempty_str(spec.get("selector"), "selector", fctx)
        attr = spec.get("attr")
        if attr is not None:
            attr = _require_nonempty_str(attr, "attr", fctx)
        out[name] = FieldSpec(selector=selector, attr=attr)
    return out


def _parse_pagination(raw: Any, ctx: str) -> PaginationRule:
    raw = _require_mapping(raw, f"{ctx}.pagination")
    mode = raw.get("mode")
    if mode not in _VALID_PAGINATION_MODES:
        raise ValueError(
            f"{ctx}.pagination.mode: must be one of {_VALID_PAGINATION_MODES!r}, got {mode!r}"
        )
    if mode == "css_selector":
        selector = _require_nonempty_str(
            raw.get("selector"), "selector", f"{ctx}.pagination"
        )
        return PaginationRule(mode=mode, selector=selector)

    base_url = _require_nonempty_str(
        raw.get("base_url"), "base_url", f"{ctx}.pagination"
    )
    if "{}" not in base_url:
        raise ValueError(
            f"{ctx}.pagination.base_url: must contain a '{{}}' placeholder, got {base_url!r}"
        )
    return PaginationRule(mode=mode, base_url=base_url)


def _parse_limits(raw: Any, ctx: str) -> Limits:
    raw = _require_mapping(raw, f"{ctx}.limits")
    mode = raw.get("mode")
    if mode not in _VALID_LIMIT_MODES:
        raise ValueError(
            f"{ctx}.limits.mode: must be one of {_VALID_LIMIT_MODES!r}, got {mode!r}"
        )
    max_pages = raw.get("max_pages")
    max_items = raw.get("max_items")

    if mode == "all":
        if max_pages is not None or max_items is not None:
            raise ValueError(
                f"{ctx}.limits: mode 'all' requires both max_pages and max_items to be null"
            )
    elif mode == "pages":
        _require_positive_int(max_pages, "max_pages", f"{ctx}.limits")
        if max_items is not None:
            raise ValueError(f"{ctx}.limits: mode 'pages' requires max_items to be null")
    elif mode == "items":
        _require_positive_int(max_items, "max_items", f"{ctx}.limits")
        if max_pages is not None:
            raise ValueError(f"{ctx}.limits: mode 'items' requires max_pages to be null")

    return Limits(mode=mode, max_pages=max_pages, max_items=max_items)


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def load_config(path: str | Path) -> Config:
    """Load and validate a source config from disk."""
    path = Path(path)
    if not path.is_file():
        raise ValueError(f"Config file not found: {path}")

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Config {path}: invalid JSON ({exc})") from exc

    raw = _require_mapping(raw, f"Config {path}")
    ctx = f"Config '{path.stem}'"

    start_url = _require_nonempty_str(raw.get("start_url"), "start_url", ctx)
    field_map = _parse_field_map(raw.get("field_map"), ctx)
    pagination = _parse_pagination(raw.get("pagination"), ctx)
    limits = _parse_limits(raw.get("limits"), ctx)
    output_file = _require_nonempty_str(raw.get("output_file"), "output_file", ctx)
    timeout = _require_positive_int(
        raw.get("request_timeout_seconds"), "request_timeout_seconds", ctx
    )
    retries = _require_nonnegative_int(raw.get("max_retries"), "max_retries", ctx)
    dedupe_key = _require_nonempty_str(raw.get("dedupe_key"), "dedupe_key", ctx)

    if dedupe_key not in field_map:
        raise ValueError(
            f"{ctx}: 'dedupe_key' ({dedupe_key!r}) must reference a key in field_map"
        )

    return Config(
        source_name=path.stem,
        start_url=start_url,
        field_map=field_map,
        pagination=pagination,
        limits=limits,
        output_file=output_file,
        request_timeout_seconds=timeout,
        max_retries=retries,
        dedupe_key=dedupe_key,
    )


def with_overrides(
    config: Config,
    *,
    max_pages: Optional[int] = None,
    max_items: Optional[int] = None,
    output: Optional[str] = None,
) -> Config:
    """Return a copy of ``config`` with CLI overrides applied.

    The loop enforces ``max_pages`` / ``max_items`` independently of ``limits.mode``,
    so overrides can relax or tighten either bound regardless of the file's mode.
    """
    new_limits = Limits(
        mode=config.limits.mode,
        max_pages=config.limits.max_pages if max_pages is None else max_pages,
        max_items=config.limits.max_items if max_items is None else max_items,
    )
    return replace(
        config,
        limits=new_limits,
        output_file=config.output_file if output is None else output,
    )