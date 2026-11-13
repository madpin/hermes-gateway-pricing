"""Recompute costs Hermes recorded as unknown. Pure stdlib (sqlite3): no Hermes imports.

Safety rules (see README "Backfill"):

* dry run by default, on a read-only connection; writing needs ``dry_run=False`` AND ``confirm=True``;
* only ``session_model_usage`` rows with ``cost_status`` NULL / '' / 'unknown' and some tokens are
  repriced; 'estimated' / 'included' rows only when the matching rule has ``override: true``;
  'actual' rows (provider invoices) are never touched, and ``actual_cost_usd`` is never written;
* a session total becomes the sum of its usage rows; a session with no usage rows is priced
  from its own token columns; sessions that look live (no ``ended_at`` and activity within
  10 minutes) are skipped, because Hermes may overwrite their totals;
* before the first write: online-backup copy of the database (5 newest kept), then
  ``BEGIN IMMEDIATE``; if anyone wrote between the backup and the lock, start over; the plan is
  recomputed inside the transaction, and any error rolls everything back;
* idempotent: a row is written only when its (cost, status, source) actually changes.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from .match import parse_endpoint
from .pricing import Priced, Usage
from .statedb import check_schema, connect_ro

PriceFn = Callable[[str, str, str, Usage], Priced]

LIVE_WINDOW_SECONDS = 600
KEEP_BACKUPS = 5
_REPRICEABLE = ("estimated", "included")
_EPSILON = 1e-12


class BackfillError(RuntimeError):
    """A refusal or failure; nothing was written."""


@dataclass
class _Change:
    table: str  # "session_model_usage" | "sessions"
    key: Any  # rowid | session id
    endpoint: str
    model: str
    rule: Any
    old_cost: float
    new_cost: float
    status: str
    source: str


@dataclass
class _Plan:
    rows_considered: int = 0
    rows_unpriced: int = 0
    sessions_skipped_live: int = 0
    sessions_skipped_priced: int = 0
    changes: list[_Change] = field(default_factory=list)

    @property
    def usage_changes(self) -> list[_Change]:
        return [c for c in self.changes if c.table == "session_model_usage"]

    @property
    def session_changes(self) -> list[_Change]:
        return [c for c in self.changes if c.table == "sessions"]


def _tokens(row) -> Usage:
    return Usage(
        input=int(row["input_tokens"] or 0),
        output=int(row["output_tokens"] or 0),
        cache_read=int(row["cache_read_tokens"] or 0),
        cache_write=int(row["cache_write_tokens"] or 0),
    )


def _endpoint(url: str) -> str:
    parsed = parse_endpoint(url or "")
    return parsed.display if parsed else (url or "")


def _same(a: float | None, b: float) -> bool:
    return a is not None and abs(float(a) - b) <= _EPSILON


def _plan(conn: sqlite3.Connection, price: PriceFn, *, now: float, days: int | None, pricing_version: str) -> _Plan:
    plan = _Plan()
    cutoff = None if days is None else now - days * 86400
    usage_by_session: dict[str, list] = {}
    for row in conn.execute(
        "SELECT rowid AS rid, session_id, model, billing_provider, billing_base_url, input_tokens, output_tokens,"
        " cache_read_tokens, cache_write_tokens, estimated_cost_usd, cost_status, cost_source, last_seen"
        " FROM session_model_usage ORDER BY session_id, rowid"
    ):
        usage_by_session.setdefault(row["session_id"], []).append(row)
    sessions = conn.execute(
        "SELECT id, model, billing_provider, billing_base_url, started_at, ended_at, last_activity_at,"
        " input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, estimated_cost_usd, cost_status,"
        " cost_source, pricing_version FROM sessions ORDER BY started_at, id"
    ).fetchall()
    for session in sessions:
        rows = usage_by_session.get(session["id"], [])
        activity = max(
            [float(session["started_at"] or 0), float(session["last_activity_at"] or 0)]
            + [float(r["last_seen"] or 0) for r in rows]
        )
        if cutoff is not None and activity < cutoff:
            continue
        if session["ended_at"] is None and now - activity < LIVE_WINDOW_SECONDS:
            plan.sessions_skipped_live += 1
            continue
        _plan_session(plan, session, rows, price, pricing_version)
    return plan


def _plan_session(plan: _Plan, session, rows: list, price: PriceFn, pricing_version: str) -> None:
    session_status = session["cost_status"] or ""
    if session_status == "actual":
        return
    if not rows:
        usage = _tokens(session)
        if session_status not in ("", "unknown") or usage.total == 0:
            return
        plan.rows_considered += 1
        result = price(
            session["model"] or "", session["billing_base_url"] or "", session["billing_provider"] or "", usage
        )
        if not result.priced:
            plan.rows_unpriced += result.matched_rule is not None
            return
        new = float(result.amount_usd)
        plan.changes.append(
            _Change(
                "sessions",
                session["id"],
                _endpoint(session["billing_base_url"]),
                session["model"] or "",
                result.matched_rule,
                float(session["estimated_cost_usd"] or 0),
                new,
                result.status,
                result.source,
            )
        )
        return
    # (cost, status, source) of every row after this run; changed rows collected separately.
    after: list[tuple[float, str, str, Usage]] = []
    changed: list[tuple[_Change, bool]] = []
    for row in rows:
        usage = _tokens(row)
        status = row["cost_status"] or ""
        old = float(row["estimated_cost_usd"] or 0)
        current = (old, status, row["cost_source"] or "", usage)
        if usage.total == 0 or status == "actual" or (status not in ("", "unknown") and status not in _REPRICEABLE):
            after.append(current)
            continue
        result = price(row["model"] or "", row["billing_base_url"] or "", row["billing_provider"] or "", usage)
        if result.matched_rule is None or (status in _REPRICEABLE and not result.override):
            after.append(current)
            continue
        plan.rows_considered += 1
        if not result.priced:
            plan.rows_unpriced += 1
            after.append(current)
            continue
        new = float(result.amount_usd)
        after.append((new, result.status, result.source, usage))
        if _same(old, new) and status == result.status and (row["cost_source"] or "") == result.source:
            continue
        changed.append(
            (
                _Change(
                    "session_model_usage",
                    row["rid"],
                    _endpoint(row["billing_base_url"]),
                    row["model"] or "",
                    result.matched_rule,
                    old,
                    new,
                    result.status,
                    result.source,
                ),
                result.override,
            )
        )
    plan.changes.extend(change for change, _override in changed)
    if not changed:
        return
    if session_status not in ("", "unknown") and not any(override for _change, override in changed):
        plan.sessions_skipped_priced += 1  # Hermes priced this session; its rows changed, its total stays
        return
    total = sum(cost for cost, _s, _src, _u in after)
    priced = [
        (cost, s, src) for cost, s, src, usage in after if usage.total and s in ("estimated", "actual", "included")
    ]
    unknown_left = any(usage.total and s not in ("estimated", "actual", "included") for _c, s, _src, usage in after)
    if any(s in ("estimated", "actual") for _c, s, _src in priced):
        status = "estimated"
    elif priced and not unknown_left:
        status = "included"
    else:
        status = session_status
    source = max(priced, key=lambda p: p[0])[2] if priced else (session["cost_source"] or "")
    if (
        _same(session["estimated_cost_usd"], total)
        and status == session_status
        and source == (session["cost_source"] or "")
        and session["pricing_version"] == pricing_version
    ):
        return
    plan.changes.append(
        _Change(
            "sessions",
            session["id"],
            _endpoint(session["billing_base_url"]),
            session["model"] or "",
            None,
            float(session["estimated_cost_usd"] or 0),
            total,
            status,
            source,
        )
    )


def _summary(plan: _Plan, *, dry_run: bool, backup_path: Path | None) -> dict[str, Any]:
    by_endpoint: dict[tuple, dict[str, Any]] = {}
    delta = Decimal(0)
    for change in plan.changes:
        if change.table == "sessions" and change.rule is None:
            continue  # a total recomputed from rows already counted
        step = Decimal(str(change.new_cost)) - Decimal(str(change.old_cost))
        delta += step
        entry = by_endpoint.setdefault(
            (change.endpoint, change.rule),
            {
                "endpoint": change.endpoint,
                "matched_rule": change.rule,
                "models": set(),
                "rows": 0,
                "delta_usd": Decimal(0),
            },
        )
        entry["models"].add(change.model)
        entry["rows"] += 1
        entry["delta_usd"] += step
    endpoints = sorted(by_endpoint.values(), key=lambda e: -e["delta_usd"])
    return {
        "dry_run": dry_run,
        "rows_considered": plan.rows_considered,
        "rows_updated": len(plan.usage_changes),
        "sessions_updated": len(plan.session_changes),
        "rows_unpriced": plan.rows_unpriced,
        "sessions_skipped_live": plan.sessions_skipped_live,
        "sessions_skipped_priced": plan.sessions_skipped_priced,
        "delta_usd": float(delta),
        "by_endpoint": [{**e, "models": sorted(e["models"]), "delta_usd": float(e["delta_usd"])} for e in endpoints],
        "backup_path": str(backup_path) if backup_path else None,
    }


def _backup(conn: sqlite3.Connection, backup_dir: Path) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    target = backup_dir / f"state-{stamp}.db"
    partial = target.with_suffix(".db.partial")
    dest = sqlite3.connect(partial)
    try:
        conn.backup(dest)
    finally:
        dest.close()
    partial.replace(target)
    return target


def _prune(backup_dir: Path, keep: int) -> None:
    backups = sorted(backup_dir.glob("state-*.db"))
    for old in backups[:-keep] if keep > 0 else backups:
        old.unlink(missing_ok=True)


def _apply(conn: sqlite3.Connection, plan: _Plan, pricing_version: str) -> None:
    for change in plan.changes:
        if change.table == "session_model_usage":
            conn.execute(
                "UPDATE session_model_usage SET estimated_cost_usd = ?, cost_status = ?, cost_source = ?"
                " WHERE rowid = ?",
                (change.new_cost, change.status, change.source, change.key),
            )
        else:
            conn.execute(
                "UPDATE sessions SET estimated_cost_usd = ?, cost_status = ?, cost_source = ?,"
                " pricing_version = ? WHERE id = ?",
                (change.new_cost, change.status, change.source, pricing_version, change.key),
            )


def run_backfill(
    db_path: Path,
    price: PriceFn,
    *,
    backup_dir: Path,
    pricing_version: str,
    dry_run: bool = True,
    confirm: bool = False,
    days: int | None = None,
    now: float | None = None,
    busy_timeout: float = 5.0,
    keep_backups: int = KEEP_BACKUPS,
) -> dict[str, Any]:
    """Plan (and, with ``dry_run=False, confirm=True``, apply) a backfill. Returns the summary."""
    if days is not None and (isinstance(days, bool) or not isinstance(days, int) or days < 1):
        raise BackfillError("days must be a positive integer")
    if not dry_run and confirm is not True:
        raise BackfillError("a non-dry-run backfill needs confirm=true (CLI: --apply); nothing was written")
    now = time.time() if now is None else now
    if dry_run:
        conn = connect_ro(db_path, timeout=busy_timeout)
        try:
            return _summary(
                _plan(conn, price, now=now, days=days, pricing_version=pricing_version), dry_run=True, backup_path=None
            )
        finally:
            conn.close()

    path = Path(db_path).resolve()
    if not path.is_file():
        raise BackfillError(f"no state.db at {path}")
    conn = sqlite3.connect(path, timeout=busy_timeout, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute(f"PRAGMA busy_timeout = {int(busy_timeout * 1000)}")
        check_schema(conn)
        plan = _plan(conn, price, now=now, days=days, pricing_version=pricing_version)
        if not plan.changes:
            return _summary(plan, dry_run=False, backup_path=None)
        for _attempt in range(3):
            version = conn.execute("PRAGMA data_version").fetchone()[0]
            backup_path = None
            try:
                backup_path = _backup(conn, backup_dir)
                conn.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError as exc:
                if backup_path is not None:
                    backup_path.unlink(missing_ok=True)
                raise BackfillError(
                    f"state.db is busy ({exc}); nothing was written. Retry when Hermes is idle."
                ) from exc
            if conn.execute("PRAGMA data_version").fetchone()[0] != version:
                conn.execute("ROLLBACK")  # someone wrote after the backup: take a fresh one
                backup_path.unlink(missing_ok=True)
                continue
            try:
                check_schema(conn)
                plan = _plan(conn, price, now=now, days=days, pricing_version=pricing_version)
                _apply(conn, plan, pricing_version)
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            _prune(backup_dir, keep_backups)
            return _summary(plan, dry_run=False, backup_path=backup_path)
        raise BackfillError("state.db kept changing while backing it up; nothing was written. Retry later.")
    except sqlite3.Error as exc:
        raise BackfillError(f"backfill failed and was rolled back: {exc}") from exc
    finally:
        conn.close()
