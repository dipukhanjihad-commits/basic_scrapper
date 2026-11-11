# Config-Driven Multi-Source Web Scraper

A small, honest scraping engine. Each target is described by a JSON file
under `sources/`; the engine (`scraper.py`) is source-agnostic. Two demo
sources ship in the box: `books` and `quotes`.

## Quick start

pip install -r requirements.txt

python scraper.py --source books
python scraper.py --source books --max-pages 3
python scraper.py --source books --max-items 50
python scraper.py --source quotes --output quotes_demo.csv
Output is UTF-8 with BOM, so it opens cleanly in Excel and Sheets.

Project layout
.
├── scraper.py          # CLI + orchestration
├── config_loader.py    # JSON → typed Config (fail-fast validation)
├── fetcher.py          # HTTP with retry/backoff, honest UA
├── parser.py           # pure (html, field_map) → ParseResult
├── paginator.py        # next-page discovery
├── exporter.py         # CSV writer with dedupe-by-key
└── sources/
    ├── books.json
    └── quotes.json
Configuration schema
{
  "start_url": "https://…",
  "field_map": {
    "field_name": { "selector": "css", "attr": "optional-attribute" },
    "other":      { "selector": "css" }   // text extraction
  },
  "pagination": {
    "mode": "css_selector" | "url_pattern",
    "selector": "li.next a",              // required for css_selector
    "base_url": "https://…/page-{}.html"  // required for url_pattern
  },
  "limits": {
    "mode": "all" | "pages" | "items",
    "max_pages": null,
    "max_items": null
  },
  "output_file": "out.csv",
  "request_timeout_seconds": 15,
  "max_retries": 3,
  "dedupe_key": "field_name"
}
limits.mode invariants (enforced at load time):
mode	max_pages	max_items
all	null	null
pages	int ≥ 1	null
items	null	int ≥ 1
CLI flags --max-pages / --max-items override the file and are enforced
independently of limits.mode.

Design decisions
Why adapter-via-config
Every source-specific concern — selectors, pagination, limits, output — is
data, not code. Adding a target is cp sources/books.json sources/new.json
and editing the selectors. No if source == "books" branches exist anywhere
in the engine. This keeps scraper.py small, makes sources reviewable in a
PR, and lets non-Python contributors add targets without touching the code.

Why two pagination modes
Real sites paginate in two fundamentally different ways:

css_selector — the next-page URL is in the HTML (a <a> in a
li.next). Best for semantic sites like books.toscrape.com and
quotes.toscrape.com. urljoin handles relative hrefs correctly.

url_pattern — the next-page URL is addressable (/page/{}/).
Best when the markup doesn't expose a next link, when the link is
rendered client-side, or when the site exposes a numeric URL scheme.
This mode cannot self-terminate; the crawler stops on a fetch failure,
an empty page, or max_pages.

One mode cannot cover both without heuristic guessing. Two explicit modes
make the trade-off visible to whoever writes the next source config.

Why dedupe-by-key
Listing pages routinely repeat rows: sticky/pinned items, cross-page
overlaps, and results that cross into a "related" section. Duplicates
pollute downstream analysis. Each source declares its own dedupe_key
(title for books, text for quotes) — identity is a source property,
not a global constant. First occurrence wins so crawl order is preserved.

Why skip-on-error
A single malformed row (broken selector, missing field, an ad slot that
happens to match) should not abort a 20-page crawl. Skips are:

Logged — one Skipped item: … line per row, with the field and row
index so the selector can be fixed.

Counted — items_skipped in the final summary. If the number is
high, something is wrong and it's easy to notice.

Not silently dropped — the exporter never sees them.

The parser also aligns by the longest column so trailing rows from a
truncated column surface as skips instead of vanishing.

Note on parse_page's return type
The brief describes parse_page(...) -> list[dict]. The implementation
returns a small ParseResult(items, skipped) dataclass instead, so the
orchestrator can report items_skipped without a second pass over the HTML.
The function is still pure — same inputs, same output, no I/O.

Explicit non-goals (and where they'd go)
Each of these has a # FUTURE EXTENSION: comment in the relevant module:

GUI (web or TUI) — scraper.py

Database persistence — exporter.py

Proxy rotation / anti-bot handling / Cloudflare — fetcher.py

JS rendering (Playwright/Selenium) — parser.py

Login / session / cookie management — scraper.py

Per-item container selector (item_selector) instead of field-aligned zip — parser.py

Async / concurrent crawling — scraper.py

Value transforms (regex, prefix-strip, value maps) on FieldSpec — parser.py

Adding a new source
Create sources/<name>.json following the schema above.

Run python scraper.py --source <name>.

No code changes required.


---

## Notes on running

- Both demo URLs are public, static, and scrape-friendly — no auth, no JS, no anti-bot in the path.
- Sample first-run output for --source books --max-pages 1:
2024-01-01 12:00:00 [INFO] Starting crawl: source=books start_url=https://books.toscrape.com/catalogue/page-1.html
2024-01-01 12:00:01 [INFO] Fetched page 1: 20 items
2024-01-01 12:00:01 [INFO] Wrote 20 rows to books.csv (0 duplicates removed)
2024-01-01 12:00:01 [INFO] Summary: pages_fetched=1 items_collected=20 items_written=20 items_skipped=0 duplicates_removed=0 elapsed_seconds=0.842


- A bad config file fails before any network call, with a ValueError that names the offending path (e.g. Config 'books'.pagination.mode: must be one of ('css_selector', 'url_pattern'), got 'magic'`).
- The `rating` field on books intentionally captures the full class string (`"star-rating Three"`) because the schema's `"attr": "class"` semantics are literal. Filtering out the `star-rating` prefix is a value-transform concern — deliberately out of scope for now (see the future-extensions list).


