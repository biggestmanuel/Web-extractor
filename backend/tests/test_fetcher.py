"""Fetcher tests using a stub session, so no real network calls happen."""

import pytest
import requests

import fetcher


class StubResponse:
    def __init__(self, *, status_code=200, headers=None, body=b"", url="https://example.com/"):
        self.status_code = status_code
        self.headers = {"content-type": "text/html; charset=utf-8"} if headers is None else headers
        self.url = url
        self.encoding = "utf-8"
        self.apparent_encoding = None
        self.raw = _Raw(body)
        self.closed = False

    @property
    def is_redirect(self):
        return self.status_code in (301, 302, 303, 307, 308)

    @property
    def is_permanent_redirect(self):
        return self.status_code == 308

    def close(self):
        self.closed = True


class _Raw:
    def __init__(self, body):
        self._body = body

    def read(self, size, decode_content=False):
        return self._body[:size]


class StubSession:
    """Returns queued responses in order; records requested URLs."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


@pytest.fixture(autouse=True)
def allow_public_url(monkeypatch):
    monkeypatch.setattr(fetcher.safety, "validate_url", lambda url, **kw: url)


def test_returns_html_and_final_url():
    session = StubSession([StubResponse(body=b"<h1>hi</h1>")])
    html, url = fetcher.fetch_html("https://example.com/", session=session)
    assert html == "<h1>hi</h1>"
    assert url == "https://example.com/"


def test_follows_redirects_within_limit():
    session = StubSession(
        [
            StubResponse(status_code=301, headers={"location": "/next"}, body=b""),
            StubResponse(body=b"<h1>ok</h1>", url="https://example.com/next"),
        ]
    )
    html, url = fetcher.fetch_html("https://example.com/", session=session)
    assert html == "<h1>ok</h1>"
    assert session.calls == ["https://example.com/", "https://example.com/next"]


def test_stops_after_max_redirects():
    session = StubSession([StubResponse(status_code=302, headers={"location": "/loop"}) for _ in range(10)])
    with pytest.raises(fetcher.FetchError, match="redirected"):
        fetcher.fetch_html("https://example.com/", session=session)
    assert len(session.calls) == fetcher.MAX_REDIRECTS + 1


def test_redirect_loop_is_capped_by_configured_limit():
    session = StubSession([StubResponse(status_code=302, headers={"location": "/loop"}) for _ in range(10)])
    with pytest.raises(fetcher.FetchError):
        fetcher.fetch_html("https://example.com/", session=session, max_redirects=2)
    assert len(session.calls) == 3


def test_redirect_without_location_fails():
    session = StubSession([StubResponse(status_code=302, headers={}, body=b"")])
    with pytest.raises(fetcher.FetchError, match="without a destination"):
        fetcher.fetch_html("https://example.com/", session=session)


def test_rejects_non_html_content_type():
    session = StubSession([StubResponse(headers={"content-type": "image/png"}, body=b"\x89PNG")])
    with pytest.raises(fetcher.FetchError) as exc:
        fetcher.fetch_html("https://example.com/x.png", session=session)
    assert exc.value.status_code == 415


def test_sniffs_body_when_content_type_missing():
    session = StubSession([StubResponse(headers={}, body=b"<!doctype html><h1>ok</h1>")])
    html, _ = fetcher.fetch_html("https://example.com/", session=session)
    assert html.startswith("<!doctype")

    binary = StubSession([StubResponse(headers={}, body=b"\x00\x01\x02binary")])
    with pytest.raises(fetcher.FetchError) as exc:
        fetcher.fetch_html("https://example.com/x", session=binary)
    assert exc.value.status_code == 415


def test_enforces_size_cap():
    session = StubSession([StubResponse(body=b"x" * 100)])
    with pytest.raises(fetcher.FetchError) as exc:
        fetcher.fetch_html("https://example.com/", session=session, max_bytes=10)
    assert exc.value.status_code == 413


def test_error_status_is_reported():
    session = StubSession([StubResponse(status_code=404, body=b"nope")])
    with pytest.raises(fetcher.FetchError, match="404"):
        fetcher.fetch_html("https://example.com/", session=session)


def test_timeout_maps_to_504():
    session = StubSession([requests.Timeout("slow")])
    with pytest.raises(fetcher.FetchError) as exc:
        fetcher.fetch_html("https://example.com/", session=session)
    assert exc.value.status_code == 504


def test_connection_error_maps_to_502():
    session = StubSession([requests.ConnectionError("refused")])
    with pytest.raises(fetcher.FetchError) as exc:
        fetcher.fetch_html("https://example.com/", session=session)
    assert exc.value.status_code == 502


def test_response_is_closed_on_every_path():
    ok = StubResponse(body=b"<h1>x</h1>")
    fetcher.fetch_html("https://example.com/", session=StubSession([ok]))
    assert ok.closed

    too_big = StubResponse(body=b"x" * 100)
    with pytest.raises(fetcher.FetchError):
        fetcher.fetch_html("https://example.com/", session=StubSession([too_big]), max_bytes=5)
    assert too_big.closed

    redirected = StubResponse(status_code=302, headers={"location": "/b"})
    fetcher.fetch_html("https://example.com/", session=StubSession([redirected, StubResponse(body=b"ok")]))
    assert redirected.closed


def test_decodes_charset_from_content_type():
    body = "<h1>café</h1>".encode("latin-1")
    response = StubResponse(headers={"content-type": "text/html; charset=iso-8859-1"}, body=body)
    response.encoding = None
    response.apparent_encoding = "iso-8859-1"
    html, _ = fetcher.fetch_html("https://example.com/", session=StubSession([response]))
    assert "café" in html


def test_undeclared_charset_is_detected_not_assumed_iso8859():
    """requests labels an undeclared text/html body ISO-8859-1; that would mangle UTF-8."""
    response = StubResponse(
        headers={"content-type": "text/html"},
        body="<h1>café — dash</h1>".encode("utf-8"),
    )
    response.encoding = "ISO-8859-1"          # what requests really sets
    response.apparent_encoding = "utf-8"      # what detection finds
    html, _ = fetcher.fetch_html("https://example.com/", session=StubSession([response]))
    assert "café — dash" in html
    assert "\ufffd" not in html and "â" not in html
