"""Shared fixtures. Every test runs against its own temporary HERMES_HOME and without network.

The HERMES_HOME redirect happens at import time, before any Hermes module is imported, because
some Hermes modules compute paths from it at import.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import socket
import sys
import tempfile
import types
from pathlib import Path

import pytest
from helpers import PLUGIN_ID, REAL_DB, ROOT, STANDARD_ALIASES, STANDARD_RULES, SYNTH_NOW, build_synthetic_db

for _var in [v for v in os.environ if v.startswith(("HERMES_SESSION_", "_HERMES_", "HERMES_GATEWAY"))]:
    os.environ.pop(_var)
_SESSION_HOME = Path(tempfile.mkdtemp(prefix="gwp-home-"))
os.environ["HERMES_HOME"] = str(_SESSION_HOME)
# Test against a specific Hermes checkout: HERMES_SRC=/path/to/hermes-agent python -m pytest
if os.environ.get("HERMES_SRC"):
    sys.path.insert(0, str(Path(os.environ["HERMES_SRC"]).resolve()))


def _load_plugin_package():
    spec = importlib.util.spec_from_file_location(
        "gateway_pricing", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)]
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["gateway_pricing"] = module
    spec.loader.exec_module(module)
    return module


gp = _load_plugin_package()
importlib.import_module("gateway_pricing.service")
importlib.import_module("gateway_pricing.cli_commands")


@pytest.fixture(autouse=True)
def hermes_home(tmp_path, monkeypatch):
    home = tmp_path / "hermes-home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    from hermes_constants import get_hermes_home

    resolved = Path(get_hermes_home()).resolve()
    assert resolved == home.resolve(), f"tests must never use a real Hermes home (got {resolved})"
    return home


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    real_connect = socket.socket.connect

    def guarded(self, address):
        if self.family in (socket.AF_INET, socket.AF_INET6):
            raise RuntimeError(f"network access attempted in a test: {address!r}")
        return real_connect(self, address)

    monkeypatch.setattr(socket.socket, "connect", guarded)
    import agent.usage_pricing as up

    def refuse(*_args, **_kwargs):
        raise RuntimeError("Hermes metadata fetch attempted in a test")

    monkeypatch.setattr(up, "fetch_endpoint_model_metadata", refuse)
    monkeypatch.setattr(up, "fetch_model_metadata", refuse)


@pytest.fixture
def write_settings(hermes_home):
    import json

    def write(settings: dict, plugin_id: str = PLUGIN_ID) -> Path:
        path = hermes_home / "config.yaml"
        doc = {"plugins": {"enabled": [plugin_id], "entries": {plugin_id: {"settings": settings}}}}
        # JSON is valid YAML. The padding changes the file size on every write, so Hermes' config
        # cache (keyed on the file signature) notices edits made within the same mtime tick.
        write.count += 1
        path.write_text(json.dumps(doc, indent=2) + "\n" + "#" * write.count + "\n", encoding="utf-8")
        return path

    write.count = 0

    return write


@pytest.fixture
def installed():
    gp.hermes_compat.uninstall()
    result = gp.hermes_compat.install(PLUGIN_ID, interval=0)
    assert result["active"], result
    yield result
    gp.hermes_compat.uninstall()


@pytest.fixture
def standard_settings(write_settings):
    return write_settings({"enabled": True, "rules": STANDARD_RULES, "aliases": STANDARD_ALIASES})


@pytest.fixture
def synthetic_db(hermes_home) -> Path:
    return build_synthetic_db(hermes_home / "state.db")


@pytest.fixture
def real_db(hermes_home) -> Path:
    if not REAL_DB.is_file():
        pytest.skip("anonymised fixture .ref/sessions-sanitized.db is not available (local-only file)")
    target = hermes_home / "state.db"
    shutil.copyfile(REAL_DB, target)
    return target


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    """detect/backfill read ``time.time()``; pin it so live-session and day-window logic is stable."""

    def pin(now: float) -> None:
        clock = types.SimpleNamespace(time=lambda: now, monotonic=__import__("time").monotonic)
        monkeypatch.setattr(gp.backfill, "time", clock)
        monkeypatch.setattr(gp.detect, "time", clock)

    pin(SYNTH_NOW)
    return pin
