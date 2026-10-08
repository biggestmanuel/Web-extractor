"""HTML parsing and page extraction.

Pure functions over an HTML string: no network, no FastAPI. Keeping this
module free of I/O makes the extraction rules straightforward to test.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

LIMITS = {
    "headings": 500,
    "links": 500,
    "images": 300,
    "metadata": 200,
    "text": 20_000,
}

SKIPPED_LINK_PREFIXES = ("#", "mailto:", "tel:", "javascript:", "data:", "sms:")
HTTP_SCHEMES = {"http", "https"}

META_NAME_RE = re.compile(r"^(description|keywords|author|robots|og:[a-z0-9_:]+|twitter:[a-z0-9_:]+|article:[a-z0-9_:]+)$", re.I)


def clean(text: str | None) -> str:
    """Collapse whitespace and strip, so multi-line HTML text renders as one line."""
    return " ".join((text or "").split())


def _absolute(base_url: str, value: str) -> str | None:
    """Resolve *value* against *base_url*, keeping only http(s) results."""
    value = (value or "").strip()
    if not value:
        return None
    try:
        resolved = urljoin(base_url, value)
    except ValueError:
        return None
    return resolved if urlparse(resolved).scheme in HTTP_SCHEMES else None


def extract_title(soup: BeautifulSoup) -> str:
    if soup.title and soup.title.string is not None:
        return clean(str(soup.title.string))
    if soup.title:
        return clean(soup.title.get_text(" ", strip=True))
    og = soup.find("meta", attrs={"property": "og:title"})
    return clean(og.get("content")) if og else ""


def extract_description(soup: BeautifulSoup) -> str:
    for attrs in ({"name": "description"}, {"property": "og:description"}, {"name": "twitter:description"}):
        tag = soup.find("meta", attrs=attrs)
        if tag:
            value = clean(tag.get("content"))
            if value:
                return value
    return ""


def extract_canonical(soup: BeautifulSoup, base_url: str) -> str:
    for link in soup.find_all("link", attrs={"rel": True}):
        rel = link.get("rel") or []
        rel = [rel] if isinstance(rel, str) else list(rel)
        if any(value.lower() == "canonical" for value in rel):
            url = _absolute(base_url, link.get("href", ""))
            if url:
                return url
    og = soup.find("meta", attrs={"property": "og:url"})
    return clean(og.get("content")) if og else ""


def extract_language(soup: BeautifulSoup) -> str:
    html = soup.find("html")
    if not html:
        return ""
    value = html.get("lang") or html.get("xml:lang")
    return clean(str(value))[:16].lower() if value else ""


def extract_author(soup: BeautifulSoup) -> str:
    for attrs in ({"name": "author"}, {"property": "article:author"}, {"name": "twitter:creator"}):
        tag = soup.find("meta", attrs=attrs)
        if tag:
            value = clean(tag.get("content"))
            if value:
                return value
    return ""


def extract_headings(soup: BeautifulSoup) -> list[dict]:
    headings = []
    for tag in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
        text = clean(tag.get_text(" ", strip=True))
        if text:
            headings.append({"level": tag.name, "text": text})
        if len(headings) >= LIMITS["headings"]:
            break
    return headings


def extract_links(soup: BeautifulSoup, base_url: str) -> list[dict]:
    links: list[dict] = []
    seen: set[str] = set()
    for tag in soup.find_all("a", href=True):
        href = tag.get("href", "").strip()
        if not href or href.lower().startswith(SKIPPED_LINK_PREFIXES):
            continue
        url = _absolute(base_url, href)
        if not url or url in seen:
            continue
        seen.add(url)
        links.append({"text": clean(tag.get_text(" ", strip=True)) or url, "url": url})
        if len(links) >= LIMITS["links"]:
            break
    return links


def extract_images(soup: BeautifulSoup, base_url: str) -> list[dict]:
    images: list[dict] = []
    seen: set[str] = set()
    for tag in soup.find_all("img"):
        raw = ""
        for attribute in ("src", "data-src", "data-original", "data-lazy-src"):
            candidate = tag.get(attribute)
            if isinstance(candidate, str) and candidate.strip():
                raw = candidate.strip()
                break
        url = _absolute(base_url, raw)
        if not url or url in seen:
            continue
        seen.add(url)
        width = tag.get("width")
        height = tag.get("height")
        images.append(
            {
                "alt": clean(tag.get("alt")),
                "url": url,
                "width": int(width) if str(width or "").isdigit() else None,
                "height": int(height) if str(height or "").isdigit() else None,
            }
        )
        if len(images) >= LIMITS["images"]:
            break
    return images


def extract_metadata(soup: BeautifulSoup) -> list[dict]:
    metadata = []
    for tag in soup.find_all("meta"):
        key = tag.get("name") or tag.get("property") or tag.get("http-equiv")
        content = tag.get("content")
        if not key or not content:
            continue
        key, content = clean(key), clean(content)
        if not META_NAME_RE.match(key):
            continue
        metadata.append({"name": key, "content": content})
        if len(metadata) >= LIMITS["metadata"]:
            break
    return metadata


LAYOUT_CLASS_HINTS = ("navbox", "navigation", "toc", "menu", "footer", "header", "breadcrumb")


def _is_layout_table(table) -> bool:
    """Heuristically detect tables used for page layout rather than tabular data.

    Old pages and CMS templates build navigation, sidebars and footers out of
    tables. Those show up as data with a nav bar as the "header row", which is
    noise for someone exporting records. Real data tables announce themselves
    with a multi-cell header row and a stable column count.
    """
    if table.get("role") == "presentation":
        return True

    markers = " ".join(
        str(table.get(attribute, "")) for attribute in ("class", "id", "data-layout")
    ).lower()
    if any(hint in markers for hint in LAYOUT_CLASS_HINTS):
        return True

    rows = [tr.find_all(["th", "td"]) for tr in table.find_all("tr")]
    rows = [cells for cells in rows if cells]
    if len(rows) < 2:
        return True

    # A header row spanning one cell is a section title, not column labels.
    if len(rows[0]) < 2:
        return True
    # Data tables keep a stable column count of two or more. Layout tables wrap
    # content in rows of differing widths, or stack everything into a single
    # column, which is the signature of markup doing presentation, not data.
    widths = [len(cells) for cells in rows]
    common, frequency = max(((w, widths.count(w)) for w in set(widths)), key=lambda pair: pair[1])
    return frequency / len(widths) < 0.8 or common < 2


def extract_tables(soup: BeautifulSoup) -> list[dict]:
    """Return data tables as a header row plus data rows, skipping layout tables."""
    tables = []
    for table in soup.find_all("table"):
        rows = []
        for tr in table.find_all("tr"):
            cells = [clean(cell.get_text(" ", strip=True)) for cell in tr.find_all(["th", "td"])]
            if any(cells):
                rows.append(cells)
        if len(rows) >= 2 and not _is_layout_table(table):
            tables.append({"headers": rows[0], "rows": rows[1:]})
        if len(tables) >= 25:
            break
    return tables


def extract_text(soup: BeautifulSoup) -> str:
    for tag in soup(["script", "style", "noscript", "template"]):
        tag.decompose()
    main = soup.find("main") or soup.find("article") or soup.body or soup
    return clean(main.get_text(" ", strip=True))[: LIMITS["text"]]


def extract_json_ld(soup: BeautifulSoup) -> list[dict]:
    """Parse schema.org JSON-LD blocks, ignoring malformed ones."""
    results = []
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = script.string or script.get_text() or ""
        raw = raw.strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            continue
        for item in data if isinstance(data, list) else [data]:
            if isinstance(item, dict):
                results.append(item)
        if len(results) >= 20:
            break
    return results


def extract_email(soup: BeautifulSoup, text: str) -> list[str]:
    """Collect mailto: links and emails in the page text, deduplicated.

    *text* is passed in because text extraction consumes script/style tags,
    so it must run before any second walk over the parsed tree.
    """
    found: list[str] = []
    seen: set[str] = set()
    for tag in soup.find_all("a", href=True):
        href = tag["href"].strip()
        if href.lower().startswith("mailto:"):
            address = href[7:].split("?")[0].strip()
            if address and address.lower() not in seen:
                seen.add(address.lower())
                found.append(address)
    if len(found) < 25:
        for address in re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text):
            if address.lower() not in seen:
                seen.add(address.lower())
                found.append(address)
                if len(found) >= 25:
                    break
    return found


def extract(html: str, url: str) -> dict:
    """Extract a structured summary of *html* fetched from *url*."""
    soup = BeautifulSoup(html, "html.parser")

    for tag in soup(["script", "noscript", "style", "template"]):
        # Keep JSON-LD, drop everything else that would pollute text extraction.
        if tag.name == "script" and (tag.get("type") or "").lower() == "application/ld+json":
            continue
        tag.decompose()

    headings = extract_headings(soup)
    links = extract_links(soup, url)
    images = extract_images(soup, url)
    metadata = extract_metadata(soup)
    tables = extract_tables(soup)
    json_ld = extract_json_ld(soup)
    text = extract_text(soup)
    emails = extract_email(soup, text)

    return {
        "url": url,
        "title": extract_title(soup),
        "description": extract_description(soup),
        "canonical": extract_canonical(soup, url),
        "language": extract_language(soup),
        "author": extract_author(soup),
        "text": text,
        "emails": emails,
        "fetchedAt": datetime.now(timezone.utc).isoformat(),
        "headings": headings,
        "links": links,
        "images": images,
        "metadata": metadata,
        "tables": tables,
        "jsonLd": json_ld,
        "summary": {
            "headings": len(headings),
            "links": len(links),
            "images": len(images),
            "metadata": len(metadata),
            "tables": len(tables),
            "jsonLd": len(json_ld),
            "emails": len(emails),
            "characters": len(text),
        },
    }