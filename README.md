# Scrapely — Web Data Extractor

Extract structured data from a webpage: title, metadata, headings, links, images, tables, JSON-LD and page text. Filter it, export to JSON or CSV.

Two front ends share the same extraction rules:

- **Web tool** — paste a URL and extract it on the server
- **Browser extension** — extract the page you are currently viewing

## Features

Extracted data
- Page title, description, canonical URL, language and author
- Headings with their level
- Links and images, resolved to absolute URLs and deduplicated
- Tables as header rows plus data rows (layout and navigation tables are skipped)
- Structured data from schema.org JSON-LD blocks
- Metadata tags, filtered to meaningful ones
- Email addresses and cleaned page text

Interface
- Filter every section from one search box
- Export to JSON or CSV
- Responsive layout, light and dark mode

Safety
- SSRF protection: private, loopback, link-local, CGNAT and reserved addresses are refused, including on every redirect hop
- Connections pinned to the validated address, so DNS cannot rebind between validation and connect
- Obfuscated hosts such as `http://2130706433/` are decoded and rejected
- Per-IP rate limiting with `Retry-After`
- Redirect limit of 5, 5 MB response cap, 12s read timeout
- Charset detected from the body when the server declares none, so UTF-8 pages are not mangled
- Port allowlist, http/https only, no embedded credentials
- Short-lived result cache

## Stack

Frontend: HTML + CSS + Vanilla JavaScript
Backend: Python + FastAPI
Parsing: Requests + BeautifulSoup

## Run locally

Python 3.10+.

```
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
uvicorn main:app --reload
```

Open http://127.0.0.1:8000. The backend serves the frontend from the repository root, so there is a single origin and no CORS setup is needed.

## Tests

```
pip install -r requirements.txt
python -m pytest
```

100 tests cover URL validation and SSRF handling, connection pinning, redirect behaviour, charset handling, extraction rules, rate limiting and caching, and the HTTP API. They use stub sessions and fixtures, so no test touches the network.

## API

`GET /api/health`

```json
{
  "status": "ok",
  "version": "1.1.0",
  "rateLimit": { "requests": 10, "windowSeconds": 60.0 },
  "allowedPorts": [80, 443, 8080, 8443],
  "maxBytes": 5000000
}
```

`GET /api/extract?url=<public page URL>`

Returns the page data, a `summary` of section counts, and `X-Cache: HIT|MISS`. Errors return a readable `detail` message: `400` for a rejected URL, `413` for an oversized page, `415` for non-HTML, `429` when rate limited, `502` for an upstream failure, `504` on timeout.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `SCRAPELY_RATE_LIMIT` | `10` | Requests per window per IP |
| `SCRAPELY_RATE_WINDOW` | `60` | Window length in seconds |
| `SCRAPELY_CACHE_TTL` | `300` | Seconds a result is cached |
| `SCRAPELY_ALLOWED_ORIGINS` | `*` | Comma-separated CORS origins |
| `SCRAPELY_TRUST_PROXY` | unset | Set to `1` to trust `X-Forwarded-For` for client IPs |

Set `SCRAPELY_TRUST_PROXY=1` only when a proxy you control sets that header. Otherwise a client could spoof its IP and bypass the rate limit.

## Project layout

```
AGENTS.md                        Working agreements for coding agents
index.html, app.js, styles.css   Web tool frontend
backend/main.py                  FastAPI app, routes, configuration
backend/safety.py                URL validation and SSRF protection
backend/fetcher.py               Redirect-aware HTTP with size and time limits
backend/extractor.py             Pure HTML parsing, no I/O
backend/ratelimit.py             Token bucket limiter and TTL cache
backend/tests/                   pytest suite
extension/manifest.json          Browser extension manifest
extension/src/extractor.js       Extraction against the live DOM
extension/src/popup.*            Extension popup UI
extension/test/                  Browser tests, served over HTTP, plus a
                                 real-browser test driven over CDP
```

`extractor.py` has no network or framework dependency, so extraction rules can be tested directly against HTML fixtures.

## Browser extension

`extension/` holds a Manifest V3 Chrome/Edge extension that reads the page you are viewing. No build step and no npm install.

### Load it unpacked

1. Open `chrome://extensions`
2. Turn on **Developer mode**
3. Click **Load unpacked** and choose the `extension` folder
4. Pin Scrapely, open any webpage, click the icon

### How it works

The popup injects `src/extractor.js` into the active tab and renders whatever it returns. Because the browser performs the fetch, none of the server-side protections apply here — there is no outbound request of your own to abuse, so SSRF blocking, DNS-rebinding defence, rate limiting and redirect caps are all the browser's problem rather than the app's.

That is the main reason to prefer the extension where either would do: a smaller attack surface and no server to operate. The trade-off is that it can only read the page in front of you, not an arbitrary URL.

Permissions requested are `activeTab`, `scripting` and `storage`. There is deliberately no `<all_urls>` host permission, so the extension cannot read anything until you click it on that page.

### Tests

Both test pages are static and must be served over HTTP, not opened as `file://`:

```
cd backend
uvicorn main:app --reload
```

Then open:

- http://127.0.0.1:8000/extension/test/extractor.test.html — 22 tests for extraction
- http://127.0.0.1:8000/extension/test/popup.test.html — 35 tests for rendering and exports

Each prints a pass/fail summary at the top. `extractor.js` is a deliberate port of `backend/extractor.py`; if you change the filtering rules in one, change them in the other.

There is also a test that loads the extension into a real browser:

```
node extension/test/browser.e2e.mjs
```

It drives Chromium over the DevTools protocol, injects `extractor.js` into a live page with the real `chrome.scripting` API, renders the result in the real popup, and checks the exports. Set `SCRAPELY_CHROMIUM` if the browser is not at the Playwright path it looks for. Branded Chrome and Edge refuse `--load-extension`, which is why this uses the CDP `Extensions.loadUnpacked` domain instead.

This covers everything except the `activeTab` grant itself, which needs a real toolbar click. The staged copy used by the test adds one host permission to work around that.

`AGENTS.md` records the commit-per-change rule and the review checklist to run before committing.

### Known limitation

Connections are pinned to the address that was validated, so DNS rebinding does not work here. Two details remain worth knowing:

- Pinning uses a single validated address. urllib3 would normally try every address a host returns, so a site whose first address is down will fail rather than fail over to the next.
- Requests routed through an explicitly configured proxy are passed through unpinned, because the proxy resolves names itself. The service does not configure a proxy.

## Before running this in public

The defaults suit a single instance. A public deployment should add:

- A shared rate limiter (Redis or gateway-level limits) instead of the in-process bucket, which is per instance
- Persistent caching and abuse monitoring
- Background jobs for large or slow pages
- A robots.txt and terms-of-service policy, surfaced in the UI
- Structured logging and alerting on error rates
- JavaScript rendering only where a site's terms allow it

The tool does not bypass CAPTCHAs, authentication, paywalls, bot protection or access controls, and it only extracts from publicly accessible HTML.

## Positioning

Turn webpages into usable data for developers, researchers, SEO teams, journalists, students and analysts.

Do not market it as a universal scraper. It is designed for responsible extraction from publicly accessible HTML.
