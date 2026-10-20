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

def test_enrich_latency_fallback(monkeypatch):
    import main

    monkeypatch.setattr(main, "APOLLO_API_KEY", "test-key")
    monkeypatch.setattr(
        main,
        "call_apollo",
        lambda domain: {
            "vp_sales": [
                {
                    "name": "Test VP Sales",
                    "title": "VP Sales"
                }
            ]
        }
    )

    response = client.post(
        "/enrich",
        headers={"x-api-key": os.getenv("ROUTER_API_KEY")},
        json={
            "domain": "stripe.com",
            "need": ["vp_sales"],
            "max_cost": 1,
            "max_latency_ms": 1000
        }
    )

    assert response.status_code == 200
    data = response.json()
    assert data["provider"] == "apollo"
    assert data["fallback_used"] is True
    assert data["fallback_reason"] == "latency" 

def test_enrich_cost_fallback(monkeypatch):
    import main

    monkeypatch.setattr(main, "APOLLO_API_KEY", "test-key")
    monkeypatch.setattr(
        main,
        "call_apollo",
        lambda domain: {
            "vp_sales": [
                {
                    "name": "Test VP Sales",
                    "title": "VP Sales"
                }
            ]
        }
    )
    response = client.post(
        "/enrich",
        headers={"x-api-key": os.getenv("ROUTER_API_KEY")},
        json={
            "domain": "stripe.com",
            "need": ["vp_sales"],
            "max_cost": 0.04,
            "max_latency_ms": 5000
        }
    )

    assert response.status_code == 200
    data = response.json()
    assert data["provider"] == "apollo"
    assert data["fallback_used"] is True
    assert data["fallback_reason"] == "cost"

from unittest.mock import patch

def test_hunter_outage():
    with patch(
    "main.call_hunter",
    side_effect=RuntimeError("Simulated Hunter outage")
    ), patch("main.get_mock_result") as mock_result:

        response = client.post(
            "/enrich",
            headers={"x-api-key": os.getenv("ROUTER_API_KEY")},
            json={
                "domain": "stripe.com",
                "need": ["verified_email"],
                "max_cost": 1,
                "max_latency_ms": 5000
            }
        )

    assert response.status_code == 502

    data = response.json()
    assert data["reason"] == "provider_unavailable"
    mock_result.assert_not_called()

def test_live_provider_detection(monkeypatch):
    import main

    monkeypatch.setattr(main, "HUNTER_API_KEY", "hunter-test-key")
    monkeypatch.setattr(main, "APOLLO_API_KEY", None)

    assert main.is_live_provider("hunter") is True
    assert main.is_live_provider("apollo") is False
    assert main.is_live_provider("pdl") is False

    monkeypatch.setattr(main, "APOLLO_API_KEY", "apollo-test-key")

    assert main.is_live_provider("apollo") is True

def test_live_fallback_provider():
    from main import get_live_fallback_provider

    assert get_live_fallback_provider(
        "hunter", ["verified_email"]
    ) is None

    assert get_live_fallback_provider(
        "apollo", ["verified_email"]
    ) is None

    assert get_live_fallback_provider(
        "apollo", ["employee_count"]
    ) is None

def test_call_apollo_requires_api_key(monkeypatch):
    import main

    monkeypatch.setattr(main, "APOLLO_API_KEY", None)

    try:
        main.call_apollo("example.com")
        assert False, "Expected ValueError"
    except ValueError as e:
        assert str(e) == "APOLLO_API_KEY is not configured"

def test_call_apollo_success(monkeypatch):
    import main

    monkeypatch.setattr(main, "APOLLO_API_KEY", "test-key")

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            pass

        def read(self):
            return b'{"people": [{"first_name": "Test", "last_name": "User", "name": "Test User", "title": "VP Sales"}]}'

    def fake_urlopen(request_obj, timeout=10):
        return FakeResponse()

    monkeypatch.setattr(
        main.urllib.request,
        "urlopen",
        fake_urlopen
    )

    result = main.call_apollo("example.com")

    assert result["vp_sales"][0]["name"] == "Test User"

def test_call_apollo_http_error(monkeypatch):
    import main
    import urllib.error

    monkeypatch.setattr(main, "APOLLO_API_KEY", "test-key")

    def fake_urlopen(request_obj, timeout=10):
        raise urllib.error.HTTPError(
            url="https://api.apollo.io/api/v1/people/match",
            code=500,
            msg="Server Error",
            hdrs=None,
            fp=None,
        )

    monkeypatch.setattr(main.urllib.request, "urlopen", fake_urlopen)

    try:
        main.call_apollo("example.com")
        assert False, "Expected RuntimeError"
    except RuntimeError as e:
        assert "Apollo API returned HTTP 500" in str(e)

def test_call_apollo_connection_error(monkeypatch):
    import main
    import urllib.error

    monkeypatch.setattr(main, "APOLLO_API_KEY", "test-key")

    def fake_urlopen(request_obj, timeout=10):
        raise urllib.error.URLError("Connection failed")

    monkeypatch.setattr(main.urllib.request, "urlopen", fake_urlopen)

    try:
        main.call_apollo("example.com")
        assert False, "Expected RuntimeError"
    except RuntimeError as e:
        assert "Apollo API connection failed or timed out" in str(e)

def test_call_apollo_invalid_json(monkeypatch):
    import main

    monkeypatch.setattr(main, "APOLLO_API_KEY", "test-key")

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            pass

        def read(self):
            return b"not-valid-json"

    def fake_urlopen(request_obj, timeout=10):
        return FakeResponse()

    monkeypatch.setattr(main.urllib.request, "urlopen", fake_urlopen)

    try:
        main.call_apollo("example.com")
        assert False, "Expected RuntimeError"
    except RuntimeError as e:
        assert "Apollo API returned an invalid response" in str(e)

def test_call_apollo_request_payload(monkeypatch):
    import main
    import json

    monkeypatch.setattr(main, "APOLLO_API_KEY", "test-key")

    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            pass

        def read(self):
            return b'{"people": []}'

    def fake_urlopen(request_obj, timeout=10):
        captured["url"] = request_obj.full_url
        captured["data"] = json.loads(
            request_obj.data.decode("utf-8")
        )
        captured["headers"] = dict(request_obj.header_items())
        return FakeResponse()

    monkeypatch.setattr(main.urllib.request, "urlopen", fake_urlopen)

    main.call_apollo("example.com")

    assert captured["url"] == (
        "https://api.apollo.io/api/v1/mixed_people/api_search"
    )
    assert captured["data"]["q_organization_domains_list"] == ["example.com"]
    assert captured["data"]["per_page"] == 10 

def test_enrich_uses_live_apollo(monkeypatch):
    import main

    monkeypatch.setattr(main, "APOLLO_API_KEY", "test-key")

    called = {"apollo": False}

    def fake_call_apollo(domain):
        called["apollo"] = True
        return {
            "vp_sales": [
                {
                    "name": "Test VP Sales",
                    "title": "VP Sales"
                }
            ]
        }

    monkeypatch.setattr(main, "call_apollo", fake_call_apollo)

    response = client.post(
        "/enrich",
        headers={"x-api-key": os.getenv("ROUTER_API_KEY")},
        json={
            "domain": "stripe.com",
            "need": ["vp_sales"],
            "max_cost": 1,
            "max_latency_ms": 1000
        }
    )

    assert response.status_code == 200
    assert called["apollo"] is True

    data = response.json()
    assert data["provider"] == "apollo"
    assert data["result"]["vp_sales"][0]["name"] == "Test VP Sales"

def test_hunter_falls_back_to_live_apollo(monkeypatch):
    import main

    monkeypatch.setattr(main, "HUNTER_API_KEY", "hunter-test-key")
    monkeypatch.setattr(main, "APOLLO_API_KEY", "apollo-test-key")

    def fake_hunter(domain):
        raise RuntimeError("Simulated Hunter outage")

    def fake_apollo(domain):
        return {
            "vp_sales": [
                {
                    "name": "Fallback VP Sales",
                    "title": "VP Sales"
                }
            ]
        }

    monkeypatch.setattr(main, "call_hunter", fake_hunter)
    monkeypatch.setattr(main, "call_apollo", fake_apollo)

    response = client.post(
        "/enrich",
        headers={"x-api-key": os.getenv("ROUTER_API_KEY")},
        json={
            "domain": "stripe.com",
            "need": ["vp_sales"],
            "max_cost": 1,
            "max_latency_ms": 5000
        }
    )

    assert response.status_code == 200

    data = response.json()

    assert data["provider"] == "apollo"
    assert data["fallback_used"] is True
    assert data["result"]["vp_sales"][0]["name"] == "Fallback VP Sales"

def test_call_pdl_success(monkeypatch):
    import main

    monkeypatch.setattr(main, "PDL_API_KEY", "test-key")

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            pass

        def read(self):
            return b'{"employee_count": 1234}'

    def fake_urlopen(request_obj, timeout=10):
        return FakeResponse()

    monkeypatch.setattr(
        main.urllib.request,
        "urlopen",
        fake_urlopen
    )

    result = main.call_pdl("example.com")

    assert result["employee_count"] == 1234

def test_call_pdl_missing_key(monkeypatch):
    import main

    monkeypatch.setattr(main, "PDL_API_KEY", None)

    try:
        main.call_pdl("example.com")
        assert False, "Expected ValueError"
    except ValueError as e:
        assert "PDL_API_KEY is not configured" in str(e)

def test_call_pdl_http_error(monkeypatch):
    import main
    import urllib.error

    monkeypatch.setattr(main, "PDL_API_KEY", "test-key")

    def fake_urlopen(request_obj, timeout=10):
        raise urllib.error.HTTPError(
            url="https://example.com",
            code=500,
            msg="Server Error",
            hdrs=None,
            fp=None,
        )

    monkeypatch.setattr(
        main.urllib.request,
        "urlopen",
        fake_urlopen
    )

    try:
        main.call_pdl("example.com")
        assert False, "Expected RuntimeError"
    except RuntimeError as e:
        assert "PDL API returned HTTP 500" in str(e)

def test_call_pdl_connection_error(monkeypatch):
    import main
    import urllib.error

    monkeypatch.setattr(main, "PDL_API_KEY", "test-key")

    def fake_urlopen(request_obj, timeout=10):
        raise urllib.error.URLError("connection failed")

    monkeypatch.setattr(
        main.urllib.request,
        "urlopen",
        fake_urlopen
    )

    try:
        main.call_pdl("example.com")
        assert False, "Expected RuntimeError"
    except RuntimeError as e:
        assert "PDL API connection failed or timed out" in str(e)

def test_call_pdl_invalid_json(monkeypatch):
    import main

    monkeypatch.setattr(main, "PDL_API_KEY", "test-key")

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            pass

        def read(self):
            return b"not-valid-json"

    def fake_urlopen(request_obj, timeout=10):
        return FakeResponse()

    monkeypatch.setattr(
        main.urllib.request,
        "urlopen",
        fake_urlopen
    )

    try:
        main.call_pdl("example.com")
        assert False, "Expected RuntimeError"
    except RuntimeError as e:
        assert "PDL API returned an invalid response" in str(e)

def test_enrich_uses_live_pdl(monkeypatch):
    import main

    monkeypatch.setattr(main, "PDL_API_KEY", "test-key")

    called = {"pdl": False}

    def fake_call_pdl(domain):
        called["pdl"] = True
        return {
            "employee_count": 1234
        }

    monkeypatch.setattr(main, "call_pdl", fake_call_pdl)

    response = client.post(
        "/enrich",
        headers={"x-api-key": os.getenv("ROUTER_API_KEY")},
        json={
            "domain": "stripe.com",
            "need": ["employee_count"],
            "max_cost": 1,
            "max_latency_ms": 5000
        }
    )

    assert response.status_code == 200
    assert called["pdl"] is True

    data = response.json()
    assert data["provider"] == "pdl"
    assert data["result"]["employee_count"] == 1234

def test_hunter_and_apollo_both_fail(monkeypatch):
    import main

    monkeypatch.setattr(main, "HUNTER_API_KEY", "hunter-test-key")
    monkeypatch.setattr(main, "APOLLO_API_KEY", "apollo-test-key")

    def fake_hunter(domain):
        raise RuntimeError("Hunter unavailable")

    def fake_apollo(domain):
        raise RuntimeError("Apollo unavailable")

    monkeypatch.setattr(main, "call_hunter", fake_hunter)
    monkeypatch.setattr(main, "call_apollo", fake_apollo)

    response = client.post(
        "/enrich",
        headers={"x-api-key": os.getenv("ROUTER_API_KEY")},
        json={
            "domain": "stripe.com",
            "need": ["vp_sales"],
            "max_cost": 1,
            "max_latency_ms": 5000,
        },
    )

    assert response.status_code == 502
    data = response.json()
    assert data["status"] == "error"
    assert data["provider"] == "apollo"
    assert data["reason"] == "provider_unavailable"                                                   