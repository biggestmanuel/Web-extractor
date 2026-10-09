# Scrapely — Web Data Extractor

Paste a public webpage URL, get structured data back: title, metadata, headings, links, images, tables, JSON-LD and page text. Filter it in the browser, export to JSON or CSV.

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
- Obfuscated hosts such as `http://2130706433/` are decoded and rejected
- Per-IP rate limiting with `Retry-After`
- Redirect limit of 5, 5 MB response cap, 12s read timeout
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

84 tests cover URL validation and SSRF handling, redirect behaviour, extraction rules, rate limiting and caching, and the HTTP API. They use stub sessions and fixtures, so no test touches the network.

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
index.html, app.js, styles.css   Frontend
backend/main.py                  FastAPI app, routes, configuration
backend/safety.py                URL validation and SSRF protection
backend/fetcher.py               Redirect-aware HTTP with size and time limits
backend/extractor.py             Pure HTML parsing, no I/O
backend/ratelimit.py             Token bucket limiter and TTL cache
backend/tests/                   pytest suite
```

`extractor.py` has no network or framework dependency, so extraction rules can be tested directly against HTML fixtures.

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
