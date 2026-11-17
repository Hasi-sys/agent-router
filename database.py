import os
import sqlite3


def get_sqlite_connection(db_path: str | None = None) -> sqlite3.Connection:
    """Create a SQLite connection using the configured database path."""
    path = db_path if db_path is not None else os.getenv(
        "AGENT_ROUTER_DB", "requests.db"
    )

    connection = sqlite3.connect(
        path,
        check_same_thread=False,
    )

    return connection

def ensure_request_columns(connection: sqlite3.Connection) -> None:
    """Add missing request columns without deleting existing data."""
    columns = {
        row[1]
        for row in connection.execute("PRAGMA table_info(requests)")
    }

    for column in ("created_at", "customer_id"):
        if column not in columns:
            connection.execute(
                f"ALTER TABLE requests ADD COLUMN {column} TEXT"
            )

    connection.commit()