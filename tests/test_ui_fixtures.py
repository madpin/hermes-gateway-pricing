"""The desktop UI tests run on JSON captured from the real backend; fail when it drifts.

The generator uses ``prices`` / ``free`` rules wherever a value matters, so every fixture is
compared exactly on any Hermes, except:

* ``SHAPE_ONLY``: fixtures whose values come from Hermes itself (a ``price_as`` preview priced from
  Hermes' bundled table, Hermes' own settings-field rendering). Their shape is compared (keys, list
  lengths, value kinds; a null stands in for any scalar, since Hermes may not price a model), plus
  the plugin-owned fields checked below;
* in ``detected``, a suggestion's ``confidence`` and ``reason``, which say whether Hermes has a list
  price for the model.

When the running Hermes reports the same, known version the fixtures were generated with, everything
is compared exactly. Regenerate with ``python tests/ui/gen_fixtures.py``.
"""

import importlib.util
import json
import re

from helpers import ROOT

FIXTURES = ROOT / "tests" / "ui" / "fixtures"
UNKNOWN_VERSIONS = {None, "", "unknown"}
OPTIONAL = {"settings-fields"}  # needs hermes_cli.plugins_settings (absent on old Hermes)
SHAPE_ONLY = {"preview-price-as", "settings-fields"}
HERMES_PRICED_SUGGESTION_KEYS = {"confidence", "reason"}


def _plugin_owned(name, value):
    """``value`` without the parts that depend on Hermes' bundled price table."""
    if name != "detected":
        return value
    return [
        {
            **row,
            "suggestion": row["suggestion"]
            and {k: v for k, v in row["suggestion"].items() if k not in HERMES_PRICED_SUGGESTION_KEYS},
        }
        for row in value
    ]


def _generator():
    spec = importlib.util.spec_from_file_location("gwp_gen_fixtures", ROOT / "tests" / "ui" / "gen_fixtures.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def same_shape(a, b, where="$"):
    """``None`` if ``a`` and ``b`` have the same structure, else the path where they differ."""
    if isinstance(a, dict) and isinstance(b, dict):
        if set(a) != set(b):
            return f"{where}: keys {sorted(set(a) ^ set(b))}"
        return next((d for k in sorted(a) if (d := same_shape(a[k], b[k], f"{where}.{k}"))), None)
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return f"{where}: {len(a)} items vs {len(b)}"
        return next(
            (d for i, (x, y) in enumerate(zip(a, b, strict=True)) if (d := same_shape(x, y, f"{where}[{i}]"))), None
        )
    scalar = (str, int, float, bool)
    if a is None or b is None:
        return None if (a is None or isinstance(a, scalar)) and (b is None or isinstance(b, scalar)) else f"{where}"
    numeric = all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (a, b))
    return None if numeric or type(a) is type(b) else f"{where}: {type(a).__name__} vs {type(b).__name__}"


def test_ui_fixtures_match_the_backend(hermes_home):
    gen = _generator()
    fresh = gen.build_fixtures(hermes_home)
    committed = {path.stem: json.loads(path.read_text(encoding="utf-8")) for path in FIXTURES.glob("*.json")}
    assert set(fresh) <= set(committed) and set(committed) - set(fresh) <= OPTIONAL, (
        "fixture files added or removed: run tests/ui/gen_fixtures.py"
    )
    recorded, running = committed["_meta"]["hermes_version"], fresh["_meta"]["hermes_version"]
    same_hermes = recorded == running and running not in UNKNOWN_VERSIONS
    for name, value in fresh.items():
        if name == "_meta":
            continue
        expected = json.loads(gen.dumps(value))
        if same_hermes:
            assert committed[name] == expected, f"{name}.json drifted: run tests/ui/gen_fixtures.py"
        elif name in SHAPE_ONLY:
            assert same_shape(committed[name], expected) is None, f"{name}.json changed shape"
        else:
            assert same_shape(committed[name], expected) is None, f"{name}.json changed shape"
            assert _plugin_owned(name, committed[name]) == _plugin_owned(name, expected), (
                f"{name}.json drifted: run tests/ui/gen_fixtures.py"
            )
    # Plugin-owned values that never depend on Hermes' price tables: always exact.
    for name in ("settings", "base-url-cases", "error-400", "error-409"):
        assert committed[name] == fresh[name], f"{name}.json drifted: run tests/ui/gen_fixtures.py"
    assert committed["status"]["vendors"] == fresh["status"]["vendors"]
    assert committed["status"]["plugin_version"] == fresh["status"]["plugin_version"]
    assert committed["status-warnings"]["warnings"] == fresh["status-warnings"]["warnings"]
    for key in ("matched_rule", "mode", "route", "warnings"):
        assert committed["preview-price-as"][key] == fresh["preview-price-as"][key], key
    if "settings-fields" in fresh:
        assert [(f["key"], f["type"], f["value"]) for f in committed["settings-fields"]] == [
            (f["key"], f["type"], f["value"]) for f in fresh["settings-fields"]
        ]


def test_shape_comparison():
    assert same_shape({"a": [1, None, "x"]}, {"a": [2.5, "y", None]}) is None
    assert same_shape({"a": 1}, {"b": 1}) == "$: keys ['a', 'b']"
    assert same_shape([1], [1, 2]) == "$: 1 items vs 2"
    assert same_shape({"a": "x"}, {"a": 1}) == "$.a: str vs int"
    assert same_shape({"a": None}, {"a": {"b": 1}}) == "$.a"


def test_ui_fixtures_are_public_safe():
    blob = "\n".join(path.read_text(encoding="utf-8") for path in FIXTURES.glob("*.json"))
    assert not re.search(r"(?<![\w$])/(?:Users|home|tmp|var|private)/", blob, re.I), "absolute path in a fixture"
    assert "$HERMES_HOME/gateway-pricing/backups/" in blob
