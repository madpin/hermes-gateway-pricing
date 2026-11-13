"""The desktop page reads and writes settings through Hermes' own plugin-settings code.

It reads ``plugins.manage list`` -> ``settings_schema[].value`` and writes
``plugins.manage settings`` with ``values: {rules, aliases, enabled}``. Both are backed by
``hermes_cli.plugins_settings``; pin that they carry the raw list / dict / bool values the page
expects, and that the writer keeps rule keys the parser does not know (forward compatibility).
"""

import pytest
from helpers import PLUGIN_ID, ROOT, STANDARD_ALIASES, STANDARD_RULES

import gateway_pricing as gp


@pytest.fixture
def plugins_settings():
    return pytest.importorskip("hermes_cli.plugins_settings")


def test_settings_fields_carry_the_raw_values(plugins_settings, standard_settings):
    fields = {f["key"]: f for f in plugins_settings.plugin_settings_fields(PLUGIN_ID, ROOT)}
    assert set(fields) == {"enabled", "rules", "aliases"}
    assert (fields["enabled"]["type"], fields["enabled"]["value"]) == ("boolean", True)
    assert (fields["rules"]["type"], fields["rules"]["value"]) == ("json", STANDARD_RULES)
    assert (fields["aliases"]["type"], fields["aliases"]["value"]) == ("json", STANDARD_ALIASES)


def test_unset_settings_fall_back_to_the_defaults(plugins_settings, write_settings):
    write_settings({})
    fields = {f["key"]: f for f in plugins_settings.plugin_settings_fields(PLUGIN_ID, ROOT)}
    assert (fields["enabled"]["value"], fields["rules"]["value"], fields["aliases"]["value"]) == (True, [], {})


def test_writer_accepts_the_payload_the_page_sends(plugins_settings, write_settings):
    write_settings({})
    future = {"id": "future", "match": {"base_url": "future.example.com", "region": "eu"}, "price_as": "auto"}
    values = {
        "rules": [*STANDARD_RULES, {**future, "tier": "gold"}],
        "aliases": {"chat-latest": "gpt-6.2"},
        "enabled": False,
    }
    written = plugins_settings.save_plugin_settings(PLUGIN_ID, ROOT, values)
    assert sorted(written) == ["aliases", "enabled", "rules"]
    assert gp.service.saved_settings(PLUGIN_ID) == values
    # The parser reports the unknown key instead of dropping the rule silently.
    assert any("unknown key(s) tier" in w for w in gp.service.status(PLUGIN_ID)["warnings"])


def test_writer_refuses_wrong_types(plugins_settings, write_settings):
    write_settings({})
    for bad in ({"rules": "[]"}, {"enabled": "yes"}, {"aliases": "chat-latest=gpt"}, {"unknown": 1}):
        with pytest.raises(ValueError):
            plugins_settings.save_plugin_settings(PLUGIN_ID, ROOT, bad)
