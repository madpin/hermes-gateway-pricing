"""Constants and the synthetic state.db builder shared by the tests."""

from __future__ import annotations

import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLUGIN_ID = "gateway-pricing"

# The anonymised real-world fixture lives in the gitignored .ref/ directory (local only).
REAL_DB = ROOT / ".ref" / "sessions-sanitized.db"
REAL_DB_NOW = 1794572713.688728 + 3600  # one hour after the newest session in that file

STANDARD_RULES = [
    {"id": "claude-shim", "match": {"base_url": "127.0.0.1:9001"}, "price_as": "anthropic"},
    {"id": "gemini-shim", "match": {"base_url": "127.0.0.1:9002"}, "price_as": "google"},
    {"id": "gateway", "match": {"base_url": "gateway.example.com/openai"}, "price_as": "openai", "multiplier": 1.1},
    {"id": "llama", "match": {"base_url": "127.0.0.1:9003"}, "free": True},
]
STANDARD_ALIASES = {"chat-latest": "gpt-6.1-sol"}


def hermes_lists_price(vendor: str, model: str) -> bool:
    """Whether the Hermes under test has a bundled list price for ``vendor``/``model``.

    Bundled prices differ between Hermes releases (v2026.9.24 has none for gpt-6.1-sol), so tests
    that depend on one take their expectation from the running Hermes, not from a hard-coded table.
    """
    import agent.usage_pricing as up

    usage = up.CanonicalUsage(input_tokens=1000, output_tokens=1000)
    cost = up.estimate_usage_cost(model, usage, provider=vendor)
    return cost.status == "estimated" and cost.source == "official_docs_snapshot"


# ── Synthetic state.db (works in CI, where .ref/ does not exist) ──────────────────────────────

SYNTH_NOW = 1_800_000_000.0
_SESSIONS_DDL = """CREATE TABLE sessions (
    id TEXT PRIMARY KEY, source TEXT NOT NULL DEFAULT 'cli', model TEXT, started_at REAL NOT NULL, ended_at REAL,
    message_count INTEGER DEFAULT 0, input_tokens INTEGER DEFAULT 0, output_tokens INTEGER DEFAULT 0,
    cache_read_tokens INTEGER DEFAULT 0, cache_write_tokens INTEGER DEFAULT 0, reasoning_tokens INTEGER DEFAULT 0,
    billing_provider TEXT, billing_base_url TEXT, billing_mode TEXT, estimated_cost_usd REAL, actual_cost_usd REAL,
    cost_status TEXT, cost_source TEXT, pricing_version TEXT, title TEXT, last_activity_at REAL,
    api_call_count INTEGER DEFAULT 0)"""
_USAGE_DDL = """CREATE TABLE session_model_usage (
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE, model TEXT NOT NULL,
    billing_provider TEXT NOT NULL DEFAULT '', billing_base_url TEXT NOT NULL DEFAULT '',
    billing_mode TEXT NOT NULL DEFAULT '', task TEXT NOT NULL DEFAULT '', api_call_count INTEGER NOT NULL DEFAULT 0,
    input_tokens INTEGER NOT NULL DEFAULT 0, output_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens INTEGER NOT NULL DEFAULT 0, cache_write_tokens INTEGER NOT NULL DEFAULT 0,
    reasoning_tokens INTEGER NOT NULL DEFAULT 0, estimated_cost_usd REAL NOT NULL DEFAULT 0,
    actual_cost_usd REAL NOT NULL DEFAULT 0, cost_status TEXT, cost_source TEXT, first_seen REAL, last_seen REAL,
    PRIMARY KEY (session_id, model, billing_provider, billing_base_url, billing_mode, task))"""

# (id, provider, base_url, model, status, ended(bool), age_seconds, rows); row = (model, base_url, task, status,
# source, cost, input, output, cache_read)
SYNTH_SESSIONS = [
    (
        "s-shim",
        "custom",
        "http://127.0.0.1:9001",
        "claude-opus-5-5",
        "unknown",
        True,
        7200,
        [
            ("claude-opus-5-5", "http://127.0.0.1:9001", "", "unknown", "none", 0.0, 1000, 2000, 50000),
            ("claude-opus-5-5", "http://127.0.0.1:9001", "title_generation", None, None, 0.0, 300, 20, 0),
        ],
    ),
    (
        "s-bedrock",
        "custom",
        "http://127.0.0.1:9001",
        "us.anthropic.claude-opus-5-5-v1:0",
        "unknown",
        True,
        7000,
        [
            ("us.anthropic.claude-opus-5-5-v1:0", "http://127.0.0.1:9001", "", "unknown", "none", 0.0, 10, 500, 9000),
        ],
    ),
    (
        "s-gateway",
        "custom",
        "https://gateway.example.com/openai/v1",
        "gpt-6.1-sol",
        "unknown",
        True,
        6000,
        [
            ("gpt-6.1-sol", "https://gateway.example.com/openai/v1", "", "unknown", "none", 0.0, 4000, 800, 20000),
            ("chat-latest", "https://gateway.example.com/openai/v1/", "", "unknown", "none", 0.0, 500, 50, 0),
        ],
    ),
    (
        "s-gemini",
        "custom",
        "http://127.0.0.1:9002/v1",
        "gemini-3.8-flash",
        "unknown",
        True,
        5000,
        [
            ("gemini-3.8-flash", "http://127.0.0.1:9002/v1", "", "unknown", "none", 0.0, 80000, 900, 0),
        ],
    ),
    (
        "s-local",
        "custom",
        "http://127.0.0.1:9003/v1",
        "Qwen3.6-35B-A3B-UD-Q4_K_M",
        "unknown",
        True,
        4000,
        [
            ("Qwen3.6-35B-A3B-UD-Q4_K_M", "http://127.0.0.1:9003/v1", "", "unknown", "none", 0.0, 20000, 2000, 30000),
        ],
    ),
    (
        "s-direct",
        "anthropic",
        "https://api.anthropic.com",
        "claude-opus-5-5",
        "estimated",
        True,
        3000,
        [
            (
                "claude-opus-5-5",
                "https://api.anthropic.com",
                "",
                "estimated",
                "official_docs_snapshot",
                1.25,
                100,
                1000,
                100000,
            ),
            ("claude-opus-5-5", "http://127.0.0.1:9001", "title_generation", None, None, 0.0, 400, 30, 0),
        ],
    ),
    (
        "s-actual",
        "custom",
        "https://gateway.example.com/openai/v1",
        "gpt-6.1-sol",
        "actual",
        True,
        2500,
        [
            (
                "gpt-6.1-sol",
                "https://gateway.example.com/openai/v1",
                "",
                "actual",
                "provider_cost_api",
                0.5,
                100,
                100,
                0,
            ),
        ],
    ),
    (
        "s-unmatched",
        "custom",
        "https://other.example.net/v1",
        "mystery-model",
        "unknown",
        True,
        2000,
        [
            ("mystery-model", "https://other.example.net/v1", "", "unknown", "none", 0.0, 100, 100, 0),
        ],
    ),
    (
        "s-amp",
        "amp",
        "process://amp",
        "glm-5.3-flash",
        "unknown",
        True,
        1800,
        [
            ("glm-5.3-flash", "process://amp", "", "unknown", "none", 0.0, 100, 10, 0),
        ],
    ),
    (
        "s-live",
        "custom",
        "http://127.0.0.1:9001",
        "claude-opus-5-5",
        "unknown",
        False,
        60,
        [
            ("claude-opus-5-5", "http://127.0.0.1:9001", "", "unknown", "none", 0.0, 100, 100, 0),
        ],
    ),
    ("s-nousage", "custom", "http://127.0.0.1:9001", "claude-opus-5-5", "unknown", True, 1500, []),
    ("s-empty", "custom", "http://127.0.0.1:9001", "claude-opus-5-5", None, True, 1400, []),
]


# What a backfill with STANDARD_RULES changes in the synthetic db. The "gateway" rule bills like
# openai, so its two s-gateway rows are repriced only when the Hermes under test has a bundled
# gpt-6.1-sol price; otherwise they must be left exactly as they were.
GATEWAY_ROWS = {
    ("s-gateway", "gpt-6.1-sol", "", "https://gateway.example.com/openai/v1"),
    ("s-gateway", "chat-latest", "", "https://gateway.example.com/openai/v1/"),
}
STANDARD_CHANGED_ROWS = GATEWAY_ROWS | {
    ("s-shim", "claude-opus-5-5", "", "http://127.0.0.1:9001"),
    ("s-shim", "claude-opus-5-5", "title_generation", "http://127.0.0.1:9001"),
    ("s-bedrock", "us.anthropic.claude-opus-5-5-v1:0", "", "http://127.0.0.1:9001"),
    ("s-gemini", "gemini-3.8-flash", "", "http://127.0.0.1:9002/v1"),
    ("s-local", "Qwen3.6-35B-A3B-UD-Q4_K_M", "", "http://127.0.0.1:9003/v1"),
    ("s-direct", "claude-opus-5-5", "title_generation", "http://127.0.0.1:9001"),
}
STANDARD_CHANGED_SESSIONS = {"s-shim", "s-bedrock", "s-gateway", "s-gemini", "s-local", "s-nousage"}


def expected_changes() -> tuple[set, set, int]:
    """(usage row keys, session ids, unpriced row count) of a STANDARD_RULES backfill on this Hermes."""
    if hermes_lists_price("openai", "gpt-6.1-sol"):
        return STANDARD_CHANGED_ROWS, STANDARD_CHANGED_SESSIONS, 0
    return STANDARD_CHANGED_ROWS - GATEWAY_ROWS, STANDARD_CHANGED_SESSIONS - {"s-gateway"}, len(GATEWAY_ROWS)


def build_synthetic_db(path: Path, now: float = SYNTH_NOW) -> Path:
    conn = sqlite3.connect(path)
    with conn:
        conn.execute(_SESSIONS_DDL)
        conn.execute(_USAGE_DDL)
        for sid, provider, url, model, status, ended, age, rows in SYNTH_SESSIONS:
            started = now - age
            tokens = (
                [sum(r[i] for r in rows) for i in (6, 7, 8)]
                if rows
                else ([2000, 300, 1000] if sid == "s-nousage" else [0, 0, 0])
            )
            conn.execute(
                "INSERT INTO sessions (id, model, started_at, ended_at, input_tokens, output_tokens,"
                " cache_read_tokens, billing_provider, billing_base_url, estimated_cost_usd, cost_status,"
                " cost_source, api_call_count) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    sid,
                    model,
                    started,
                    started + 30 if ended else None,
                    *tokens,
                    provider,
                    url,
                    sum(r[5] for r in rows),
                    status,
                    "none" if status == "unknown" else None,
                    len(rows) or 1,
                ),
            )
            for rmodel, rurl, task, rstatus, source, cost, inp, out, cread in rows:
                conn.execute(
                    "INSERT INTO session_model_usage (session_id, model, billing_provider, billing_base_url, task,"
                    " api_call_count, input_tokens, output_tokens, cache_read_tokens, estimated_cost_usd,"
                    " actual_cost_usd, cost_status, cost_source, first_seen, last_seen)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        sid,
                        rmodel,
                        provider if rurl == url else "custom",
                        rurl,
                        task,
                        1,
                        inp,
                        out,
                        cread,
                        cost,
                        cost if rstatus == "actual" else 0.0,
                        rstatus,
                        source,
                        started,
                        started + 20,
                    ),
                )
    conn.close()
    return path
