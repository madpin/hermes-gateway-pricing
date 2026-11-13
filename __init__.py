"""gateway-pricing: list-price cost estimates for endpoints Hermes cannot price.

``register(ctx)`` does two things and nothing else: installs the pricing wrap (see
``hermes_compat.py``) and registers the ``hermes gateway-pricing`` CLI command. Rules are read
live from ``plugins.entries.<plugin id>.settings`` of the active profile.
"""

from __future__ import annotations

# No module-level relative imports here: test runners import this file without a parent package.
__version__ = "0.2.1"  # kept equal to version.py and plugin.yaml by tests/test_api_cli.py

__all__ = ["__version__", "register"]


def register(ctx) -> None:
    from . import cli_commands, hermes_compat

    plugin_id = ctx.plugin_id
    hermes_compat.install(plugin_id)
    ctx.register_cli_command(
        "gateway-pricing",
        help=cli_commands.HELP,
        setup_fn=cli_commands.setup,
        handler_fn=lambda args: cli_commands.run(args, plugin_id),
        description="Inspect and test gateway-pricing rules, find unpriced endpoints, backfill old sessions.",
    )
