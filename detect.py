"""Find endpoints and models Hermes recorded without a price. Pure stdlib: no Hermes imports.

Reads ``state.db`` through a read-only connection and groups unknown-cost usage by
``(host[:port], model)``. Sessions that have no per-model usage rows are grouped from their own
token columns. The vendor suggestion is computed by a callback (it needs Hermes' price tables).
"""

from __future__ import annotations

import time
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .match import parse_endpoint
from .rules import RuleSet
from .statedb import TOKENS_SQL, UNKNOWN_SQL, connect_ro

Suggest = Callable[[str, str], dict | None]

_USAGE_SQL = """
SELECT u.model, u.billing_base_url AS base_url, u.session_id, u.api_call_count AS calls,
       u.input_tokens, u.output_tokens, u.cache_read_tokens,
       COALESCE(u.last_seen, s.last_activity_at, s.started_at) AS seen
  FROM session_model_usage u JOIN sessions s ON s.id = u.session_id
 WHERE (u.cost_status IS NULL OR u.cost_status IN ('', 'unknown'))
   AND (u.input_tokens + u.output_tokens + u.cache_read_tokens + u.cache_write_tokens) > 0
   AND COALESCE(u.last_seen, s.last_activity_at, s.started_at) >= ?
"""
_SESSION_SQL = f"""
SELECT s.model, s.billing_base_url AS base_url, s.id AS session_id, s.api_call_count AS calls,
       s.input_tokens, s.output_tokens, s.cache_read_tokens,
       COALESCE(s.last_activity_at, s.started_at) AS seen
  FROM sessions s
 WHERE {UNKNOWN_SQL.replace("cost_status", "s.cost_status")} AND {TOKENS_SQL}
   AND NOT EXISTS (SELECT 1 FROM session_model_usage u WHERE u.session_id = s.id)
   AND COALESCE(s.last_activity_at, s.started_at) >= ?
"""


def _clean_url(url: str) -> str:
    return (url or "").strip().rstrip("/")


def detect(
    db_path: Path, ruleset: RuleSet, suggest: Suggest | None = None, *, days: int = 30, now: float | None = None
) -> list[dict[str, Any]]:
    """Unknown-cost endpoints/models, biggest token volume first (ties: most recent first)."""
    if isinstance(days, bool) or not isinstance(days, int) or days < 1:
        raise ValueError("days must be a positive integer")
    cutoff = (time.time() if now is None else now) - days * 86400
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    conn = connect_ro(db_path)
    try:
        rows = list(conn.execute(_USAGE_SQL, (cutoff,))) + list(conn.execute(_SESSION_SQL, (cutoff,)))
    finally:
        conn.close()
    for row in rows:
        url = _clean_url(row["base_url"] or "")
        endpoint = parse_endpoint(url)
        host = endpoint.display if endpoint else url
        model = row["model"] or ""
        group = groups.setdefault(
            (host, model),
            {
                "host": host,
                "model": model,
                "_urls": Counter(),
                "_sessions": set(),
                "calls": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_read_tokens": 0,
                "last_seen": 0.0,
            },
        )
        group["_urls"][url] += 1
        group["_sessions"].add(row["session_id"])
        group["calls"] += int(row["calls"] or 0)
        for key in ("input_tokens", "output_tokens", "cache_read_tokens"):
            group[key] += int(row[key] or 0)
        group["last_seen"] = max(group["last_seen"], float(row["seen"] or 0))
    results = []
    for group in groups.values():
        base_url = group.pop("_urls").most_common(1)[0][0]
        group["sessions"] = len(group.pop("_sessions"))
        decision = ruleset.decide(group["model"], base_url, "unknown")
        group["base_url"] = base_url
        group["matched_rule"] = decision.rule.label if decision else None
        group["suggestion"] = suggest(group["model"], base_url) if (decision is None and suggest) else None
        results.append(group)
    results.sort(
        key=lambda g: (
            -(g["input_tokens"] + g["output_tokens"] + g["cache_read_tokens"]),
            -g["last_seen"],
            g["host"],
            g["model"],
        )
    )
    return [
        {
            k: g[k]
            for k in (
                "base_url",
                "host",
                "model",
                "sessions",
                "calls",
                "input_tokens",
                "output_tokens",
                "cache_read_tokens",
                "last_seen",
                "matched_rule",
                "suggestion",
            )
        }
        for g in results
    ]
