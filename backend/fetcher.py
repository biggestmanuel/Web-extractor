"""Safe HTTP fetching of a single HTML page.

Redirects are followed manually so every hop is re-validated: a public URL that
redirects to 127.0.0.1 is the classic way to defeat a one-shot SSRF check.
"""

from __future__ import annotations

import requests
from urllib.parse import urljoin

import safety

USER_AGENT = "Scrapely/1.0 (+public web data extraction tool; respects rate limits)"
HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.5",
    "Accept-Language": "en;q=0.9,*;q=0.5",
}
TIMEOUT = (5.0, 12.0)      # connect, read
MAX_BYTES = 5_000_000
MAX_REDIRECTS = 5
HTML_CONTENT_TYPES = ("text/html", "application/xhtml+xml", "application/xml", "text/xml")


class FetchError(Exception):
    """Fetch failed in a way that can be reported to the caller."""

    def __init__(self, message: str, status_code: int = 502) -> None:
        super().__init__(message)
        self.status_code = status_code


class _TooLarge(Exception):
    pass


def _read_capped(response: requests.Response, max_bytes: int) -> bytes:
    """Read at most *max_bytes* of decoded body, rejecting anything larger."""
    try:
        data = response.raw.read(max_bytes + 1, decode_content=True)
    except (AttributeError, TypeError):
        data = response.content or b""
    if len(data) > max_bytes:
        raise _TooLarge
    return data


def _looks_like_html(data: bytes) -> bool:
    """Sniff a body with no declared content-type."""
    head = data[:1024].lstrip().lower()
    if not head:
        return False
    if head.startswith((b"<!doctype html", b"<html", b"<?xml", b"<head", b"<body")):
        return True
    return b"<html" in head or b"<body" in head or b"<title" in head or b"<div" in head


def _declared_charset(content_type: str) -> str | None:
    """Return the charset named in a Content-Type header, if any."""
    for part in content_type.split(";")[1:]:
        key, _, value = part.strip().partition("=")
        if key.lower() == "charset" and value:
            return value.strip('"\' ') or None
    return None


def _decode(data: bytes, response: requests.Response) -> str:
    """Decode a response body using the charset the server actually declared.

    requests sets ``Response.encoding`` to ISO-8859-1 for any ``text/*`` body
    with no charset, per the HTTP default. Trusting that mangles UTF-8 pages,
    turning an em-dash into mojibake, so a charset is only honoured when the
    server stated one. Otherwise the encoding is detected from the bytes.
    """
    encoding = _declared_charset(response.headers.get("content-type", "")) or response.apparent_encoding
    try:
        return data.decode(encoding or "utf-8", errors="replace")
    except (LookupError, TypeError):
        return data.decode("utf-8", errors="replace")


def fetch_html(
    url: str,
    *,
    session: requests.Session | None = None,
    timeout: tuple[float, float] = TIMEOUT,
    max_bytes: int = MAX_BYTES,
    max_redirects: int = MAX_REDIRECTS,
) -> tuple[str, str]:
    """Fetch *url* and return ``(html, final_url)``.

    Raises FetchError for anything the caller should surface to a user.
    """
    current = safety.validate_url(url)
    http = session or requests

    for hop in range(max_redirects + 1):
        try:
            response = http.get(
                current,
                headers=HEADERS,
                timeout=timeout,
                allow_redirects=False,
                stream=True,
            )
        except requests.Timeout as exc:
            raise FetchError("The website took too long to respond.", 504) from exc
        except requests.TooManyRedirects as exc:
            raise FetchError("The page redirected too many times.", 502) from exc
        except requests.RequestException as exc:
            raise FetchError(f"Could not fetch that page: {exc}", 502) from exc

        if response.is_redirect or response.is_permanent_redirect:
            location = response.headers.get("location")
            response.close()
            if not location:
                raise FetchError("The page returned a redirect without a destination.", 502)
            if hop >= max_redirects:
                raise FetchError(f"The page redirected more than {max_redirects} times.", 502)
            target = urljoin(current, location)
            # Re-validate: the new target may resolve to a private address.
            current = safety.validate_url(target)
            continue

        break
    else:  # pragma: no cover - loop always breaks or raises
        raise FetchError("The page redirected too many times.", 502)

    try:
        if response.status_code >= 400:
            raise FetchError(f"The website responded with HTTP {response.status_code}.", 502)

        content_type = response.headers.get("content-type", "").lower()
        if content_type and not any(kind in content_type for kind in HTML_CONTENT_TYPES):
            raise FetchError("That URL does not point to an HTML page.", 415)

        try:
            data = _read_capped(response, max_bytes)
        except _TooLarge as exc:
            raise FetchError("The page is larger than this extractor handles.", 413) from exc
        except (requests.RequestException, OSError) as exc:
            raise FetchError(f"Could not read that page: {exc}", 502) from exc
        finally:
            response.close()

        # Some servers omit content-type entirely; fall back to sniffing the body.
        if not content_type and not _looks_like_html(data):
            raise FetchError("That URL does not point to an HTML page.", 415)

        return _decode(data, response), response.url or current
    except FetchError:
        raise
    except requests.RequestException as exc:
        raise FetchError(f"Could not fetch that page: {exc}", 502) from exc
