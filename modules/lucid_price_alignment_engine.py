"""
Deterministic price alignment layer for Lucid.

This module compares recent price behavior with the macro pair backdrop. It is
not a technical-analysis layer and does not produce recommendations.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, Optional

from modules.lucid_compliance import assert_lucid_object_clean, clean_lucid_text


ALLOWED_PRICE_ALIGNMENT_STATES = frozenset({"Aligned", "Mixed", "Diverging", "Transitioning"})
PRICE_ALIGNMENT_CAVEAT = "Price behavior can differ from macro context in the short term."

STATE_SUMMARIES = {
    "Aligned": "Recent price behavior broadly reflects the current macro backdrop.",
    "Mixed": "Price behavior remains mixed relative to the macro backdrop.",
    "Diverging": "Price behavior is not fully reflecting the macro backdrop yet.",
    "Transitioning": "Price behavior appears to be reassessing the current macro regime.",
}


@dataclass(frozen=True)
class PriceAlignment:
    pair: str
    state: str
    summary: str
    observed_behavior: str
    caveat: str
    price_updated_at: str | None


def _label_score(label: str) -> int:
    if label == "Supported":
        return 1
    if label == "Weak":
        return -1
    return 0


def _expected_pair_pressure(pair_context: dict) -> int:
    difference = _label_score(pair_context.get("base_label", "Neutral")) - _label_score(pair_context.get("quote_label", "Neutral"))
    if difference >= 2:
        return 1
    if difference <= -2:
        return -1
    return 0


def _parse_timestamp(value: object) -> Optional[datetime]:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _freshness_limit_hours(parsed: datetime, current: datetime, default_max_age_hours: int) -> int:
    if current.weekday() in {5, 6} or (current.weekday() == 0 and parsed.weekday() == 4):
        return max(default_max_age_hours, 84)
    return default_max_age_hours


def _is_stale(timestamp: object, now: Optional[datetime], max_age_hours: int) -> bool:
    parsed = _parse_timestamp(timestamp)
    if not parsed:
        return True
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)
    if parsed > current:
        return parsed > current + timedelta(hours=24)
    age_seconds = (current - parsed).total_seconds()
    limit_hours = _freshness_limit_hours(parsed, current, max_age_hours)
    return age_seconds < 0 or age_seconds > limit_hours * 3600


def _number(value: object) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _recent_change(price_item: dict) -> Optional[float]:
    for key in ("recent_change_pct", "change_1d_pct", "change_5d_pct"):
        value = _number(price_item.get(key))
        if value is not None:
            return value
    return None


def _previous_change(price_item: dict) -> Optional[float]:
    for key in ("previous_change_pct", "prior_change_pct", "previous_5d_change_pct"):
        value = _number(price_item.get(key))
        if value is not None:
            return value
    return None


def _sign(value: float, threshold: float) -> int:
    if value > threshold:
        return 1
    if value < -threshold:
        return -1
    return 0


def _state(expected_pressure: int, recent: float, previous: Optional[float], threshold: float) -> str:
    recent_sign = _sign(recent, threshold)
    if expected_pressure == 0 or recent_sign == 0:
        return "Mixed"

    previous_sign = _sign(previous, threshold) if previous is not None else 0
    if recent_sign == expected_pressure:
        if previous_sign == -expected_pressure:
            return "Transitioning"
        return "Aligned"
    return "Diverging"


def _observed_behavior(pair: str, state: str) -> str:
    if state == "Aligned":
        return f"Recent {pair} price behavior has broadly moved with the macro backdrop."
    if state == "Transitioning":
        return f"Recent {pair} price behavior has started to reassess the macro backdrop."
    if state == "Diverging":
        return f"Recent {pair} price behavior has not fully reflected the macro backdrop."
    return f"Recent {pair} price behavior remains mixed versus the macro backdrop."


def build_price_alignment(
    pair_context: dict,
    price_item: Optional[dict],
    *,
    now: Optional[datetime] = None,
    max_age_hours: int = 36,
    threshold_pct: float = 0.15,
) -> Optional[dict]:
    """Build one safe price-alignment object, or None when data is missing/stale."""
    if not pair_context or not price_item:
        return None

    pair = clean_lucid_text(pair_context.get("pair") or price_item.get("pair") or "")
    if not pair:
        return None

    updated_at = price_item.get("price_updated_at") or price_item.get("updated_at") or price_item.get("timestamp")
    if _is_stale(updated_at, now, max_age_hours):
        return None

    recent = _recent_change(price_item)
    if recent is None:
        return None

    expected_pressure = _expected_pair_pressure(pair_context)
    state = _state(expected_pressure, recent, _previous_change(price_item), threshold_pct)
    alignment = PriceAlignment(
        pair=pair,
        state=state,
        summary=STATE_SUMMARIES[state],
        observed_behavior=_observed_behavior(pair, state),
        caveat=PRICE_ALIGNMENT_CAVEAT,
        price_updated_at=str(updated_at),
    )
    assert alignment.state in ALLOWED_PRICE_ALIGNMENT_STATES
    assert_lucid_object_clean(alignment)
    return asdict(alignment)


def build_price_alignments(
    pair_contexts: Iterable[dict],
    price_data: Optional[object],
    *,
    now: Optional[datetime] = None,
    max_age_hours: int = 36,
) -> Dict[str, dict]:
    """Return pair-keyed alignments for fresh local price data only."""
    if not price_data:
        return {}

    if isinstance(price_data, dict):
        raw_items = price_data.get("pairs", price_data)
    else:
        raw_items = price_data

    if isinstance(raw_items, dict):
        price_by_pair = {
            str(pair): dict(item, pair=str(pair)) if isinstance(item, dict) else {}
            for pair, item in raw_items.items()
        }
    elif isinstance(raw_items, list):
        price_by_pair = {
            str(item.get("pair")): item
            for item in raw_items
            if isinstance(item, dict) and item.get("pair")
        }
    else:
        return {}

    alignments: Dict[str, dict] = {}
    for pair_context in pair_contexts:
        pair = pair_context.get("pair")
        alignment = build_price_alignment(
            pair_context,
            price_by_pair.get(pair),
            now=now,
            max_age_hours=max_age_hours,
        )
        if alignment:
            alignments[pair] = alignment

    assert_lucid_object_clean(alignments)
    return alignments
