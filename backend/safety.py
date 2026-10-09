"""URL validation and SSRF protection.

The extractor fetches URLs supplied by anonymous users, which makes it a
server-side request forgery primitive unless the destination is checked. This
module is the single place that decides whether a URL may be fetched.

Checks performed per request and per redirect hop:
  * scheme must be http/https (blocks file://, gopher://, ftp://, ...)
  * hostname must resolve to at least one public IP
  * every resolved address must be outside the private/reserved ranges
  * no embedded credentials
  * port must be allowed
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse, urlunparse

MAX_URL_LENGTH = 2048
DEFAULT_ALLOWED_PORTS = frozenset({80, 443, 8080, 8443})

# Networks that must never be reachable through a user-supplied URL.
BLOCKED_NETWORKS = tuple(
    ipaddress.ip_network(net)
    for net in (
        "0.0.0.0/8",          # "this" network
        "10.0.0.0/8",         # RFC1918 private
        "100.64.0.0/10",      # CGNAT
        "127.0.0.0/8",        # loopback
        "169.254.0.0/16",     # link-local, incl. cloud metadata 169.254.169.254
        "172.16.0.0/12",      # RFC1918 private
        "192.0.0.0/24",       # IETF protocol assignments
        "192.0.2.0/24",       # TEST-NET-1
        "192.88.99.0/24",     # 6to4 relay anycast
        "192.168.0.0/16",     # RFC1918 private
        "198.18.0.0/15",      # benchmarking
        "198.51.100.0/24",    # TEST-NET-2
        "203.0.113.0/24",     # TEST-NET-3
        "224.0.0.0/4",        # multicast
        "240.0.0.0/4",        # reserved, includes 255.255.255.255
        "::/128",             # unspecified
        "::1/128",            # loopback
        "64:ff9b::/96",       # NAT64
        "100::/64",           # discard-only
        "2001:db8::/32",      # documentation
        "fc00::/7",           # unique local
        "fe80::/10",          # link-local
        "ff00::/8",           # multicast
    )
)

# Hostnames that resolve nowhere useful and are common SSRF targets.
BLOCKED_HOSTNAMES = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "ip6-localhost",
        "ip6-loopback",
        "metadata",
        "metadata.google.internal",
        "instance-data",
    }
)


class UnsafeUrlError(ValueError):
    """Raised when a URL is malformed or points somewhere it may not go."""


def is_blocked_ip(ip: ipaddress._BaseAddress) -> str | None:
    """Return a human-readable reason if *ip* is not publicly routable."""
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        # ::ffff:10.0.0.1 must be judged as the IPv4 address it wraps.
        ip = ip.ipv4_mapped
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
        return f"address {ip} is in a private or reserved range"
    for network in BLOCKED_NETWORKS:
        if ip.version == network.version and ip in network:
            return f"address {ip} is in the reserved range {network}"
    return None


def _numeric_ip_literal(hostname: str) -> ipaddress._BaseAddress | None:
    """Decode obfuscated IP literals such as 2130706433, 0177.0.0.1 or 0x7f.1.

    These look like host names but the resolver turns them into loopback
    addresses, so they must be judged as addresses.
    """
    if not hostname or hostname[-1].isdigit() is False and "." not in hostname:
        return None
    if not any(char.isdigit() for char in hostname):
        return None
    try:
        packed = socket.inet_aton(hostname)          # decimal, octal and hex forms
    except OSError:
        return None
    return ipaddress.ip_address(packed)


def resolve_addresses(hostname: str, port: int) -> list[ipaddress._BaseAddress]:
    """Resolve *hostname* to every address it maps to.

    All addresses are returned rather than the first one, because a hostname
    may resolve to a mix of public and private records and any private answer
    is enough to make the request unsafe.
    """
    bare = hostname.strip("[]")
    try:
        return [ipaddress.ip_address(bare)]
    except ValueError:
        pass

    numeric = _numeric_ip_literal(bare)
    if numeric is not None:
        return [numeric]

    try:
        infos = socket.getaddrinfo(hostname, port or 80, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise UnsafeUrlError(f"could not resolve host '{hostname}'.") from exc

    addresses: list[ipaddress._BaseAddress] = []
    for info in infos:
        sockaddr = info[4]
        raw = sockaddr[0] if sockaddr else None
        if not raw:
            continue
        try:
            addresses.append(ipaddress.ip_address(raw))
        except ValueError:
            continue

    if not addresses:
        raise UnsafeUrlError(f"could not resolve host '{hostname}'.")
    return addresses


def validate_url(
    url: str,
    *,
    allowed_ports: frozenset[int] = DEFAULT_ALLOWED_PORTS,
    resolve: bool = True,
) -> str:
    """Validate *url* and return it normalised.

    Raises UnsafeUrlError with a message suitable for showing to the user.
    """
    if not isinstance(url, str) or not url.strip():
        raise UnsafeUrlError("Enter a URL to extract.")
    url = url.strip()

    if len(url) > MAX_URL_LENGTH:
        raise UnsafeUrlError(f"URL is too long (limit {MAX_URL_LENGTH} characters).")

    try:
        parsed = urlparse(url)
    except ValueError as exc:
        raise UnsafeUrlError("That URL could not be parsed.") from exc

    scheme = parsed.scheme.lower()
    if scheme not in ("http", "https"):
        raise UnsafeUrlError("Only http:// and https:// URLs are supported.")
    if not parsed.netloc:
        raise UnsafeUrlError("That URL is missing a host name.")

    if parsed.username or parsed.password:
        raise UnsafeUrlError("URLs with embedded credentials are not allowed.")

    try:
        hostname = parsed.hostname
    except ValueError as exc:  # malformed IPv6 literal, bad port, ...
        raise UnsafeUrlError("That URL has an invalid host.") from exc
    if not hostname:
        raise UnsafeUrlError("That URL is missing a host name.")

    hostname = hostname.rstrip(".").lower()
    if not hostname:
        raise UnsafeUrlError("That URL is missing a host name.")
    if hostname in BLOCKED_HOSTNAMES or hostname.endswith(".localhost"):
        raise UnsafeUrlError("Local and internal addresses cannot be extracted.")

    try:
        port = parsed.port or (443 if scheme == "https" else 80)
    except ValueError as exc:
        raise UnsafeUrlError("That URL has an invalid port.") from exc

    if port not in allowed_ports:
        allowed = ", ".join(str(p) for p in sorted(allowed_ports))
        raise UnsafeUrlError(f"Port {port} is not allowed. Allowed ports: {allowed}.")

    if resolve:
        for ip in resolve_addresses(hostname, port):
            reason = is_blocked_ip(ip)
            if reason:
                raise UnsafeUrlError(f"Refusing to fetch a non-public target: {reason}.")

    host_for_url = hostname if ":" not in hostname else f"[{hostname}]"
    netloc = host_for_url if port in (80, 443) else f"{host_for_url}:{port}"
    return urlunparse((scheme, netloc, parsed.path, parsed.params, parsed.query, ""))
