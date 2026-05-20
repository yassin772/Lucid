"""
Shared compliance and copy guards for the Lucid product layer.

This module only protects user-facing Lucid strings. Backend rule keys may still
use internal macro vocabulary because users never see those values directly.
"""

from __future__ import annotations

import re
from dataclasses import fields, is_dataclass
from typing import Iterable, List, Optional


DISCLAIMER = (
    "Lucid is for macro understanding and education. It does not provide "
    "financial advice, investment recommendations, or trading signals."
)

ALLOWED_SUMMARY_LABELS = frozenset({"Supported", "Neutral", "Weak"})
ALLOWED_CONFIDENCE_LEVELS = frozenset({"Low", "Medium", "High"})
ALLOWED_TIMEFRAMES = frozenset({"Short-term", "Medium-term", "Mixed"})

BANNED_USER_TERMS = (
    "hawkish",
    "dovish",
    "divergence",
    "yield curve",
    "spread",
    "carry",
    "dsi",
    "signal",
    "setup",
    "buy",
    "sell",
    "opportunity",
    "trade idea",
    "edge",
    "entry",
    "target",
    "stop loss",
    "take profit",
    "risk/reward",
    "confirmed trade",
    "confirmation trade",
    "price confirmation",
    "best trade",
    "dominates",
    "long",
    "short",
    "priced in",
    "conviction",
    "probability",
    "probabilities",
    "probable",
)

def _banned_pattern(term: str) -> re.Pattern:
    if term == "long":
        return re.compile(r"(?<![a-z0-9])long(?![a-z0-9-]|\s+term)", re.IGNORECASE)
    if term == "short":
        return re.compile(r"(?<![a-z0-9])short(?![a-z0-9-]|\s+term)", re.IGNORECASE)
    return re.compile(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", re.IGNORECASE)


_BANNED_PATTERNS = tuple((term, _banned_pattern(term)) for term in BANNED_USER_TERMS)

_TEXT_REPLACEMENTS = (
    (re.compile(r"\bhawkish\b", re.IGNORECASE), "supportive"),
    (re.compile(r"\bdovish\b", re.IGNORECASE), "more cautious"),
    (re.compile(r"\bdivergence\b", re.IGNORECASE), "difference"),
    (re.compile(r"\byield curve\b", re.IGNORECASE), "rate outlook"),
    (re.compile(r"\bcarry\b", re.IGNORECASE), "rate appeal"),
    (re.compile(r"\bDSI\b", re.IGNORECASE), "recent data"),
    (re.compile(r"\bsignals?\b", re.IGNORECASE), "points"),
    (re.compile(r"\bsetups?\b", re.IGNORECASE), "contexts"),
    (re.compile(r"\bbuy\b", re.IGNORECASE), "use"),
    (re.compile(r"\bsell\b", re.IGNORECASE), "reduce"),
    (re.compile(r"\bopportunity\b", re.IGNORECASE), "context"),
    (re.compile(r"\btrade idea\b", re.IGNORECASE), "market context"),
    (re.compile(r"\bedge\b", re.IGNORECASE), "support"),
    (re.compile(r"\bentry\b", re.IGNORECASE), "starting point"),
    (re.compile(r"\btarget\b", re.IGNORECASE), "focus"),
    (re.compile(r"\bstop loss\b", re.IGNORECASE), "risk limit"),
    (re.compile(r"\btake profit\b", re.IGNORECASE), "profit-taking area"),
    (re.compile(r"\brisk/reward\b", re.IGNORECASE), "risk balance"),
    (re.compile(r"\bconfirmed trade\b", re.IGNORECASE), "aligned context"),
    (re.compile(r"\bconfirmation trade\b", re.IGNORECASE), "aligned context"),
    (re.compile(r"\bprice confirmation\b", re.IGNORECASE), "price alignment"),
    (re.compile(r"\bbest trade\b", re.IGNORECASE), "clearest context"),
    (re.compile(r"\bdominates\b", re.IGNORECASE), "has the firmer backdrop than"),
    (re.compile(r"\blong\b(?!-term|\s+term)", re.IGNORECASE), "lasting"),
    (re.compile(r"\bshort\b(?!-term|\s+term)", re.IGNORECASE), "brief"),
    (re.compile(r"\bpriced in\b", re.IGNORECASE), "already expected"),
    (re.compile(r"\bconviction\b", re.IGNORECASE), "confidence"),
    (re.compile(r"\bprobabilities\b", re.IGNORECASE), "possible outcomes"),
    (re.compile(r"\bprobability\b", re.IGNORECASE), "chance"),
    (re.compile(r"\bprobable\b", re.IGNORECASE), "possible"),
)


def clean_lucid_text(value: Optional[str]) -> str:
    """Return a compact, user-safe string for Lucid UI fields."""
    if value is None:
        return ""

    text = str(value).strip()
    for pattern, replacement in _TEXT_REPLACEMENTS:
        text = pattern.sub(replacement, text)

    text = re.sub(r"\s+", " ", text)
    return text.strip(" -")


def find_banned_terms(text: Optional[str]) -> List[str]:
    """List banned product terms present in a user-facing string."""
    if not text:
        return []
    return [term for term, pattern in _BANNED_PATTERNS if pattern.search(text)]


def assert_lucid_text_clean(text: Optional[str]) -> None:
    """Raise if a user-facing Lucid string contains banned language."""
    banned = find_banned_terms(text)
    if banned:
        raise ValueError(f"Lucid copy contains banned terms: {', '.join(banned)}")


def iter_user_facing_text(obj) -> Iterable[str]:
    """Yield string values from dataclasses, dicts, lists, tuples, and sets."""
    if obj is None:
        return

    if isinstance(obj, str):
        yield obj
        return

    if is_dataclass(obj):
        for item in fields(obj):
            yield from iter_user_facing_text(getattr(obj, item.name))
        return

    if isinstance(obj, dict):
        for value in obj.values():
            yield from iter_user_facing_text(value)
        return

    if isinstance(obj, (list, tuple, set, frozenset)):
        for value in obj:
            yield from iter_user_facing_text(value)


def assert_lucid_object_clean(obj) -> None:
    """Raise if any user-facing string inside an object contains banned language."""
    for text in iter_user_facing_text(obj):
        assert_lucid_text_clean(text)
