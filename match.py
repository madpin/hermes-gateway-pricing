"""Endpoint matching on parsed URLs. Pure stdlib: no Hermes imports.

A rule's ``match.base_url`` is ``host[:port][/path-prefix]``. Matching is done on the parsed
URL, never on substrings:

* the hostname must be equal (case-insensitive, trailing dot ignored), or, for a ``*.`` pattern,
  a strict subdomain of it (``*.example.com`` matches ``a.example.com`` and ``a.b.example.com``,
  not ``example.com`` itself);
* a port in the pattern must equal the URL's effective port (explicit, else 80/443 from the
  scheme); a pattern without a port matches any port;
* a path prefix matches only on a segment boundary (``/openai`` matches ``/openai`` and
  ``/openai/v1``, not ``/openai-beta``);
* userinfo (``user@host``) is never part of the hostname, and URLs with whitespace, control
  characters or backslashes never match anything.
"""

from __future__ import annotations

import ipaddress
import posixpath
import re
from dataclasses import dataclass
from functools import lru_cache
from urllib.parse import urlsplit

_DEFAULT_PORTS = {"http": 80, "https": 443}
# Characters that different URL parsers disagree on (WHATWG treats "\" as "/", Python does not).
_AMBIGUOUS = re.compile(r"[\s\\\x00-\x1f\x7f]")
_LABEL = re.compile(r"^[a-z0-9_]([a-z0-9_-]*[a-z0-9_])?$")


@dataclass(frozen=True)
class Endpoint:
    """A parsed request URL: lowercase host, effective port (None if unknown), normalised path."""

    host: str
    port: int | None
    path: str

    @property
    def display(self) -> str:
        """``host[:port]`` (IPv6 hosts bracketed); the grouping key used by detection."""
        host = f"[{self.host}]" if ":" in self.host else self.host
        return host if self.port is None else f"{host}:{self.port}"


def _normalise_host(host: str) -> str:
    host = host.lower().rstrip(".")
    try:
        return ipaddress.ip_address(host).compressed
    except ValueError:
        return host


def _normalise_path(path: str) -> str:
    if not path or path == "/":
        return ""
    # Resolve "." / ".." / "//" the way an HTTP client would before sending the request.
    norm = posixpath.normpath("/" + path.lstrip("/"))
    return "" if norm == "/" else norm.lower()


def _split(text: str):
    raw = text.strip()
    if not raw or _AMBIGUOUS.search(raw):
        return None
    if "://" not in raw:
        raw = "//" + raw
    try:
        parts = urlsplit(raw)
        _ = parts.port  # raises ValueError on a non-numeric or out-of-range port
    except ValueError:
        return None
    if not parts.hostname:
        return None
    return parts


@lru_cache(maxsize=2048)
def parse_endpoint(url: str) -> Endpoint | None:
    """Parse a base URL as Hermes recorded it; ``None`` when it has no usable host."""
    parts = _split(url or "")
    if parts is None:
        return None
    port = parts.port
    if port is None:
        port = _DEFAULT_PORTS.get(parts.scheme.lower())
    return Endpoint(_normalise_host(parts.hostname), port, _normalise_path(parts.path))


@dataclass(frozen=True)
class EndpointPattern:
    """A compiled ``match.base_url``."""

    host: str
    wildcard: bool
    port: int | None
    path: str

    def matches(self, endpoint: Endpoint | None) -> bool:
        if endpoint is None:
            return False
        if self.wildcard:
            if not endpoint.host.endswith("." + self.host):
                return False
        elif endpoint.host != self.host:
            return False
        if self.port is not None and endpoint.port != self.port:
            return False
        if self.path and not (endpoint.path == self.path or endpoint.path.startswith(self.path + "/")):
            return False
        return True

    def matches_url(self, url: str) -> bool:
        return self.matches(parse_endpoint(url))


def _valid_hostname(host: str) -> bool:
    return len(host) <= 253 and all(_LABEL.match(label) for label in host.split("."))


def parse_pattern(text: object) -> EndpointPattern:
    """Compile a rule's ``match.base_url``; raises ``ValueError`` with a readable reason."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("match.base_url must be a non-empty string like 'gateway.example.com/openai'")
    if _AMBIGUOUS.search(text.strip()):
        raise ValueError("match.base_url must not contain whitespace, control characters or backslashes")
    raw = text.strip()
    if "://" not in raw:
        raw = "//" + raw
    try:
        parts = urlsplit(raw)
        port = parts.port
    except ValueError:
        raise ValueError(f"match.base_url {text!r}: invalid port") from None
    if "@" in parts.netloc:
        raise ValueError(f"match.base_url {text!r}: userinfo ('user@') is not allowed")
    if parts.query or parts.fragment:
        raise ValueError(f"match.base_url {text!r}: query strings and fragments are not allowed")
    host = (parts.hostname or "").rstrip(".")
    if not host:
        raise ValueError(f"match.base_url {text!r}: no host")
    wildcard = host.startswith("*.")
    if wildcard:
        host = host[2:]
    if "*" in host:
        raise ValueError(f"match.base_url {text!r}: '*' is only allowed as a leading '*.' label")
    try:
        host = ipaddress.ip_address(host).compressed
        if wildcard:
            raise ValueError(f"match.base_url {text!r}: '*.' cannot be used with an IP address")
    except ValueError as exc:
        if wildcard and "IP address" in str(exc):
            raise
        if not _valid_hostname(host):
            raise ValueError(f"match.base_url {text!r}: {host!r} is not a valid hostname") from None
    return EndpointPattern(host=host, wildcard=wildcard, port=port, path=_normalise_path(parts.path))
