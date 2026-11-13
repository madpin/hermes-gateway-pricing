"""Plugin version and identity. Pure stdlib: no Hermes imports."""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

__version__ = "0.2.0"

PLUGIN_ROOT = Path(__file__).resolve().parent
PRICING_VERSION = f"gateway-pricing:{__version__}"


@lru_cache(maxsize=1)
def manifest_name() -> str:
    """``name`` from ``plugin.yaml``.

    For a top-level user plugin Hermes uses the manifest name as the plugin id (the settings key
    ``plugins.entries.<id>`` and the ``/api/plugins/<id>/`` mount), whatever directory the repo was
    cloned into. Code that has a ``ctx`` uses ``ctx.plugin_id`` instead; this is for the dashboard
    backend, which gets no ``ctx``.
    """
    text = (PLUGIN_ROOT / "plugin.yaml").read_text(encoding="utf-8-sig")
    found = re.search(r"^name:\s*['\"]?([A-Za-z0-9_.-]+)['\"]?\s*$", text, re.MULTILINE)
    if not found:
        raise RuntimeError("plugin.yaml has no top-level name")
    return found.group(1)
