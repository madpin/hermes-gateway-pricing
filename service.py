"""Operations shared by the CLI and the HTTP backend: status, detect, preview, backfill.

Everything here works on the ACTIVE profile (the Hermes home of the current request / process).
Hermes access goes through :mod:`hermes_compat`; detection and backfill themselves are pure.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from . import hermes_compat
from .backfill import run_backfill
from .detect import detect
from .match import parse_endpoint
from .pricing import Priced, Usage, priced_to_json
from .rules import RuleSet, parse_settings
from .vendors import KNOWN_VENDORS, infer_vendor, vendor_model_id
from .version import PRICING_VERSION, __version__

_LOOPBACK = ("localhost", "127.0.0.1", "::1")


def saved_settings(plugin_id: str) -> dict[str, Any]:
    return hermes_compat.read_settings(plugin_id)


def ruleset_from(plugin_id: str, rules: list | None = None, aliases: Mapping | None = None) -> RuleSet:
    """The saved rules, with draft ``rules`` / ``aliases`` substituted when given."""
    settings = saved_settings(plugin_id)
    if rules is not None:
        settings["rules"] = rules
        settings["enabled"] = True  # previewing drafts is explicit
    if aliases is not None:
        settings["aliases"] = aliases
    return parse_settings(settings)


def status(plugin_id: str) -> dict[str, Any]:
    ruleset = parse_settings(saved_settings(plugin_id))
    live = hermes_compat.activity()
    warnings = list(ruleset.warnings)
    if not ruleset.enabled:
        warnings.insert(0, "disabled in settings (enabled: false): Hermes prices are left untouched")
    return {
        "active": live["active"],
        "reason": live["reason"],
        "plugin_version": __version__,
        "hermes_version": hermes_compat.hermes_version(),
        "rules_total": ruleset.rules_total,
        "rules_valid": ruleset.rules_valid,
        "warnings": warnings,
        "wrapped": live["wrapped"],
        # The vendors a rule's price_as accepts (besides "auto" and vendor/model): the desktop
        # settings page builds its vendor list from this instead of keeping a second copy.
        "vendors": sorted(KNOWN_VENDORS),
    }


def preview(
    plugin_id: str,
    model: str,
    base_url: str,
    *,
    rules: list | None = None,
    aliases: Mapping | None = None,
    usage: Mapping | None = None,
    provider: str = "custom",
) -> dict:
    if not isinstance(model, str) or not model.strip():
        raise ValueError("model must be a non-empty string")
    if not isinstance(base_url, str) or not base_url.strip():
        raise ValueError("base_url must be a non-empty string")
    if rules is not None and not isinstance(rules, list):
        raise ValueError("rules must be a list of draft rules")
    if aliases is not None and not isinstance(aliases, Mapping):
        raise ValueError("aliases must be a mapping")
    ruleset = ruleset_from(plugin_id, rules, aliases)
    result = hermes_compat.evaluate(
        model.strip(), base_url.strip(), Usage.from_mapping(usage), ruleset, provider=provider
    )
    body = priced_to_json(result)
    body["warnings"] = list(ruleset.warnings)
    return body


def _suggest(model: str, base_url: str, aliases: Mapping[str, str]) -> dict | None:
    if not base_url.lower().startswith(("http://", "https://")) and "://" in base_url:
        return None  # process:// / moa:// style routes: subscriptions or local orchestration
    endpoint = parse_endpoint(base_url)
    if endpoint is None:
        return None
    aliased = aliases.get(model, model)
    vendor = infer_vendor(aliased)
    if vendor is None:
        if endpoint.host in _LOOPBACK:
            return {
                "mode": "free",
                "price_as": None,
                "confidence": "low",
                "reason": "loopback endpoint serving a model with no known vendor; if this is local "
                "inference, a 'free: true' rule records it as included ($0)",
            }
        return None
    probe = parse_settings(
        {
            "rules": [{"match": {"base_url": endpoint.display + endpoint.path}, "price_as": "auto"}],
            "aliases": dict(aliases),
        }
    )
    try:
        priced = hermes_compat.evaluate(model, base_url, Usage(input=1_000_000, output=1_000_000), probe)
    except hermes_compat.CompatError:
        priced = Priced()
    target = vendor_model_id(vendor, aliased)
    if priced.priced:
        return {
            "mode": "price_as",
            "price_as": vendor,
            "confidence": "high",
            "reason": f"model id looks like {vendor}; Hermes has a {vendor} list price for {target!r}",
        }
    return {
        "mode": "price_as",
        "price_as": vendor,
        "confidence": "low",
        "reason": f"model id looks like {vendor}, but Hermes has no {vendor} price for {target!r} "
        "(add an alias or explicit prices)",
    }


def detected(plugin_id: str, days: int = 30, *, now: float | None = None) -> list[dict]:
    ruleset = parse_settings(saved_settings(plugin_id))
    return detect(
        hermes_compat.state_db_path(),
        ruleset,
        lambda model, url: _suggest(model, url, ruleset.aliases),
        days=days,
        now=now,
    )


def backfill(
    plugin_id: str,
    *,
    dry_run: bool = True,
    days: int | None = None,
    rules: list | None = None,
    confirm: bool = False,
    now: float | None = None,
    busy_timeout: float = 5.0,
) -> dict:
    if not isinstance(dry_run, bool) or not isinstance(confirm, bool):
        raise ValueError("dry_run and confirm must be true or false")
    if rules is not None and not isinstance(rules, list):
        raise ValueError("rules must be a list of draft rules")
    ruleset = ruleset_from(plugin_id, rules)
    if hermes_compat.installed_state() is None:
        raise hermes_compat.CompatError(hermes_compat.activity()["reason"])

    def price(model: str, base_url: str, provider: str, usage: Usage) -> Priced:
        return hermes_compat.evaluate(model, base_url, usage, ruleset, provider=provider or "custom")

    result = run_backfill(
        hermes_compat.state_db_path(),
        price,
        backup_dir=hermes_compat.backup_dir(),
        pricing_version=PRICING_VERSION,
        dry_run=dry_run,
        confirm=confirm,
        days=days,
        now=now,
        busy_timeout=busy_timeout,
    )
    result["warnings"] = list(ruleset.warnings)
    return result
