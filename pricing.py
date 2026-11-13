"""Value types shared by preview, detection and backfill. Pure stdlib: no Hermes imports."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

ONE_MILLION = 1_000_000


@dataclass(frozen=True)
class Usage:
    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any] | None) -> Usage:
        """``{input, output, cache_read, cache_write}``; missing keys are 0, negatives are refused."""
        if raw is None:
            return cls(input=ONE_MILLION, output=ONE_MILLION)
        if not isinstance(raw, Mapping):
            raise ValueError("usage must be a mapping like {input: 1000000, output: 1000000}")
        unknown = sorted(str(k) for k in raw if k not in ("input", "output", "cache_read", "cache_write"))
        if unknown:
            raise ValueError(f"usage: unknown key(s) {', '.join(unknown)}")
        values = {}
        for key in ("input", "output", "cache_read", "cache_write"):
            value = raw.get(key, 0)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"usage.{key} must be a non-negative integer")
            values[key] = value
        return cls(**values)

    @property
    def total(self) -> int:
        return self.input + self.output + self.cache_read + self.cache_write


@dataclass(frozen=True)
class Priced:
    """The outcome of evaluating one (model, base_url, usage) against a rule set."""

    matched_rule: Any = None  # rule id, else 0-based index, else None
    override: bool = False
    mode: str | None = None  # price_as | prices | free | None (no rule)
    status: str = "unknown"  # Hermes CostStatus: estimated | included | unknown
    source: str = "none"  # Hermes CostSource
    amount_usd: Decimal | None = None
    per_million: Mapping[str, Decimal | None] = field(default_factory=dict)
    route: Mapping[str, str] = field(default_factory=dict)
    pricing_version: str | None = None
    reason: str = ""

    @property
    def priced(self) -> bool:
        return self.matched_rule is not None and self.status != "unknown" and self.amount_usd is not None


def decimal_to_json(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


def priced_to_json(result: Priced) -> dict[str, Any]:
    """The ``POST /preview`` response body."""
    return {
        "matched_rule": result.matched_rule,
        "mode": result.mode,
        "status": result.status,
        "source": result.source,
        "amount_usd": decimal_to_json(result.amount_usd),
        "per_million": {
            k: decimal_to_json(result.per_million.get(k)) for k in ("input", "output", "cache_read", "cache_write")
        },
        "route": dict(result.route),
        "pricing_version": result.pricing_version,
        "reason": result.reason,
    }
