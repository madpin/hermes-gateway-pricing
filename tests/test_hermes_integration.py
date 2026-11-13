"""Against the real (unpatched) Hermes ``agent.usage_pricing``."""

import dataclasses
import inspect
import threading
import time
from decimal import Decimal

import agent.usage_pricing as up
import pytest
from helpers import PLUGIN_ID, hermes_lists_price

import gateway_pricing as gp

compat = gp.hermes_compat
M = 1_000_000
USAGE = up.CanonicalUsage(input_tokens=M, output_tokens=M, cache_read_tokens=M)


class TestHermesContract:
    """Every Hermes symbol the plugin relies on. A failure names the Hermes API that changed."""

    def test_check_contract_is_clean(self):
        assert compat.check_contract() == []

    @pytest.mark.parametrize("name,params", sorted(compat.EXPECTED_SIGNATURES.items()))
    def test_signature(self, name, params):
        fn = getattr(up, name, None)
        assert callable(fn), f"Hermes API changed: agent.usage_pricing.{name} is gone"
        names = tuple(inspect.signature(fn).parameters)
        assert names[: len(params)] == params, f"Hermes API changed: {name}{names}, expected {params}"

    @pytest.mark.parametrize("cls,fields", sorted(compat.EXPECTED_FIELDS.items()))
    def test_dataclass_fields(self, cls, fields):
        have = {f.name for f in dataclasses.fields(getattr(up, cls))}
        assert set(fields) <= have, f"Hermes API changed: {cls} lacks {sorted(set(fields) - have)}"

    def test_cost_sources(self):
        import typing

        assert set(compat.EXPECTED_COST_SOURCES) <= set(typing.get_args(up.CostSource)), (
            "Hermes API changed: CostSource no longer has user_override"
        )

    def test_callers_use_module_globals(self):
        assert "resolve_billing_route" in up.get_pricing_entry.__code__.co_names, (
            "Hermes API changed: get_pricing_entry no longer resolves the route through the module global"
        )
        for name in ("resolve_billing_route", "get_pricing_entry"):
            assert name in up.estimate_usage_cost.__code__.co_names, (
                f"Hermes API changed: estimate_usage_cost no longer calls {name} through the module global"
            )

    def test_plugin_context_api(self):
        assert compat.check_plugin_api() == []

    def test_hermes_loader_accepts_requires_hermes(self):
        # The contract above can pass while Hermes' loader still skips the plugin on a version gate.
        import re
        from types import SimpleNamespace

        from helpers import ROOT
        from hermes_cli.plugins_manifest import requires_hermes_error

        spec = re.search(r"^requires_hermes:\s*\"([^\"]+)\"", (ROOT / "plugin.yaml").read_text(), re.M).group(1)
        assert requires_hermes_error(SimpleNamespace(requires_hermes=spec)) is None

    def test_python_311_syntax(self):
        import ast

        from helpers import ROOT

        for path in sorted(ROOT.rglob("*.py")):
            if ".ref" not in path.parts:
                ast.parse(path.read_text(encoding="utf-8"), str(path), feature_version=(3, 11))

    def test_contract_detects_a_broken_module(self):
        class Fake:
            BillingRoute = up.BillingRoute

        problems = compat.check_contract(Fake)
        assert any("resolve_billing_route is missing" in p for p in problems)
        assert all("Hermes changed" in p for p in problems)


def test_install_is_idempotent_and_uninstall_restores(write_settings):
    originals = (up.resolve_billing_route, up.get_pricing_entry, up.estimate_usage_cost)
    try:
        assert compat.install(PLUGIN_ID)["active"]
        wrapped = (up.resolve_billing_route, up.get_pricing_entry)
        assert compat.install(PLUGIN_ID)["active"]
        assert (up.resolve_billing_route, up.get_pricing_entry) == wrapped
        assert up.estimate_usage_cost is originals[2]  # never replaced
        assert compat.activity() == {"active": True, "reason": "", "wrapped": list(compat.WRAPPED)}
    finally:
        assert compat.uninstall()
    assert (up.resolve_billing_route, up.get_pricing_entry, up.estimate_usage_cost) == originals
    assert not compat.uninstall()
    assert compat.activity()["active"] is False


def test_install_refuses_when_the_contract_breaks(monkeypatch, caplog):
    monkeypatch.delattr(up, "PricingEntry")
    result = compat.install(PLUGIN_ID)
    assert result["active"] is False and "PricingEntry" in result["reason"]
    assert compat.installed_state() is None
    assert sum(r.levelname == "WARNING" for r in caplog.records) == 1


def _hermes(model, provider):
    return up.estimate_usage_cost(model, USAGE, provider=provider)


@pytest.mark.parametrize(
    "model,vendor,shim_model",
    [
        ("claude-opus-5-5", "anthropic", "claude-opus-5-5"),
        ("claude-opus-5-5", "anthropic", "us.anthropic.claude-opus-5-5-v1:0"),
        ("claude-opus-4-7", "anthropic", "claude-opus-4.7"),
        ("gpt-6.1-sol", "openai", "gpt-6.1-sol"),
        ("gemini-3.8-flash", "google", "gemini-3.8-flash"),
    ],
)
def test_vendor_alias_costs_equal_hermes_own_numbers(installed, standard_settings, model, vendor, shim_model):
    url = {"anthropic": "http://127.0.0.1:9001", "google": "http://127.0.0.1:9002/v1"}.get(vendor)
    if vendor == "openai":
        url = "https://gateway.example.com/openai/v1"
    expected = up.estimate_usage_cost(model, USAGE, provider=vendor)
    got = up.estimate_usage_cost(shim_model, USAGE, provider="custom", base_url=url)
    if not hermes_lists_price(vendor, model):
        # This Hermes has no list price for the vendor model: the shim stays unpriced, like Hermes itself.
        assert got.status == expected.status == "unknown" and got.amount_usd is None
        return
    factor = Decimal("1.1") if vendor == "openai" else 1
    assert got.status == "estimated" and got.source == expected.source == "official_docs_snapshot"
    assert got.amount_usd == expected.amount_usd * factor
    assert got.pricing_version == expected.pricing_version


def test_deepseek_and_explicit_vendor_model(installed, write_settings):
    write_settings(
        {
            "rules": [
                {"match": {"base_url": "127.0.0.1:9005"}, "price_as": "deepseek"},
                {"match": {"base_url": "127.0.0.1:9006"}, "price_as": "openai/gpt-6-luna"},
            ]
        }
    )
    ds = up.estimate_usage_cost("deepseek-v4-pro", USAGE, provider="custom", base_url="http://127.0.0.1:9005")
    route = up.BillingRoute(provider="deepseek", model="deepseek-v4-pro")
    entry = up._lookup_official_docs_pricing(route)
    assert (
        ds.amount_usd
        == entry.input_cost_per_million + entry.output_cost_per_million + entry.cache_read_cost_per_million
    )
    luna = up.estimate_usage_cost("house-model", USAGE, provider="custom", base_url="http://127.0.0.1:9006/v1")
    assert luna.amount_usd == _hermes("gpt-6-luna", "openai").amount_usd


def test_free_and_explicit_prices(installed, write_settings):
    write_settings(
        {
            "rules": [
                {"id": "llama", "match": {"base_url": "127.0.0.1:9003"}, "free": True},
                {
                    "id": "house",
                    "match": {"base_url": "gateway.example.com"},
                    "multiplier": 2,
                    "prices": {"input": 3.0, "output": 15.0, "cache_read": 0.3},
                },
                {"id": "nocache", "match": {"base_url": "nocache.example.com"}, "prices": {"input": 1, "output": 1}},
            ]
        }
    )
    free = up.estimate_usage_cost("Qwen3.6", USAGE, provider="custom", base_url="http://127.0.0.1:9003/v1")
    assert (free.status, free.amount_usd, free.label) == ("included", Decimal(0), "included")
    priced = up.estimate_usage_cost("anything", USAGE, provider="custom", base_url="https://gateway.example.com/v1")
    assert priced.status == "estimated" and priced.source == "user_override"
    assert priced.amount_usd == Decimal("36.6")  # (3 + 15 + 0.3) * 2
    assert priced.pricing_version == gp.version.PRICING_VERSION
    # Hermes' own rule: tokens in a bucket without a rate make the whole estimate unknown.
    nocache = up.estimate_usage_cost("m", USAGE, provider="custom", base_url="https://nocache.example.com")
    assert nocache.status == "unknown"
    entry = up.get_pricing_entry("anything", provider="custom", base_url="https://gateway.example.com/v1")
    assert entry.input_cost_per_million == Decimal("6.0") and entry.source == "user_override"


def test_routes_hermes_already_prices_are_byte_identical(write_settings, monkeypatch):
    monkeypatch.setattr(
        up,
        "fetch_model_metadata",
        lambda *a, **k: {"anthropic/claude-opus-5-5": {"pricing": {"prompt": "0.000004", "completion": "0.00002"}}},
    )
    monkeypatch.setattr(up, "_UTC_NOW", lambda: None)
    write_settings(
        {
            "rules": [
                {"match": {"base_url": "*.anthropic.com"}, "free": True},
                {"match": {"base_url": "openrouter.ai"}, "price_as": "openai"},
                {"match": {"base_url": "127.0.0.1"}, "price_as": "auto"},
            ]
        }
    )
    calls = [
        ("claude-opus-5-5", "anthropic", "https://api.anthropic.com"),
        ("claude-opus-5-5", "anthropic", None),
        ("anthropic/claude-opus-5-5", "openrouter", "https://openrouter.ai/api/v1"),
        ("gpt-6.1-sol", "openai-codex", None),
        ("claude-opus-5-5", None, None),
    ]

    def snapshot():
        return [
            (
                up.resolve_billing_route(m, provider=p, base_url=u),
                repr(up.get_pricing_entry(m, provider=p, base_url=u)),
                repr(up.estimate_usage_cost(m, USAGE, provider=p, base_url=u)),
            )
            for m, p, u in calls
        ]

    before = snapshot()
    assert compat.install(PLUGIN_ID, interval=0)["active"]
    try:
        assert snapshot() == before
    finally:
        compat.uninstall()


def test_unmatched_custom_route_is_left_to_hermes(installed, standard_settings):
    route = up.resolve_billing_route("m", provider="custom", base_url="https://other.example.net/v1")
    assert route.billing_mode == "unknown"


def test_hot_reload_of_settings(installed, write_settings):
    url = "https://gateway.example.com/v1"
    assert up.resolve_billing_route("gpt-6-luna", provider="custom", base_url=url).billing_mode == "unknown"
    write_settings({"rules": [{"match": {"base_url": "gateway.example.com"}, "free": True}]})
    assert (
        up.resolve_billing_route("gpt-6-luna", provider="custom", base_url=url).billing_mode == "subscription_included"
    )
    write_settings(
        {
            "enabled": False,
            "rules": [{"match": {"base_url": "gateway.example.com"}, "free": True}],
            "aliases": {"x": "y"},
        }
    )
    assert up.resolve_billing_route("gpt-6-luna", provider="custom", base_url=url).billing_mode == "unknown"


def test_reload_interval_caches_between_checks(write_settings):
    write_settings({"rules": [{"match": {"base_url": "gateway.example.com"}, "free": True}]})
    cache = compat.SettingsCache(PLUGIN_ID, interval=3600)
    first = cache.current()
    write_settings({"rules": []})
    assert cache.current() is first  # not re-read inside the interval
    cache.invalidate()
    assert cache.current().rules == ()


def test_settings_are_per_profile(installed, tmp_path, monkeypatch, write_settings):
    url = "https://gateway.example.com/v1"
    write_settings({"rules": [{"match": {"base_url": "gateway.example.com"}, "free": True}]})
    assert up.resolve_billing_route("m", provider="custom", base_url=url).billing_mode == "subscription_included"
    other = tmp_path / "other-profile"
    other.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(other))
    assert up.resolve_billing_route("m", provider="custom", base_url=url).billing_mode == "unknown"


def test_a_failure_inside_the_wrapper_returns_hermes_result(installed, standard_settings, monkeypatch):
    url = "http://127.0.0.1:9001"
    state = compat.installed_state()

    def boom():
        raise RuntimeError("synthetic")

    monkeypatch.setattr(state, "ruleset", boom)
    # Hermes' own path for an unknown custom route asks the endpoint's /models: stub it (no network).
    monkeypatch.setattr(up, "fetch_endpoint_model_metadata", lambda *a, **k: {})
    original = state.originals["resolve_billing_route"]("claude-opus-5-5", provider="custom", base_url=url)
    assert up.resolve_billing_route("claude-opus-5-5", provider="custom", base_url=url) == original
    assert up.estimate_usage_cost("claude-opus-5-5", USAGE, provider="custom", base_url=url).status == "unknown"


def test_thread_safety_smoke(installed, standard_settings):
    expected = up.estimate_usage_cost("claude-opus-5-5", USAGE, provider="anthropic").amount_usd
    errors = []

    def worker():
        try:
            for _ in range(300):
                got = up.estimate_usage_cost(
                    "claude-opus-5-5", USAGE, provider="custom", base_url="http://127.0.0.1:9001"
                )
                assert got.amount_usd == expected
                compat.installed_state().cache.invalidate()
        except Exception as exc:  # pragma: no cover - reported below
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []


def test_benchmark_overhead_with_20_rules(write_settings):
    rules = [{"match": {"base_url": f"host{i}.example.com:{9000 + i}/p"}, "price_as": "auto"} for i in range(19)]
    rules.append({"match": {"base_url": "127.0.0.1:9001"}, "price_as": "anthropic"})
    write_settings({"rules": rules})
    calls = [
        ("claude-opus-5-5", "custom", "http://127.0.0.1:9001"),
        ("claude-opus-5-5", "anthropic", "https://api.anthropic.com"),
        ("m", "custom", "https://other.example.net/v1"),
    ]
    n = 3000

    def run():
        start = time.perf_counter()
        for _ in range(n):
            for m, p, u in calls:
                up.resolve_billing_route(m, provider=p, base_url=u)
                up.get_pricing_entry(m, provider=p, base_url=u) if p == "anthropic" else None
        return (time.perf_counter() - start) / (n * len(calls))

    baseline = min(run() for _ in range(3))
    assert compat.install(PLUGIN_ID)["active"]
    try:
        run()  # warm-up: settings read, decisions memoised
        wrapped = min(run() for _ in range(3))
    finally:
        compat.uninstall()
    overhead_us = (wrapped - baseline) * 1e6
    assert overhead_us < 50, f"gateway-pricing adds {overhead_us:.1f}us per call"
