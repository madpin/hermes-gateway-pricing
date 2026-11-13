import pytest

import gateway_pricing as gp

parse_pattern = gp.match.parse_pattern
parse_endpoint = gp.match.parse_endpoint


def matches(pattern: str, url: str) -> bool:
    return parse_pattern(pattern).matches_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://gateway.example.com",
        "https://gateway.example.com/",
        "http://gateway.example.com:8080/openai/v1",
        "gateway.example.com/v1",
        "HTTPS://GATEWAY.EXAMPLE.COM/v1",
        "https://gateway.example.com./v1",
        "https://user:secret@gateway.example.com/v1",
    ],
)
def test_host_only_pattern_matches(url):
    assert matches("gateway.example.com", url)


@pytest.mark.parametrize(
    "url",
    [
        "https://gateway.example.com.evil.com/v1",
        "http://evil.com/gateway.example.com",
        "http://evil.com/?h=gateway.example.com",
        "https://gateway.example.com@evil.com/v1",
        "https://evil.com#@gateway.example.com",
        "https://evil.com\\@gateway.example.com",
        "https://gateway.example.com\t.evil.com",
        "https://xgateway.example.com/v1",
        "https://example.com/v1",
        "",
        "not a url at all",
        "https://gateway.example.com:99999/v1",
        "https://gateway.example.com:abc/v1",
    ],
)
def test_host_only_pattern_rejects_lookalikes(url):
    assert not matches("gateway.example.com", url)


def test_scheme_in_pattern_is_ignored():
    assert matches("https://gateway.example.com/openai", "http://gateway.example.com/openai/v1")


@pytest.mark.parametrize(
    "url,expected",
    [
        ("http://127.0.0.1:9001", True),
        ("http://127.0.0.1:9001/v1", True),
        ("127.0.0.1:9001", True),
        ("http://127.0.0.1:9002", False),
        ("http://127.0.0.1", False),
        ("http://localhost:9001", False),  # hostnames are not resolved
        ("http://127.0.0.10:9001", False),
    ],
)
def test_port_must_match_when_given(url, expected):
    assert matches("127.0.0.1:9001", url) is expected


def test_default_ports_come_from_the_scheme():
    assert matches("gateway.example.com:443", "https://gateway.example.com/v1")
    assert not matches("gateway.example.com:443", "http://gateway.example.com/v1")
    assert matches("gateway.example.com:80", "http://gateway.example.com")
    assert not matches("gateway.example.com:443", "gateway.example.com/v1")  # no scheme, no port: unknown


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://gateway.example.com/openai", True),
        ("https://gateway.example.com/openai/", True),
        ("https://gateway.example.com/openai/v1", True),
        ("https://gateway.example.com/OpenAI/v1", True),
        ("https://gateway.example.com/openai-beta/v1", False),
        ("https://gateway.example.com/openaiv1", False),
        ("https://gateway.example.com/anthropic/v1", False),
        ("https://gateway.example.com/", False),
        ("https://gateway.example.com/x/../openai/v1", True),
        ("https://gateway.example.com/openai/../anthropic", False),
        ("https://gateway.example.com//openai/v1", True),
    ],
)
def test_path_prefix_matches_on_segment_boundary(url, expected):
    assert matches("gateway.example.com/openai", url) is expected


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://a.example.com", True),
        ("https://a.b.example.com/v1", True),
        ("https://example.com", False),
        ("https://a.example.com.evil.com", False),
        ("https://aexample.com", False),
    ],
)
def test_wildcard_subdomains(url, expected):
    assert matches("*.example.com", url) is expected


@pytest.mark.parametrize(
    "pattern,url,expected",
    [
        ("[::1]:9001", "http://[::1]:9001/v1", True),
        ("[::1]:9001", "http://[0:0:0:0:0:0:0:1]:9001/v1", True),
        ("[::1]:9001", "http://[::2]:9001/v1", False),
        ("[::1]", "http://[::1]:1234", True),
        ("127.0.0.1", "http://[::1]:9001", False),
    ],
)
def test_ipv6(pattern, url, expected):
    assert matches(pattern, url) is expected


@pytest.mark.parametrize(
    "pattern",
    [
        "",
        "   ",
        None,
        42,
        "user@gateway.example.com",
        "gateway.example.com?x=1",
        "gateway.example.com#f",
        "*.127.0.0.1",
        "gate*way.example.com",
        "*",
        "gateway.example.com:99999",
        "gateway .example.com",
        "back\\slash.example.com",
        "under_score..example.com",
    ],
)
def test_invalid_patterns_raise(pattern):
    with pytest.raises(ValueError):
        parse_pattern(pattern)


def test_endpoint_display():
    assert parse_endpoint("https://gateway.example.com/openai/v1/").display == "gateway.example.com:443"
    assert parse_endpoint("http://[::1]:9001").display == "[::1]:9001"
    assert parse_endpoint("process://amp").display == "amp"
    assert parse_endpoint("https://gateway.example.com/openai/v1/").path == "/openai/v1"
