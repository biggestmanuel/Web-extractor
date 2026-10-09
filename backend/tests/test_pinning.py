"""Tests for connection pinning, which closes the DNS rebinding window.

Without pinning, DNS is resolved during validation and again when the HTTP
client connects. A host with a short TTL can answer with a public address the
first time and a private address the second. These tests assert that the socket
target is always the address that was validated.

No network calls: only the adapter's pool construction is exercised.
"""

import ipaddress

import pytest
import requests

import fetcher
import safety

from test_fetcher import StubResponse, StubSession

PUBLIC = ipaddress.ip_address("93.184.216.34")
PRIVATE = ipaddress.ip_address("127.0.0.1")


@pytest.fixture
def resolver(monkeypatch):
    """Replace DNS with a scripted list of answers, and record every lookup."""

    def install(*answers):
        calls = []
        queue = list(answers)

        def fake(hostname, port):
            value = queue.pop(0) if len(queue) > 1 else queue[0]
            calls.append(value)
            return [value]

        monkeypatch.setattr(safety, "resolve_addresses", fake)
        return calls

    return install


def pool_for(url, monkeypatched):
    """Build the pool the adapter would use for *url*."""
    adapter = fetcher.PinnedAdapter()
    request = requests.Request("GET", url).prepare()
    conn = adapter.get_connection_with_tls_context(request, True, None, None)
    return conn, request


def test_pins_to_the_validated_address(resolver):
    resolver(PUBLIC)
    conn, _ = pool_for("https://example.com/x", None)
    assert conn.conn_kw["pinned_address"] == "93.184.216.34"


def test_ignores_a_later_private_dns_answer(resolver):
    """The rebinding answer is never consulted for the socket."""
    calls = resolver(PUBLIC, PRIVATE)
    conn, _ = pool_for("https://example.com/x", None)
    assert conn.conn_kw["pinned_address"] == "93.184.216.34"
    assert all(str(ip) == "93.184.216.34" for ip in calls)


def test_socket_dials_the_pinned_address_but_sni_is_the_hostname(resolver):
    resolver(PUBLIC)
    conn, _ = pool_for("https://example.com/x", None)

    connection = conn.ConnectionCls(
        host="example.com",
        pinned_address=conn.conn_kw["pinned_address"],
        tls_hostname=conn.conn_kw["tls_hostname"],
    )
    # _dns_host is what the socket dials; server_hostname is what TLS presents.
    assert str(connection._dns_host) == "93.184.216.34"
    assert connection.server_hostname == "example.com"


def test_host_header_is_the_hostname_not_the_ip(resolver):
    """urllib3 derives Host from the socket address, so it must be set explicitly."""
    resolver(PUBLIC)
    _, request = pool_for("https://example.com/x", None)
    assert request.headers["Host"] == "example.com"


def test_port_is_preserved_in_the_host_header(resolver):
    resolver(PUBLIC)
    _, request = pool_for("http://example.com:8080/x", None)
    assert request.headers["Host"] == "example.com:8080"


def test_https_uses_the_pinned_https_connection(resolver):
    resolver(PUBLIC)
    conn, _ = pool_for("https://example.com/x", None)
    assert conn.ConnectionCls is fetcher._PinnedHTTPSConnection


def test_http_uses_the_pinned_plain_connection_without_a_tls_name(resolver):
    resolver(PUBLIC)
    conn, _ = pool_for("http://example.com/x", None)
    assert conn.ConnectionCls is fetcher._PinnedHTTPConnection
    assert "tls_hostname" not in conn.conn_kw


def test_private_address_is_refused_when_the_pool_is_built(resolver):
    """Belt and braces: the adapter refuses even if validation were skipped."""
    resolver(PRIVATE)
    with pytest.raises(safety.UnsafeUrlError, match="non-public"):
        pool_for("https://evil.test/", None)


def test_proxy_requests_are_passed_through_untouched(monkeypatch):
    """A proxy resolves names itself, so pinning would not apply to it."""

    def boom(*args, **kwargs):
        raise AssertionError("DNS must not be resolved for a proxied request")

    monkeypatch.setattr(safety, "resolve_addresses", boom)
    adapter = fetcher.PinnedAdapter()
    request = requests.Request("GET", "https://example.com/x").prepare()
    conn = adapter.get_connection_with_tls_context(
        request, True, {"https": "http://proxy.internal:3128"}, None
    )
    assert "pinned_address" not in (conn.conn_kw or {})


def test_connection_without_a_pin_keeps_its_own_hostname():
    """The mixin must not alter a connection that was not pinned."""
    connection = fetcher._PinnedHTTPSConnection(host="example.com")
    assert str(connection._dns_host) == "example.com"


def test_build_session_mounts_the_adapter_on_both_schemes():
    session = fetcher.build_session()
    try:
        assert isinstance(session.get_adapter("https://example.com"), fetcher.PinnedAdapter)
        assert isinstance(session.get_adapter("http://example.com"), fetcher.PinnedAdapter)
    finally:
        session.close()


def test_fetch_closes_the_session_it_created(monkeypatch):
    """A session built per request must not leak sockets."""
    created = []

    class RecordingSession:
        def __init__(self):
            self.closed = False
            created.append(self)

        def get(self, *args, **kwargs):
            return StubResponse(body=b"<h1>ok</h1>", url="https://example.com/")

        def close(self):
            self.closed = True

    monkeypatch.setattr(fetcher, "build_session", RecordingSession)
    fetcher.fetch_html("https://example.com/")
    assert len(created) == 1 and created[0].closed is True


def test_fetch_leaves_a_caller_supplied_session_open(monkeypatch):
    session = StubSession([StubResponse(body=b"<h1>ok</h1>")])
    monkeypatch.setattr(
        fetcher, "build_session", lambda: pytest.fail("must not build a session when one is given")
    )
    fetcher.fetch_html("https://example.com/", session=session)
    assert not hasattr(session, "closed")


def test_rebinding_at_connect_time_is_reported_as_a_rejected_url(monkeypatch):
    """The adapter's refusal must reach the caller as a 400, not a 500."""
    answers = [[PUBLIC], [PRIVATE]]
    monkeypatch.setattr(safety, "resolve_addresses", lambda hostname, port: answers.pop(0))

    with pytest.raises(fetcher.FetchError) as excinfo:
        fetcher.fetch_html("https://rebind.test/")

    assert excinfo.value.status_code == 400
    assert "non-public" in str(excinfo.value)