from app import db


def make_result(i, ok=True):
    return {"url": f"https://site{i}.example", "status_code": 200 if ok else None,
            "response_ms": 12.5, "ok": ok, "error": None if ok else "Timed out"}


def test_init_creates_directory_and_table(tmp_path):
    path = db.init_db(tmp_path / "nested" / "dir")
    assert path.name == "netpulse.db"
    assert path.exists()
    assert db.recent_checks(path) == []


def test_init_is_idempotent(tmp_path):
    db.init_db(tmp_path)
    path = db.init_db(tmp_path)
    assert db.recent_checks(path) == []


def test_save_and_read_back(tmp_path):
    path = db.init_db(tmp_path)
    db.save_check(path, make_result(1, ok=False))
    rows = db.recent_checks(path)
    assert rows[0]["url"] == "https://site1.example"
    assert rows[0]["ok"] is False
    assert rows[0]["error"] == "Timed out"
    assert rows[0]["checked_at"].endswith("+00:00")


def test_recent_returns_last_20_newest_first(tmp_path):
    path = db.init_db(tmp_path)
    for i in range(25):
        db.save_check(path, make_result(i))
    rows = db.recent_checks(path)
    assert len(rows) == 20
    assert rows[0]["url"] == "https://site24.example"
    assert rows[-1]["url"] == "https://site5.example"


def test_sql_injection_is_stored_as_text(tmp_path):
    path = db.init_db(tmp_path)
    evil = make_result(0) | {"url": "https://x.example/'); DROP TABLE checks;--"}
    db.save_check(path, evil)
    assert db.recent_checks(path)[0]["url"] == evil["url"]


def test_api_checks_empty(client):
    response = client.get("/api/checks")
    assert response.status_code == 200
    assert response.json() == []
