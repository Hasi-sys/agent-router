import os
from urllib.parse import urlparse
import uuid
from datetime import datetime, timezone

from postgres_database import (
    get_postgres_connection,
    create_postgres_customer,
    rotate_postgres_customer_api_key,
    insert_postgres_admin_audit_log,
)

database_url = urlparse(os.environ.get("DATABASE_URL", ""))

if (
    database_url.hostname not in ("localhost", "127.0.0.1")
    or database_url.path != "/agent_router"
):
    raise RuntimeError(
        "Verification is restricted to the local Agent Router database."
    )

customer_id = str(uuid.uuid4())

with get_postgres_connection() as conn:
    create_postgres_customer(
        conn,
        customer_id=customer_id,
        name="Temporary rotation test",
        api_key_hash="original-test-hash-" + customer_id,
        created_at=datetime.now(timezone.utc).isoformat(),
    )

    try:
        with conn.transaction():
            rotate_postgres_customer_api_key(
                conn, customer_id, "rollback-" + customer_id
            )

            insert_postgres_admin_audit_log(
                conn,
                "customer_api_key_rotated",
                customer_id,
                "system",
            )

            raise RuntimeError("Intentional rollback")

    except RuntimeError as exc:
        if str(exc) != "Intentional rollback":
            raise

    row = conn.execute(
        "SELECT api_key_hash FROM customers WHERE customer_id = %s",
        (customer_id,),
    ).fetchone()

    assert row[0] == "original-test-hash-" + customer_id

    count = conn.execute(
        "SELECT COUNT(*) FROM admin_audit_logs WHERE customer_id = %s",
        (customer_id,),
    ).fetchone()[0]

    assert count == 0
    print("PASS: PostgreSQL rollback verified")

    rotate_postgres_customer_api_key(
        conn, customer_id, "rotated-" + customer_id
    )

    insert_postgres_admin_audit_log(
        conn,
        "customer_api_key_rotated",
        customer_id,
        "system",
    )

    row = conn.execute(
        "SELECT api_key_hash FROM customers WHERE customer_id = %s",
        (customer_id,),
    ).fetchone()

    assert row[0] == "rotated-" + customer_id

    count = conn.execute(
        "SELECT COUNT(*) FROM admin_audit_logs WHERE customer_id = %s",
        (customer_id,),
    ).fetchone()[0]

    assert count == 1
    print("PASS: PostgreSQL rotation and audit insertion verified")

    conn.rollback()
    print("PASS: Temporary test data rolled back")

with get_postgres_connection() as conn:
    create_postgres_customer(
        conn,
        customer_id=customer_id,
        name="Temporary commit test",
        api_key_hash="original-" + customer_id,
        created_at=datetime.now(timezone.utc).isoformat(),
    )

try:
    with get_postgres_connection() as conn:
        rotate_postgres_customer_api_key(
            conn, customer_id, "committed-" + customer_id
        )

        insert_postgres_admin_audit_log(
            conn,
            "customer_api_key_rotated",
            customer_id,
            "system",
        )

    with get_postgres_connection() as conn:
        row = conn.execute(
            "SELECT api_key_hash FROM customers WHERE customer_id = %s",
            (customer_id,),
        ).fetchone()

        assert row[0] == "committed-" + customer_id

        count = conn.execute(
            "SELECT COUNT(*) FROM admin_audit_logs WHERE customer_id = %s",
            (customer_id,),
        ).fetchone()[0]

        assert count == 1

        print("PASS: Durable PostgreSQL commit verified")

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

    print("PASS: Temporary committed test data removed")