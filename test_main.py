import os
import pytest
from dotenv import load_dotenv
from fastapi.testclient import TestClient
from main import app
from fastapi import HTTPException

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
    response = client.get("/config", headers={"x-api-key": os.getenv("ROUTER_API_KEY", "")})

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
            "organization": {
                "name": "Fallback Company",
                "domain": domain,
                "employee_count": 100,
            }
        },
    )

    response = client.post(
        "/enrich",
        headers={"x-api-key": os.getenv("ROUTER_API_KEY")},
        json={
            "domain": "stripe.com",
            "need": ["employee_count"],
            "max_cost": 100,
            "max_latency_ms": 1000,
        },
    )

    assert response.status_code == 200

    data = response.json()

    assert data["provider"] == "apollo"
    assert data["result"]["organization"]["employee_count"] == 100

def test_enrich_cost_fallback(monkeypatch):
    import main

    monkeypatch.setattr(main, "APOLLO_API_KEY", "test-key")

    monkeypatch.setattr(
        main,
        "call_apollo",
        lambda domain: {
            "organization": {
                "name": "Fallback Company",
                "domain": domain,
                "employee_count": 100,
            }
        },
    )

    response = client.post(
        "/enrich",
        headers={"x-api-key": os.getenv("ROUTER_API_KEY")},
        json={
            "domain": "stripe.com",
            "need": ["employee_count"],
            "max_cost": 0.04,
            "max_latency_ms": 5000,
        },
    )

    assert response.status_code == 200

    data = response.json()

    assert data["provider"] == "apollo"
    assert data["result"]["organization"]["employee_count"] == 100

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
            return False

        def read(self):
            return b'''
            {
                "organization": {
                    "name": "Example Inc",
                    "primary_domain": "example.com",
                    "website_url": "https://example.com",
                    "industry": "software",
                    "estimated_num_employees": 100,
                    "annual_revenue": 5000000,
                    "annual_revenue_printed": "$5M",
                    "city": "Louisville",
                    "state": "Kentucky",
                    "country": "United States",
                    "linkedin_url": "https://linkedin.com/company/example"
                }
            }
            '''

    monkeypatch.setattr(
        main.urllib.request,
        "urlopen",
        lambda request, timeout=10: FakeResponse(),
    )

    result = main.call_apollo("example.com")

    assert result["organization"]["name"] == "Example Inc"
    assert result["organization"]["domain"] == "example.com"
    assert result["organization"]["employee_count"] == 100

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
    import urllib.parse

    monkeypatch.setattr(main, "APOLLO_API_KEY", "test-key")

    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b'{"organization": null}'

    def fake_urlopen(request_obj, timeout=10):
        captured["url"] = request_obj.full_url
        captured["headers"] = dict(request_obj.header_items())
        captured["method"] = request_obj.get_method()
        return FakeResponse()

    monkeypatch.setattr(
        main.urllib.request,
        "urlopen",
        fake_urlopen,
    )

    main.call_apollo("example.com")

    parsed = urllib.parse.urlparse(captured["url"])
    query = urllib.parse.parse_qs(parsed.query)

    assert parsed.path == "/api/v1/organizations/enrich"
    assert query["domain"] == ["example.com"]
    assert captured["method"] == "GET"
    assert captured["headers"]["X-api-key"] == "test-key"

def test_enrich_uses_live_apollo(monkeypatch):
    import main

    monkeypatch.setattr(main, "APOLLO_API_KEY", "test-key")

    called = {"apollo": False}

    def fake_call_apollo(domain):
        called["apollo"] = True
        return {
            "organization": {
                "name": "Example Inc",
                "domain": "stripe.com",
                "employee_count": 100
            }
        }

    monkeypatch.setattr(main, "call_apollo", fake_call_apollo)

    response = client.post(
        "/enrich",
        headers={"x-api-key": os.getenv("ROUTER_API_KEY")},
        json={
            "domain": "stripe.com",
            "need": ["employee_count"],
            "max_cost": 1,
            "max_latency_ms": 1000
        }
    )

    assert response.status_code == 200
    assert called["apollo"] is True

    data = response.json()
    assert data["provider"] == "apollo"
    assert data["result"]["organization"]["employee_count"] == 100

def test_pdl_falls_back_to_live_apollo(monkeypatch):
    import main

    monkeypatch.setattr(main, "APOLLO_API_KEY", "apollo-test-key")

    def fake_apollo(domain):
        return {
            "organization": {
                "name": "Fallback Company",
                "domain": domain,
                "employee_count": 100,
            }
        }

    monkeypatch.setattr(main, "call_apollo", fake_apollo)

    response = client.post(
        "/enrich",
        headers={"x-api-key": os.getenv("ROUTER_API_KEY")},
        json={
            "domain": "stripe.com",
            "need": ["employee_count"],
            "max_cost": 100,
            "max_latency_ms": 5000,
        },
    )

    assert response.status_code == 200

    data = response.json()

    assert data["provider"] == "apollo"
    assert data["fallback_used"] is True
    assert data["result"]["organization"]["employee_count"] == 100

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

def test_pdl_and_apollo_both_fail(monkeypatch):
    import main

    monkeypatch.setattr(main, "PDL_API_KEY", "pdl-test-key")
    monkeypatch.setattr(main, "APOLLO_API_KEY", "apollo-test-key")

    def fake_pdl(domain):
        raise RuntimeError("Simulated PDL outage")

    def fake_apollo(domain):
        raise RuntimeError("Simulated Apollo outage")

    monkeypatch.setattr(main, "call_pdl", fake_pdl)
    monkeypatch.setattr(main, "call_apollo", fake_apollo)

    response = client.post(
        "/enrich",
        headers={"x-api-key": os.getenv("ROUTER_API_KEY")},
        json={
            "domain": "stripe.com",
            "need": ["employee_count"],
            "max_cost": 100,
            "max_latency_ms": 5000,
        },
    )

    assert response.status_code == 502

    data = response.json()

    assert data["status"] == "error"
    assert data["reason"] == "provider_unavailable"

    saved = main.conn.execute(
        "SELECT status, reason, provider, fallback_used FROM requests WHERE domain = 'stripe.com' AND provider = 'apollo' AND status = 'error' ORDER BY rowid DESC LIMIT 1"
    ).fetchone()

    assert saved == ("error", "provider_unavailable", "apollo", 1)

def test_pdl_missing_api_key(monkeypatch):
    import main
    import pytest

    monkeypatch.setattr(main, "PDL_API_KEY", "")

    with pytest.raises(ValueError, match="PDL_API_KEY is not configured"):
        main.call_pdl("stripe.com")

def test_pdl_successful_response(monkeypatch):
    import main
    import io
    import json

    monkeypatch.setattr(main, "PDL_API_KEY", "fake-test-key")

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

        def read(self):
            return json.dumps({
                "employee_count": 1234
            }).encode("utf-8")

    def fake_urlopen(request, timeout=10):
        return FakeResponse()

    monkeypatch.setattr(
        main.urllib.request,
        "urlopen",
        fake_urlopen
    )

    result = main.call_pdl("stripe.com")

    assert result == {"employee_count": 1234}

def test_pdl_invalid_json_response(monkeypatch):
    import main
    import pytest

    monkeypatch.setattr(main, "PDL_API_KEY", "fake-test-key")

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

        def read(self):
            return b"invalid json"

    monkeypatch.setattr(
        main.urllib.request,
        "urlopen",
        lambda request, timeout=10: FakeResponse()
    )

    with pytest.raises(
        RuntimeError,
        match="PDL API returned an invalid response"
    ):
        main.call_pdl("stripe.com")

def test_customer_api_key_authentication():
    import main

    customer = main.create_customer("Customer Auth Test")

    try:
        auth = main.verify_api_key(customer["api_key"])

        assert auth["type"] == "customer"
        assert auth["customer_id"] == customer["customer_id"]
        assert auth["name"] == "Customer Auth Test"
        assert auth["monthly_limit"] == 1000
    finally:
        main.cursor.execute(
            "DELETE FROM customers WHERE customer_id = ?",
            (customer["customer_id"],),
        )
        main.conn.commit()


def test_invalid_customer_api_key_rejected():
    import main

    with pytest.raises(HTTPException) as exc_info:
        main.verify_api_key("definitely-not-a-valid-key")

    assert exc_info.value.status_code == 401


def test_inactive_customer_api_key_rejected():
    import main

    customer = main.create_customer("Inactive Customer")

    try:
        main.cursor.execute(
            "UPDATE customers SET active = 0 WHERE customer_id = ?",
            (customer["customer_id"],),
        )
        main.conn.commit()

        with pytest.raises(HTTPException) as exc_info:
            main.verify_api_key(customer["api_key"])

        assert exc_info.value.status_code == 401
    finally:
        main.cursor.execute(
            "DELETE FROM customers WHERE customer_id = ?",
            (customer["customer_id"],),
        )
        main.conn.commit()


def test_system_api_key_still_works(monkeypatch):
    import main

    monkeypatch.setattr(main, "ROUTER_API_KEY", "system-test-key")

    auth = main.verify_api_key("system-test-key")

    assert auth["type"] == "system"
    assert auth["customer_id"] is None
    assert auth["name"] == "system"

def test_customer_usage_limit_enforced():
    import main

    customer = main.create_customer("Usage Limit Test", monthly_limit=2)
    customer_id = customer["customer_id"]

    try:
        assert main.consume_customer_usage(customer_id, 2) == 1
        assert main.consume_customer_usage(customer_id, 2) == 2

        with pytest.raises(HTTPException) as exc_info:
            main.consume_customer_usage(customer_id, 2)

        assert exc_info.value.status_code == 429
        assert main.get_customer_usage(customer_id) == 2
    finally:
        main.cursor.execute(
            "DELETE FROM customer_usage WHERE customer_id = ?",
            (customer_id,),
        )
        main.cursor.execute(
            "DELETE FROM customers WHERE customer_id = ?",
            (customer_id,),
        )
        main.conn.commit()


def test_customer_usage_isolated_between_customers():
    import main

    customer_a = main.create_customer("Usage Customer A", monthly_limit=2)
    customer_b = main.create_customer("Usage Customer B", monthly_limit=2)

    try:
        main.consume_customer_usage(customer_a["customer_id"], 2)

        assert main.get_customer_usage(customer_a["customer_id"]) == 1
        assert main.get_customer_usage(customer_b["customer_id"]) == 0
    finally:
        for customer_id in (
            customer_a["customer_id"],
            customer_b["customer_id"],
        ):
            main.cursor.execute(
                "DELETE FROM customer_usage WHERE customer_id = ?",
                (customer_id,),
            )
            main.cursor.execute(
                "DELETE FROM customers WHERE customer_id = ?",
                (customer_id,),
            )

        main.conn.commit()

def test_enrich_customer_quota_returns_429(monkeypatch):
    import main

    customer = main.create_customer("Quota Endpoint Test", monthly_limit=1)

    try:
        monkeypatch.setattr(main, "provider_supports", lambda provider, need: True)
        monkeypatch.setattr(main, "choose_provider", lambda need: "hunter")
        monkeypatch.setattr(
            main,
            "call_hunter",
            lambda domain: {"data": {"domain": domain}},
        )

        headers = {"x-api-key": customer["api_key"]}
        payload = {
            "domain": "stripe.com",
            "need": ["verified_email"],
        }

        first = client.post("/enrich", json=payload, headers=headers)
        second = client.post("/enrich", json=payload, headers=headers)

        assert first.status_code != 429
        assert second.status_code == 429
    finally:
        main.cursor.execute(
            "DELETE FROM customer_usage WHERE customer_id = ?",
            (customer["customer_id"],),
        )
        main.cursor.execute(
            "DELETE FROM customers WHERE customer_id = ?",
            (customer["customer_id"],),
        )
        main.conn.commit()

def test_system_api_key_not_subject_to_customer_quota(monkeypatch):
    import main

    monkeypatch.setattr(main, "ROUTER_API_KEY", "system-unlimited-test-key")
    monkeypatch.setattr(main, "provider_supports", lambda provider, need: True)
    monkeypatch.setattr(main, "choose_provider", lambda need: "hunter")
    monkeypatch.setattr(
        main,
        "call_hunter",
        lambda domain: {"data": {"domain": domain}},
    )

    headers = {"x-api-key": "system-unlimited-test-key"}
    payload = {
        "domain": "stripe.com",
        "need": ["verified_email"],
    }

    first = client.post("/enrich", json=payload, headers=headers)
    second = client.post("/enrich", json=payload, headers=headers)
    third = client.post("/enrich", json=payload, headers=headers)

    assert first.status_code != 429
    assert second.status_code != 429
    assert third.status_code != 429

def test_apollo_live_response_mapping(monkeypatch):
    import io
    import json
    import main

    monkeypatch.setattr(main, "APOLLO_API_KEY", "test-key")

    payload = {
        "organization": {
            "name": "Example Inc",
            "primary_domain": "example.com",
            "estimated_num_employees": 125,
        }
    }

    def fake_urlopen(*args, **kwargs):
        return io.BytesIO(json.dumps(payload).encode("utf-8"))

    monkeypatch.setattr(
        main.urllib.request,
        "urlopen",
        fake_urlopen,
    )

    result = main.call_apollo("example.com")

    assert result["organization"]["name"] == "Example Inc"
    assert result["organization"]["employee_count"] == 125

def test_apollo_missing_organization_returns_502(monkeypatch):
    import main

    monkeypatch.setattr(main, "APOLLO_API_KEY", "test-key")
    monkeypatch.setattr(main, "choose_provider", lambda need: "apollo")
    monkeypatch.setattr(
        main,
        "call_apollo",
        lambda domain: {"organization": None},
    )

    response = client.post(
        "/enrich",
        headers={"x-api-key": os.getenv("ROUTER_API_KEY")},
        json={
            "domain": "example.com",
            "need": ["employee_count"],
            "max_cost": 1,
            "max_latency_ms": 5000,
        },
    )

    assert response.status_code == 502

def test_apollo_missing_employee_count_returns_502(monkeypatch):
    import main

    monkeypatch.setattr(main, "APOLLO_API_KEY", "test-key")
    monkeypatch.setattr(main, "choose_provider", lambda need: "apollo")
    monkeypatch.setattr(
        main,
        "call_apollo",
        lambda domain: {
            "organization": {
                "name": "Example Inc",
                "employee_count": None,
            }
        },
    )

    response = client.post(
        "/enrich",
        headers={"x-api-key": os.getenv("ROUTER_API_KEY")},
        json={
            "domain": "example.com",
            "need": ["employee_count"],
            "max_cost": 1,
            "max_latency_ms": 5000,
        },
    )

    assert response.status_code == 502


def test_apollo_live_routing_success(monkeypatch):
    import main

    monkeypatch.setattr(main, "APOLLO_API_KEY", "test-key")
    monkeypatch.setattr(
        main, "choose_provider", lambda need: "apollo"
    )
    monkeypatch.setattr(
        main,
        "call_apollo",
        lambda domain: {
            "organization": {
                "name": "Example Inc",
                "employee_count": 125,
            }
        },
    )

    response = client.post(
        "/enrich",
        headers={"x-api-key": os.getenv("ROUTER_API_KEY")},
        json={
            "domain": "example.com",
            "need": ["employee_count"],
            "max_cost": 1,
            "max_latency_ms": 5000,
        },
    )

    assert response.status_code == 200

    data = response.json()
    assert data["provider"] == "apollo"
    assert data["status"] == "success"
    assert data["result"]["organization"]["employee_count"] == 125


def test_apollo_outage_never_uses_mock(monkeypatch):
    import main

    monkeypatch.setattr(main, "APOLLO_API_KEY", "test-key")
    monkeypatch.setattr(
        main, "choose_provider", lambda need: "apollo"
    )

    def failing_apollo(domain):
        raise RuntimeError("Simulated Apollo outage")

    monkeypatch.setattr(main, "call_apollo", failing_apollo)

    mock_called = {"value": False}

    def fake_mock(provider, need):
        mock_called["value"] = True
        return {"mock": True}

    monkeypatch.setattr(main, "get_mock_result", fake_mock)

    response = client.post(
        "/enrich",
        headers={"x-api-key": os.getenv("ROUTER_API_KEY")},
        json={
            "domain": "example.com",
            "need": ["employee_count"],
            "max_cost": 1,
            "max_latency_ms": 5000,
        },
    )

    assert response.status_code == 502
    assert mock_called["value"] is False


def test_no_incompatible_live_fallback(monkeypatch):
    import main

    monkeypatch.setattr(main, "HUNTER_API_KEY", "test-key")
    monkeypatch.setattr(main, "APOLLO_API_KEY", "test-key")
    monkeypatch.setattr(main, "PDL_API_KEY", "")

    assert main.get_live_fallback_provider(
        "hunter", ["verified_email"]
    ) is None

    assert main.get_live_fallback_provider(
        "apollo", ["employee_count"]
    ) is None


def test_customer_request_history_isolation():
    import main

    customer_a = main.create_customer("History Test A")
    customer_b = main.create_customer("History Test B")

    try:
        request_id = "isolation-test-" + customer_a["customer_id"]

        main.save_request_to_db({
            "request_id": request_id,
            "customer_id": customer_a["customer_id"],
            "domain": "example.com",
            "need": ["employee_count"],
            "status": "success",
            "created_at": "2026-11-03T12:00:00",
        })

        headers_a = {"x-api-key": customer_a["api_key"]}
        headers_b = {"x-api-key": customer_b["api_key"]}

        own = client.get(f"/requests/{request_id}", headers=headers_a)
        other = client.get(f"/requests/{request_id}", headers=headers_b)
        history_a = client.get("/requests?limit=100", headers=headers_a)
        history_b = client.get("/requests?limit=100", headers=headers_b)

        assert own.status_code == 200
        assert other.status_code == 404
        assert history_a.status_code == 200
        assert history_b.status_code == 200
        assert request_id in [r["request_id"] for r in history_a.json()]
        assert request_id not in [r["request_id"] for r in history_b.json()]

    finally:
        main.cursor.execute(
            "DELETE FROM requests WHERE request_id = ?",
            (request_id,),
        )
        main.cursor.execute(
            "DELETE FROM customers WHERE customer_id IN (?, ?)",
            (customer_a["customer_id"], customer_b["customer_id"]),
        )
        main.conn.commit()


def test_system_can_access_customer_request(monkeypatch):
    import main

    monkeypatch.setattr(main, "ROUTER_API_KEY", "system-test-key")

    customer = main.create_customer("System Access Test")
    request_id = "system-test-" + customer["customer_id"]

    try:
        main.save_request_to_db({
            "request_id": request_id,
            "customer_id": customer["customer_id"],
            "domain": "example.com",
            "need": ["employee_count"],
            "status": "success",
            "created_at": "2026-11-03T12:00:00",
        })

        response = client.get(
            f"/requests/{request_id}",
            headers={"x-api-key": "system-test-key"},
        )

        assert response.status_code == 200
        assert response.json()["request_id"] == request_id

    finally:
        main.cursor.execute(
            "DELETE FROM requests WHERE request_id = ?",
            (request_id,),
        )
        main.cursor.execute(
            "DELETE FROM customers WHERE customer_id = ?",
            (customer["customer_id"],),
        )
        main.conn.commit()


def test_customer_cannot_access_unowned_request():
    import main
    import uuid

    customer = main.create_customer("Unowned History Test")
    request_id = "unowned-test-" + str(uuid.uuid4())

    try:
        main.save_request_to_db({
            "request_id": request_id,
            "customer_id": None,
            "domain": "example.com",
            "need": ["employee_count"],
            "status": "success",
            "created_at": "2026-11-03T12:00:00",
        })

        headers = {"x-api-key": customer["api_key"]}

        detail = client.get(f"/requests/{request_id}", headers=headers)
        history = client.get("/requests?limit=100", headers=headers)

        assert detail.status_code == 404
        assert history.status_code == 200
        assert request_id not in [
            item["request_id"] for item in history.json()
        ]

    finally:
        main.cursor.execute(
            "DELETE FROM requests WHERE request_id = ?",
            (request_id,),
        )
        main.cursor.execute(
            "DELETE FROM customers WHERE customer_id = ?",
            (customer["customer_id"],),
        )
        main.conn.commit()


def test_customer_api_key_rotation():
    import main
    import pytest
    from fastapi import HTTPException

    customer = main.create_customer(
        "Rotation Security Test",
        monthly_limit=25,
    )
    customer_id = customer["customer_id"]
    old_key = customer["api_key"]

    try:
        rotated = main.rotate_customer_api_key(customer_id)
        new_key = rotated["api_key"]

        assert new_key != old_key
        assert rotated["customer_id"] == customer_id

        with pytest.raises(HTTPException) as exc:
            main.verify_api_key(old_key)

        assert exc.value.status_code == 401

        auth = main.verify_api_key(new_key)
        assert auth["type"] == "customer"
        assert auth["customer_id"] == customer_id
        assert auth["monthly_limit"] == 25

    finally:
        main.cursor.execute(
            "DELETE FROM customers WHERE customer_id = ?",
            (customer_id,),
        )
        main.conn.commit()


def test_api_key_rotation_rejects_inactive_customer():
    import main
    import pytest

    customer = main.create_customer("Inactive Rotation Test")
    customer_id = customer["customer_id"]

    try:
        main.cursor.execute(
            "UPDATE customers SET active = 0 WHERE customer_id = ?",
            (customer_id,),
        )
        main.conn.commit()

        with pytest.raises(ValueError, match="Active customer not found"):
            main.rotate_customer_api_key(customer_id)

    finally:
        main.cursor.execute(
            "DELETE FROM customers WHERE customer_id = ?",
            (customer_id,),
        )
        main.conn.commit()


def test_api_key_rotation_rejects_unknown_customer():
    import main
    import pytest
    import uuid

    nonexistent_id = str(uuid.uuid4())

    with pytest.raises(ValueError, match="Active customer not found"):
        main.rotate_customer_api_key(nonexistent_id)


def test_api_key_rotation_preserves_usage_and_history():
    import main
    import uuid

    customer = main.create_customer(
        "Rotation Preservation Test",
        monthly_limit=25,
    )
    customer_id = customer["customer_id"]
    request_id = "rotation-history-" + str(uuid.uuid4())

    try:
        assert main.consume_customer_usage(customer_id, 25) == 1

        main.save_request_to_db({
            "request_id": request_id,
            "customer_id": customer_id,
            "domain": "example.com",
            "need": ["employee_count"],
            "status": "success",
            "created_at": "2026-11-03T12:00:00",
        })

        new_key = main.rotate_customer_api_key(customer_id)["api_key"]

        assert main.get_customer_usage(customer_id) == 1

        response = client.get(
            f"/requests/{request_id}",
            headers={"x-api-key": new_key},
        )

        assert response.status_code == 200
        assert response.json()["request_id"] == request_id

    finally:
        main.cursor.execute(
            "DELETE FROM requests WHERE request_id = ?",
            (request_id,),
        )
        main.cursor.execute(
            "DELETE FROM customer_usage WHERE customer_id = ?",
            (customer_id,),
        )
        main.cursor.execute(
            "DELETE FROM customers WHERE customer_id = ?",
            (customer_id,),
        )
        main.conn.commit()
