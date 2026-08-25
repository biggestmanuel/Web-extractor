# Scrapely — Web Data Extractor

A public-facing web data extraction tool.

## V1 features
- Extract page title and description
- Extract headings
- Extract links
- Extract images
- Extract metadata
- Search/filter extracted data
- Export JSON and CSV
- Responsive UI
- Light/dark mode

## Stack
Frontend: HTML + CSS + Vanilla JavaScript
Backend: Python + FastAPI
Parsing: Requests + BeautifulSoup

## Run locally

Install Python 3.10+.

From `backend`:

    python -m venv .venv
    .venv\Scripts\activate
    pip install -r requirements.txt
    uvicorn main:app --reload

The frontend calls `/api/extract`, so deploy it behind the same origin or configure the frontend API base URL for a separate backend.

## Production requirements

This is a V1 foundation. A public deployment should add:
- Per-IP rate limiting
- SSRF/private-network blocking
- Redirect limits
- Abuse monitoring
- Request quotas
- Caching
- Larger-page/background-job handling
- Clear robots.txt/legal policy
- Optional JavaScript rendering where appropriate

The tool does not bypass CAPTCHAs, authentication, paywalls, bot protection, or access controls.

## Positioning

Turn webpages into usable data for developers, researchers, SEO teams, journalists, students and analysts.

Do not market it as a universal scraper. It is designed for responsible extraction from publicly accessible HTML.
# Web-extractor
