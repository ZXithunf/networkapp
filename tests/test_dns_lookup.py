import dns.exception
import dns.rdata
import dns.rdataclass
import dns.rdatatype
import dns.resolver
import pytest

from app import dns_lookup


class FakeAnswer(list):
    """Mimics dns.resolver.Answer: iterable rdata plus an rrset with a TTL."""

    def __init__(self, rtype, values, ttl=300):
        super().__init__(
            dns.rdata.from_text(dns.rdataclass.IN, dns.rdatatype.from_text(rtype), v)
            for v in values
        )
        self.rrset = type("RRset", (), {"ttl": ttl})()


class FakeResolver:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def resolve(self, name, rtype):
        self.calls.append((name, rtype))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@pytest.fixture
def fake_dns(monkeypatch):
    def install(result):
        resolver = FakeResolver(result)
        monkeypatch.setattr(dns_lookup, "_resolver", lambda: resolver)
        return resolver

    return install


@pytest.mark.parametrize(
    ("rtype", "raw", "expected"),
    [
        ("A", ["93.184.215.14"], ["93.184.215.14"]),
        ("AAAA", ["2606:2800:21f:cb07::1"], ["2606:2800:21f:cb07::1"]),
        ("MX", ["10 mail.example.com.", "5 mx.example.com."],
         ["10 mail.example.com", "5 mx.example.com"]),
        ("TXT", ['"v=spf1 -all"'], ["v=spf1 -all"]),
        ("NS", ["a.iana-servers.net."], ["a.iana-servers.net"]),
        ("CNAME", ["target.example.net."], ["target.example.net"]),
    ],
)
def test_api_dns_record_types(client, fake_dns, rtype, raw, expected):
    resolver = fake_dns(FakeAnswer(rtype, raw))
    response = client.get("/api/dns", params={"domain": "Example.COM.", "type": rtype.lower()})
    assert response.status_code == 200
    assert response.json() == {"domain": "example.com", "type": rtype, "ttl": 300,
                               "records": expected}
    assert resolver.calls == [("example.com", rtype)]


def test_default_type_is_a(client, fake_dns):
    fake_dns(FakeAnswer("A", ["1.2.3.4"]))
    assert client.get("/api/dns", params={"domain": "example.com"}).json()["type"] == "A"


def test_no_answer_returns_empty_list(client, fake_dns):
    fake_dns(dns.resolver.NoAnswer())
    response = client.get("/api/dns", params={"domain": "example.com", "type": "MX"})
    assert response.status_code == 200
    assert response.json()["records"] == []


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (dns.resolver.NXDOMAIN(), 404),
        (dns.exception.Timeout(), 504),
        (dns.resolver.NoNameservers(), 502),
    ],
)
def test_resolver_errors_map_to_status(client, fake_dns, error, status):
    fake_dns(error)
    response = client.get("/api/dns", params={"domain": "example.com"})
    assert response.status_code == status
    assert response.json()["detail"]


@pytest.mark.parametrize(
    "domain", ["", "localhost", "-bad.com", "bad-.com", "a..com", "exa mple.com",
               "http://example.com", "example.123", ("a" * 64) + ".com"],
)
def test_invalid_domain_is_422(client, fake_dns, domain):
    resolver = fake_dns(FakeAnswer("A", ["1.2.3.4"]))
    response = client.get("/api/dns", params={"domain": domain})
    assert response.status_code == 422
    assert resolver.calls == []  # never reaches the resolver


def test_underscore_labels_allowed():
    assert dns_lookup.validate_domain("_dmarc.example.com") == "_dmarc.example.com"


def test_invalid_type_is_422(client):
    response = client.get("/api/dns", params={"domain": "example.com", "type": "SOA"})
    assert response.status_code == 422
    assert "type must be one of" in response.json()["detail"]


def test_real_resolver_has_3_second_timeout():
    resolver = dns_lookup._resolver()
    assert resolver.lifetime == 3.0
    assert resolver.timeout == 3.0
