import os


def get_postgres_connection():
    """Create a PostgreSQL connection using DATABASE_URL."""
    database_url = os.getenv("DATABASE_URL")

    if not database_url:
        raise ValueError(
            "DATABASE_URL is required for PostgreSQL"
        )

    import psycopg

    return psycopg.connect(database_url)

def postgres_sql(sql: str) -> str:
    """Convert placeholders while preserving single-quoted SQL strings."""
    result = []
    in_string = False
    i = 0

    while i < len(sql):
        char = sql[i]

        if char == "'":
            if in_string and i + 1 < len(sql) and sql[i + 1] == "'":
                result.append("''")
                i += 2
                continue

            in_string = not in_string
            result.append(char)

        elif char == "?" and not in_string:
            result.append("%s")

        else:
            result.append(char)

        i += 1

    return "".join(result)

def postgres_table_exists(connection, table_name: str) -> bool:
    """Check whether a table exists in the PostgreSQL public schema."""
    row = connection.execute(
        """
        SELECT EXISTS (
            SELECT 1
            FROM information_schema.tables
            WHERE table_schema = 'public'
              AND table_name = %s
        )
        """,
        (table_name,),
    ).fetchone()

    return bool(row[0])

def create_postgres_customer(connection, customer_id, name, api_key_hash, created_at, monthly_limit=1000):
    """Insert a customer into PostgreSQL."""
    connection.execute(
        """
        INSERT INTO customers (
            customer_id,
            name,
            api_key_hash,
            monthly_limit,
            active,
            created_at
        )
        VALUES (%s, %s, %s, %s, 1, %s)
        """,
        (
            customer_id,
            name,
            api_key_hash,
            monthly_limit,
            created_at,
        ),
    )

def get_postgres_customer_by_api_key_hash(connection, api_key_hash):
    """Find a customer by hashed API key in PostgreSQL."""
    return connection.execute(
        """
        SELECT customer_id, name, monthly_limit, active
        FROM customers
        WHERE api_key_hash = %s
        """,
        (api_key_hash,),
    ).fetchone()

def get_postgres_customer_usage(connection, customer_id, period):
    """Retrieve a customer's usage for a billing period."""
    row = connection.execute(
        """
        SELECT request_count
        FROM customer_usage
        WHERE customer_id = %s AND period = %s
        """,
        (customer_id, period),
    ).fetchone()

    return row[0] if row else 0

def consume_postgres_customer_usage(
    connection, customer_id, period, monthly_limit
):
    """Atomically increment usage without exceeding the monthly limit."""
    if monthly_limit <= 0:
        return None

    row = connection.execute(
        """
        INSERT INTO customer_usage (
            customer_id, period, request_count
        )
        VALUES (%s, %s, 1)
        ON CONFLICT (customer_id, period)
        DO UPDATE SET
            request_count = customer_usage.request_count + 1
        WHERE customer_usage.request_count < %s
        RETURNING request_count
        """,
        (customer_id, period, monthly_limit),
    ).fetchone()

    return row[0] if row else None

def rotate_postgres_customer_api_key(
    connection, customer_id: str, new_api_key_hash: str
) -> None:
    """Update an active customer's API-key hash."""
    result = connection.execute(
        """
        UPDATE customers
        SET api_key_hash = %s
        WHERE customer_id = %s AND active = 1
        """,
        (new_api_key_hash, customer_id),
    )

    if result.rowcount != 1:
        raise ValueError("Active customer not found")

def insert_postgres_admin_audit_log(
    connection, action: str, customer_id: str, actor: str
) -> None:
    """Record an administrative action in the current transaction."""
    connection.execute(
        """
        INSERT INTO admin_audit_logs (
            action, customer_id, actor
        )
        VALUES (%s, %s, %s)
        """,
        (action, customer_id, actor),
    )

def save_postgres_request(connection, entry: dict) -> None:
    """Store a request without allowing ownership changes."""
    import json

    result = connection.execute(
        """
        INSERT INTO requests (
            request_id,
            customer_id,
            domain,
            need,
            max_cost,
            max_latency_ms,
            status,
            reason,
            provider,
            original_provider,
            fallback_used,
            fallback_reason,
            cost,
            latency_ms,
            result,
            created_at
        )
        VALUES (
            %s, %s, %s, %s,
            %s, %s, %s, %s,
            %s, %s, %s, %s,
            %s, %s, %s, %s
        )
        ON CONFLICT (request_id) DO UPDATE SET
            domain = excluded.domain,
            need = excluded.need,
            max_cost = excluded.max_cost,
            max_latency_ms = excluded.max_latency_ms,
            status = excluded.status,
            reason = excluded.reason,
            provider = excluded.provider,
            original_provider = excluded.original_provider,
            fallback_used = excluded.fallback_used,
            fallback_reason = excluded.fallback_reason,
            cost = excluded.cost,
            latency_ms = excluded.latency_ms,
            result = excluded.result
        WHERE requests.customer_id
            IS NOT DISTINCT FROM excluded.customer_id
        """,
        (
            entry.get("request_id"),
            entry.get("customer_id"),
            entry.get("domain"),
            json.dumps(entry.get("need")),
            entry.get("max_cost"),
            entry.get("max_latency_ms"),
            entry.get("status"),
            entry.get("reason"),
            entry.get("provider"),
            entry.get("original_provider"),
            1 if entry.get("fallback_used") else 0,
            entry.get("fallback_reason"),
            entry.get("cost"),
            entry.get("latency_ms"),
            json.dumps(entry.get("result")),
            entry.get("created_at"),
        ),
    )

    if result.rowcount != 1:
        raise ValueError(
            "Cannot change request customer ownership"
        )

POSTGRES_REQUEST_COLUMNS = """
    request_id,
    domain,
    need,
    max_cost,
    max_latency_ms,
    status,
    reason,
    provider,
    original_provider,
    fallback_used,
    fallback_reason,
    cost,
    latency_ms,
    result,
    created_at
"""


def get_postgres_requests(
    connection,
    auth_type: str,
    customer_id,
    limit: int = 10,
):
    return connection.execute(
        f"""
        SELECT {POSTGRES_REQUEST_COLUMNS}
        FROM requests
        WHERE (%s = 'system' OR customer_id = %s)
        ORDER BY created_at DESC
        LIMIT %s
        """,
        (auth_type, customer_id, limit),
    ).fetchall()


def get_postgres_request(
    connection,
    request_id: str,
    auth_type: str,
    customer_id,
):
    return connection.execute(
        f"""
        SELECT {POSTGRES_REQUEST_COLUMNS}
        FROM requests
        WHERE request_id = %s
        AND (%s = 'system' OR customer_id = %s)
        """,
        (request_id, auth_type, customer_id),
    ).fetchone()