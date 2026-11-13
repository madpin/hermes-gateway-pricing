"""Rule parsing, validation and evaluation. Pure stdlib: no Hermes imports.

``parse_settings(raw)`` turns the plugin's settings (``enabled``, ``rules``, ``aliases``) into an
immutable :class:`RuleSet`. Invalid rules are skipped with one warning each and never affect the
other rules. ``RuleSet.decide`` is the hot-path entry point: first enabled rule that matches wins.
"""

from __future__ import annotations

import fnmatch
import logging
import math
import threading
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from .match import EndpointPattern, parse_endpoint, parse_pattern
from .vendors import canonical_vendor, glob_forms, infer_vendor, vendor_model_id

logger = logging.getLogger(__name__)

MODES = ("price_as", "prices", "free")
PRICE_KEYS = ("input", "output", "cache_read", "cache_write")
_RULE_KEYS = frozenset({"id", "enabled", "match", "price_as", "prices", "free", "multiplier", "override"})
_MATCH_KEYS = frozenset({"base_url", "models"})
_ONE = Decimal(1)
_MEMO_LIMIT = 4096


@dataclass(frozen=True)
class Rule:
    index: int
    id: str | None
    enabled: bool
    base_url: str
    pattern: EndpointPattern
    models: tuple[str, ...]
    mode: str
    vendor: str | None = None  # price_as: None means "auto"
    forced_model: str | None = None  # price_as: "vendor/model"
    prices: tuple[tuple[str, Decimal], ...] | None = None  # per 1M tokens, multiplier applied
    multiplier: Decimal = _ONE
    override: bool = False

    @property
    def label(self) -> Any:
        """What the API reports as ``matched_rule``: the rule's id, else its 0-based index."""
        return self.id if self.id is not None else self.index

    def matches_model(self, model: str) -> bool:
        if not self.models:
            return True
        forms = glob_forms(model)
        return any(fnmatch.fnmatchcase(form, glob) for glob in self.models for form in forms)


@dataclass(frozen=True)
class Decision:
    """What a matched rule does to one (model, base_url)."""

    rule: Rule
    model: str  # the model id after aliases
    mode: str
    vendor: str | None = None
    vendor_model: str | None = None

    @property
    def multiplier(self) -> Decimal:
        return self.rule.multiplier


def _rule_name(index: int, raw: Any) -> str:
    rid = raw.get("id") if isinstance(raw, Mapping) else None
    return f"rules[{index}]" + (f" ({rid})" if isinstance(rid, str) and rid else "")


def _decimal(value: Any, what: str, *, positive: bool = False) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError(f"{what} must be a number")
    try:
        number = Decimal(str(value).strip())
    except InvalidOperation:
        raise ValueError(f"{what} must be a number") from None
    if not number.is_finite() or (isinstance(value, float) and not math.isfinite(value)):
        raise ValueError(f"{what} must be finite")
    if number < 0 or (positive and number == 0):
        raise ValueError(f"{what} must be {'greater than 0' if positive else '0 or more'}")
    return number


def _parse_rule(index: int, raw: Any) -> Rule:
    if not isinstance(raw, Mapping):
        raise ValueError("a rule must be a mapping")
    unknown = sorted(str(k) for k in raw if k not in _RULE_KEYS)
    if unknown:
        raise ValueError(f"unknown key(s) {', '.join(unknown)}")
    rid = raw.get("id")
    if rid is not None and (not isinstance(rid, str) or not rid.strip()):
        raise ValueError("id must be a non-empty string")
    enabled = raw.get("enabled", True)
    override = raw.get("override", False)
    for key, value in (("enabled", enabled), ("override", override)):
        if not isinstance(value, bool):
            raise ValueError(f"{key} must be true or false")
    match = raw.get("match")
    if not isinstance(match, Mapping):
        raise ValueError("match must be a mapping with a base_url")
    unknown = sorted(str(k) for k in match if k not in _MATCH_KEYS)
    if unknown:
        raise ValueError(f"unknown match key(s) {', '.join(unknown)}")
    pattern = parse_pattern(match.get("base_url"))
    models = match.get("models", [])
    if isinstance(models, str):
        models = [models]
    if not isinstance(models, list) or not all(isinstance(m, str) and m.strip() for m in models):
        raise ValueError("match.models must be a list of non-empty glob strings")
    modes = [m for m in MODES if m in raw]
    if len(modes) != 1:
        raise ValueError(
            "exactly one of price_as, prices, free is required"
            if not modes
            else f"only one of price_as, prices, free is allowed (got {', '.join(modes)})"
        )
    mode = modes[0]
    multiplier = _decimal(raw.get("multiplier", 1), "multiplier", positive=True)
    rule = dict(
        index=index,
        id=rid.strip() if rid else None,
        enabled=enabled,
        base_url=str(match["base_url"]),
        pattern=pattern,
        models=tuple(m.strip().lower() for m in models),
        mode=mode,
        multiplier=multiplier,
        override=override,
    )
    if mode == "free":
        if raw["free"] is not True:
            raise ValueError("free must be true (remove it to disable the rule's free mode)")
        return Rule(**rule)
    if mode == "prices":
        prices = raw["prices"]
        if not isinstance(prices, Mapping):
            raise ValueError("prices must be a mapping like {input: 3.0, output: 15.0}")
        unknown = sorted(str(k) for k in prices if k not in PRICE_KEYS)
        if unknown:
            raise ValueError(f"unknown prices key(s) {', '.join(unknown)}")
        for key in ("input", "output"):
            if key not in prices:
                raise ValueError(f"prices.{key} is required")
        rates = tuple((k, _decimal(prices[k], f"prices.{k}") * multiplier) for k in PRICE_KEYS if k in prices)
        return Rule(**rule, prices=rates)
    target = raw["price_as"]
    if not isinstance(target, str) or not target.strip():
        raise ValueError("price_as must be 'auto', a vendor (anthropic, openai, google, ...) or 'vendor/model'")
    target = target.strip()
    if target.lower() == "auto":
        return Rule(**rule)
    vendor_part, _, model_part = target.partition("/")
    vendor = canonical_vendor(vendor_part)
    if vendor is None:
        raise ValueError(f"price_as: unknown vendor {vendor_part!r}")
    if "/" in target and not model_part.strip():
        raise ValueError("price_as: 'vendor/model' needs a model after the slash")
    return Rule(**rule, vendor=vendor, forced_model=model_part.strip() or None)


@dataclass(frozen=True, eq=False)
class RuleSet:
    """An immutable, parsed snapshot of the settings."""

    enabled: bool = True
    rules: tuple[Rule, ...] = ()
    aliases: Mapping[str, str] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()
    rules_total: int = 0
    _memo: dict = field(default_factory=dict, repr=False)

    @property
    def rules_valid(self) -> int:
        return len(self.rules)

    def alias(self, model: str) -> str:
        """One alias hop (exact id first, then case-insensitive); aliases never chain."""
        if not self.aliases:
            return model
        hit = self.aliases.get(model)
        if hit is None:
            hit = self.aliases.get((model or "").strip().lower())
        return hit if hit is not None else model

    def decide(self, model: str, base_url: str, hermes_mode: str = "unknown") -> Decision | None:
        """The decision for one call, or ``None`` to leave Hermes' own route alone. Memoised."""
        key = (model, base_url, hermes_mode == "unknown")
        try:
            return self._memo[key]
        except KeyError:
            pass
        decision = self._decide(model or "", base_url or "", key[2])
        if len(self._memo) >= _MEMO_LIMIT:
            self._memo.clear()
        self._memo[key] = decision
        return decision

    def explain(self, model: str, base_url: str, hermes_mode: str = "unknown") -> tuple[Decision | None, str]:
        """``decide`` plus a human-readable reason (for preview and the CLI; not memoised)."""
        if not self.enabled:
            return None, "plugin disabled in settings (enabled: false)"
        if parse_endpoint(base_url or "") is None:
            return None, "base URL has no usable host"
        decision = self._decide(model or "", base_url or "", hermes_mode == "unknown")
        if decision is not None:
            return decision, f"matched rule {decision.rule.label!r}"
        if hermes_mode != "unknown":
            return None, "Hermes already prices this route and no matching rule has override: true"
        return None, "no rule matches this model and base URL"

    def _decide(self, model: str, base_url: str, hermes_unknown: bool) -> Decision | None:
        if not self.enabled or not self.rules:
            return None
        endpoint = parse_endpoint(base_url)
        if endpoint is None:
            return None
        aliased = None
        for rule in self.rules:
            if not rule.enabled or not (hermes_unknown or rule.override):
                continue
            if not rule.pattern.matches(endpoint) or not rule.matches_model(model):
                continue
            if aliased is None:
                aliased = self.alias(model)
            if rule.mode != "price_as":
                return Decision(rule=rule, model=aliased, mode=rule.mode)
            vendor = rule.vendor or infer_vendor(rule.forced_model or aliased)
            if vendor is None:
                continue  # "auto" cannot tell the vendor: the rule does not apply to this model
            return Decision(
                rule=rule,
                model=aliased,
                mode="price_as",
                vendor=vendor,
                vendor_model=vendor_model_id(vendor, rule.forced_model or aliased),
            )
        return None


def parse_settings(raw: Mapping[str, Any] | None) -> RuleSet:
    """Build a :class:`RuleSet` from the plugin settings mapping. Never raises."""
    raw = raw if isinstance(raw, Mapping) else {}
    warnings: list[str] = []
    enabled = raw.get("enabled", True)
    if enabled is None:
        enabled = True
    if not isinstance(enabled, bool):
        warnings.append("enabled must be true or false; treating it as true")
        enabled = True
    raw_rules = raw.get("rules") or []
    if not isinstance(raw_rules, list):
        warnings.append("rules must be a list; ignoring it")
        raw_rules = []
    raw_aliases = raw.get("aliases") or {}
    aliases: dict[str, str] = {}
    if not isinstance(raw_aliases, Mapping):
        warnings.append("aliases must be a mapping of model id -> model id; ignoring it")
    else:
        for src, dst in raw_aliases.items():
            if isinstance(src, str) and isinstance(dst, str) and src.strip() and dst.strip():
                aliases[src.strip()] = dst.strip()
                aliases.setdefault(src.strip().lower(), dst.strip())
            else:
                warnings.append(f"aliases: {src!r} -> {dst!r} is not a string -> string pair; skipped")
    rules: list[Rule] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(raw_rules):
        try:
            rule = _parse_rule(index, item)
            if rule.id is not None:
                if rule.id in seen_ids:
                    raise ValueError(f"duplicate id {rule.id!r}")
                seen_ids.add(rule.id)
        except ValueError as exc:
            warnings.append(f"{_rule_name(index, item)}: {exc}; rule skipped")
            continue
        rules.append(rule)
    return RuleSet(
        enabled=enabled, rules=tuple(rules), aliases=aliases, warnings=tuple(warnings), rules_total=len(raw_rules)
    )


_logged: set[str] = set()
_logged_lock = threading.Lock()


def log_warnings_once(ruleset: RuleSet, prefix: str = "gateway-pricing") -> None:
    """Log each distinct settings problem once per process (not once per reload)."""
    with _logged_lock:
        fresh = [w for w in ruleset.warnings if w not in _logged]
        _logged.update(fresh)
    for warning in fresh:
        logger.warning("%s: %s", prefix, warning)
