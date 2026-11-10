"""CLI entry point and crawl orchestration.

    python scraper.py --source books
    python scraper.py --source books --max-pages 3
    python scraper.py --source books --max-items 50
    python scraper.py --source quotes --output quotes.csv

# FUTURE EXTENSION: GUI (web UI or TUI) on top of the same engine.
# FUTURE EXTENSION: async / concurrent crawling (asyncio + aiohttp).
# FUTURE EXTENSION: login / session management (cookies, auth headers).
# FUTURE EXTENSION: scheduling, daemon mode, on-disk state between runs.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

from config_loader import Config, load_config, with_overrides
from exporter import export_csv
from fetcher import Fetcher, FetchError
from paginator import next_page_url
from parser import parse_page

logger = logging.getLogger("scraper")

# Guardrail for url_pattern pagination when pages start failing: if we skip
# this many in a row without a success, we stop instead of hammering forever.
_MAX_CONSECUTIVE_FETCH_FAILURES = 3


# --------------------------------------------------------------------------- #
# Logging
# --------------------------------------------------------------------------- #
def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Config-driven multi-source web scraper.",
    )
    parser.add_argument(
        "--source",
        required=True,
        help="Source name (file in sources/, with or without .json) or a path to a config file.",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=None,
        help="Override the source's max page limit.",
    )
    parser.add_argument(
        "--max-items",
        type=int,
        default=None,
        help="Override the source's max item limit.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Override the config's output CSV path.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        help="Logging verbosity (default: INFO).",
    )
    return parser


def _resolve_source_path(source: str) -> Path:
    """Accept either a bare name, ``<name>.json``, or an explicit path."""
    direct = Path(source)
    if direct.is_file():
        return direct

    sources_dir = Path(__file__).resolve().parent / "sources"
    name = source[:-5] if source.endswith(".json") else source
    candidate = sources_dir / f"{name}.json"
    if candidate.is_file():
        return candidate

    raise SystemExit(
        f"Source not found: {source!r} "
        f"(looked at {direct} and {candidate})"
    )


# --------------------------------------------------------------------------- #
# Crawl loop
# --------------------------------------------------------------------------- #
def crawl(config: Config) -> dict[str, float | int]:
    started = time.monotonic()
    logger.info(
        "Starting crawl: source=%s start_url=%s",
        config.source_name,
        config.start_url,
    )

    all_items: list[dict[str, str]] = []
    pages_fetched = 0
    items_skipped = 0

    current_url: str | None = config.start_url
    current_page_num = 1
    consecutive_failures = 0

    with Fetcher(
        timeout=config.request_timeout_seconds,
        max_retries=config.max_retries,
    ) as fetcher:
        while current_url is not None:
            # -- page budget --------------------------------------------------
            if (
                config.limits.max_pages is not None
                and pages_fetched >= config.limits.max_pages
            ):
                logger.info(
                    "Reached max_pages limit (%d), stopping.",
                    config.limits.max_pages,
                )
                break

            # -- fetch --------------------------------------------------------
            try:
                html = fetcher.fetch(current_url)
            except FetchError as exc:
                consecutive_failures += 1
                logger.error(
                    "Giving up on page %d (%s): %s",
                    current_page_num,
                    current_url,
                    exc,
                )

                if (
                    config.pagination.mode == "url_pattern"
                    and consecutive_failures < _MAX_CONSECUTIVE_FETCH_FAILURES
                ):
                    next_url = next_page_url(
                        "", current_url, config.pagination, current_page_num
                    )
                    if next_url is None:
                        logger.error(
                            "Cannot determine a next page after failure; stopping."
                        )
                        break
                    logger.warning(
                        "Skipping failed page %d, trying %d instead.",
                        current_page_num,
                        current_page_num + 1,
                    )
                    current_page_num += 1
                    current_url = next_url
                    continue

                logger.error(
                    "Stopping after fetch failure (mode=%s, consecutive_failures=%d).",
                    config.pagination.mode,
                    consecutive_failures,
                )
                break

            consecutive_failures = 0
            pages_fetched += 1

            # -- parse --------------------------------------------------------
            parsed = parse_page(html, config.field_map)
            logger.info(
                "Fetched page %d: %d items",
                current_page_num,
                len(parsed.items),
            )
            items_skipped += parsed.skipped

            # -- item budget --------------------------------------------------
            if config.limits.max_items is not None:
                remaining = config.limits.max_items - len(all_items)
                if remaining <= 0:
                    logger.info(
                        "Reached max_items limit (%d), stopping.",
                        config.limits.max_items,
                    )
                    break
                if len(parsed.items) > remaining:
                    all_items.extend(parsed.items[:remaining])
                    logger.info(
                        "Reached max_items limit (%d) mid-page, stopping.",
                        config.limits.max_items,
                    )
                    break
                all_items.extend(parsed.items)
            else:
                all_items.extend(parsed.items)

            # -- stop conditions ---------------------------------------------
            if not parsed.items:
                logger.info(
                    "Page %d yielded zero items, stopping.", current_page_num
                )
                break

            next_url = next_page_url(
                html, current_url, config.pagination, current_page_num
            )
            if next_url is None:
                logger.info(
                    "No next page found at %s, stopping", current_url
                )
                break

            current_url = next_url
            current_page_num += 1

    elapsed = time.monotonic() - started

    # -- export ---------------------------------------------------------------
    written, duplicates = export_csv(
        all_items,
        config.field_names,
        config.output_file,
        config.dedupe_key,
    )

    summary: dict[str, float | int] = {
        "pages_fetched": pages_fetched,
        "items_collected": len(all_items),
        "items_written": written,
        "items_skipped": items_skipped,
        "duplicates_removed": duplicates,
        "elapsed_seconds": round(elapsed, 3),
    }

    logger.info(
        "Summary: pages_fetched=%d items_collected=%d items_written=%d "
        "items_skipped=%d duplicates_removed=%d elapsed_seconds=%.3f",
        summary["pages_fetched"],
        summary["items_collected"],
        summary["items_written"],
        summary["items_skipped"],
        summary["duplicates_removed"],
        summary["elapsed_seconds"],
    )
    return summary


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    _configure_logging(args.log_level)

    if args.max_pages is not None and args.max_pages < 1:
        logger.error("--max-pages must be >= 1")
        return 2
    if args.max_items is not None and args.max_items < 1:
        logger.error("--max-items must be >= 1")
        return 2

    try:
        config = load_config(_resolve_source_path(args.source))
        config = with_overrides(
            config,
            max_pages=args.max_pages,
            max_items=args.max_items,
            output=args.output,
        )
    except ValueError as exc:
        logger.error("Configuration error: %s", exc)
        return 2

    try:
        crawl(config)
    except KeyboardInterrupt:
        logger.warning("Interrupted by user.")
        return 130

    return 0


if __name__ == "__main__":
    sys.exit(main())