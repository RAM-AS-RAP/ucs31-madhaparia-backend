from fastapi.testclient import TestClient

import app.main as main_module
from app.main import app


client = TestClient(app)


def test_root():
    response = client.get("/")

    assert response.status_code == 200
    assert (
        response.json()["message"]
        == "Respiratory Health Awareness API is running."
    )


def test_health():
    response = client.get("/health")

    assert response.status_code == 200

    data = response.json()

    assert data["status"] == "ok"
    assert "service" in data
    assert "environment" in data


def test_model_status():
    response = client.get("/model/status")

    assert response.status_code == 200

    data = response.json()

    assert data["status"] == "loaded"
    assert data["model_type"] == "RandomForestClassifier"
    assert data["feature_count"] == 198
    assert data["model_feature_count"] == 198
    assert data["class_count"] == 21
    assert data["model_class_count"] == 21


def test_database_status_connected(monkeypatch):
    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(
            self,
            exc_type,
            exc_value,
            traceback,
        ):
            return False

        def execute(self, statement):
            return None

    class FakeEngine:
        def connect(self):
            return FakeConnection()

    monkeypatch.setattr(
        main_module,
        "engine",
        FakeEngine(),
    )

    response = client.get("/database/status")

    assert response.status_code == 200

    data = response.json()

    assert data["status"] == "connected"
    assert data["database"] == "postgresql"


def test_database_status_unavailable(monkeypatch):
    class FakeEngine:
        def connect(self):
            raise RuntimeError(
                "Database unavailable"
            )

    monkeypatch.setattr(
        main_module,
        "engine",
        FakeEngine(),
    )

    response = client.get("/database/status")

    assert response.status_code == 503
    assert (
        response.json()["detail"]
        == "Database connection unavailable."
    )

def test_cors_allows_frontend_origin():
    response = client.options(
        "/auth/register",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": (
                "content-type"
            ),
        },
    )

    assert response.status_code == 200

    assert (
        response.headers[
            "access-control-allow-origin"
        ]
        == "http://localhost:5173"
    )

def test_cors_does_not_allow_unknown_origin():
    response = client.options(
        "/auth/register",
        headers={
            "Origin": "https://example.com",
            "Access-Control-Request-Method": "POST",
        },
    )

    assert (
        "access-control-allow-origin"
        not in response.headers
    )