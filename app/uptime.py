"""Uptime checker with SSRF protection.

Flow for every hop (the first request and each redirect):
  1. Validate scheme and hostname.
  2. Resolve the hostname ourselves and reject any non-public address.
  3. Connect to the exact IP we validated ("pinning"), sending the original
     Host header and TLS SNI. This stops DNS-rebinding tricks where a second
     lookup by the HTTP client would return a different, internal address.
"""

import ipaddress
import socket
import time
from urllib.parse import urljoin

import httpx

TIMEOUT_SECONDS = 5.0
MAX_REDIRECTS = 3
REDIRECT_CODES = {301, 302, 303, 307, 308}


class InvalidUrlError(ValueError):
    """The URL itself is malformed or uses a scheme we do not allow (422)."""


class BlockedTargetError(Exception):
    """The URL points at a private/internal address (400)."""


def is_public_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """True only for globally routable unicast addresses."""
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped  # ::ffff:127.0.0.1 must be treated as 127.0.0.1
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local  # includes 169.254.169.254 (EC2 metadata service)
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
        or not ip.is_global  # catches leftovers such as 100.64.0.0/10 (CGNAT)
    )


def _resolve(host: str, port: int) -> list[str]:
    """Resolve a hostname to IP strings. Tests replace this function."""
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return sorted({info[4][0] for info in infos})


def parse_url(url: str) -> httpx.URL:
    try:
        parsed = httpx.URL((url or "").strip())
    except httpx.InvalidURL as exc:
        raise InvalidUrlError(f"Invalid URL: {exc}") from exc
    if parsed.scheme not in ("http", "https"):
        raise InvalidUrlError("Only http:// and https:// URLs are allowed")
    if not parsed.host:
        raise InvalidUrlError("URL must include a hostname")
    return parsed


def resolve_public_ip(url: httpx.URL) -> str:
    """Resolve the URL's host; return one safe IP or raise BlockedTargetError.

    If ANY resolved address is internal we refuse, rather than picking the
    public one - a name that points inside the network is suspicious.
    """
    try:
        addresses = _resolve(url.host, url.port or (443 if url.scheme == "https" else 80))
    except (socket.gaierror, UnicodeError) as exc:
        raise BlockedTargetError(f"Could not resolve host '{url.host}'") from exc
    if not addresses:
        raise BlockedTargetError(f"Could not resolve host '{url.host}'")
    for address in addresses:
        if not is_public_ip(ipaddress.ip_address(address.split("%")[0])):
            raise BlockedTargetError(
                f"Target '{url.host}' resolves to a non-public address ({address})"
            )
    return addresses[0]


def _client() -> httpx.Client:
    """HTTP client used for checks. Tests replace this with a mock transport."""
    return httpx.Client(timeout=TIMEOUT_SECONDS, follow_redirects=False)


def _get_pinned(client: httpx.Client, url: httpx.URL, ip: str) -> httpx.Response:
    """GET `url` but connect to `ip`, keeping Host header and SNI as the hostname."""
    pinned = url.copy_with(host=ip)
    request = client.build_request(
        "GET",
        pinned,
        headers={"Host": url.netloc.decode("ascii"), "User-Agent": "NetPulse/1.0"},
        extensions={"sni_hostname": url.host},
    )
    response = client.send(request, stream=True)  # we only need the status line
    response.close()
    return response


def check(url: str) -> dict:
    """Check a URL. Network failures are a result (ok=False), not an exception."""
    current = parse_url(url)
    started = time.perf_counter()
    result = {"url": str(current), "status_code": None, "ok": False, "error": None}

    try:
        with _client() as client:
            redirects = 0
            while True:
                ip = resolve_public_ip(current)  # re-checked on every redirect
                response = _get_pinned(client, current, ip)
                result["status_code"] = response.status_code
                location = response.headers.get("location")
                if response.status_code not in REDIRECT_CODES or not location:
                    break
                if redirects >= MAX_REDIRECTS:
                    result["error"] = f"Too many redirects (more than {MAX_REDIRECTS})"
                    break
                redirects += 1
                current = parse_url(urljoin(str(current), location))
    except httpx.TimeoutException:
        result["error"] = f"Timed out after {TIMEOUT_SECONDS:g}s"
    except httpx.HTTPError as exc:
        result["error"] = f"Request failed: {exc.__class__.__name__}"
    except InvalidUrlError as exc:
        # A redirect to e.g. ftp:// or a malformed Location header.
        result["error"] = f"Bad redirect: {exc}"

    result["response_ms"] = round((time.perf_counter() - started) * 1000, 1)
    code = result["status_code"]
    result["ok"] = result["error"] is None and code is not None and 200 <= code < 400
    return result
