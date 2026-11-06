import pytest

from app.vlsm import VlsmError, plan, prefix_for_hosts


@pytest.mark.parametrize(
    ("hosts", "prefix"), [(1, 30), (2, 30), (3, 29), (6, 29), (50, 26), (62, 26), (63, 25)]
)
def test_prefix_for_hosts(hosts, prefix):
    assert prefix_for_hosts(hosts) == prefix


def test_api_vlsm_happy_path(client):
    response = client.post("/api/vlsm", json={"base": "10.0.0.0/24", "hosts": [50, 20, 10, 2]})
    assert response.status_code == 200
    data = response.json()
    assert [a["cidr"] for a in data["allocations"]] == [
        "10.0.0.0/26",
        "10.0.0.64/27",
        "10.0.0.96/28",
        "10.0.0.112/30",
    ]
    assert data["allocations"][0]["wasted"] == 12
    assert data["leftover"] == ["10.0.0.116/30", "10.0.0.120/29", "10.0.0.128/25"]
    assert data["leftover_addresses"] == 140


def test_largest_first_regardless_of_input_order():
    data = plan("10.0.0.0/24", [2, 50])
    assert [a["hosts_requested"] for a in data["allocations"]] == [50, 2]
    assert data["allocations"][0]["name"] == "Subnet 2"  # keeps the original position


def test_exact_fit_has_no_leftover():
    data = plan("10.0.0.0/24", [126, 126])
    assert data["leftover"] == []
    assert data["leftover_addresses"] == 0


def test_api_vlsm_overflow_is_422(client):
    response = client.post("/api/vlsm", json={"base": "10.0.0.0/24", "hosts": [100, 100, 100]})
    assert response.status_code == 422
    assert "Not enough space" in response.json()["detail"]


def test_single_request_bigger_than_base():
    with pytest.raises(VlsmError, match="larger than"):
        plan("10.0.0.0/28", [100])


@pytest.mark.parametrize(
    ("base", "hosts", "message"),
    [
        ("not-a-network", [10], "Invalid base"),
        ("2001:db8::/64", [10], "IPv4 only"),
        ("10.0.0.0/24", [], "at least one"),
        ("10.0.0.0/24", [0], "at least 1"),
        ("10.0.0.0/8", [1] * 65, "At most"),
    ],
)
def test_vlsm_validation(base, hosts, message):
    with pytest.raises(VlsmError, match=message):
        plan(base, hosts)


def test_api_vlsm_wrong_types_is_422(client):
    response = client.post("/api/vlsm", json={"base": "10.0.0.0/24", "hosts": ["lots"]})
    assert response.status_code == 422
