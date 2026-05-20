"""
Deterministic narrative orchestration for Lucid.

This layer chooses the clearest macro transmission channel for the payload.
It does not rebalance currencies, override labels, or create new market claims.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Dict, Iterable, List, Optional, Tuple

from modules.lucid_compliance import assert_lucid_object_clean, clean_lucid_text


THEMES = (
    "us_policy",
    "us_inflation",
    "europe_growth",
    "uk_inflation",
    "china_global_demand",
    "oil_cad",
    "risk_mood",
    "safe_haven",
    "macro_pressure",
    "central_bank_guidance",
    "commodities",
    "global_macro_backdrop",
)

THEME_PRIORITY = {theme: index for index, theme in enumerate(THEMES)}


@dataclass(frozen=True)
class LucidNarrativeFocus:
    theme: str
    focus_currency: Optional[str]
    headline: str
    supporting_themes: List[str]
    rationale: str


def _get(obj, key: str, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _summary_text(summary: dict) -> str:
    parts = [
        summary.get("headline", ""),
        summary.get("insight", ""),
        summary.get("invalidation", ""),
        " ".join(summary.get("reasons", []) or []),
    ]
    return " ".join(str(part) for part in parts if part).lower()


def _add(scores: Dict[str, float], theme: str, amount: float) -> None:
    if theme in scores:
        scores[theme] += amount


def _importance(event) -> str:
    return str(_get(event, "importance", "") or "").lower()


def _event_type(event) -> str:
    return str(_get(event, "event_type", "") or "").upper()


def _event_text(event) -> str:
    return " ".join(
        str(part)
        for part in (
            _get(event, "title", ""),
            _get(event, "event_type", ""),
            _get(event, "currency", ""),
        )
        if part
    ).lower()


def _risk_label(risk_environment) -> str:
    return str(_get(risk_environment, "label", "") or "").lower()


def _score_macro_pressure(scores: Dict[str, float], macro_pressure: Optional[dict]) -> Optional[str]:
    if not macro_pressure:
        return None

    shock_type = str(macro_pressure.get("shock_type") or "")
    _add(scores, "macro_pressure", 6.0)
    if shock_type == "oil_supply_shock":
        _add(scores, "oil_cad", 5.0)
        _add(scores, "commodities", 2.0)
        return "CAD"
    if shock_type in {"geopolitical_risk", "banking_stress", "recession_fear", "political_risk", "sanctions"}:
        _add(scores, "risk_mood", 4.0)
        _add(scores, "safe_haven", 3.0)
        return "JPY"
    if shock_type == "trade_tension":
        _add(scores, "china_global_demand", 4.0)
        _add(scores, "risk_mood", 2.0)
        return "AUD"
    return None


def _score_events(scores: Dict[str, float], lucid_events: Iterable) -> Dict[str, str]:
    event_focus: Dict[str, str] = {}
    for event in lucid_events or []:
        if _importance(event) != "high":
            continue

        currency = str(_get(event, "currency", "") or "")
        event_type = _event_type(event)
        text = _event_text(event)

        if currency == "USD" and event_type in {"CPI", "PPI"}:
            _add(scores, "us_inflation", 6.0)
            event_focus.setdefault("us_inflation", "USD")
        elif currency == "USD" and event_type in {"FOMC", "INTEREST_RATE"}:
            _add(scores, "us_policy", 5.0)
            _add(scores, "central_bank_guidance", 2.0)
            event_focus.setdefault("us_policy", "USD")
        elif event_type in {"FOMC", "ECB", "BOE", "BOJ", "SNB", "BOC", "RBA", "RBNZ", "INTEREST_RATE"}:
            _add(scores, "central_bank_guidance", 5.0)
            event_focus.setdefault("central_bank_guidance", currency or None)
        elif currency == "EUR" and event_type in {"GDP", "PMI_COMPOSITE", "PMI_MFG", "PMI_SERVICES", "ISM"}:
            _add(scores, "europe_growth", 5.0)
            event_focus.setdefault("europe_growth", "EUR")
        elif currency == "GBP" and event_type in {"CPI", "PPI"}:
            _add(scores, "uk_inflation", 5.0)
            event_focus.setdefault("uk_inflation", "GBP")

        if currency in {"AUD", "NZD"} and any(term in text for term in ("china", "trade", "commodity", "global demand")):
            _add(scores, "china_global_demand", 4.0)
            event_focus.setdefault("china_global_demand", currency)
        if currency == "CAD" and any(term in text for term in ("oil", "crude", "energy")):
            _add(scores, "oil_cad", 4.0)
            event_focus.setdefault("oil_cad", "CAD")

    return event_focus


def _score_summaries(scores: Dict[str, float], summaries: Dict[str, dict]) -> Dict[str, str]:
    theme_focus: Dict[str, str] = {}
    for currency, summary in (summaries or {}).items():
        text = _summary_text(summary)
        label = summary.get("label", "Neutral")
        confidence = summary.get("confidence", "Low")
        weight = 1.4 if confidence == "High" else 0.9 if confidence == "Medium" else 0.45
        if label == "Neutral":
            weight *= 0.7

        if currency == "USD" and any(term in text for term in ("fed", "policy", "rate", "restrictive")):
            _add(scores, "us_policy", 3.0 * weight)
            theme_focus.setdefault("us_policy", "USD")
        if currency == "USD" and any(term in text for term in ("inflation", "cpi", "price pressure", "ppi")):
            _add(scores, "us_inflation", 2.6 * weight)
            theme_focus.setdefault("us_inflation", "USD")
        if currency == "EUR" and any(term in text for term in ("growth", "europe", "economy", "weak data")):
            _add(scores, "europe_growth", 3.0 * weight)
            theme_focus.setdefault("europe_growth", "EUR")
        if currency == "GBP" and any(term in text for term in ("inflation", "sticky", "boe")):
            _add(scores, "uk_inflation", 3.0 * weight)
            theme_focus.setdefault("uk_inflation", "GBP")
        if currency in {"AUD", "NZD"} and any(term in text for term in ("china", "global demand", "global trade", "commodity")):
            _add(scores, "china_global_demand", 2.8 * weight)
            _add(scores, "commodities", 1.2 * weight)
            theme_focus.setdefault("china_global_demand", currency)
        if currency == "CAD" and any(term in text for term in ("oil", "energy", "us demand")):
            _add(scores, "oil_cad", 2.8 * weight)
            _add(scores, "commodities", 1.1 * weight)
            theme_focus.setdefault("oil_cad", "CAD")
        if currency in {"JPY", "CHF"} and any(term in text for term in ("risk mood", "defensive", "safe", "safer", "market mood")):
            _add(scores, "safe_haven", 2.2 * weight)
            _add(scores, "risk_mood", 1.8 * weight)
            theme_focus.setdefault("safe_haven", currency)

    return theme_focus


def _apply_risk_context(scores: Dict[str, float], risk_environment) -> None:
    risk = _risk_label(risk_environment)
    if risk in {"risk_off", "defensive"}:
        _add(scores, "risk_mood", 3.0)
        _add(scores, "safe_haven", 2.0)
    elif risk in {"risk_on", "constructive"}:
        _add(scores, "risk_mood", 2.0)
        _add(scores, "china_global_demand", 1.2)


def _apply_repetition_penalty(scores: Dict[str, float], summaries: Dict[str, dict]) -> None:
    policy_mentions = 0
    for summary in (summaries or {}).values():
        text = _summary_text(summary)
        if any(term in text for term in ("fed", "rate", "rates", "policy")):
            policy_mentions += 1
    if policy_mentions >= 4:
        # Light penalty only: USD/Fed can still win when supported by events or pressure.
        scores["us_policy"] = max(0.0, scores["us_policy"] - 1.5)


def _theme_headline(theme: str, focus_currency: Optional[str], macro_pressure: Optional[dict]) -> str:
    if theme == "macro_pressure" and macro_pressure:
        return macro_pressure.get("title") or "Macro pressure is shaping market mood"
    headlines = {
        "us_policy": "Fed policy remains the main macro anchor",
        "us_inflation": "US inflation is the main macro focus",
        "europe_growth": "European growth is the clearest macro drag",
        "uk_inflation": "UK inflation keeps GBP in focus",
        "china_global_demand": "China and global demand are shaping cyclical currencies",
        "oil_cad": "Oil sensitivity is keeping CAD in focus",
        "risk_mood": "Market mood is shaping currency backdrops",
        "safe_haven": "Defensive demand is shaping safer currencies",
        "macro_pressure": "Macro pressure is shaping market mood",
        "central_bank_guidance": "Central bank guidance is in focus",
        "commodities": "Commodity sensitivity is shaping currency backdrops",
        "global_macro_backdrop": "The market is waiting for clearer macro direction",
    }
    return headlines.get(theme, "The market is waiting for clearer macro direction")


def _theme_rationale(theme: str, scores: Dict[str, float], macro_pressure: Optional[dict]) -> str:
    if theme == "macro_pressure" and macro_pressure:
        return "A confirmed macro pressure is strong enough to shape the current reading."
    rationales = {
        "us_policy": "Policy expectations remain the strongest structural channel in the current payload.",
        "us_inflation": "A high-impact US inflation event makes inflation the clearest transmission channel.",
        "europe_growth": "The EUR backdrop points to growth as the clearest non-USD driver.",
        "uk_inflation": "The GBP backdrop points to inflation as the clearest UK driver.",
        "china_global_demand": "AUD and NZD are being shaped by China and global demand sensitivity.",
        "oil_cad": "CAD and oil sensitivity create the clearest commodity-linked channel.",
        "risk_mood": "Market mood is affecting several currency backdrops at once.",
        "safe_haven": "JPY and CHF are most tied to defensive market behavior in this reading.",
        "central_bank_guidance": "Central bank communication is the clearest calendar-linked theme.",
        "commodities": "Commodity sensitivity appears across the clearest currency drivers.",
        "global_macro_backdrop": "No single theme is strong enough to stand out clearly.",
    }
    return rationales.get(theme, rationales["global_macro_backdrop"])


def _focus_currency(theme: str, focus_by_theme: Dict[str, str], macro_focus: Optional[str]) -> Optional[str]:
    if theme == "macro_pressure" and macro_focus:
        return macro_focus
    defaults = {
        "us_policy": "USD",
        "us_inflation": "USD",
        "europe_growth": "EUR",
        "uk_inflation": "GBP",
        "china_global_demand": "AUD",
        "oil_cad": "CAD",
        "risk_mood": None,
        "safe_haven": "JPY",
        "macro_pressure": macro_focus,
        "central_bank_guidance": focus_by_theme.get(theme),
        "commodities": "AUD",
        "global_macro_backdrop": None,
    }
    return focus_by_theme.get(theme) or defaults.get(theme)


def _choose_theme(scores: Dict[str, float]) -> str:
    candidates: List[Tuple[float, int, str]] = [
        (score, -THEME_PRIORITY[theme], theme)
        for theme, score in scores.items()
        if theme in THEMES
    ]
    score, _priority, theme = max(candidates)
    return theme if score >= 2.0 else "global_macro_backdrop"


def build_narrative_focus(
    summaries: Dict[str, dict],
    lucid_events: Optional[Iterable] = None,
    risk_environment=None,
    macro_pressure: Optional[dict] = None,
) -> dict:
    scores = {theme: 0.0 for theme in THEMES}
    focus_by_theme: Dict[str, str] = {}

    macro_focus = _score_macro_pressure(scores, macro_pressure)
    focus_by_theme.update(_score_summaries(scores, summaries or {}))
    focus_by_theme.update(_score_events(scores, lucid_events or []))
    _apply_risk_context(scores, risk_environment)
    _apply_repetition_penalty(scores, summaries or {})

    theme = _choose_theme(scores)
    supporting = [
        item[0]
        for item in sorted(scores.items(), key=lambda item: (-item[1], THEME_PRIORITY[item[0]]))
        if item[0] != theme and item[1] >= 2.0
    ][:3]

    focus = LucidNarrativeFocus(
        theme=theme,
        focus_currency=_focus_currency(theme, focus_by_theme, macro_focus),
        headline=clean_lucid_text(_theme_headline(theme, _focus_currency(theme, focus_by_theme, macro_focus), macro_pressure)),
        supporting_themes=supporting,
        rationale=clean_lucid_text(_theme_rationale(theme, scores, macro_pressure)),
    )
    result = asdict(focus)
    assert_lucid_object_clean(result)
    return result
