from fastapi.testclient import TestClient

from app.main import create_app


def test_health_default_version(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": "dev"}


def test_health_uses_app_version_env(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_VERSION", "1.2.3")
    with TestClient(create_app(data_dir=str(tmp_path))) as c:
        assert c.get("/health").json()["version"] == "1.2.3"


def test_data_dir_env_is_used(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "custom"))
    create_app()
    assert (tmp_path / "custom" / "netpulse.db").exists()


def test_index_page_renders_with_csp(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "NetPulse" in response.text
    for tab in ("Subnet", "VLSM", "DNS", "Uptime"):
        assert tab in response.text
    assert "default-src 'self'" in response.headers["content-security-policy"]


def test_security_headers_on_every_response(client):
    response = client.get("/health")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"


def test_static_files_served(client):
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/style.css").status_code == 200


def test_validation_errors_are_flattened_to_a_string(client):
    response = client.post("/api/vlsm", json={"base": "10.0.0.0/24"})
    assert response.status_code == 422
    assert response.json()["detail"] == "hosts: Field required"


def test_rate_limit_returns_429_on_api(tmp_path):
    with TestClient(create_app(data_dir=str(tmp_path), rate_limit=3)) as c:
        codes = [c.get("/api/subnet?cidr=10.0.0.0/24").status_code for _ in range(4)]
        assert codes == [200, 200, 200, 429]
        blocked = c.get("/api/subnet?cidr=10.0.0.0/24")
        assert blocked.json()["detail"].startswith("Rate limit exceeded")
        assert int(blocked.headers["retry-after"]) >= 1
        # Non-API routes are never rate limited.
        assert c.get("/health").status_code == 200
