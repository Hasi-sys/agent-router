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