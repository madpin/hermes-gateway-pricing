"""Vendor inference and model-id normalisation. Pure stdlib: no Hermes imports.

The normalisation mirrors what Hermes does before a price lookup (``agent.usage_pricing``):
Anthropic ids map dotted versions to dashes (``opus-4.7`` -> ``opus-4-7``); Bedrock ids drop the
cross-region scope (``us.``, ``global.``...) and the ``-v1:0`` style trailers. Hermes' Anthropic
normaliser does NOT strip Bedrock scopes, so a ``us.anthropic.claude-...`` id sent to a Claude
shim must be unwrapped here before it is priced as ``anthropic``.
"""

from __future__ import annotations

import re

#: Vendor keys Hermes ships prices for (snapshot tables and/or models.dev direct hosts).
KNOWN_VENDORS = frozenset(
    {
        "anthropic",
        "openai",
        "google",
        "deepseek",
        "xai",
        "bedrock",
        "fireworks",
        "minimax",
        "minimax-cn",
        "xiaomi",
    }
)
_VENDOR_ALIASES = {
    "claude": "anthropic",
    "gemini": "google",
    "vertex": "google",
    "google-gemini": "google",
    "grok": "xai",
    "x-ai": "xai",
    "openai-api": "openai",
    "aws": "bedrock",
    "amazon-bedrock": "bedrock",
}
BEDROCK_REGION_PREFIXES = ("global.", "us.", "eu.", "apac.", "ap.", "au.", "jp.", "ca.", "sa.", "me.", "af.")
# Bedrock foundation-model ids carry the model vendor as a dotted prefix.
_BEDROCK_VENDOR_PREFIXES = {
    "anthropic.": "anthropic",
    "amazon.": "bedrock",
    "meta.": "bedrock",
    "mistral.": "bedrock",
    "cohere.": "bedrock",
    "ai21.": "bedrock",
    "deepseek.": "deepseek",
    "openai.": "openai",
}
_OPENAI_RE = re.compile(r"^(gpt-|chatgpt-|codex-|o[1-9](-|$))")
_DOTTED_VERSION = re.compile(r"(\d+)\.(\d+)")


def canonical_vendor(name: object) -> str | None:
    """``"Gemini"`` -> ``"google"``; ``None`` when the name is not a vendor Hermes can price."""
    if not isinstance(name, str):
        return None
    key = name.strip().lower()
    key = _VENDOR_ALIASES.get(key, key)
    return key if key in KNOWN_VENDORS else None


def bare_model(model: str) -> str:
    """Last path segment, lowercased (``accounts/x/models/m`` -> ``m``, ``openai/gpt-x`` -> ``gpt-x``)."""
    return (model or "").strip().rsplit("/", 1)[-1].lower()


def _strip_bedrock_scope(name: str) -> tuple[str, str | None]:
    """``us.anthropic.claude-x`` -> (``claude-x``, ``"anthropic"``); other ids unchanged."""
    scoped = next((name[len(p) :] for p in BEDROCK_REGION_PREFIXES if name.startswith(p)), name)
    for prefix, vendor in _BEDROCK_VENDOR_PREFIXES.items():
        if scoped.startswith(prefix):
            return scoped[len(prefix) :], vendor
    return name, None


def _unwrap_bedrock(name: str) -> str:
    """Bedrock id -> vendor id, like Hermes' bedrock normaliser: scope and vendor prefix removed, then
    the ``:0`` / ``-v1`` / ``-YYYYMMDD`` trailers. Ids that are not Bedrock-wrapped are unchanged."""
    unwrapped, vendor = _strip_bedrock_scope(name)
    if vendor is None:
        return name
    for trailer in (r":\d+$", r"-v\d+$", r"-\d{8}$"):
        unwrapped = re.sub(trailer, "", unwrapped)
    return unwrapped


def glob_forms(model: str) -> tuple[str, ...]:
    """The spellings a ``match.models`` glob is tested against: full id, last segment, unwrapped."""
    full = (model or "").strip().lower()
    bare = bare_model(full)
    return tuple(dict.fromkeys((full, bare, _unwrap_bedrock(bare))))


def infer_vendor(model: str) -> str | None:
    """Best-effort vendor for a model id; ``None`` when the id does not name a known family."""
    raw = (model or "").strip().lower()
    if "/" in raw:
        vendor = canonical_vendor(raw.split("/", 1)[0])
        if vendor:
            return vendor
    name, bedrock_vendor = _strip_bedrock_scope(bare_model(raw))
    if bedrock_vendor:
        return bedrock_vendor
    if name.startswith("claude"):
        return "anthropic"
    if _OPENAI_RE.match(name):
        return "openai"
    if name.startswith("gemini"):
        return "google"
    if name.startswith("deepseek"):
        return "deepseek"
    if name.startswith("grok"):
        return "xai"
    return None


def vendor_model_id(vendor: str, model: str) -> str:
    """The id to look up under ``vendor`` in Hermes' price tables."""
    name = bare_model(model)
    if vendor == "bedrock":
        # Hermes' bedrock normaliser handles scopes, dotted versions and trailers itself;
        # a bare Claude id needs the foundation-model prefix.
        return name if _strip_bedrock_scope(name)[1] else (f"anthropic.{name}" if name.startswith("claude") else name)
    name = _unwrap_bedrock(name)
    if vendor == "anthropic":
        name = _DOTTED_VERSION.sub(r"\1-\2", name)
    return name
