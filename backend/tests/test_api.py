"""API tests via FastAPI's TestClient, with fetcher and safety stubbed."""

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import main  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

HTML = """
<html lang="en"><head><title>Stub Page</title>
<meta name="description" content="Fixture page">
</head><body><h1>Hello</h1><a href="/next">Next</a>
<img src="/a.png" alt="A"><table><tr><th>A</th><th>B</th></tr><tr><td>1</td><td>2</td></tr></table>
</body></html>
"""

EXPECTED = {
    "url": "https://example.com/",
    "title": "Stub Page",
    "description": "Fixture page",
    "headings": [{"level": "h1", "text": "Hello"}],
    "links": [{"text": "Next", "url": "https://example.com/next"}],
    "images": [{"alt": "A", "url": "https://example.com/a.png", "width": None, "height": None}],
    "tables": [{"headers": ["A", "B"], "rows": [["1", "2"]]}],
}


@pytest.fixture
def client(monkeypatch):
    main.cache.clear()
    main.limiter = main.TokenBucketLimiter(50, 60)
    monkeypatch.setattr(main, "limiter", main.limiter)
    monkeypatch.setattr(main.fetcher, "fetch_html", lambda url, **kw: (HTML, "https://example.com/"))
    monkeypatch.setattr(main.safety, "validate_url", lambda url, **kw: url if "example.com" in url else (_ for _ in ()).throw(main.safety.UnsafeUrlError("bad url")))
    with TestClient(main.app) as test_client:
        yield test_client


def test_health(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert 80 in body["allowedPorts"]


def test_extract_returns_sections(client):
    response = client.get("/api/extract", params={"url": "https://example.com/"})
    assert response.status_code == 200
    body = response.json()
    for key, value in EXPECTED.items():
        assert body[key] == value
    assert body["summary"]["headings"] == 1


def test_extract_caches_repeat_requests(client, monkeypatch):
    calls = []

    def counting(url, **kwargs):
        calls.append(url)
        return HTML, "https://example.com/"

    monkeypatch.setattr(main.fetcher, "fetch_html", counting)
    client.get("/api/extract", params={"url": "https://example.com/"})
    response = client.get("/api/extract", params={"url": "https://example.com/"})
    assert len(calls) == 1
    assert response.headers["X-Cache"] == "HIT"


def test_extract_rejects_unsafe_url(client):
    response = client.get("/api/extract", params={"url": "http://127.0.0.1/"})
    assert response.status_code == 400
    assert "detail" in response.json()


def test_extract_requires_url_param(client):
    assert client.get("/api/extract").status_code == 422


def test_rate_limit_returns_429(client, monkeypatch):
    monkeypatch.setattr(main.limiter, "capacity", 1.0)
    monkeypatch.setattr(main.limiter, "window", 600.0)
    monkeypatch.setattr(main.limiter, "_tokens", {})
    assert client.get("/api/extract", params={"url": "https://example.com/"}).status_code == 200
    blocked = client.get("/api/extract", params={"url": "https://example.com/"})
    assert blocked.status_code == 429
    assert "Retry-After" in blocked.headers


def test_ratelimit_header_present(client):
    assert "X-RateLimit-Remaining" in client.get("/api/extract", params={"url": "https://example.com/"}).headers


def test_fetch_error_is_reported(client, monkeypatch):
    def fail(url, **kwargs):
        raise main.fetcher.FetchError("The website took too long to respond.", 504)

    monkeypatch.setattr(main.fetcher, "fetch_html", fail)
    response = client.get("/api/extract", params={"url": "https://example.com/"})
    assert response.status_code == 504
    assert "too long" in response.json()["detail"]


def test_static_frontend_is_served(client):
    assert client.get("/").status_code == 200
    assert "Scrapely" in client.get("/").text


def test_real_validation_runs_for_unsafe_hosts(client, monkeypatch):
    """The endpoint must not trust the frontend to vet URLs."""
    monkeypatch.undo()
    response = client.get("/api/extract", params={"url": "http://169.254.169.254/"})
    assert response.status_code == 400


def test_cache_expires(client, monkeypatch):
    monkeypatch.setattr(main.cache, "ttl", 0.05)
    client.get("/api/extract", params={"url": "https://example.com/"})
    time.sleep(0.1)
    main.cache._entries.clear()  # force a miss by clearing stored entries
    assert main.cache.get("https://example.com/") is None