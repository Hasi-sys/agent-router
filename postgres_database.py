import os


def get_postgres_connection():
    """Create a PostgreSQL connection using DATABASE_URL."""
    database_url = os.getenv("DATABASE_URL")

    if not database_url:
        raise ValueError(
            "DATABASE_URL is required for PostgreSQL"
        )

    import psycopg

    return psycopg.connect(
        database_url,
        connect_timeout=5,
    )

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

def check_postgres_health() -> bool:
    """Verify PostgreSQL database connectivity."""
    with get_postgres_connection() as connection:
        row = connection.execute("SELECT 1").fetchone()

        return row is not None and row[0] == 1

def get_postgres_stats(connection, auth_type, customer_id):
    """Return operational metrics within the authorized scope."""

    filters = (auth_type, customer_id)

    row = connection.execute(
        """
        SELECT
            COUNT(*),
            COUNT(*) FILTER (WHERE status = 'rejected'),
            COUNT(*) FILTER (WHERE fallback_used = 1),
            COUNT(*) FILTER (WHERE status != 'rejected'),
            COUNT(*) FILTER (WHERE status = 'success'),
            COUNT(*) FILTER (WHERE status = 'mock'),
            COUNT(*) FILTER (WHERE status = 'error'),
            COALESCE(
                SUM(cost) FILTER (
                    WHERE status != 'rejected'
                ), 0
            ),
            COALESCE(
                AVG(latency_ms) FILTER (
                    WHERE status != 'rejected'
                ), 0
            )
        FROM requests
        WHERE (%s = 'system' OR customer_id = %s)
        """,
        filters,
    ).fetchone()

    provider_rows = connection.execute(
        """
        SELECT provider, COUNT(*)
        FROM requests
        WHERE provider IS NOT NULL
        AND (%s = 'system' OR customer_id = %s)
        GROUP BY provider
        """,
        filters,
    ).fetchall()

    (
        total,
        rejected,
        fallback,
        completed,
        successful,
        mocked,
        failed,
        cost,
        latency,
    ) = row

    return {
        "status": "ok",
        "total_requests": total,
        "rejected_requests": rejected,
        "fallback_requests": fallback,
        "completed_requests": completed,
        "successful_requests": successful,
        "mock_requests": mocked,
        "failed_requests": failed,
        "success_rate": (
            round(successful / total * 100, 2)
            if total else 0
        ),
        "fallback_rate": (
            round(fallback / total * 100, 2)
            if total else 0
        ),
        "provider_usage": dict(provider_rows),
        "estimated_total_cost": round(float(cost), 2),
        "average_latency_ms": round(float(latency), 2),
    }

def validate_postgres_startup() -> None:
    """Validate PostgreSQL connectivity and required tables."""

    required_tables = (
        "customers",
        "customer_usage",
        "requests",
        "admin_audit_logs",
    )

    with get_postgres_connection() as connection:
        row = connection.execute("SELECT 1").fetchone()

        if row is None or row[0] != 1:
            raise RuntimeError(
                "PostgreSQL connectivity validation failed"
            )

        missing_tables = [
            table
            for table in required_tables
            if not postgres_table_exists(connection, table)
        ]

        if missing_tables:
            raise RuntimeError(
                "Missing PostgreSQL tables: "
                + ", ".join(missing_tables)
            )
        validate_postgres_columns(connection)
        validate_postgres_column_types(connection)
        validate_postgres_constraints(connection)
        validate_postgres_permissions(connection)
        validate_postgres_nullability(connection)

def validate_postgres_columns(connection) -> None:
    """Ensure all required database columns exist."""

    required_columns = {
        "customers": {
            "customer_id", "name", "api_key_hash",
            "monthly_limit", "active", "created_at",
        },
        "customer_usage": {
            "customer_id", "period", "request_count",
        },
        "requests": {
            "request_id", "customer_id", "domain",
            "need", "max_cost", "max_latency_ms",
            "status", "reason", "provider",
            "original_provider", "fallback_used",
            "fallback_reason", "cost", "latency_ms",
            "result", "created_at",
        },
        "admin_audit_logs": {
            "id", "action", "customer_id",
            "actor", "created_at",
        },
    }

    rows = connection.execute("""
        SELECT table_name, column_name
        FROM information_schema.columns
        WHERE table_schema = 'public'
    """).fetchall()

    existing_columns = {}

    for table, column in rows:
        existing_columns.setdefault(table, set()).add(column)

    for table, required in required_columns.items():
        missing = required - existing_columns.get(table, set())

        if missing:
            raise RuntimeError(
                f"Missing columns in {table}: "
                + ", ".join(sorted(missing))
            )

def validate_postgres_column_types(connection) -> None:
    """Validate required PostgreSQL column data types."""

    expected_types = {
        "customers": {
            "customer_id": "text",
            "name": "text",
            "api_key_hash": "text",
            "monthly_limit": "integer",
            "active": "integer",
            "created_at": "text",
        },
        "customer_usage": {
            "customer_id": "text",
            "period": "text",
            "request_count": "integer",
        },
        "requests": {
            "request_id": "text",
            "customer_id": "text",
            "domain": "text",
            "need": "text",
            "max_cost": "double precision",
            "max_latency_ms": "integer",
            "status": "text",
            "reason": "text",
            "provider": "text",
            "original_provider": "text",
            "fallback_used": "integer",
            "fallback_reason": "text",
            "cost": "double precision",
            "latency_ms": "integer",
            "result": "text",
            "created_at": "text",
        },
        "admin_audit_logs": {
            "id": "integer",
            "action": "text",
            "customer_id": "text",
            "actor": "text",
            "created_at": "text",
        },
    }

    rows = connection.execute("""
        SELECT table_name, column_name, data_type
        FROM information_schema.columns
        WHERE table_schema = 'public'
    """).fetchall()

    actual_types = {
        (table, column): data_type
        for table, column, data_type in rows
    }

    for table, columns in expected_types.items():
        for column, expected in columns.items():
            actual = actual_types.get((table, column))

            if actual != expected:
                raise RuntimeError(
                    f"Invalid PostgreSQL column type: "
                    f"{table}.{column}; "
                    f"expected {expected}, found {actual}"
                )

def validate_postgres_constraints(connection) -> None:
    """Validate required PostgreSQL database constraints."""

    required_constraints = {
        ("customers", "PRIMARY KEY (customer_id)"),
        ("customers", "UNIQUE (api_key_hash)"),
        ("customer_usage", "PRIMARY KEY (customer_id, period)"),
        (
            "customer_usage",
            "FOREIGN KEY (customer_id) REFERENCES customers(customer_id)",
        ),
        ("requests", "PRIMARY KEY (request_id)"),
        ("admin_audit_logs", "PRIMARY KEY (id)"),
    }

    rows = connection.execute("""
        SELECT
            rel.relname,
            pg_get_constraintdef(con.oid)
        FROM pg_constraint con
        JOIN pg_class rel ON rel.oid = con.conrelid
        JOIN pg_namespace ns ON ns.oid = rel.relnamespace
        WHERE ns.nspname = 'public'
          AND con.convalidated = TRUE
    """).fetchall()

    existing_constraints = set(rows)

    missing = required_constraints - existing_constraints

    if missing:
        details = ", ".join(
            f"{table}: {constraint}"
            for table, constraint in sorted(missing)
        )

        raise RuntimeError(
            "Missing PostgreSQL constraints: " + details
        )

def validate_postgres_permissions(connection) -> None:
    """Validate required PostgreSQL application permissions."""

    required_permissions = {
        "customers": ("SELECT", "INSERT", "UPDATE"),
        "customer_usage": ("SELECT", "INSERT", "UPDATE"),
        "requests": ("SELECT", "INSERT", "UPDATE"),
        "admin_audit_logs": ("INSERT",),
    }

    for table, permissions in required_permissions.items():
        for permission in permissions:
            row = connection.execute(
                "SELECT has_table_privilege(%s, %s)",
                (f"public.{table}", permission),
            ).fetchone()

            if not row or not row[0]:
                raise RuntimeError(
                    f"Missing PostgreSQL permission: "
                    f"{table}.{permission}"
                )

    row = connection.execute("""
        SELECT pg_get_serial_sequence(
            'public.admin_audit_logs', 'id'
        )
    """).fetchone()

    if not row or not row[0]:
        raise RuntimeError("Missing audit log sequence")

    sequence = row[0]

    allowed = connection.execute(
        "SELECT has_sequence_privilege(%s, 'USAGE')",
        (sequence,),
    ).fetchone()

    if not allowed or not allowed[0]:
        raise RuntimeError(
            "Missing audit log sequence USAGE permission"
        )

def validate_postgres_nullability(connection) -> None:
    """Validate mandatory PostgreSQL columns."""

    required_not_null = {
        "customers": {
            "customer_id", "name", "api_key_hash",
            "monthly_limit", "active", "created_at",
        },
        "customer_usage": {
            "customer_id", "period", "request_count",
        },
        "admin_audit_logs": {
            "id", "action", "customer_id",
            "actor", "created_at",
        },
        "requests": {
            "request_id", "domain", "need",
            "status", "created_at", "fallback_used",
        },
    }

    rows = connection.execute("""
        SELECT table_name, column_name, is_nullable
        FROM information_schema.columns
        WHERE table_schema = 'public'
    """).fetchall()

    actual = {
        (table, column): nullable
        for table, column, nullable in rows
    }

    for table, columns in required_not_null.items():
        for column in sorted(columns):
            if actual.get((table, column)) != "NO":
                raise RuntimeError(
                    f"Missing NOT NULL constraint: "
                    f"{table}.{column}"
                )