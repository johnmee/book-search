# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx"]
# ///
"""Searches several BorrowBox libraries for a book and reports availability in each.

BorrowBox has no public API. Each library has its own site (https://<slug>.borrowbox.com),
and its search page is a Next.js page that embeds the results as JSON in __NEXT_DATA__.
Searching needs no login, so no credentials are involved.

    uv run borrowbox_search.py "the women kristin hannah"
    uv run borrowbox_search.py "dune" "frank herbert" --format audiobook --available-only --json

Exit codes: 0 all libraries answered, 1 at least one library failed, 2 config or argument error.
"""

import argparse
import json
import re
import sys
import tomllib
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

USER_AGENT = "Mozilla/5.0 (borrowbox-search)"
TIMEZONE = ZoneInfo("Australia/Sydney")
FORMATS = {"ebook": "eBooks", "audiobook": "eAudiobooks", "all": None}
NEXT_DATA = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.DOTALL)


class SearchError(Exception):
    pass


@dataclass
class Result:
    query: str
    title: str
    authors: list[str]
    isbn: str
    format: str
    status: str
    next_available: str | None
    url: str


@dataclass
class LibraryReport:
    library: str
    url: str
    ok: bool = True
    error: str | None = None
    results: list[Result] = field(default_factory=list)


def search(client: httpx.Client, base_url: str, query: str) -> list[Result]:
    """Returns the products a library's search page lists for query. Raises SearchError."""
    try:
        response = client.get(f"{base_url}/search", params={"q": query})
        response.raise_for_status()
    except httpx.HTTPError as e:
        raise SearchError(f"request failed: {e}") from e
    match = NEXT_DATA.search(response.text)
    if not match:
        raise SearchError("no __NEXT_DATA__ on the search page; the site layout may have changed")
    props = json.loads(match.group(1))["props"]
    # An unknown library slug still answers 200, with siteError "unknown-site".
    if props.get("siteError"):
        raise SearchError(f"site error {props['siteError']!r}; is {base_url} a BorrowBox library site?")
    queries = props["pageProps"].get("dehydratedState", {}).get("queries", [])
    data = next((q["state"]["data"] for q in queries if q.get("queryKey", [None])[0] == "productsSearch"), None)
    if data is None:
        raise SearchError("no productsSearch data on the search page; the site layout may have changed")
    return [_to_result(base_url, query, p) for p in data.get("products", [])]


def _to_result(base_url: str, query: str, product: dict) -> Result:
    availability = product.get("availability", {})
    next_ms = availability.get("nextAvailableDate")
    return Result(
        query=query,
        title=product.get("title", ""),
        authors=[a["name"] for a in product.get("authors", [])],
        isbn=product.get("isbn", ""),
        format=product.get("formatSpecifics", {}).get("loanFormat", ""),
        status=availability.get("status", "UNKNOWN"),
        next_available=datetime.fromtimestamp(next_ms / 1000, TIMEZONE).date().isoformat() if next_ms else None,
        url=f"{base_url}/product/{product.get('productId', '')}",
    )


def load_libraries(path: Path, names: list[str] | None) -> list[dict]:
    libraries = tomllib.loads(path.read_text())["library"]
    if names:
        unknown = set(names) - {lib["name"] for lib in libraries}
        if unknown:
            raise ValueError(f"unknown library: {', '.join(sorted(unknown))}")
        libraries = [lib for lib in libraries if lib["name"] in names]
    return libraries


def _debug_request(request: httpx.Request) -> None:
    body = request.read()
    print(f"> {request.method} {request.url}" + (f"\n> data: {body.decode(errors='replace')}" if body else ""), file=sys.stderr)


def _debug_response(response: httpx.Response) -> None:
    print(f"< {response.status_code} {response.reason_phrase} {response.request.url}", file=sys.stderr)


def run(libraries: list[dict], queries: list[str], loan_format: str | None, available_only: bool,
        debug: bool = False) -> list[LibraryReport]:
    reports = []
    hooks = {"request": [_debug_request], "response": [_debug_response]} if debug else {}
    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=20, follow_redirects=True, event_hooks=hooks) as client:
        for lib in libraries:
            report = LibraryReport(lib["name"], lib["url"].rstrip("/"))
            try:
                for query in queries:
                    report.results += search(client, report.url, query)
            except SearchError as e:
                report.ok, report.error = False, str(e)
            report.results = [
                r for r in report.results
                if (loan_format is None or r.format == loan_format) and (not available_only or r.status == "AVAILABLE")
            ]
            reports.append(report)
    return reports


def print_table(reports: list[LibraryReport]) -> None:
    for report in reports:
        print(f"\n## {report.library} ({report.url})")
        if not report.ok:
            print(f"  ERROR: {report.error}")
            continue
        if not report.results:
            print("  no matches")
        for r in report.results:
            when = f" until {r.next_available}" if r.next_available else ""
            print(f"  {r.status}{when} | {r.title} | {', '.join(r.authors)} | {r.format} | {r.url}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("query", nargs="+", help="title and/or author; each argument is a separate search")
    parser.add_argument("--library", action="append", help="limit to this library name from the config (repeatable)")
    parser.add_argument("--format", choices=FORMATS, default="all")
    parser.add_argument("--available-only", action="store_true", help="hide titles that are on loan")
    parser.add_argument("--json", action="store_true", help="print JSON instead of a table")
    parser.add_argument("--config", type=Path, default=Path(__file__).with_name("config.toml"))
    parser.add_argument("--debug", action="store_true", help="print each outgoing request and its response code to stderr")
    args = parser.parse_args()

    try:
        libraries = load_libraries(args.config, args.library)
    except (OSError, KeyError, ValueError, tomllib.TOMLDecodeError) as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2

    reports = run(libraries, args.query, FORMATS[args.format], args.available_only, args.debug)
    if args.json:
        print(json.dumps([asdict(r) for r in reports], indent=2))
    else:
        print_table(reports)
    return 0 if all(r.ok for r in reports) else 1


if __name__ == "__main__":
    sys.exit(main())
