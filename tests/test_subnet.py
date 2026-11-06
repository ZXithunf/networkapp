import pytest

from app.subnet import calculate


def test_api_subnet_happy_path(client):
    response = client.get("/api/subnet", params={"cidr": "192.168.10.0/26"})
    assert response.status_code == 200
    data = response.json()
    assert data["network"] == "192.168.10.0"
    assert data["broadcast"] == "192.168.10.63"
    assert data["netmask"] == "255.255.255.192"
    assert data["wildcard"] == "0.0.0.63"
    assert data["prefix"] == 26
    assert data["first_host"] == "192.168.10.1"
    assert data["last_host"] == "192.168.10.62"
    assert data["usable_hosts"] == 62


def test_host_bits_are_normalised():
    assert calculate("192.168.10.77/26")["network"] == "192.168.10.64"


def test_slash_31_point_to_point():
    data = calculate("10.0.0.0/31")
    assert data["usable_hosts"] == 2
    assert data["first_host"] == "10.0.0.0"
    assert data["last_host"] == "10.0.0.1"
    assert data["broadcast"] is None


def test_slash_32_single_host():
    data = calculate("10.1.2.3/32")
    assert data["usable_hosts"] == 1
    assert data["first_host"] == data["last_host"] == "10.1.2.3"
    assert data["broadcast"] is None
    assert data["netmask"] == "255.255.255.255"


def test_slash_0():
    assert calculate("0.0.0.0/0")["usable_hosts"] == 2**32 - 2


def test_ipv6_bonus():
    data = calculate("2001:db8::/126")
    assert data["version"] == 6
    assert data["broadcast"] is None
    assert data["first_host"] == "2001:db8::"
    assert data["last_host"] == "2001:db8::3"
    assert data["usable_hosts"] == 4


@pytest.mark.parametrize(
    "cidr", ["", "192.168.1.0", "192.168.1.0/33", "300.1.1.1/24", "abc/24", "10.0.0.0/-1"]
)
def test_api_subnet_bad_input_is_422(client, cidr):
    response = client.get("/api/subnet", params={"cidr": cidr})
    assert response.status_code == 422
    assert response.json()["detail"]


def test_api_subnet_missing_param_is_422(client):
    response = client.get("/api/subnet")
    assert response.status_code == 422
    assert "cidr" in response.json()["detail"]
