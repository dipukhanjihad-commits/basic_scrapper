"""CSV export with UTF-8 BOM and deduplication by key.

# FUTURE EXTENSION: database persistence (Postgres/SQLite), JSON/Parquet
# writers, streaming output for very large crawls.
"""
from __future__ import annotations

import csv
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def export_csv(
    items: list[dict[str, str]],
    field_names: list[str],
    output_path: str | Path,
    dedupe_key: str,
) -> tuple[int, int]:
    """Write ``items`` to ``output_path`` and return ``(written, duplicates_removed)``.

    Deduplication preserves the *first* occurrence of each key, keeping the
    crawl order.  UTF-8 with BOM so Excel opens it cleanly.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    seen: set[str] = set()
    deduped: list[dict[str, str]] = []
    duplicates = 0

    for row in items:
        key = row.get(dedupe_key, "")
        if key in seen:
            duplicates += 1
            continue
        seen.add(key)
        deduped.append(row)

    with output_path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=field_names)
        writer.writeheader()
        for row in deduped:
            writer.writerow({name: row.get(name, "") for name in field_names})

    logger.info(
        "Wrote %d rows to %s (%d duplicates removed)",
        len(deduped),
        output_path,
        duplicates,
    )
    return len(deduped), duplicates