"""Safe HTTP fetching of a single HTML page.

Redirects are followed manually so every hop is re-validated: a public URL that
redirects to 127.0.0.1 is the classic way to defeat a one-shot SSRF check.

Connections are also *pinned* to the address that was validated. Without this,
DNS is resolved once during validation and again by the HTTP client when it
connects, and a host with a short TTL can answer with a public address the
first time and a private one the second. See PinnedAdapter.
"""

from __future__ import annotations

import requests
import urllib3.connection
from requests.adapters import HTTPAdapter, select_proxy
from urllib.parse import urljoin, urlparse

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


class _PinnedConnectionMixin:
    """Connect to a pre-validated address instead of resolving the host again.

    ``_dns_host`` is the address the socket dials; ``server_hostname`` is the
    name presented in the TLS handshake and checked against the certificate.
    Setting only the first would send the IP as SNI and every HTTPS site would
    reject the handshake.
    """

    def __init__(self, *args, pinned_address=None, tls_hostname=None, **kwargs):
        super().__init__(*args, **kwargs)
        if pinned_address:
            self._dns_host = pinned_address
            self.server_hostname = tls_hostname


class _PinnedHTTPSConnection(_PinnedConnectionMixin, urllib3.connection.HTTPSConnection):
    pass


class _PinnedHTTPConnection(_PinnedConnectionMixin, urllib3.connection.HTTPConnection):
    pass


class PinnedAdapter(HTTPAdapter):
    """Adapter that resolves, validates and pins the address it connects to.

    Only the socket target changes: the URL, the ``Host`` header, SNI and
    certificate verification all still refer to the original hostname, so a
    pinned connection is indistinguishable from a normal one to the server.

    Requests routed through an explicit proxy are passed through untouched,
    because the proxy performs its own name resolution and pinning would not
    apply. The service does not configure a proxy, so that path is only a
    fallback for unusual deployments.
    """

    def get_connection_with_tls_context(self, request, verify, proxies=None, cert=None):
        conn = super().get_connection_with_tls_context(request, verify, proxies, cert)

        if select_proxy(request.url, proxies):
            return conn

        parsed = urlparse(request.url)
        hostname = parsed.hostname
        if not hostname:
            return conn

        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        addresses = safety.resolve_addresses(hostname, port)
        for ip in addresses:
            reason = safety.is_blocked_ip(ip)
            if reason:
                raise safety.UnsafeUrlError(f"Refusing to fetch a non-public target: {reason}.")

        # Pin to a single validated address. urllib3 would otherwise try every
        # address DNS returned, so this trades failover across A records for the
        # guarantee that the socket only ever reaches an address we checked.
        extra = {"pinned_address": str(addresses[0])}
        if parsed.scheme == "https":
            extra["tls_hostname"] = hostname

        conn.ConnectionCls = (
            _PinnedHTTPSConnection if parsed.scheme == "https" else _PinnedHTTPConnection
        )
        conn.conn_kw = {**(conn.conn_kw or {}), **extra}
        # urllib3 derives Host from the socket address, so set it explicitly or
        # virtual hosts answer with 421 Misdirected Request.
        request.headers["Host"] = parsed.netloc
        return conn


def build_session() -> requests.Session:
    """A session whose connections are pinned to validated addresses."""
    session = requests.Session()
    adapter = PinnedAdapter()
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


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

    # A pinned session closes the DNS rebinding window; a caller-supplied
    # session (the tests use stubs) is used as given.
    owned: requests.Session | None = None
    if session is not None:
        http = session
    else:
        owned = build_session()
        http = owned

    try:
        return _follow(current, http, timeout, max_bytes, max_redirects)
    except safety.UnsafeUrlError as exc:
        # The adapter refuses a target that rebinds to a private address
        # between validation and connect. Report it as a rejected URL rather
        # than letting a ValueError escape as a 500.
        raise FetchError(str(exc), 400) from exc
    finally:
        if owned is not None:
            owned.close()


def _follow(
    current: str,
    http,
    timeout: tuple[float, float],
    max_bytes: int,
    max_redirects: int,
) -> tuple[str, str]:
    """Issue the request, re-validating every redirect hop."""

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
