import hashlib
import sqlite3
from decimal import Decimal

import agent.usage_pricing as up
import pytest
from helpers import REAL_DB_NOW, SYNTH_NOW, build_synthetic_db, expected_changes

import gateway_pricing as gp

service = gp.service
BackfillError = gp.backfill.BackfillError
VERSION = gp.version.PRICING_VERSION


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rows(path, sql, *args):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, args)]
    finally:
        conn.close()


def usage(path):
    return {
        (r["session_id"], r["model"], r["task"], r["billing_base_url"]): r
        for r in rows(path, "SELECT * FROM session_model_usage")
    }


def sessions(path):
    return {r["id"]: r for r in rows(path, "SELECT * FROM sessions")}


def opus(inp, out, cache_read):
    entry = up._lookup_official_docs_pricing(up.BillingRoute(provider="anthropic", model="claude-opus-5-5"))
    return float(
        (
            Decimal(inp) * entry.input_cost_per_million
            + Decimal(out) * entry.output_cost_per_million
            + Decimal(cache_read) * entry.cache_read_cost_per_million
        )
        / 1_000_000
    )


@pytest.fixture
def ready(installed, standard_settings, synthetic_db):
    return synthetic_db


def apply(**kw):
    return service.backfill("gateway-pricing", dry_run=False, confirm=True, now=SYNTH_NOW, **kw)


def test_dry_run_writes_nothing(ready, hermes_home):
    before = digest(ready)
    result = service.backfill("gateway-pricing", now=SYNTH_NOW)
    assert digest(ready) == before
    assert result["dry_run"] is True and result["backup_path"] is None
    want_rows, want_sessions, unpriced = expected_changes()
    assert (result["rows_updated"], result["sessions_updated"]) == (len(want_rows), len(want_sessions))
    assert result["rows_unpriced"] == unpriced
    assert result["sessions_skipped_live"] == 1 and result["sessions_skipped_priced"] == 1
    assert result["delta_usd"] > 0
    assert not (hermes_home / "gateway-pricing").exists()


def test_non_dry_run_requires_confirm(ready):
    before = digest(ready)
    with pytest.raises(BackfillError, match="confirm"):
        service.backfill("gateway-pricing", dry_run=False, now=SYNTH_NOW)
    assert digest(ready) == before


def test_apply_updates_exactly_the_expected_rows(ready):
    before_u, before_s = usage(ready), sessions(ready)
    result = apply()
    after_u, after_s = usage(ready), sessions(ready)
    want_rows, want_sessions, _unpriced = expected_changes()
    changed_u = {k for k in after_u if after_u[k] != before_u[k]}
    assert changed_u == want_rows
    changed_s = {k for k in after_s if after_s[k] != before_s[k]}
    assert changed_s == want_sessions
    assert (result["rows_updated"], result["sessions_updated"]) == (len(want_rows), len(want_sessions))

    shim = after_u[("s-shim", "claude-opus-5-5", "", "http://127.0.0.1:9001")]
    assert shim["estimated_cost_usd"] == pytest.approx(opus(1000, 2000, 50000))
    assert (shim["cost_status"], shim["cost_source"]) == ("estimated", "official_docs_snapshot")
    local = after_u[("s-local", "Qwen3.6-35B-A3B-UD-Q4_K_M", "", "http://127.0.0.1:9003/v1")]
    assert (local["cost_status"], local["estimated_cost_usd"]) == ("included", 0.0)
    assert after_s["s-local"]["cost_status"] == "included"
    assert after_s["s-nousage"]["estimated_cost_usd"] == pytest.approx(opus(2000, 300, 1000))
    for sid in changed_s:
        assert after_s[sid]["pricing_version"] == VERSION
    # Never touched: actual/estimated rows, actual_cost_usd anywhere, the live session, unmatched routes.
    for key, row in before_u.items():
        assert after_u[key]["actual_cost_usd"] == row["actual_cost_usd"]
        if row["cost_status"] in ("actual", "estimated"):
            assert after_u[key] == row
    for sid in ("s-direct", "s-actual", "s-live", "s-unmatched", "s-amp", "s-empty"):
        assert after_s[sid] == before_s[sid]


def test_session_totals_equal_the_sum_of_their_rows(ready):
    apply()
    totals = {
        r["session_id"]: r["total"]
        for r in rows(ready, "SELECT session_id, SUM(estimated_cost_usd) AS total FROM session_model_usage GROUP BY 1")
    }
    for sid, session in sessions(ready).items():
        if session["pricing_version"] == VERSION and sid in totals:
            assert session["estimated_cost_usd"] == pytest.approx(totals[sid], abs=1e-12)


def test_second_run_is_a_no_op(ready, hermes_home):
    first = apply()
    snapshot = digest(ready)
    second = apply()
    assert (second["rows_updated"], second["sessions_updated"], second["backup_path"]) == (0, 0, None)
    assert digest(ready) == snapshot
    assert len(list((hermes_home / "gateway-pricing" / "backups").glob("state-*.db"))) == 1
    assert first["backup_path"]


def test_backup_exists_opens_and_rotates(ready, hermes_home):
    original = digest(ready)
    backups = hermes_home / "gateway-pricing" / "backups"
    backups.mkdir(parents=True)
    for i in range(7):
        (backups / f"state-20200101T00000{i}.000000Z.db").write_bytes(b"old")
    result = apply()
    kept = sorted(backups.glob("state-*.db"))
    assert len(kept) == 5 and str(kept[-1]) == result["backup_path"]
    conn = sqlite3.connect(kept[-1])
    try:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("SELECT COUNT(*) FROM session_model_usage WHERE cost_status = 'unknown'").fetchone()[0] > 0
    finally:
        conn.close()
    assert original != digest(ready)


def test_refuses_a_doctored_schema(installed, standard_settings, hermes_home):
    db = build_synthetic_db(hermes_home / "state.db")
    conn = sqlite3.connect(db)
    conn.execute("ALTER TABLE session_model_usage DROP COLUMN cost_source")
    conn.commit()
    conn.close()
    before = digest(db)
    for kwargs in ({}, {"dry_run": False, "confirm": True}):
        with pytest.raises(gp.statedb.SchemaError, match="cost_source"):
            service.backfill("gateway-pricing", now=SYNTH_NOW, **kwargs)
    assert digest(db) == before
    assert not (hermes_home / "gateway-pricing").exists()


def test_handles_a_locked_database(ready, hermes_home):
    before = digest(ready)
    locker = sqlite3.connect(ready, isolation_level=None)
    locker.execute("BEGIN EXCLUSIVE")
    try:
        with pytest.raises(BackfillError, match="nothing was written|rolled back"):
            service.backfill("gateway-pricing", dry_run=False, confirm=True, now=SYNTH_NOW, busy_timeout=0.2)
    finally:
        locker.execute("ROLLBACK")
        locker.close()
    assert digest(ready) == before
    assert (
        not list((hermes_home / "gateway-pricing" / "backups").glob("*.db"))
        if (hermes_home / "gateway-pricing" / "backups").exists()
        else True
    )


def test_wal_database_dry_run_and_writer_lock(installed, standard_settings, hermes_home):
    # Hermes' real state.db runs in WAL mode, where a writer does not block readers.
    db = build_synthetic_db(hermes_home / "state.db")
    conn = sqlite3.connect(db)
    assert conn.execute("PRAGMA journal_mode=wal").fetchone()[0] == "wal"
    conn.close()
    before = digest(db)
    want_rows = len(expected_changes()[0])
    assert service.backfill("gateway-pricing", now=SYNTH_NOW)["rows_updated"] == want_rows
    assert digest(db) == before
    locker = sqlite3.connect(db, isolation_level=None)
    locker.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(BackfillError, match="busy"):
            service.backfill("gateway-pricing", dry_run=False, confirm=True, now=SYNTH_NOW, busy_timeout=0.2)
    finally:
        locker.execute("ROLLBACK")
        locker.close()
    assert digest(db) == before
    assert list((hermes_home / "gateway-pricing" / "backups").glob("state-*.db")) == []
    assert apply()["rows_updated"] == want_rows  # works once the writer is gone


def test_days_window(ready):
    result = service.backfill("gateway-pricing", days=1, now=SYNTH_NOW + 3 * 86400)
    assert result["rows_considered"] == 0


def test_override_rule_reprices_hermes_estimates(installed, write_settings, synthetic_db):
    write_settings(
        {
            "rules": [
                {
                    "id": "flat",
                    "match": {"base_url": "api.anthropic.com"},
                    "override": True,
                    "prices": {"input": 1, "output": 1, "cache_read": 1},
                }
            ]
        }
    )
    apply()
    direct = usage(synthetic_db)[("s-direct", "claude-opus-5-5", "", "https://api.anthropic.com")]
    assert direct["cost_source"] == "user_override"
    assert direct["estimated_cost_usd"] == pytest.approx((100 + 1000 + 100000) / 1e6)
    assert sessions(synthetic_db)["s-direct"]["pricing_version"] == VERSION
    actual = usage(synthetic_db)[("s-actual", "gpt-6.1-sol", "", "https://gateway.example.com/openai/v1")]
    assert actual["cost_status"] == "actual"  # invoices are never overridden
    assert apply()["rows_updated"] == 0


def test_draft_rules_are_not_saved(installed, write_settings, synthetic_db):
    write_settings({"rules": []})
    draft = [{"match": {"base_url": "127.0.0.1:9003"}, "free": True}]
    result = service.backfill("gateway-pricing", rules=draft, now=SYNTH_NOW)
    assert result["rows_updated"] == 1 and result["by_endpoint"][0]["endpoint"] == "127.0.0.1:9003"
    assert gp.service.saved_settings("gateway-pricing") == {"rules": []}


class TestRealFixture:
    """The anonymised 187-session database (local only; skipped in CI)."""

    def test_dry_run_apply_and_idempotence(self, installed, standard_settings, real_db, fixed_clock):
        fixed_clock(REAL_DB_NOW)
        before = digest(real_db)
        before_u = usage(real_db)
        dry = service.backfill("gateway-pricing", now=REAL_DB_NOW)
        assert digest(real_db) == before and dry["rows_updated"] > 10
        done = service.backfill("gateway-pricing", dry_run=False, confirm=True, now=REAL_DB_NOW)
        assert (done["rows_updated"], done["sessions_updated"]) == (dry["rows_updated"], dry["sessions_updated"])
        after_u = usage(real_db)
        for key, row in before_u.items():
            if row["cost_status"] in ("actual", "estimated", "included"):
                assert after_u[key] == row
            assert after_u[key]["actual_cost_usd"] == row["actual_cost_usd"]
        again = service.backfill("gateway-pricing", dry_run=False, confirm=True, now=REAL_DB_NOW)
        assert (again["rows_updated"], again["sessions_updated"]) == (0, 0)
        totals = {
            r["session_id"]: r["total"]
            for r in rows(
                real_db, "SELECT session_id, SUM(estimated_cost_usd) AS total FROM session_model_usage GROUP BY 1"
            )
        }
        for sid, session in sessions(real_db).items():
            if session["pricing_version"] == VERSION and sid in totals:
                assert session["estimated_cost_usd"] == pytest.approx(totals[sid], abs=1e-9)
