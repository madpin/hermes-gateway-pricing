from decimal import Decimal

import pytest

import gateway_pricing as gp

parse = gp.rules.parse_settings
infer = gp.vendors.infer_vendor
vendor_model = gp.vendors.vendor_model_id


@pytest.mark.parametrize(
    "model,vendor",
    [
        ("claude-opus-5-5", "anthropic"),
        ("Claude-Sonnet-4.6", "anthropic"),
        ("anthropic/claude-opus-5-5", "anthropic"),
        ("us.anthropic.claude-opus-5-5-v1:0", "anthropic"),
        ("global.anthropic.claude-haiku-4-5-20251001-v1:0", "anthropic"),
        ("anthropic.claude-opus-4-7", "anthropic"),
        ("gpt-6.1-sol", "openai"),
        ("openai/gpt-4o", "openai"),
        ("chatgpt-4o-latest", "openai"),
        ("o3", "openai"),
        ("o3-mini", "openai"),
        ("o4-mini", "openai"),
        ("gemini-3.8-flash", "google"),
        ("models/gemini-2.5-pro", "google"),
        ("google/gemini-3.1-pro", "google"),
        ("deepseek-v4-pro", "deepseek"),
        ("grok-4", "xai"),
        ("Qwen3.6-35B-A3B-UD-Q4_K_M", None),
        ("glm-5.3-flash", None),
        ("omni-model", None),
        ("o", None),
        ("", None),
    ],
)
def test_infer_vendor(model, vendor):
    assert infer(model) == vendor


@pytest.mark.parametrize(
    "vendor,model,expected",
    [
        ("anthropic", "claude-opus-4.7", "claude-opus-4-7"),
        ("anthropic", "us.anthropic.claude-opus-5-5-v1:0", "claude-opus-5-5"),
        ("anthropic", "anthropic/claude-sonnet-4.6", "claude-sonnet-4-6"),
        ("anthropic", "anthropic.claude-sonnet-4-20250514-v1:0", "claude-sonnet-4"),  # same as Hermes' bedrock rule
        ("anthropic", "claude-sonnet-4-20250514", "claude-sonnet-4-20250514"),  # not Bedrock-wrapped: date kept
        ("openai", "openai/gpt-6.1-sol", "gpt-6.1-sol"),
        ("google", "models/gemini-3.8-flash", "gemini-3.8-flash"),
        ("deepseek", "deepseek-v4", "deepseek-v4"),  # "-v4" is a version, not a Bedrock trailer
        ("bedrock", "claude-opus-5-5", "anthropic.claude-opus-5-5"),
        ("bedrock", "us.anthropic.claude-opus-5-5-v1:0", "us.anthropic.claude-opus-5-5-v1:0"),
    ],
)
def test_vendor_model_id(vendor, model, expected):
    assert vendor_model(vendor, model) == expected


def test_bedrock_unwrapping_agrees_with_hermes():
    import agent.usage_pricing as up

    for model in (
        "us.anthropic.claude-opus-5-5-v1:0",
        "global.anthropic.claude-sonnet-4.6",
        "eu.anthropic.claude-haiku-4-5-20251001-v1:0",
    ):
        hermes = up._normalize_bedrock_model_name(model)  # "anthropic.<id>"
        assert hermes == "anthropic." + vendor_model("anthropic", model).replace(".", "-")


def rule(**kw):
    base = {"match": {"base_url": "gateway.example.com"}, "price_as": "auto"}
    base.update(kw)
    return {k: v for k, v in base.items() if v is not None}


def test_valid_rules_and_defaults():
    rs = parse({"rules": [rule(id="a"), rule(price_as=None, prices={"input": 3, "output": "15"})]})
    assert rs.rules_valid == 2 and rs.rules_total == 2 and rs.warnings == ()
    assert rs.rules[1].mode == "prices" and rs.rules[1].label == 1
    first = rs.rules[0]
    assert first.enabled and not first.override and first.multiplier == 1 and first.label == "a"


@pytest.mark.parametrize(
    "bad,needle",
    [
        ("not a mapping", "must be a mapping"),
        ({"price_as": "auto"}, "match must be a mapping"),
        (rule(match={"base_url": "evil.com/x", "host": "x"}), "unknown match key"),
        (rule(match={"base_url": "user@gateway.example.com"}), "userinfo"),
        (rule(match={"base_url": "gateway.example.com", "models": [1]}), "glob strings"),
        ({"match": {"base_url": "gateway.example.com"}}, "exactly one of"),
        (rule(free=True), "only one of"),
        (rule(price_as="nobody"), "unknown vendor"),
        (rule(price_as="openai/"), "needs a model"),
        (rule(price_as=""), "price_as must be"),
        ({"match": {"base_url": "a.example.com"}, "free": False}, "free must be true"),
        ({"match": {"base_url": "a.example.com"}, "prices": {"input": 1}}, "prices.output is required"),
        ({"match": {"base_url": "a.example.com"}, "prices": {"input": -1, "output": 1}}, "0 or more"),
        ({"match": {"base_url": "a.example.com"}, "prices": {"input": "NaN", "output": 1}}, "finite"),
        ({"match": {"base_url": "a.example.com"}, "prices": {"input": True, "output": 1}}, "number"),
        (
            {"match": {"base_url": "a.example.com"}, "prices": {"input": 1, "output": 1, "reasoning": 2}},
            "unknown prices",
        ),
        (rule(multiplier=0), "greater than 0"),
        (rule(multiplier="x"), "number"),
        (rule(override="yes"), "override must be"),
        (rule(enabled=1), "enabled must be"),
        (rule(id=""), "id must be"),
        (rule(pricee_as="auto"), "unknown key"),
    ],
)
def test_invalid_rule_is_skipped_with_one_warning(bad, needle):
    rs = parse({"rules": [bad, rule(id="good")]})
    assert [r.id for r in rs.rules] == ["good"]
    assert len(rs.warnings) == 1 and needle in rs.warnings[0] and rs.warnings[0].startswith("rules[0]")


def test_duplicate_ids_skip_the_later_rule():
    rs = parse({"rules": [rule(id="x"), rule(id="x", free=True, price_as=None) | {"free": True}]})
    assert len(rs.rules) == 1 and "rules[1]" in rs.warnings[-1]


def test_top_level_shapes_never_raise():
    for raw in (None, [], "x", {"rules": "x"}, {"aliases": ["x"]}, {"enabled": "maybe"}):
        rs = parse(raw)
        assert rs.rules == ()
    assert parse({"enabled": "maybe"}).enabled is True
    assert "aliases" in parse({"aliases": {"a": 1}}).warnings[0]


def test_first_match_wins_and_precedence():
    rs = parse(
        {
            "rules": [
                rule(id="disabled", enabled=False, free=True, price_as=None) | {"free": True},
                rule(
                    id="claude-only",
                    match={"base_url": "gateway.example.com", "models": ["claude-*"]},
                    price_as="anthropic",
                ),
                rule(id="catch-all", price_as=None, prices={"input": 1, "output": 2}),
            ]
        }
    )
    assert rs.decide("claude-opus-5-5", "https://gateway.example.com/v1").rule.id == "claude-only"
    assert rs.decide("us.anthropic.claude-opus-5-5-v1:0", "https://gateway.example.com").rule.id == "claude-only"
    assert rs.decide("gpt-6.1-sol", "https://gateway.example.com/v1").rule.id == "catch-all"
    assert rs.decide("gpt-6.1-sol", "https://other.example.com/v1") is None


def test_override_only_applies_to_routes_hermes_prices_when_set():
    rs = parse({"rules": [rule(id="plain", match={"base_url": "openrouter.ai"})]})
    assert rs.decide("anthropic/claude-opus-5-5", "https://openrouter.ai/api/v1", "official_models_api") is None
    rs = parse({"rules": [rule(id="forced", match={"base_url": "openrouter.ai"}, override=True)]})
    assert (
        rs.decide("anthropic/claude-opus-5-5", "https://openrouter.ai/api/v1", "official_models_api").rule.id
        == "forced"
    )


def test_auto_without_a_known_vendor_falls_through_to_the_next_rule():
    rs = parse({"rules": [rule(id="auto"), rule(id="free", price_as=None) | {"free": True}]})
    assert rs.decide("Qwen3.6-35B", "https://gateway.example.com").rule.id == "free"
    assert rs.decide("gpt-6-luna", "https://gateway.example.com").rule.id == "auto"


def test_aliases_rewrite_before_vendor_inference():
    rs = parse({"rules": [rule(id="auto")], "aliases": {"chat-latest": "gpt-6.1-sol", "Fast": "claude-opus-4.7"}})
    d = rs.decide("chat-latest", "https://gateway.example.com")
    assert (d.vendor, d.vendor_model, d.model) == ("openai", "gpt-6.1-sol", "gpt-6.1-sol")
    d = rs.decide("fast", "https://gateway.example.com")
    assert (d.vendor, d.vendor_model) == ("anthropic", "claude-opus-4-7")


def test_forced_vendor_model():
    rs = parse({"rules": [rule(price_as="openai/gpt-6-luna")]})
    d = rs.decide("whatever-the-gateway-calls-it", "https://gateway.example.com")
    assert (d.vendor, d.vendor_model) == ("openai", "gpt-6-luna")


def test_prices_and_multiplier_are_decimal_and_scaled():
    rs = parse(
        {
            "rules": [
                {
                    "match": {"base_url": "a.example.com"},
                    "multiplier": 1.5,
                    "prices": {"input": 3.0, "output": 15, "cache_read": "0.3"},
                }
            ]
        }
    )
    assert dict(rs.rules[0].prices) == {
        "input": Decimal("4.50"),
        "output": Decimal("22.5"),
        "cache_read": Decimal("0.45"),
    }


def test_disabled_settings_decide_nothing():
    rs = parse({"enabled": False, "rules": [rule()]})
    assert rs.decide("gpt-6-luna", "https://gateway.example.com") is None
    assert "disabled" in rs.explain("gpt-6-luna", "https://gateway.example.com")[1]


def test_warnings_are_logged_once(caplog):
    rs = parse({"rules": [{"bogus": 1}]})
    gp.rules.log_warnings_once(rs)
    gp.rules.log_warnings_once(parse({"rules": [{"bogus": 1}]}))
    assert sum("bogus" in r.getMessage() for r in caplog.records) == 1
