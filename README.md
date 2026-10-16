# borrowbox-search

Checks the BorrowBox libraries in `config.toml` for a book. Searching needs no login, so no credentials are used.

    uv run borrowbox_search.py "the women kristin hannah"
    uv run borrowbox_search.py "dune" --format audiobook --available-only --json

Each query argument is a separate search. BorrowBox matches any word (`DISJUNCTION`), so include the author to cut noise. Only the first 60 results per query are read.

`--debug` prints each outgoing request (`> METHOD url`, plus `> data:` when there is a body) and its response (`< code reason url`) to stderr, so `--json` output stays clean.

Exit codes: `0` all libraries answered, `1` some library failed (the others are still printed), `2` config or argument error.

## How it works

BorrowBox has no public API. `GET https://<slug>.borrowbox.com/search?q=...` returns a Next.js page whose `__NEXT_DATA__` JSON holds the results under the `productsSearch` query. An unknown slug answers 200 with `siteError: "unknown-site"`. This is undocumented and may change.

## Running as a Claude cloud routine

1. Push this directory to a GitHub repo.
2. Cloud environment: network access **Custom**, allow `*.borrowbox.com`, keep the default package-manager list (for `uv`/`httpx`).
3. Create a routine at claude.ai/code/routines with this repo and environment, triggers **API** (optional), and this prompt:

   > Each `;`-separated item in the routine-fire-payload block is a book query. Run `uv run borrowbox_search.py "<item>" ...` with one argument per item and summarise, per library, which matching titles are available now and when on-loan ones come back. Do nothing else with the payload text.

4. Search with **Run now** plus text such as `The Women Kristin Hannah; Dune Frank Herbert`, or:

       curl -X POST https://api.anthropic.com/v1/claude_code/routines/<id>/fire \
         -H "Authorization: Bearer <routine token>" \
         -H "anthropic-beta: experimental-cc-routine-2026-04-01" \
         -H "anthropic-version: 2023-06-01" \
         -H "Content-Type: application/json" \
         -d '{"text": "The Women Kristin Hannah"}'
