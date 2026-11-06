import os
import socket
import tempfile

import pytest
from fastapi.testclient import TestClient

# app.main builds a module-level `app` on import; keep its database out of the repo.
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="netpulse-test-"))

from app.main import create_app  # noqa: E402


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Fail loudly if any test tries to open a real network connection."""

    def guard(*_args, **_kwargs):
        raise RuntimeError("Network access is not allowed in tests")

    monkeypatch.setattr(socket.socket, "connect", guard)
    monkeypatch.setattr(socket, "create_connection", guard)


@pytest.fixture
def app(tmp_path):
    """A fresh app per test: its own SQLite file and its own rate limiter."""
    return create_app(data_dir=str(tmp_path), rate_limit=1000)


@pytest.fixture
def client(app):
    with TestClient(app) as test_client:
        yield test_client
