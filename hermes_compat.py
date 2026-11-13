"""The ONLY module of this plugin that touches Hermes internals.

Hermes has no public hook for "price this endpoint", and ``ProviderProfile.get_usage_cost`` never
sees the base URL. So at load this module rebinds two attributes of ``agent.usage_pricing``:

* ``resolve_billing_route`` -- calls Hermes' original first; only when a configured rule matches
  (and Hermes said ``unknown``, unless the rule has ``override: true``) it returns a different
  route: the vendor's route (``price_as``), an included route (``free``) or a ``user_override``
  route (``prices``);
* ``get_pricing_entry`` -- returns the rule's own prices for ``prices`` rules and applies the
  rule's ``multiplier``; everything else is Hermes' original result.

Hermes' ``estimate_usage_cost`` and ``get_pricing_entry`` look both names up as module globals at
call time, so every consumer (including ``from agent.usage_pricing import estimate_usage_cost``)
picks the wrappers up. ``estimate_usage_cost`` itself is never replaced.

Guarantees: installed once (idempotent, marked on the wrapper itself so a second copy of the
plugin never double-wraps); if Hermes' API differs from what :func:`check_contract` expects,
nothing is patched and one WARNING is logged; any exception inside the plugin's code returns
Hermes' original result; no I/O and no INFO logging per call (settings are re-read at most every
``RELOAD_INTERVAL_SECONDS`` per profile, and only re-parsed when they changed).

``hermes plugins validate`` reports this rebind as "no core override" -- see the README section
"Why this is not in the plugin catalog".
"""

from __future__ import annotations

import contextvars
import dataclasses
import functools
import inspect
import json
import logging
import threading
import time
import typing
from collections.abc import Mapping
from decimal import Decimal
from pathlib import Path
from typing import Any

from .pricing import Priced, Usage
from .rules import Decision, RuleSet, log_warnings_once, parse_settings
from .version import PRICING_VERSION, __version__

logger = logging.getLogger(__name__)

MARK = "__gateway_pricing__"
RELOAD_INTERVAL_SECONDS = 2.0
WRAPPED = ("resolve_billing_route", "get_pricing_entry")

#: Every Hermes symbol the plugin depends on. Checked by :func:`check_contract`, the contract
#: tests and ``scripts/check_hermes_compat.py``.
EXPECTED_SIGNATURES = {
    "resolve_billing_route": ("model_name", "provider", "base_url"),
    "get_pricing_entry": ("model_name", "provider", "base_url", "api_key"),
    "estimate_usage_cost": ("model_name", "usage", "provider", "base_url", "api_key"),
}
_RATE_FIELDS = (
    "input_cost_per_million",
    "output_cost_per_million",
    "cache_read_cost_per_million",
    "cache_write_cost_per_million",
    "request_cost",
    "input_cost_per_million_above",
    "output_cost_per_million_above",
    "cache_read_cost_per_million_above",
    "cache_write_cost_per_million_above",
)
EXPECTED_FIELDS = {
    "BillingRoute": ("provider", "model", "base_url", "billing_mode"),
    "PricingEntry": _RATE_FIELDS + ("source", "pricing_version", "tier_threshold_tokens"),
    "CostResult": ("amount_usd", "status", "source", "pricing_version", "notes"),
    "CanonicalUsage": ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens"),
}
EXPECTED_COST_SOURCES = ("user_override", "official_docs_snapshot", "provider_models_api", "none")
_PER_MILLION_FIELDS = {
    "input": "input_cost_per_million",
    "output": "output_cost_per_million",
    "cache_read": "cache_read_cost_per_million",
    "cache_write": "cache_write_cost_per_million",
}

# Draft rules for preview/backfill (context-local, never leaks into other threads or requests).
_DRAFT: contextvars.ContextVar[RuleSet | None] = contextvars.ContextVar("gateway_pricing_draft", default=None)
# Fallback path: evaluate Hermes' original behaviour without any rule.
_BYPASS: contextvars.ContextVar[bool] = contextvars.ContextVar("gateway_pricing_bypass", default=False)

_install_lock = threading.Lock()
_last_failure: str | None = None
_hot_path_errors: set[str] = set()


class CompatError(RuntimeError):
    """The running Hermes does not offer what this plugin needs."""


# ── Hermes access ─────────────────────────────────────────────────────────────────────────────


def _pricing_module():
    import agent.usage_pricing as usage_pricing

    return usage_pricing


def hermes_home() -> Path:
    from hermes_constants import get_hermes_home

    return Path(get_hermes_home())


def state_db_path() -> Path:
    return hermes_home() / "state.db"


def backup_dir() -> Path:
    return hermes_home() / "gateway-pricing" / "backups"


def hermes_version() -> str:
    """The version Hermes' plugin loader checks ``requires_hermes`` against."""
    try:
        from hermes_cli.plugins_manifest import running_hermes_version

        version = str(running_hermes_version() or "")
        if version:
            return version
    except Exception:
        pass
    try:
        import hermes_cli

        version = str(getattr(hermes_cli, "__version__", "") or "")
        if version:
            return version
    except Exception:
        pass
    try:
        from importlib.metadata import version as dist_version

        return dist_version("hermes-agent")
    except Exception:
        return "unknown"


def read_settings(plugin_id: str) -> dict[str, Any]:
    """``plugins.entries.<plugin_id>.settings`` of the active profile (the same place ``ctx.get_config``
    reads). Uses Hermes' cached, profile-scoped config loader."""
    from hermes_cli.config import load_config_readonly

    node: Any = load_config_readonly() or {}
    for key in ("plugins", "entries", plugin_id, "settings"):
        node = node.get(key) if isinstance(node, Mapping) else None
    return dict(node) if isinstance(node, Mapping) else {}


def _unwrapped(fn: Any) -> Any:
    state = getattr(fn, MARK, None)
    if state is not None:
        return state.originals.get(getattr(fn, "__name__", ""), fn)
    return fn


def check_contract(usage_pricing: Any = None) -> list[str]:
    """Readable problems with the running Hermes, ``[]`` when everything the plugin needs is there."""
    problems: list[str] = []
    suffix = " -- Hermes changed an API gateway-pricing depends on"
    try:
        up = usage_pricing if usage_pricing is not None else _pricing_module()
    except Exception as exc:
        return [f"cannot import agent.usage_pricing ({type(exc).__name__}: {exc}){suffix}"]
    for name, params in EXPECTED_SIGNATURES.items():
        fn = _unwrapped(getattr(up, name, None))
        if not callable(fn):
            problems.append(f"agent.usage_pricing.{name} is missing{suffix}")
            continue
        try:
            names = tuple(inspect.signature(fn).parameters)
        except (TypeError, ValueError) as exc:
            problems.append(f"agent.usage_pricing.{name}: signature unreadable ({exc}){suffix}")
            continue
        if names[: len(params)] != params:
            problems.append(f"agent.usage_pricing.{name}{names} no longer starts with {params}{suffix}")
    for cls_name, wanted in EXPECTED_FIELDS.items():
        cls = getattr(up, cls_name, None)
        if cls is None or not dataclasses.is_dataclass(cls):
            problems.append(f"agent.usage_pricing.{cls_name} is missing or not a dataclass{suffix}")
            continue
        have = {f.name for f in dataclasses.fields(cls)}
        missing = [f for f in wanted if f not in have]
        if missing:
            problems.append(f"agent.usage_pricing.{cls_name} lacks field(s) {', '.join(missing)}{suffix}")
    sources = set(typing.get_args(getattr(up, "CostSource", None)) or ())
    missing_sources = [s for s in EXPECTED_COST_SOURCES if s not in sources]
    if missing_sources:
        problems.append(f"agent.usage_pricing.CostSource lacks {', '.join(missing_sources)}{suffix}")
    # The rebind only reaches Hermes' own callers if they look the names up as globals.
    for caller, callees in (
        ("get_pricing_entry", ("resolve_billing_route",)),
        ("estimate_usage_cost", ("resolve_billing_route", "get_pricing_entry")),
    ):
        code = getattr(_unwrapped(getattr(up, caller, None)), "__code__", None)
        absent = [c for c in callees if code is None or c not in code.co_names]
        if absent:
            problems.append(
                f"agent.usage_pricing.{caller} no longer calls {', '.join(absent)} through the module"
                f" globals, so the plugin could not reach it{suffix}"
            )
    try:
        from hermes_cli.config import load_config_readonly  # noqa: F401
        from hermes_constants import get_hermes_home  # noqa: F401
    except Exception as exc:
        problems.append(f"hermes_cli.config.load_config_readonly / hermes_constants.get_hermes_home: {exc}{suffix}")
    return problems


def check_plugin_api() -> list[str]:
    """Problems with the public plugin API ``register()`` uses (``PluginContext``). Reported by the
    contract tests and ``scripts/check_hermes_compat.py``; not a gate for the pricing wrap."""
    suffix = " -- Hermes changed an API gateway-pricing depends on"
    try:
        from hermes_cli.plugins import PluginContext
    except Exception as exc:
        return [f"cannot import hermes_cli.plugins.PluginContext ({type(exc).__name__}: {exc}){suffix}"]
    problems = []
    if not isinstance(inspect.getattr_static(PluginContext, "plugin_id", None), property):
        problems.append(f"PluginContext.plugin_id is no longer a property{suffix}")
    wanted = ("self", "name", "help", "setup_fn", "handler_fn", "description")
    try:
        names = tuple(inspect.signature(PluginContext.register_cli_command).parameters)
    except (AttributeError, TypeError, ValueError) as exc:
        return [*problems, f"PluginContext.register_cli_command is unusable ({exc}){suffix}"]
    if names[: len(wanted)] != wanted:
        problems.append(f"PluginContext.register_cli_command{names} no longer takes {wanted}{suffix}")
    return problems


# ── Settings snapshots (per profile) ──────────────────────────────────────────────────────────


@dataclasses.dataclass
class _Snapshot:
    fingerprint: str
    ruleset: RuleSet
    next_check: float


class SettingsCache:
    """Parsed rules per profile home. The hot path is a dict lookup and a clock read; the config is
    re-read at most every ``interval`` seconds and re-parsed only when it changed."""

    def __init__(self, plugin_id: str, interval: float = RELOAD_INTERVAL_SECONDS):
        self.plugin_id = plugin_id
        self.interval = interval
        self._by_home: dict[str, _Snapshot] = {}
        self._lock = threading.Lock()

    def current(self) -> RuleSet:
        home = str(hermes_home())
        snap = self._by_home.get(home)
        now = time.monotonic()
        if snap is not None and now < snap.next_check:
            return snap.ruleset
        return self._refresh(home, now)

    def invalidate(self) -> None:
        with self._lock:
            self._by_home.clear()

    def _refresh(self, home: str, now: float) -> RuleSet:
        with self._lock:
            snap = self._by_home.get(home)
            if snap is not None and now < snap.next_check:
                return snap.ruleset
            try:
                raw = read_settings(self.plugin_id)
                fingerprint = json.dumps(raw, sort_keys=True, default=repr)
            except Exception as exc:  # unreadable config: keep the last good rules, retry later
                _note_hot_path_error("settings", exc)
                if snap is not None:
                    snap.next_check = now + self.interval
                    return snap.ruleset
                raw, fingerprint = {}, "{}"
            if snap is not None and snap.fingerprint == fingerprint:
                snap.next_check = now + self.interval
                return snap.ruleset
            ruleset = parse_settings(raw)
            log_warnings_once(ruleset)
            self._by_home[home] = _Snapshot(fingerprint, ruleset, now + self.interval)
            return ruleset


# ── The wrappers ──────────────────────────────────────────────────────────────────────────────


@dataclasses.dataclass
class WrapState:
    plugin_id: str
    cache: SettingsCache
    originals: dict[str, Any]
    plugin_version: str = __version__

    def ruleset(self) -> RuleSet:
        draft = _DRAFT.get()
        return draft if draft is not None else self.cache.current()


def _note_hot_path_error(where: str, exc: BaseException) -> None:
    key = f"{where}:{type(exc).__name__}:{exc}"
    if key not in _hot_path_errors:
        _hot_path_errors.add(key)
        logger.warning("gateway-pricing: %s failed (%s: %s); using Hermes' own result", where, type(exc).__name__, exc)


def _scaled(entry: Any, multiplier: Decimal) -> Any:
    changes = {
        name: getattr(entry, name) * multiplier for name in _RATE_FIELDS if getattr(entry, name, None) is not None
    }
    return dataclasses.replace(entry, **changes)


def _make_wrappers(up: Any, state: WrapState):
    original_resolve = state.originals["resolve_billing_route"]
    original_get = state.originals["get_pricing_entry"]
    billing_route = up.BillingRoute
    pricing_entry = up.PricingEntry
    rule_entries: dict[Any, Any] = {}

    def decide(model_name: Any, base_url: Any, hermes_mode: str) -> Decision | None:
        if _BYPASS.get():
            return None
        return state.ruleset().decide(model_name or "", base_url, hermes_mode)

    def route_for(decision: Decision, route: Any) -> Any:
        if decision.mode == "free":
            return dataclasses.replace(route, billing_mode="subscription_included")
        if decision.mode == "prices":
            # A neutral provider for overridden first-party routes keeps Hermes' anthropic fast-mode /
            # openai ultrafast branches from swapping in their own snapshot entries.
            provider = route.provider if route.billing_mode == "unknown" else "custom"
            return billing_route(
                provider=provider, model=route.model, base_url=route.base_url, billing_mode="user_override"
            )
        return billing_route(
            provider=decision.vendor, model=decision.vendor_model, base_url="", billing_mode="official_docs_snapshot"
        )

    def entry_for(decision: Decision) -> Any:
        rule = decision.rule
        entry = rule_entries.get(rule)
        if entry is None:
            rates = dict(rule.prices or ())
            entry = pricing_entry(
                **{field: rates.get(key) for key, field in _PER_MILLION_FIELDS.items()},
                source="user_override",
                pricing_version=PRICING_VERSION,
            )
            if len(rule_entries) > 256:
                rule_entries.clear()
            rule_entries[rule] = entry
        return entry

    @functools.wraps(original_resolve)
    def resolve_billing_route(model_name, provider=None, base_url=None):
        route = original_resolve(model_name, provider=provider, base_url=base_url)
        if not base_url:
            return route
        try:
            decision = decide(model_name, base_url, route.billing_mode)
            return route if decision is None else route_for(decision, route)
        except Exception as exc:
            _note_hot_path_error("resolve_billing_route", exc)
            return route

    @functools.wraps(original_get)
    def get_pricing_entry(model_name, provider=None, base_url=None, api_key=None):
        decision = None
        if base_url and not _BYPASS.get():
            try:
                hermes_mode = original_resolve(model_name, provider=provider, base_url=base_url).billing_mode
                decision = decide(model_name, base_url, hermes_mode)
            except Exception as exc:
                _note_hot_path_error("get_pricing_entry", exc)
                decision = None
        if decision is None or decision.mode == "free":
            return original_get(model_name, provider=provider, base_url=base_url, api_key=api_key)
        try:
            if decision.mode == "prices":
                return entry_for(decision)
            entry = original_get(model_name, provider=provider, base_url=base_url, api_key=api_key)
            if entry is not None and decision.multiplier != 1:
                entry = _scaled(entry, decision.multiplier)
            return entry
        except Exception as exc:
            _note_hot_path_error("get_pricing_entry", exc)
            token = _BYPASS.set(True)
            try:
                return original_get(model_name, provider=provider, base_url=base_url, api_key=api_key)
            finally:
                _BYPASS.reset(token)

    # Mark the plugin's own wrapper functions so any copy of the plugin recognises them.
    resolve_billing_route.__gateway_pricing__ = state
    get_pricing_entry.__gateway_pricing__ = state
    return resolve_billing_route, get_pricing_entry


def installed_state(usage_pricing: Any = None) -> WrapState | None:
    """The live wrap state in this process, or ``None`` when Hermes' functions are not wrapped."""
    try:
        up = usage_pricing if usage_pricing is not None else _pricing_module()
    except Exception:
        return None
    states = [getattr(getattr(up, name, None), MARK, None) for name in WRAPPED]
    return states[0] if states[0] is not None and all(s is states[0] for s in states) else None


def install(plugin_id: str, *, interval: float = RELOAD_INTERVAL_SECONDS) -> dict[str, Any]:
    """Wrap Hermes' pricing functions once. Never raises; returns ``{active, reason, wrapped}``."""
    global _last_failure
    with _install_lock:
        try:
            up = _pricing_module()
        except Exception as exc:
            _last_failure = f"cannot import agent.usage_pricing ({type(exc).__name__}: {exc})"
            logger.warning("gateway-pricing: inactive, %s", _last_failure)
            return {"active": False, "reason": _last_failure, "wrapped": []}
        state = installed_state(up)
        if state is not None:
            state.cache.plugin_id = plugin_id
            return {"active": True, "reason": "", "wrapped": list(WRAPPED)}
        if any(getattr(getattr(up, name, None), MARK, None) is not None for name in WRAPPED):
            _last_failure = "agent.usage_pricing is partially wrapped by another copy of gateway-pricing"
            logger.warning("gateway-pricing: inactive, %s", _last_failure)
            return {"active": False, "reason": _last_failure, "wrapped": []}
        problems = check_contract(up)
        if problems:
            _last_failure = "; ".join(problems)
            logger.warning("gateway-pricing: inactive, Hermes pricing left unpatched: %s", _last_failure)
            return {"active": False, "reason": _last_failure, "wrapped": []}
        state = WrapState(
            plugin_id=plugin_id,
            cache=SettingsCache(plugin_id, interval),
            originals={name: getattr(up, name) for name in WRAPPED},
        )
        resolve_wrapper, get_wrapper = _make_wrappers(up, state)
        up.resolve_billing_route = resolve_wrapper
        up.get_pricing_entry = get_wrapper
        _last_failure = None
        return {"active": True, "reason": "", "wrapped": list(WRAPPED)}


def uninstall() -> bool:
    """Restore Hermes' original functions (tests and troubleshooting). True when something was restored."""
    with _install_lock:
        try:
            up = _pricing_module()
        except Exception:
            return False
        state = next((s for s in (getattr(getattr(up, n, None), MARK, None) for n in WRAPPED) if s), None)
        if state is None:
            return False
        up.get_pricing_entry = state.originals["get_pricing_entry"]
        up.resolve_billing_route = state.originals["resolve_billing_route"]
        return True


def activity() -> dict[str, Any]:
    """``{active, reason, wrapped}`` for the running process."""
    state = installed_state()
    if state is not None:
        return {"active": True, "reason": "", "wrapped": list(WRAPPED)}
    reason = _last_failure or "not installed in this process (the plugin's register() has not run here)"
    return {"active": False, "reason": reason, "wrapped": []}


# ── Evaluation shared by preview, detect and backfill ─────────────────────────────────────────


def evaluate(model: str, base_url: str, usage: Usage, ruleset: RuleSet, *, provider: str = "custom") -> Priced:
    """Price one call the way the running wrapper would, with ``ruleset`` instead of the saved rules.

    Routes no rule matches are NOT priced here: Hermes would query the endpoint's ``/models`` for
    them, and this plugin makes no network calls.
    """
    state = installed_state()
    if state is None:
        raise CompatError(activity()["reason"])
    up = _pricing_module()
    provider_arg = provider or None
    hermes_route = state.originals["resolve_billing_route"](model, provider=provider_arg, base_url=base_url or None)
    decision, reason = ruleset.explain(model, base_url, hermes_route.billing_mode)
    if decision is None:
        return Priced(
            reason=reason,
            route={
                "provider": hermes_route.provider,
                "model": hermes_route.model,
                "billing_mode": hermes_route.billing_mode,
            },
        )
    canonical = up.CanonicalUsage(
        input_tokens=usage.input,
        output_tokens=usage.output,
        cache_read_tokens=usage.cache_read,
        cache_write_tokens=usage.cache_write,
    )
    token = _DRAFT.set(ruleset)
    try:
        route = up.resolve_billing_route(model, provider=provider_arg, base_url=base_url)
        entry = up.get_pricing_entry(model, provider=provider_arg, base_url=base_url)
        cost = up.estimate_usage_cost(model, canonical, provider=provider_arg, base_url=base_url)
    finally:
        _DRAFT.reset(token)
    notes = [reason, *getattr(cost, "notes", ())]
    if decision.mode == "price_as" and entry is None:
        notes.append(f"Hermes has no {decision.vendor} price for {decision.vendor_model!r}")
    return Priced(
        matched_rule=decision.rule.label,
        override=decision.rule.override,
        mode=decision.mode,
        status=cost.status,
        source=cost.source,
        amount_usd=cost.amount_usd,
        per_million={k: getattr(entry, f, None) if entry is not None else None for k, f in _PER_MILLION_FIELDS.items()},
        route={"provider": route.provider, "model": route.model, "billing_mode": route.billing_mode},
        pricing_version=cost.pricing_version,
        reason="; ".join(n for n in notes if n),
    )
