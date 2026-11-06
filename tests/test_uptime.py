import ipaddress
import socket

import httpx
import pytest

from app import uptime

# Fake DNS for the uptime checker: hostname -> addresses.
FAKE_DNS = {
    "example.com": ["93.184.215.14"],
    "other.example": ["8.8.8.8"],
    "localhost": ["127.0.0.1", "::1"],
    "metadata.evil.test": ["169.254.169.254"],
    "mixed.evil.test": ["93.184.215.14", "10.0.0.5"],
}


def fake_resolve(host, _port):
    try:
        ipaddress.ip_address(host)
        return [host]  # IP literals resolve to themselves
    except ValueError:
        pass
    if host not in FAKE_DNS:
        raise socket.gaierror("Name or service not known")
    return FAKE_DNS[host]


@pytest.fixture
def fake_net(monkeypatch):
    """Install fake DNS and a mock HTTP transport. Returns the list of seen requests."""
    seen = []
    routes = {}

    def handler(request: httpx.Request):
        seen.append(request)
        key = (request.headers["host"], request.url.path)
        action = routes.get(key, httpx.Response(200))
        if isinstance(action, Exception):
            raise action
        return action

    monkeypatch.setattr(uptime, "_resolve", fake_resolve)
    monkeypatch.setattr(
        uptime,
        "_client",
        lambda: httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False),
    )
    return seen, routes


# ---------- happy paths ----------


def test_check_ok_and_saved(client, fake_net):
    seen, _ = fake_net
    response = client.post("/api/check", json={"url": "https://example.com"})
    assert response.status_code == 200
    data = response.json()
    assert data["status_code"] == 200
    assert data["ok"] is True
    assert isinstance(data["response_ms"], float)

    # Connected to the validated IP, but kept the real hostname for Host + SNI.
    assert seen[0].url.host == "93.184.215.14"
    assert seen[0].headers["host"] == "example.com"
    assert seen[0].extensions["sni_hostname"] == "example.com"

    history = client.get("/api/checks").json()
    assert len(history) == 1
    assert history[0]["url"] == "https://example.com"
    assert history[0]["ok"] is True


def test_server_error_is_not_ok(client, fake_net):
    _, routes = fake_net
    routes[("example.com", "/down")] = httpx.Response(503)
    data = client.post("/api/check", json={"url": "http://example.com/down"}).json()
    assert data["status_code"] == 503
    assert data["ok"] is False


def test_follows_redirects(client, fake_net):
    seen, routes = fake_net
    routes[("example.com", "/a")] = httpx.Response(301, headers={"location": "/b"})
    routes[("example.com", "/b")] = httpx.Response(
        302, headers={"location": "https://other.example/c"}
    )
    data = client.post("/api/check", json={"url": "https://example.com/a"}).json()
    assert data["ok"] is True
    assert data["status_code"] == 200
    assert [r.headers["host"] for r in seen] == ["example.com", "example.com", "other.example"]


def test_non_default_port_kept_in_host_header(client, fake_net):
    seen, _ = fake_net
    client.post("/api/check", json={"url": "http://example.com:8080/"})
    assert seen[0].headers["host"] == "example.com:8080"
    assert seen[0].url.port == 8080


def test_too_many_redirects(client, fake_net):
    seen, routes = fake_net
    routes[("example.com", "/loop")] = httpx.Response(302, headers={"location": "/loop"})
    data = client.post("/api/check", json={"url": "https://example.com/loop"}).json()
    assert data["ok"] is False
    assert "Too many redirects" in data["error"]
    assert len(seen) == 4  # first request + 3 redirects


def test_timeout_is_recorded_as_down(client, fake_net):
    _, routes = fake_net
    routes[("example.com", "/slow")] = httpx.ReadTimeout("slow")
    data = client.post("/api/check", json={"url": "https://example.com/slow"}).json()
    assert data["ok"] is False
    assert data["status_code"] is None
    assert "Timed out" in data["error"]
    assert client.get("/api/checks").json()[0]["ok"] is False


def test_connection_error_is_recorded_as_down(client, fake_net):
    _, routes = fake_net
    routes[("example.com", "/")] = httpx.ConnectError("refused")
    data = client.post("/api/check", json={"url": "https://example.com/"}).json()
    assert data["ok"] is False
    assert data["error"] == "Request failed: ConnectError"


def test_redirect_to_bad_scheme(client, fake_net):
    _, routes = fake_net
    routes[("example.com", "/")] = httpx.Response(302, headers={"location": "ftp://example.com/"})
    data = client.post("/api/check", json={"url": "https://example.com/"}).json()
    assert data["ok"] is False
    assert data["error"].startswith("Bad redirect")


# ---------- SSRF protection ----------


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://10.0.0.5/",
        "http://169.254.169.254/latest/meta-data/",
        "http://localhost:8000/health",
        "http://metadata.evil.test/",  # public-looking name pointing at metadata IP
        "http://mixed.evil.test/",  # one public + one private address
        "http://[::1]/",
        "http://[::ffff:127.0.0.1]/",
        "http://0.0.0.0/",
        "http://224.0.0.1/",
        "http://100.64.0.1/",
    ],
)
def test_ssrf_targets_blocked_with_400(client, fake_net, url):
    seen, _ = fake_net
    response = client.post("/api/check", json={"url": url})
    assert response.status_code == 400
    assert "non-public" in response.json()["detail"]
    assert seen == []  # no request was ever sent
    assert client.get("/api/checks").json() == []  # blocked attempts aren't stored


def test_redirect_to_metadata_service_is_blocked(client, fake_net):
    seen, routes = fake_net
    routes[("example.com", "/")] = httpx.Response(
        302, headers={"location": "http://169.254.169.254/latest/meta-data/"}
    )
    response = client.post("/api/check", json={"url": "https://example.com/"})
    assert response.status_code == 400
    assert "169.254.169.254" in response.json()["detail"]
    assert len(seen) == 1  # only the first, public hop was requested


def test_unresolvable_host_is_400(client, fake_net):
    response = client.post("/api/check", json={"url": "https://no-such-host.example/"})
    assert response.status_code == 400
    assert "Could not resolve" in response.json()["detail"]


def test_empty_resolution_is_blocked(monkeypatch):
    monkeypatch.setattr(uptime, "_resolve", lambda host, port: [])
    with pytest.raises(uptime.BlockedTargetError):
        uptime.resolve_public_ip(httpx.URL("https://example.com"))


# ---------- input validation ----------


@pytest.mark.parametrize(
    "url", ["ftp://example.com", "file:///etc/passwd", "javascript:alert(1)", "example.com",
            "", "http://"],
)
def test_bad_urls_are_422(client, fake_net, url):
    response = client.post("/api/check", json={"url": url})
    assert response.status_code == 422


def test_invalid_url_syntax():
    with pytest.raises(uptime.InvalidUrlError):
        uptime.parse_url("http://example.com:abc/")


# ---------- helpers ----------


def test_real_resolver_handles_ip_literal_without_network():
    assert uptime._resolve("127.0.0.1", 80) == ["127.0.0.1"]


def test_real_client_settings():
    with uptime._client() as c:
        assert c.timeout.read == 5.0
        assert c.follow_redirects is False


@pytest.mark.parametrize(
    ("address", "public"),
    [("8.8.8.8", True), ("2606:4700::1111", True), ("192.168.1.1", False),
     ("172.16.0.1", False), ("fe80::1", False), ("240.0.0.1", False)],
)
def test_is_public_ip(address, public):
    assert uptime.is_public_ip(ipaddress.ip_address(address)) is public
