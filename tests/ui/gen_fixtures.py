"""Generate tests/ui/fixtures/*.json from the REAL backend, so the UI tests use real response shapes.

    python tests/ui/gen_fixtures.py          # rewrite the fixtures
    python tests/ui/gen_fixtures.py --check  # exit 1 when the committed files are stale

It drives ``dashboard/plugin_api.py`` through FastAPI's TestClient against a synthetic ``state.db``
(``tests/helpers.py``) in a temporary Hermes home: never your own data, never the network.
``tests/test_ui_fixtures.py`` regenerates them in memory and fails on drift.

Values that depend on the machine or on the Hermes release are normalised: ``hermes_version`` (also
recorded in ``_meta.json``), and the backup path and timestamp in ``backfill-applied.json``.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
import tempfile
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
FIXTURES = HERE / "fixtures"
HERMES_PLACEHOLDER = "0.0.0-fixture"
# Same ids and base URLs as helpers.STANDARD_RULES, but with explicit ``prices`` / ``free`` instead of
# ``price_as``: the backfill fixtures then do not depend on which Hermes (and bundled price table)
# generated them. ``price_as`` appears only in fixtures the drift test compares by shape.
FIXTURE_RULES = [
    {
        "id": "claude-shim",
        "match": {"base_url": "127.0.0.1:9001"},
        "prices": {"input": 5, "output": 25, "cache_read": 0.5},
    },
    {"id": "gemini-shim", "match": {"base_url": "127.0.0.1:9002"}, "prices": {"input": 0.5, "output": 3}},
    {
        "id": "gateway",
        "match": {"base_url": "gateway.example.com/openai"},
        "prices": {"input": 2, "output": 10, "cache_read": 0.2},
        "multiplier": 1.1,
    },
    {"id": "llama", "match": {"base_url": "127.0.0.1:9003"}, "free": True},
]
INVALID_RULE = {"id": "future", "match": {"base_url": "future.example.com"}, "price_as": "auto", "tier": "gold"}
# match.base_url inputs; the backend's verdict on each is recorded so the editor's validation can be
# checked against it (tests/ui/units.test.js).
BASE_URL_CASES = [
    "gateway.example.com",
    "gateway.example.com/openai",
    "gateway.example.com/openai/v1/",
    "127.0.0.1:9001",
    "https://gateway.example.com/v1",
    "http://127.0.0.1:9001",
    "*.example.com",
    "[::1]:8080",
    "localhost",
    "localhost:",
    "UPPER.Example.COM",
    "trailing.dot.example.com.",
    "under_score.example.com",
    "a b.example.com",
    "back\\slash.example.com",
    "user@gateway.example.com",
    "gateway.example.com?x=1",
    "gateway.example.com#frag",
    "gateway.example.com:99999",
    "gateway.example.com:abc",
    "*.127.0.0.1",
    "foo*.example.com",
    "*",
    "",
    "   ",
    "-bad.example.com",
    "bad-.example.com",
    "http://",
    "exa%mple.com",
]


def _package():
    """The plugin package (the copy the test session already loaded, else load it by path)."""
    if "gateway_pricing" in sys.modules:
        return sys.modules["gateway_pricing"]
    spec = importlib.util.spec_from_file_location(
        "gateway_pricing", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)]
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["gateway_pricing"] = module
    spec.loader.exec_module(module)
    for name in ("service", "detect", "backfill", "hermes_compat"):
        importlib.import_module(f"gateway_pricing.{name}")
    return module


def _helpers():
    sys.path.insert(0, str(ROOT / "tests"))
    try:
        import helpers
    finally:
        sys.path.remove(str(ROOT / "tests"))
    return helpers


def _client(gp):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    spec = importlib.util.spec_from_file_location("gwp_fixture_plugin_api", ROOT / "dashboard" / "plugin_api.py")
    api = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(api)
    app = FastAPI()
    app.include_router(api.router, prefix=f"/api/plugins/{api.PLUGIN_ID}")
    return TestClient(app), f"/api/plugins/{api.PLUGIN_ID}"


def build_fixtures(home: Path) -> dict[str, object]:
    """All fixtures, keyed by file stem. ``HERMES_HOME`` must already point at ``home``."""
    from hermes_constants import get_hermes_home

    if Path(get_hermes_home()).resolve() != home.resolve():
        raise RuntimeError("refusing to run: Hermes does not resolve the temporary home")
    gp = _package()
    helpers = _helpers()
    writes = [0]

    def write_settings(settings: dict) -> None:
        writes[0] += 1  # padding: Hermes' config cache keys on the file signature
        doc = {"plugins": {"enabled": [helpers.PLUGIN_ID], "entries": {helpers.PLUGIN_ID: {"settings": settings}}}}
        (home / "config.yaml").write_text(json.dumps(doc, indent=2) + "\n" + "#" * writes[0] + "\n", encoding="utf-8")

    clock = types.SimpleNamespace(time=lambda: helpers.SYNTH_NOW, monotonic=__import__("time").monotonic)
    saved_time = (gp.detect.time, gp.backfill.time)
    gp.detect.time = gp.backfill.time = clock
    gp.hermes_compat.uninstall()
    try:
        gp.hermes_compat.install(helpers.PLUGIN_ID, interval=0)
        helpers.build_synthetic_db(home / "state.db")
        client, base = _client(gp)
        standard = {"enabled": True, "rules": helpers.STANDARD_RULES, "aliases": helpers.STANDARD_ALIASES}
        out: dict[str, object] = {"settings": standard}

        def get(path, **params):
            response = client.get(base + path, params=params)
            return response.json()

        def post(path, body, status=200):
            response = client.post(base + path, json=body)
            assert response.status_code == status, (path, response.status_code, response.text)
            return response.json()

        # Settings with only the loopback shim priced, so detection has suggestions to offer.
        write_settings({"rules": [helpers.STANDARD_RULES[3]], "aliases": helpers.STANDARD_ALIASES})
        out["detected"] = get("/detected", days=30)
        write_settings({"rules": helpers.STANDARD_RULES, "aliases": helpers.STANDARD_ALIASES})
        out["detected-all-priced"] = [r for r in get("/detected", days=30) if r["matched_rule"] is not None]

        write_settings(standard)
        fields = _settings_fields(helpers.PLUGIN_ID)
        if fields is not None:
            # What plugins.manage list puts in settings_schema: the page reads the values from here.
            out["settings-fields"] = fields
        out["status"] = get("/status")
        out["preview-price-as"] = post(
            "/preview",
            {"model": "claude-opus-5-5", "base_url": "http://127.0.0.1:9001", "rules": helpers.STANDARD_RULES},
        )
        prices_rule = {"id": "flat", "match": {"base_url": "other.example.net"}, "prices": {"input": 3, "output": 15}}
        out["preview-prices"] = post(
            "/preview", {"model": "mystery-model", "base_url": "https://other.example.net/v1", "rules": [prices_rule]}
        )
        out["preview-free"] = post(
            "/preview", {"model": "Qwen3.6-35B-A3B-UD-Q4_K_M", "base_url": "http://127.0.0.1:9003/v1"}
        )
        out["preview-no-match"] = post("/preview", {"model": "mystery-model", "base_url": "https://other.example.net"})
        out["preview-invalid-draft"] = post(
            "/preview",
            {"model": "lint", "base_url": "https://lint.invalid", "rules": [*helpers.STANDARD_RULES, INVALID_RULE]},
        )
        out["error-400"] = post("/preview", {"model": "", "base_url": "x"}, status=400)

        write_settings({"enabled": True, "rules": FIXTURE_RULES, "aliases": helpers.STANDARD_ALIASES})
        out["backfill-dry-run"] = post("/backfill", {"dry_run": True, "days": 30})
        out["error-409"] = post("/backfill", {"dry_run": False}, status=409)
        applied = post("/backfill", {"dry_run": False, "confirm": True, "days": 30})
        applied["backup_path"] = _normalise_backup(applied.get("backup_path"), home)
        out["backfill-applied"] = applied

        write_settings({**standard, "rules": [*helpers.STANDARD_RULES, INVALID_RULE]})
        out["status-warnings"] = get("/status")
        gp.hermes_compat.uninstall()
        out["status-inactive"] = get("/status")
    finally:
        gp.hermes_compat.uninstall()
        gp.detect.time, gp.backfill.time = saved_time

    out["base-url-cases"] = [{"base_url": case, "valid": _accepts(gp, case)} for case in BASE_URL_CASES]
    hermes_version = out["status"]["hermes_version"]
    for name in ("status", "status-warnings", "status-inactive"):
        out[name]["hermes_version"] = HERMES_PLACEHOLDER
    out["_meta"] = {"hermes_version": hermes_version, "generator": "tests/ui/gen_fixtures.py"}
    return out


def _settings_fields(plugin_id: str) -> list | None:
    """Hermes' own rendering of this plugin's config_schema + current values (None on old Hermes)."""
    try:
        from hermes_cli.plugins_settings import plugin_settings_fields
    except ImportError:
        return None
    return plugin_settings_fields(plugin_id, ROOT)


def _accepts(gp, base_url: str) -> bool:
    try:
        gp.match.parse_pattern(base_url)
    except ValueError:
        return False
    return True


def _normalise_backup(path: str | None, home: Path) -> str | None:
    if not path:
        return path
    resolved = Path(path).resolve()
    try:
        rel = resolved.relative_to(home.resolve()).as_posix()
    except ValueError:
        raise RuntimeError(f"backup written outside the temporary home: {path}") from None
    return "$HERMES_HOME/" + re.sub(r"\d{8}T?\d{6}(\.\d+)?Z?", "20260101T000000Z", rel)


def dumps(value: object) -> str:
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


def main(argv: list[str]) -> int:
    for var in [v for v in os.environ if v.startswith(("HERMES_SESSION_", "_HERMES_", "HERMES_GATEWAY"))]:
        os.environ.pop(var)
    with tempfile.TemporaryDirectory(prefix="gwp-fixtures-") as tmp:
        home = Path(tmp) / "hermes-home"
        home.mkdir()
        os.environ["HERMES_HOME"] = str(home)
        fixtures = build_fixtures(home)
    stale = [name for name, value in fixtures.items() if _read(name) != dumps(value)]
    if "--check" in argv:
        print("stale: " + ", ".join(stale) if stale else "fixtures are up to date")
        return 1 if stale else 0
    FIXTURES.mkdir(exist_ok=True)
    for name, value in fixtures.items():
        (FIXTURES / f"{name}.json").write_text(dumps(value), encoding="utf-8")
    print(f"wrote {len(fixtures)} fixtures to {FIXTURES.relative_to(ROOT)}")
    return 0


def _read(name: str) -> str | None:
    path = FIXTURES / f"{name}.json"
    return path.read_text(encoding="utf-8") if path.is_file() else None


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
