"""Schema-checked access to Hermes' ``state.db``. Pure stdlib (sqlite3): no Hermes imports.

Hermes has no public accessor for ``session_model_usage``, so reads use a plain read-only
connection (``mode=ro``). Every entry point verifies the columns it needs with
``PRAGMA table_info`` first and refuses to run on anything else (fail closed).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

SESSION_COLUMNS = frozenset(
    {
        "id",
        "model",
        "started_at",
        "ended_at",
        "last_activity_at",
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
        "billing_provider",
        "billing_base_url",
        "api_call_count",
        "estimated_cost_usd",
        "actual_cost_usd",
        "cost_status",
        "cost_source",
        "pricing_version",
    }
)
USAGE_COLUMNS = frozenset(
    {
        "session_id",
        "model",
        "billing_provider",
        "billing_base_url",
        "task",
        "api_call_count",
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
        "estimated_cost_usd",
        "actual_cost_usd",
        "cost_status",
        "cost_source",
        "first_seen",
        "last_seen",
    }
)
REQUIRED = {"sessions": SESSION_COLUMNS, "session_model_usage": USAGE_COLUMNS}

#: ``cost_status`` values meaning "Hermes could not price this".
UNKNOWN_STATUSES = (None, "", "unknown")
UNKNOWN_SQL = "(cost_status IS NULL OR cost_status IN ('', 'unknown'))"
TOKENS_SQL = (
    "(COALESCE(input_tokens,0) + COALESCE(output_tokens,0) + COALESCE(cache_read_tokens,0)"
    " + COALESCE(cache_write_tokens,0)) > 0"
)


class SchemaError(RuntimeError):
    """state.db does not have the shape this plugin was written for."""


def check_schema(conn: sqlite3.Connection) -> None:
    problems = []
    for table, needed in REQUIRED.items():
        present = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if not present:
            problems.append(f"table {table!r} is missing")
            continue
        missing = sorted(needed - present)
        if missing:
            problems.append(f"table {table!r} lacks column(s) {', '.join(missing)}")
    if problems:
        raise SchemaError(
            "state.db has an unexpected schema (" + "; ".join(problems) + "). gateway-pricing refuses to "
            "read or write it; check that the plugin supports your Hermes version."
        )


def connect_ro(db_path: Path, timeout: float = 5.0) -> sqlite3.Connection:
    path = Path(db_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"no state.db at {path}")
    conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=timeout)
    conn.row_factory = sqlite3.Row
    try:
        check_schema(conn)
    except BaseException:
        conn.close()
        raise
    return conn
