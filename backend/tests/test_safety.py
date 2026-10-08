"""SSRF and URL validation tests."""

import socket

import pytest

import safety


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://127.0.0.1:8000/admin",
        "http://localhost/",
        "http://LOCALHOST/",
        "http://localhost.localdomain/",
        "http://[::1]/",
        "http://0.0.0.0/",
        "http://10.0.0.5/",
        "http://172.16.0.1/",
        "http://192.168.1.1/",
        "http://169.254.169.254/latest/meta-data/",  # AWS/GCP metadata
        "http://metadata.google.internal/",
        "http://100.64.0.1/",       # CGNAT
        "http://198.18.0.1/",       # benchmarking
        "http://[::ffff:127.0.0.1]/",
        "http://[fc00::1]/",
        "http://[fe80::1]/",
        "http://2130706433/",        # decimal-encoded 127.0.0.1
    ],
)
def test_rejects_non_public_targets(url, monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _fake_resolver(url))
    with pytest.raises(safety.UnsafeUrlError):
        safety.validate_url(url)


def _fake_resolver(target_url):
    """Resolve any hostname to a public IP unless the URL already holds one."""
    def resolve(host, port, *args, **kwargs):
        import ipaddress

        try:
            ip = ipaddress.ip_address(host.strip("[]"))
            # Let validation reject literal private addresses through the
            # normal path instead of pretending they resolved publicly.
            return [(socket.AF_INET if ip.version == 4 else socket.AF_INET6, socket.SOCK_STREAM, 6, "", (str(ip), port or 80))]
        except ValueError:
            pass
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port or 80))]

    return resolve


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "gopher://127.0.0.1:11211/",
        "ftp://example.com/",
        "javascript:alert(1)",
        "data:text/html,<h1>hi</h1>",
        "//example.com/no-scheme",
        "http://user:pass@example.com/",
        "https://example.com:notallowed/",
    ],
)
def test_rejects_unsupported_or_malformed_urls(url):
    with pytest.raises(safety.UnsafeUrlError):
        safety.validate_url(url, resolve=False)


def test_rejects_empty_and_oversized_urls():
    with pytest.raises(safety.UnsafeUrlError):
        safety.validate_url("")
    with pytest.raises(safety.UnsafeUrlError):
        safety.validate_url("https://example.com/" + "a" * 3000)


def test_normalises_valid_url():
    assert safety.validate_url("https://Example.COM:443/Path?x=1#frag") == "https://example.com/Path?x=1"
    assert safety.validate_url("http://example.com:8080/a") == "http://example.com:8080/a"


def test_allows_public_dns_answer(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))],
    )
    assert safety.validate_url("https://example.com/page").startswith("https://example.com")


def test_blocks_when_any_answer_is_private(monkeypatch):
    """A DNS answer mixing public and private IPs must be refused."""
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **k: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 80)),
        ],
    )
    with pytest.raises(safety.UnsafeUrlError, match="non-public"):
        safety.validate_url("https://rebind.example.com/")


def test_unresolvable_host_is_rejected(monkeypatch):
    def fail(*args, **kwargs):
        raise socket.gaierror("nope")

    monkeypatch.setattr(socket, "getaddrinfo", fail)
    with pytest.raises(safety.UnsafeUrlError, match="could not resolve"):
        safety.validate_url("https://does-not-exist.invalid/")


def test_trailing_dot_localhost_is_blocked():
    with pytest.raises(safety.UnsafeUrlError):
        safety.validate_url("http://localhost./", resolve=False)