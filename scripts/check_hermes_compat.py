"""Check that the installed Hermes still offers every API gateway-pricing depends on.

Run it with the Python that runs Hermes (the plugin loads Hermes' ``agent.usage_pricing``)::

    python scripts/check_hermes_compat.py

Exit status 0: compatible. 1: something changed (the report says what). 2: Hermes not importable.
It only imports and inspects; it never patches Hermes, reads secrets or touches state.db.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_plugin():
    spec = importlib.util.spec_from_file_location(
        "gateway_pricing_check", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)]
    )
    package = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = package
    spec.loader.exec_module(package)
    return importlib.import_module(spec.name + ".hermes_compat")


def main() -> int:
    compat = _load_plugin()
    print(f"gateway-pricing {compat.__version__} compatibility check")
    problems = compat.check_contract()
    print(f"Hermes version: {compat.hermes_version()}")
    if problems and problems[0].startswith("cannot import agent.usage_pricing"):
        print(f"FAIL {problems[0]}")
        print("Run this script with the Python interpreter of your Hermes installation.")
        return 2
    problems += compat.check_plugin_api()
    checked = [f"agent.usage_pricing.{name}{params}" for name, params in compat.EXPECTED_SIGNATURES.items()]
    checked += [f"agent.usage_pricing.{cls} fields" for cls in compat.EXPECTED_FIELDS]
    checked += [
        "agent.usage_pricing.CostSource values",
        "module-global call sites",
        "hermes_cli.config.load_config_readonly",
        "hermes_constants.get_hermes_home",
        "hermes_cli.plugins.PluginContext.plugin_id / register_cli_command",
    ]
    if not problems:
        for item in checked:
            print(f"ok   {item}")
        print("Compatible: the pricing wrap can be installed.")
        return 0
    for problem in problems:
        print(f"FAIL {problem}")
    print(f"{len(problems)} problem(s): gateway-pricing will stay inactive (Hermes prices are left untouched).")
    return 1


if __name__ == "__main__":
    sys.exit(main())
