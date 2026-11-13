import argparse
import hashlib
import importlib.util
import io
import json
import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from helpers import PLUGIN_ID, REAL_DB_NOW, ROOT, SYNTH_NOW

import gateway_pricing as gp


@pytest.fixture
def client(installed):
    spec = importlib.util.spec_from_file_location("gwp_test_plugin_api", ROOT / "dashboard" / "plugin_api.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    app = FastAPI()
    app.include_router(module.router, prefix=f"/api/plugins/{module.PLUGIN_ID}")
    return TestClient(app)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ── detect ─────────────────────────────────────────────────────────────────────────────────────


def test_detect_groups_by_host_and_model(installed, write_settings, synthetic_db):
    write_settings({"rules": [{"id": "llama", "match": {"base_url": "127.0.0.1:9003"}, "free": True}]})
    found = gp.service.detected(PLUGIN_ID, days=30, now=SYNTH_NOW)
    by_key = {(r["host"], r["model"]): r for r in found}
    shim = by_key[("127.0.0.1:9001", "claude-opus-5-5")]
    # s-shim (2 rows), s-direct's aux row, s-live; s-nousage is grouped from its session columns
    assert shim["sessions"] == 4 and shim["matched_rule"] is None
    assert shim["suggestion"]["price_as"] == "anthropic" and shim["suggestion"]["confidence"] == "high"
    assert by_key[("127.0.0.1:9003", "Qwen3.6-35B-A3B-UD-Q4_K_M")]["matched_rule"] == "llama"
    gateway = by_key[("gateway.example.com:443", "chat-latest")]
    assert gateway["base_url"] == "https://gateway.example.com/openai/v1"
    assert gateway["suggestion"] is None or gateway["suggestion"]["confidence"] == "low"
    assert by_key[("amp", "glm-5.3-flash")]["suggestion"] is None
    totals = [r["input_tokens"] + r["output_tokens"] + r["cache_read_tokens"] for r in found]
    assert totals == sorted(totals, reverse=True)
    assert gp.service.detected(PLUGIN_ID, days=1, now=SYNTH_NOW + 5 * 86400) == []


def test_detect_on_the_real_fixture(installed, standard_settings, real_db):
    found = gp.service.detected(PLUGIN_ID, days=30, now=REAL_DB_NOW)
    hosts = {r["host"] for r in found}
    assert {"127.0.0.1:9001", "127.0.0.1:9002", "gateway.example.com:443"} <= hosts
    assert all(r["sessions"] >= 1 for r in found)
    local = [r for r in found if r["host"] == "127.0.0.1:9003"]
    assert local and all(r["matched_rule"] == "llama" for r in local)


def test_detect_fails_closed_on_a_bad_schema(installed, hermes_home):
    import sqlite3

    conn = sqlite3.connect(hermes_home / "state.db")
    conn.execute("CREATE TABLE sessions (id TEXT)")
    conn.close()
    with pytest.raises(gp.statedb.SchemaError):
        gp.service.detected(PLUGIN_ID)


# ── HTTP API ───────────────────────────────────────────────────────────────────────────────────


def test_api_status(client, standard_settings):
    body = client.get("/api/plugins/gateway-pricing/status", params={"profile": "default"}).json()
    assert body["active"] is True and body["reason"] == ""
    assert body["plugin_version"] == gp.version.__version__
    assert (body["rules_total"], body["rules_valid"], body["warnings"]) == (4, 4, [])
    assert body["wrapped"] == ["resolve_billing_route", "get_pricing_entry"]
    assert set(body) == {
        "active",
        "reason",
        "plugin_version",
        "hermes_version",
        "rules_total",
        "rules_valid",
        "warnings",
        "wrapped",
        "vendors",
    }


def test_api_status_lists_the_vendors_price_as_accepts(client, write_settings):
    body = client.get("/api/plugins/gateway-pricing/status").json()
    assert body["vendors"] == sorted(gp.vendors.KNOWN_VENDORS) and "anthropic" in body["vendors"]
    rules = [{"match": {"base_url": "x.example.com"}, "price_as": vendor} for vendor in body["vendors"]]
    ruleset = gp.rules.parse_settings({"rules": rules})
    assert (ruleset.rules_valid, ruleset.warnings) == (len(rules), ())


def test_api_status_reports_invalid_rules_and_inactive(client, write_settings):
    write_settings({"rules": [{"match": {"base_url": "x.example.com"}}]})
    body = client.get("/api/plugins/gateway-pricing/status").json()
    assert (body["rules_total"], body["rules_valid"]) == (1, 0) and "exactly one of" in body["warnings"][0]
    gp.hermes_compat.uninstall()
    body = client.get("/api/plugins/gateway-pricing/status").json()
    assert body["active"] is False and body["reason"]


def test_api_detected(client, standard_settings, synthetic_db):
    body = client.get("/api/plugins/gateway-pricing/detected", params={"days": 3650}).json()
    assert isinstance(body, list) and body
    assert set(body[0]) >= {
        "base_url",
        "host",
        "model",
        "sessions",
        "calls",
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "matched_rule",
        "suggestion",
    }
    assert client.get("/api/plugins/gateway-pricing/detected", params={"days": 0}).status_code == 422


def test_api_detected_without_state_db(client, standard_settings):
    assert client.get("/api/plugins/gateway-pricing/detected").status_code == 404


def test_api_preview_with_draft_rules(client, write_settings):
    write_settings({"rules": []})
    body = client.post(
        "/api/plugins/gateway-pricing/preview",
        json={
            "model": "chat-latest",
            "base_url": "https://gateway.example.com/openai/v1",
            "rules": [{"id": "gw", "match": {"base_url": "gateway.example.com"}, "price_as": "auto", "multiplier": 2}],
            "aliases": {"chat-latest": "gpt-6.1-sol"},
        },
    ).json()
    assert body["matched_rule"] == "gw" and body["mode"] == "price_as" and body["status"] == "estimated"
    assert body["route"] == {"provider": "openai", "model": "gpt-6.1-sol", "billing_mode": "official_docs_snapshot"}
    assert body["per_million"]["input"] == pytest.approx(4.0)  # 2.00 * 2
    # Default usage is 1M in + 1M out: above Hermes' 272K tier, so its "above" rates (4 + 15) apply, x2.
    assert body["amount_usd"] == pytest.approx(38.0)
    assert gp.service.saved_settings(PLUGIN_ID) == {"rules": []}  # drafts are never saved


def test_api_preview_no_match_and_errors(client, standard_settings):
    url = "/api/plugins/gateway-pricing/preview"
    body = client.post(url, json={"model": "m", "base_url": "https://other.example.net"}).json()
    assert (body["matched_rule"], body["status"], body["amount_usd"]) == (None, "unknown", None)
    assert "no rule" in body["reason"]
    assert client.post(url, json={"model": "", "base_url": "x"}).status_code == 400
    assert client.post(url, json={"model": "m", "base_url": "x", "usage": {"input": -1}}).status_code == 400
    custom = client.post(
        url,
        json={
            "model": "claude-opus-5-5",
            "base_url": "http://127.0.0.1:9001",
            "usage": {"input": 0, "output": 0, "cache_read": 1_000_000},
        },
    ).json()
    assert custom["amount_usd"] == pytest.approx(custom["per_million"]["cache_read"])


def test_api_backfill(client, standard_settings, synthetic_db, hermes_home):
    url = "/api/plugins/gateway-pricing/backfill"
    before = digest(synthetic_db)
    dry = client.post(url, json={}).json()
    assert dry["dry_run"] is True and dry["rows_updated"] == 8 and digest(synthetic_db) == before
    assert set(dry) >= {
        "dry_run",
        "rows_considered",
        "rows_updated",
        "sessions_updated",
        "delta_usd",
        "by_endpoint",
        "backup_path",
    }
    refused = client.post(url, json={"dry_run": False})
    assert refused.status_code == 409 and digest(synthetic_db) == before
    done = client.post(url, json={"dry_run": False, "confirm": True}).json()
    assert done["rows_updated"] == 8 and done["backup_path"].startswith(str(hermes_home))
    assert client.post(url, json={"dry_run": False, "confirm": True}).json()["rows_updated"] == 0
    assert client.post(url, json={"days": "x"}).status_code == 400


def test_api_never_returns_secrets(client, write_settings, synthetic_db):
    write_settings({"rules": [{"match": {"base_url": "gateway.example.com"}, "price_as": "auto"}]})
    blob = json.dumps(
        [
            client.get("/api/plugins/gateway-pricing/status").json(),
            client.get("/api/plugins/gateway-pricing/detected").json(),
        ]
    )
    assert not re.search(r"(api[_-]?key|secret|token\")", blob, re.I)


# ── CLI ────────────────────────────────────────────────────────────────────────────────────────


def cli(*argv):
    parser = argparse.ArgumentParser(prog="hermes gateway-pricing")
    gp.cli_commands.setup(parser)
    args = parser.parse_args(list(argv))
    out, err = io.StringIO(), io.StringIO()
    code = gp.cli_commands.run(args, PLUGIN_ID, out=out, err=err)
    return code, out.getvalue(), err.getvalue()


def test_cli_status(installed, standard_settings):
    code, out, _ = cli("status")
    assert code == 0 and "ACTIVE" in out and "4 valid of 4" in out
    code, out, _ = cli("status", "--json")
    assert json.loads(out)["active"] is True


def test_cli_status_inactive_exit_code(standard_settings):
    gp.hermes_compat.uninstall()
    code, out, _ = cli("status")
    assert code == 2 and "INACTIVE" in out


def test_cli_preview_detect_backfill(installed, standard_settings, synthetic_db):
    code, out, _ = cli("preview", "us.anthropic.claude-opus-5-5-v1:0", "http://127.0.0.1:9001")
    assert code == 0 and "claude-shim" in out and "anthropic / claude-opus-5-5" in out
    code, out, _ = cli("detect", "--days", "3650", "--json")
    assert code == 0 and isinstance(json.loads(out), list)
    before = digest(synthetic_db)
    code, out, _ = cli("backfill")
    assert code == 0 and "DRY RUN" in out and "--apply" in out and digest(synthetic_db) == before
    code, out, _ = cli("backfill", "--apply", "--json")
    assert code == 0 and json.loads(out)["rows_updated"] == 8
    code, out, err = cli("detect", "--days", "0")
    assert code == 1 and "days" in err


def test_register_wires_wrap_and_cli_only(write_settings):
    calls = []

    class Ctx:
        plugin_id = "renamed-install"

        def register_cli_command(self, name, help, setup_fn, handler_fn=None, description=""):
            calls.append((name, setup_fn, handler_fn))

    try:
        gp.register(Ctx())
        assert compat_state().cache.plugin_id == "renamed-install"
    finally:
        gp.hermes_compat.uninstall()
    assert [c[0] for c in calls] == ["gateway-pricing"]
    parser = argparse.ArgumentParser()
    calls[0][1](parser)
    assert parser.parse_args(["status"]).gateway_pricing_command == "status"


def compat_state():
    return gp.hermes_compat.installed_state()


def test_versions_agree():
    manifest = (ROOT / "plugin.yaml").read_text()
    assert re.search(r"^version: (\S+)$", manifest, re.M).group(1) == gp.version.__version__ == gp.__version__
    assert json.loads((ROOT / "dashboard" / "manifest.json").read_text())["version"] == gp.__version__
    assert gp.version.manifest_name() == "gateway-pricing"
    changelog = (ROOT / "CHANGELOG.md").read_text()
    assert f"## [{gp.__version__}]" in changelog and "## [0.1.0]" in changelog


def test_pure_modules_have_no_hermes_imports():
    import ast

    hermes_roots = {"agent", "hermes_cli", "hermes_constants", "hermes_state", "utils", "providers", "tools"}
    for name in ("match", "vendors", "rules", "pricing", "statedb", "detect", "backfill", "version"):
        tree = ast.parse((ROOT / f"{name}.py").read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert not {a.name.split(".")[0] for a in node.names} & hermes_roots, name
            elif isinstance(node, ast.ImportFrom) and not node.level:
                assert (node.module or "").split(".")[0] not in hermes_roots, name
