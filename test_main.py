import os
from dotenv import load_dotenv

load_dotenv()

from fastapi.testclient import TestClient
from main import app

client = TestClient(app)


def test_health():
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["database"] == "connected"

def test_version():
    response = client.get("/version")

    assert response.status_code == 200
    assert response.json()["version"] == "0.1.0"
    assert response.json()["stage"] == "MVP"

def test_config():
    response = client.get("/config")

    assert response.status_code == 200
    assert "hunter_configured" in response.json()
    assert "apollo_configured" in response.json()
    assert "pdl_configured" in response.json()

def test_requests_requires_api_key():
    response = client.get(
        "/requests",
        headers={"x-api-key": "invalid-test-key"}
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid or missing API key" 

def test_request_not_found():
    response = client.get(
        "/requests/00000000-0000-0000-0000-000000000000",
        headers={"x-api-key": "invalid-test-key"}
    )

    assert response.status_code == 401  

def test_requests_without_api_key():
    response = client.get("/requests")

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid or missing API key"

def test_stats_requires_api_key():
    response = client.get(
        "/stats",
        headers={"x-api-key": "invalid-test-key"}
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid or missing API key"

def test_enrich_rejects_impossible_constraints():
    response = client.post(
        "/enrich",
        headers={"x-api-key": os.getenv("ROUTER_API_KEY")},
        json={
            "domain": "stripe.com",
            "need": ["verified_email"],
            "max_cost": 0.01,
            "max_latency_ms": 100
        }
    )

    assert response.status_code == 400
    assert response.json()["status"] == "rejected"
    assert response.json()["reason"] == "no_valid_provider" 

def test_enrich_latency_fallback():
    response = client.post(
        "/enrich",
        headers={"x-api-key": os.getenv("ROUTER_API_KEY")},
        json={
            "domain": "stripe.com",
            "need": ["verified_email"],
            "max_cost": 1,
            "max_latency_ms": 1000
        }
    )

    assert response.status_code == 200
    data = response.json()
    assert data["provider"] == "apollo"
    assert data["fallback_used"] is True
    assert data["fallback_reason"] == "latency" 

def test_enrich_cost_fallback():
    response = client.post(
        "/enrich",
        headers={"x-api-key": os.getenv("ROUTER_API_KEY")},
        json={
            "domain": "stripe.com",
            "need": ["verified_email"],
            "max_cost": 0.04,
            "max_latency_ms": 5000
        }
    )

    assert response.status_code == 200
    data = response.json()
    assert data["provider"] == "apollo"
    assert data["fallback_used"] is True
    assert data["fallback_reason"] == "cost"                              