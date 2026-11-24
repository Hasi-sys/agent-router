import os
import secrets
from urllib.parse import urlparse
from fastapi.testclient import TestClient
import main

from postgres_database import get_postgres_connection

database_url = urlparse(os.environ.get("DATABASE_URL", ""))

if (
    database_url.hostname not in ("localhost", "127.0.0.1")
    or database_url.path != "/agent_router"
):
    raise RuntimeError(
        "Verification is restricted to the local Agent Router database."
    )

main.DB_ENGINE = "postgres"
main.ROUTER_API_KEY = secrets.token_urlsafe(32)

client = TestClient(main.app)

customer = main.create_customer(
    name="Temporary endpoint test",
    monthly_limit=10,
)

customer_id = customer["customer_id"]
old_key = customer["api_key"]

url = f"/admin/customers/{customer_id}/rotate-key"

try:
    response = client.post(
        url,
        headers={"x-api-key": old_key},
    )
    assert response.status_code == 403
    print("PASS: Customer cannot rotate API keys")

    response = client.post(url)
    assert response.status_code == 401
    print("PASS: Missing API key rejected")

    response = client.post(
        url,
        headers={"x-api-key": main.ROUTER_API_KEY},
    )
    assert response.status_code == 200

    new_key = response.json()["api_key"]
    assert new_key != old_key
    assert response.headers["Cache-Control"] == "no-store"
    print("PASS: Administrator rotation successful")

    response = client.post(
        url,
        headers={"x-api-key": old_key},
    )
    assert response.status_code == 401
    print("PASS: Old API key rejected")

    response = client.post(
        url,
        headers={"x-api-key": new_key},
    )
    assert response.status_code == 403
    print("PASS: New API key authenticated")

    with get_postgres_connection() as conn:
        row = conn.execute(
            """
            SELECT api_key_hash
            FROM customers
            WHERE customer_id = %s
            """,
            (customer_id,),
        ).fetchone()

        assert row[0] == main.hash_api_key(new_key)

        audit = conn.execute(
            """
            SELECT action, actor
            FROM admin_audit_logs
            WHERE customer_id = %s
            """,
            (customer_id,),
        ).fetchall()

        assert audit == [
            ("customer_api_key_rotated", "system")
        ]

    print("PASS: Hashed key and audit persisted")

finally:
    with get_postgres_connection() as conn:
        conn.execute(
            "DELETE FROM admin_audit_logs WHERE customer_id = %s",
            (customer_id,),
        )

        conn.execute(
            "DELETE FROM customers WHERE customer_id = %s",
            (customer_id,),
        )

    client.close()
    print("PASS: Temporary test data removed")