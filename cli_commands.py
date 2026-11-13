"""``hermes gateway-pricing status | detect | preview | backfill`` (same core as the HTTP API)."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from typing import Any, TextIO

from . import service
from .backfill import BackfillError
from .hermes_compat import CompatError
from .statedb import SchemaError

HELP = "Price custom / loopback / gateway endpoints Hermes reports as unknown"


def setup(parser: argparse.ArgumentParser) -> None:
    sub = parser.add_subparsers(dest="gateway_pricing_command", metavar="{status,detect,preview,backfill}")
    sub.required = True
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="print machine-readable JSON")
    sub.add_parser("status", parents=[common], help="is the pricing wrap active, are the rules valid")
    det = sub.add_parser("detect", parents=[common], help="endpoints/models Hermes recorded without a price")
    det.add_argument("--days", type=int, default=30, help="look back this many days (default 30)")
    pre = sub.add_parser("preview", parents=[common], help="what the saved rules do for one model + base URL")
    pre.add_argument("model")
    pre.add_argument("base_url")
    pre.add_argument("--provider", default="custom", help="Hermes provider name (default: custom)")
    bf = sub.add_parser("backfill", parents=[common], help="recompute unknown costs in state.db (dry run by default)")
    bf.add_argument("--days", type=int, default=None, help="only sessions active in the last N days")
    bf.add_argument("--apply", action="store_true", help="write the changes (a backup is taken first)")


def _money(value: Any) -> str:
    return "n/a" if value is None else f"${value:,.4f}"


def _print_status(data: dict, out: TextIO) -> None:
    state = "ACTIVE" if data["active"] else "INACTIVE"
    out.write(f"gateway-pricing {data['plugin_version']} on Hermes {data['hermes_version']}: {state}\n")
    if data["reason"]:
        out.write(f"  reason: {data['reason']}\n")
    out.write(f"  wrapped: {', '.join(data['wrapped']) or '-'}\n")
    out.write(f"  rules: {data['rules_valid']} valid of {data['rules_total']}\n")
    for warning in data["warnings"]:
        out.write(f"  warning: {warning}\n")


def _print_detect(rows: list, out: TextIO) -> None:
    if not rows:
        out.write("Nothing unpriced found.\n")
        return
    for row in rows:
        rule = "-" if row["matched_rule"] is None else row["matched_rule"]
        out.write(
            f"{row['host']:<32} {row['model']:<36} sessions={row['sessions']:<4} calls={row['calls']:<5}"
            f" in={row['input_tokens']} out={row['output_tokens']} cache_read={row['cache_read_tokens']}"
            f" rule={rule}\n"
        )
        if row["suggestion"]:
            s = row["suggestion"]
            out.write(f"    suggestion ({s['confidence']}): {s.get('price_as') or s.get('mode')} -- {s['reason']}\n")


def _print_preview(data: dict, out: TextIO) -> None:
    out.write(
        f"matched rule: {data['matched_rule'] if data['matched_rule'] is not None else '-'}"
        f"  mode: {data['mode'] or '-'}\n"
    )
    out.write(
        f"status: {data['status']}  source: {data['source']}  amount: {_money(data['amount_usd'])}"
        " (for the given usage; default 1M input + 1M output)\n"
    )
    rates = ", ".join(f"{k}={'-' if v is None else v}" for k, v in data["per_million"].items())
    out.write(f"per 1M tokens: {rates}\n")
    out.write(f"route: {data['route'].get('provider')} / {data['route'].get('model')}\n")
    if data["reason"]:
        out.write(f"why: {data['reason']}\n")


def _print_backfill(data: dict, out: TextIO) -> None:
    out.write("DRY RUN -- nothing written\n" if data["dry_run"] else "APPLIED\n")
    out.write(
        f"rows considered: {data['rows_considered']}  usage rows updated: {data['rows_updated']}  "
        f"sessions updated: {data['sessions_updated']}  delta: {_money(data['delta_usd'])}\n"
    )
    out.write(
        f"skipped: {data['sessions_skipped_live']} live session(s), {data['sessions_skipped_priced']} "
        f"session(s) Hermes already priced, {data['rows_unpriced']} matched row(s) without a price\n"
    )
    for entry in data["by_endpoint"]:
        out.write(
            f"  {entry['endpoint']:<32} rule={entry['matched_rule']} rows={entry['rows']}"
            f" delta={_money(entry['delta_usd'])} models={', '.join(entry['models'])}\n"
        )
    if data["backup_path"]:
        out.write(f"backup: {data['backup_path']}\n")
    if data["dry_run"] and data["rows_updated"] + data["sessions_updated"]:
        out.write("Run again with --apply to write these changes.\n")


def run(args: argparse.Namespace, plugin_id: str, out: TextIO = sys.stdout, err: TextIO = sys.stderr) -> int:
    command = args.gateway_pricing_command
    handlers: dict[str, tuple[Callable[[], Any], Callable[[Any, TextIO], None]]] = {
        "status": (lambda: service.status(plugin_id), _print_status),
        "detect": (lambda: service.detected(plugin_id, days=args.days), _print_detect),
        "preview": (
            lambda: service.preview(plugin_id, args.model, args.base_url, provider=args.provider),
            _print_preview,
        ),
        "backfill": (
            lambda: service.backfill(plugin_id, dry_run=not args.apply, confirm=bool(args.apply), days=args.days),
            _print_backfill,
        ),
    }
    compute, render = handlers[command]
    try:
        data = compute()
    except (ValueError, CompatError, SchemaError, BackfillError, FileNotFoundError) as exc:
        if getattr(args, "json", False):
            out.write(json.dumps({"error": str(exc)}) + "\n")
        else:
            err.write(f"gateway-pricing {command}: {exc}\n")
        return 1
    if args.json:
        out.write(json.dumps(data, indent=2, default=str) + "\n")
    else:
        render(data, out)
    return 0 if command != "status" or data["active"] else 2
