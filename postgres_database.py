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