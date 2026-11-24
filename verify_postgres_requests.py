import os
import uuid
import json
from urllib.parse import urlparse
from datetime import datetime, timezone

from postgres_database import (
    get_postgres_connection,
    save_postgres_request,
)

database_url = urlparse(os.environ.get("DATABASE_URL", ""))

if (
    database_url.hostname not in ("localhost", "127.0.0.1")
    or database_url.path != "/agent_router"
):
    raise RuntimeError("Local database required.")

request_id = str(uuid.uuid4())
customer_id = str(uuid.uuid4())

entry = {
    "request_id": request_id,
    "customer_id": customer_id,
    "domain": "example.com",
    "need": ["employee_count"],
    "status": "success",
    "provider": "apollo",
    "fallback_used": False,
    "cost": 0.01,
    "latency_ms": 120,
    "result": {"employee_count": 100},
    "created_at": datetime.now(timezone.utc).isoformat(),
}

with get_postgres_connection() as conn:
    try:
        save_postgres_request(conn, entry)

        row = conn.execute(
            """
            SELECT customer_id, status, result
            FROM requests
            WHERE request_id = %s
            """,
            (request_id,),
        ).fetchone()

        assert row[0] == customer_id
        assert row[1] == "success"
        assert json.loads(row[2]) == {"employee_count": 100}

        print("PASS: PostgreSQL request insertion")

        entry["result"] = {"employee_count": 200}
        save_postgres_request(conn, entry)

        row = conn.execute(
            "SELECT result FROM requests WHERE request_id = %s",
            (request_id,),
        ).fetchone()

        assert json.loads(row[0]) == {"employee_count": 200}

        print("PASS: PostgreSQL request update")

        unauthorized_entry = dict(entry)
        unauthorized_entry["customer_id"] = str(uuid.uuid4())

        try:
            save_postgres_request(conn, unauthorized_entry)
        except ValueError:
            print("PASS: Customer ownership change rejected")
        else:
            raise AssertionError("Ownership protection failed")

        row = conn.execute(
            "SELECT customer_id FROM requests WHERE request_id = %s",
            (request_id,),
        ).fetchone()

        assert row[0] == customer_id

        print("PASS: Original customer ownership preserved")

    finally:
        conn.rollback()

print("PASS: Temporary request data rolled back")

try:
    with get_postgres_connection() as conn:
        save_postgres_request(conn, entry)

    with get_postgres_connection() as conn:
        row = conn.execute(
            """
            SELECT customer_id, result
            FROM requests
            WHERE request_id = %s
            """,
            (request_id,),
        ).fetchone()

        assert row is not None
        assert row[0] == customer_id
        assert json.loads(row[1]) == entry["result"]

    print("PASS: Durable request persistence verified")

finally:
    with get_postgres_connection() as conn:
        conn.execute(
            "DELETE FROM requests WHERE request_id = %s",
            (request_id,),
        )

print("PASS: Temporary committed request removed")

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

race_request_id = str(uuid.uuid4())
owners = [str(uuid.uuid4()), str(uuid.uuid4())]
barrier = Barrier(2)


def concurrent_save(owner):
    race_entry = dict(entry)
    race_entry["request_id"] = race_request_id
    race_entry["customer_id"] = owner

    barrier.wait(timeout=10)

    try:
        with get_postgres_connection() as conn:
            save_postgres_request(conn, race_entry)

        return "accepted", owner

    except ValueError:
        return "rejected", owner


try:
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(concurrent_save, owners))

    accepted = [
        owner for status, owner in results
        if status == "accepted"
    ]

    rejected = [
        owner for status, owner in results
        if status == "rejected"
    ]

    assert len(accepted) == 1
    assert len(rejected) == 1

    print("PASS: Concurrent ownership conflict rejected")

    with get_postgres_connection() as conn:
        row = conn.execute(
            "SELECT customer_id FROM requests WHERE request_id = %s",
            (race_request_id,),
        ).fetchone()

        assert row[0] == accepted[0]

    print("PASS: Concurrent customer ownership preserved")

finally:
    with get_postgres_connection() as conn:
        conn.execute(
            "DELETE FROM requests WHERE request_id = %s",
            (race_request_id,),
        )

print("PASS: Concurrent test data removed")

from postgres_database import (
    get_postgres_requests,
    get_postgres_request,
)

owner_a = str(uuid.uuid4())
owner_b = str(uuid.uuid4())

entry_a = dict(entry)
entry_a["request_id"] = str(uuid.uuid4())
entry_a["customer_id"] = owner_a

entry_b = dict(entry)
entry_b["request_id"] = str(uuid.uuid4())
entry_b["customer_id"] = owner_b

with get_postgres_connection() as conn:
    try:
        save_postgres_request(conn, entry_a)
        save_postgres_request(conn, entry_b)

        rows_a = get_postgres_requests(
            conn, "customer", owner_a, 10
        )

        assert len(rows_a) == 1
        assert rows_a[0][0] == entry_a["request_id"]
        print("PASS: Customer A request history isolated")

        rows_b = get_postgres_requests(
            conn, "customer", owner_b, 10
        )

        assert len(rows_b) == 1
        assert rows_b[0][0] == entry_b["request_id"]
        print("PASS: Customer B request history isolated")

        own_request = get_postgres_request(
            conn, entry_a["request_id"], "customer", owner_a
        )

        assert own_request is not None
        print("PASS: Customer can retrieve own request")

        unauthorized = get_postgres_request(
            conn, entry_b["request_id"], "customer", owner_a
        )

        assert unauthorized is None
        print("PASS: Cross-customer retrieval blocked")

        for test_entry in (entry_a, entry_b):
            admin_request = get_postgres_request(
                conn,
                test_entry["request_id"],
                "system",
                None,
            )

            assert admin_request is not None

        print("PASS: Administrator request access")

        admin_rows = get_postgres_requests(
            conn, "system", None, 100
        )

        assert len(admin_rows) <= 100
        print("PASS: Administrator request history")

        limited_rows = get_postgres_requests(
            conn, "customer", owner_a, 1
        )

        assert len(limited_rows) == 1
        print("PASS: Request history limit")

    finally:
        conn.rollback()

print("PASS: Temporary retrieval data rolled back")

import secrets
import main
from fastapi.testclient import TestClient

main.DB_ENGINE = "postgres"
main.ROUTER_API_KEY = secrets.token_urlsafe(32)

client = TestClient(main.app)

customer_a = main.create_customer("Temporary customer A", 10)
customer_b = main.create_customer("Temporary customer B", 10)

headers_a = {"x-api-key": customer_a["api_key"]}
headers_b = {"x-api-key": customer_b["api_key"]}
admin_headers = {"x-api-key": main.ROUTER_API_KEY}

test_entries = []

for customer in (customer_a, customer_b):
    test_entry = dict(entry)
    test_entry["request_id"] = str(uuid.uuid4())
    test_entry["customer_id"] = customer["customer_id"]
    test_entries.append(test_entry)

request_a, request_b = test_entries

try:
    for test_entry in test_entries:
        main.save_request_to_db(test_entry)

    print("PASS: Application-level PostgreSQL persistence")

    response = client.get("/requests", headers=headers_a)

    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["request_id"] == request_a["request_id"]

    print("PASS: Customer A HTTP history isolated")

    response = client.get("/requests", headers=headers_b)

    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["request_id"] == request_b["request_id"]

    print("PASS: Customer B HTTP history isolated")

    url_a = "/requests/" + request_a["request_id"]
    url_b = "/requests/" + request_b["request_id"]

    response = client.get(url_a, headers=headers_a)

    assert response.status_code == 200
    assert response.json()["result"] == request_a["result"]

    print("PASS: Individual request response verified")

    assert client.get(url_b, headers=headers_a).status_code == 404
    assert client.get(url_a, headers=headers_b).status_code == 404

    print("PASS: Cross-customer HTTP access blocked")

    assert client.get(url_a, headers=admin_headers).status_code == 200
    assert client.get(url_b, headers=admin_headers).status_code == 200

    print("PASS: Administrator HTTP access verified")

    assert client.get("/requests").status_code == 401
    assert client.get(url_a).status_code == 401

    print("PASS: Unauthenticated retrieval blocked")

finally:
    with get_postgres_connection() as conn:
        for test_entry in test_entries:
            conn.execute(
                "DELETE FROM requests WHERE request_id = %s",
                (test_entry["request_id"],),
            )

        for customer in (customer_a, customer_b):
            conn.execute(
                "DELETE FROM customers WHERE customer_id = %s",
                (customer["customer_id"],),
            )

    client.close()

print("PASS: Temporary HTTP test data removed")

owner = str(uuid.uuid4())

with get_postgres_connection() as conn:
    try:
        for day in range(1, 4):
            test_entry = dict(entry)
            test_entry["request_id"] = str(uuid.uuid4())
            test_entry["customer_id"] = owner
            test_entry["created_at"] = (
                f"2026-11-{day:02d}T12:00:00+00:00"
            )

            save_postgres_request(conn, test_entry)

        rows = get_postgres_requests(
            conn, "customer", owner, 2
        )

        assert len(rows) == 2
        print("PASS: Request history limit enforced")

        assert rows[0][14] > rows[1][14]
        assert rows[0][14].startswith("2026-11-03")
        assert rows[1][14].startswith("2026-11-02")

        print("PASS: Request history sorting verified")

        admin_rows = get_postgres_requests(
            conn, "system", None, 2
        )

        assert len(admin_rows) == 2
        print("PASS: Administrator history limit enforced")

    finally:
        conn.rollback()

print("PASS: Temporary history test data removed")