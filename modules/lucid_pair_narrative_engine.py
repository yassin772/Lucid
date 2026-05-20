"""
Deterministic relational narratives for Lucid FX pairs.

The currency layer explains each currency on its own. This module explains the
macro tension between two currencies without adding trading advice or forecasts.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Dict, Optional, Tuple

from modules.lucid_compliance import assert_lucid_object_clean, clean_lucid_text


@dataclass(frozen=True)
class PairNarrative:
    pair: str
    theme: str
    directional_state: str
    interaction_type: str
    headline: str
    rationale: str
    what_changes_this: str
    dominant_currency: Optional[str]
    tension_summary: str
    left_reason: str
    right_reason: str
    interaction_reason: str


def _label_score(label: str) -> int:
    if label == "Supported":
        return 1
    if label == "Weak":
        return -1
    return 0


def _summary_text(summary: dict) -> str:
    return " ".join(
        str(part)
        for part in [
            summary.get("headline", ""),
            summary.get("insight", ""),
            " ".join(summary.get("reasons", []) or []),
        ]
        if part
    ).lower()


STRUCTURAL_DRIVER_BY_CURRENCY = {
    "USD": "policy",
    "EUR": "growth",
    "GBP": "inflation",
    "JPY": "risk_mood",
    "CHF": "risk_mood",
    "AUD": "global_demand",
    "NZD": "global_demand",
    "CAD": "oil",
}


def _driver_key(summary: dict) -> str:
    headline = str(summary.get("headline", "")).lower()
    text = _summary_text(summary)
    currency = str(summary.get("currency", "")).upper()

    if (
        "no major event" in headline
        or "calendar is quiet" in text
        or "no single fresh driver" in text
    ):
        return STRUCTURAL_DRIVER_BY_CURRENCY.get(currency, "macro")

    # The headline is the currency layer's chosen dominant idea, so let it
    # outweigh supporting reasons when classifying pair tension.
    if any(term in headline for term in ("jobs", "employment", "unemployment", "labor")):
        return "jobs"
    if any(term in headline for term in ("inflation", "price pressure", "cpi", "ppi")):
        return "inflation"
    if any(term in headline for term in ("rate", "policy", "fed", "ecb", "boe", "boj", "snb", "boc", "rba", "rbnz")):
        return "policy"
    if any(term in headline for term in ("oil", "canadian demand")):
        return "oil"
    if any(term in headline for term in ("china", "commodity", "global demand", "global trade")):
        return "global_demand"
    if any(term in headline for term in ("risk mood", "defensive", "safer", "safety", "yen", "franc")):
        return "risk_mood"
    if any(term in headline for term in ("growth", "economy", "economic", "gdp", "pmi", "manufacturing")):
        return "growth"

    if any(term in text for term in ("oil", "canadian demand")):
        return "oil"
    if any(term in text for term in ("china", "commodity", "global demand", "global trade")):
        return "global_demand"
    if any(term in text for term in ("risk mood", "defensive", "safer", "safety", "yen", "franc")):
        return "risk_mood"
    if any(term in text for term in ("jobs", "employment", "unemployment", "labor")):
        return "jobs"
    if any(term in text for term in ("rate", "policy", "central bank", "fed", "ecb", "boe", "boj", "snb", "boc", "rba", "rbnz")):
        return "policy"
    if any(term in text for term in ("inflation", "price pressure")):
        return "inflation"
    if any(term in text for term in ("growth", "economy", "economic", "gdp", "pmi", "manufacturing")):
        return "growth"
    return "macro"


def _driver_phrase(currency: str, driver: str, label: str) -> str:
    if label == "Neutral":
        if driver == "policy":
            return f"{currency} is shaped by policy expectations"
        if driver == "jobs":
            return f"{currency} is shaped by labor data"
        if driver == "growth":
            return f"{currency} is shaped by its growth backdrop"
        if driver == "inflation":
            return f"{currency} is shaped by inflation data"
        if driver == "risk_mood":
            return f"{currency} is shaped by market mood"
        if driver == "global_demand":
            return f"{currency} is shaped by global demand"
        if driver == "oil":
            return f"{currency} is shaped by oil and demand"
        return f"{currency} is shaped by macro context"
    if driver == "policy":
        return f"{currency} still has firmer policy support" if label == "Supported" else f"{currency} is pressured by softer policy expectations"
    if driver == "jobs":
        return f"{currency} is supported by resilient labor data" if label == "Supported" else f"{currency} is held back by softer labor data"
    if driver == "growth":
        return f"{currency} is supported by stronger growth" if label == "Supported" else f"{currency} is held back by weaker growth"
    if driver == "inflation":
        return f"{currency} is shaped by inflation pressure" if label == "Supported" else f"{currency} is pressured as inflation slows"
    if driver == "risk_mood":
        return f"{currency} benefits from defensive market mood" if label == "Supported" else f"{currency} has less support from market mood"
    if driver == "global_demand":
        return f"{currency} is helped by global demand" if label == "Supported" else f"{currency} is pressured by softer global demand"
    if driver == "oil":
        return f"{currency} is helped by oil and demand" if label == "Supported" else f"{currency} is pressured by softer oil and demand"
    return f"{currency} has a firmer macro backdrop" if label == "Supported" else f"{currency} has a softer macro backdrop"


def _country_phrase(currency: str, driver: str) -> str:
    if currency == "EUR":
        return "Europe"
    if currency == "USD":
        return "the US"
    if currency == "GBP":
        return "the UK"
    if currency == "JPY":
        return "the yen"
    if currency == "CHF":
        return "the franc"
    if currency == "CAD" and driver == "oil":
        return "CAD"
    if currency == "AUD":
        return "AUD"
    if currency == "NZD":
        return "NZD"
    return currency


def _sentence_case(value: str) -> str:
    return value[:1].upper() + value[1:] if value else value


def _join_clause(left: str, connector: str, right: str) -> str:
    if left.endswith("expectations"):
        connector = {
            "is set against": "are set against",
            "sits against": "sit against",
            "meets": "meet",
            "differs from": "differ from",
        }.get(connector, connector)
    return f"{_sentence_case(left)} {connector} {right}."


def _driver_clause(currency: str, driver: str, label: str) -> str:
    place = _country_phrase(currency, driver)
    if driver == "policy":
        if label == "Neutral":
            if currency == "USD":
                return "US policy expectations"
            if currency == "GBP":
                return "UK policy expectations"
            return f"{place} policy expectations"
        if currency == "USD" and label == "Supported":
            return "US rate support"
        if currency == "USD":
            return "softer US policy expectations"
        if currency == "GBP":
            return "firmer UK policy expectations" if label == "Supported" else "softer UK policy expectations"
        return f"firmer {place} policy expectations" if label == "Supported" else f"softer {place} policy expectations"
    if driver == "jobs":
        if label == "Neutral":
            return f"{place} labor data"
        return f"resilient {place} labor data" if label == "Supported" else f"softer {place} labor data"
    if driver == "growth":
        if label == "Neutral":
            return f"{place}'s growth picture"
        return f"{place}'s growth support" if label == "Supported" else f"{place}'s softer growth picture"
    if driver == "inflation":
        if label == "Neutral":
            return f"{place} inflation pressure"
        return f"{place} inflation pressure" if label == "Supported" else f"cooler {place} inflation pressure"
    if driver == "risk_mood":
        if currency in {"JPY", "CHF"}:
            name = "yen" if currency == "JPY" else "franc"
            return f"the {name}'s sensitivity to defensive market mood"
        return f"{currency}'s sensitivity to market mood"
    if driver == "global_demand":
        return f"global demand sensitivity in {currency}"
    if driver == "oil":
        if label == "Neutral":
            return f"oil sensitivity in {currency}"
        return f"oil-linked support in {currency}" if label == "Supported" else f"oil sensitivity in {currency}"
    return f"{currency}'s macro backdrop"


def _interaction_type(base_driver: str, quote_driver: str, macro_pressure: Optional[dict], base: str, quote: str) -> str:
    if _macro_pressure_applies(macro_pressure, base, quote):
        return "macro_pressure_transmission"

    drivers = {base_driver, quote_driver}
    if drivers == {"policy", "growth"} or drivers == {"jobs", "growth"}:
        return "policy_vs_growth"
    if "policy" in drivers and "risk_mood" in drivers:
        return "policy_vs_safe_haven"
    if "global_demand" in drivers and "policy" in drivers:
        return "global_demand_vs_policy"
    if "oil" in drivers and "risk_mood" in drivers:
        return "oil_vs_risk_mood"
    if drivers == {"inflation", "growth"}:
        return "inflation_vs_growth"
    if "global_demand" in drivers and "risk_mood" in drivers:
        return "cyclical_vs_defensive"
    if "risk_mood" in drivers and ("policy" in drivers or "jobs" in drivers):
        return "risk_mood_vs_rates"
    if "oil" in drivers and quote in {"JPY", "CHF", "USD"}:
        return "commodity_vs_safe_haven"
    if drivers == {"growth", "global_demand"}:
        return "growth_vs_global_demand"
    return "mixed_macro_forces"


def _theme_for_interaction(interaction_type: str, base_driver: str, quote_driver: str) -> str:
    if interaction_type == "macro_pressure_transmission":
        return "macro_pressure"
    if interaction_type in {"global_demand_vs_policy", "cyclical_vs_defensive"}:
        return "global_demand"
    if interaction_type in {"oil_vs_risk_mood", "commodity_vs_safe_haven"}:
        return "commodities"
    if interaction_type in {"policy_vs_safe_haven", "risk_mood_vs_rates"}:
        return "risk_mood"
    if interaction_type == "inflation_vs_growth":
        return "inflation"
    if interaction_type == "policy_vs_growth":
        return "policy_growth"
    if interaction_type == "growth_vs_global_demand":
        return "global_demand"
    if base_driver == quote_driver:
        return base_driver
    return "macro_backdrop"


def _macro_pressure_applies(macro_pressure: Optional[dict], base: str, quote: str) -> bool:
    if not macro_pressure:
        return False
    affected = set(macro_pressure.get("supports", []) or []) | set(macro_pressure.get("pressures", []) or [])
    return base in affected and quote in affected


def _headline(
    base: str,
    quote: str,
    base_driver: str,
    quote_driver: str,
    base_label: str,
    quote_label: str,
    interaction_type: str,
    macro_pressure: Optional[dict],
) -> str:
    if interaction_type == "macro_pressure_transmission":
        return f"Current macro pressure is affecting {base} and {quote} through different channels."

    base_clause = _driver_clause(base, base_driver, base_label)
    quote_clause = _driver_clause(quote, quote_driver, quote_label)

    if interaction_type == "policy_vs_growth":
        return _join_clause(base_clause, "is set against", quote_clause)
    if interaction_type == "policy_vs_safe_haven":
        return _join_clause(base_clause, "sits against", quote_clause)
    if interaction_type == "global_demand_vs_policy":
        return _join_clause(base_clause, "meets", quote_clause)
    if interaction_type == "oil_vs_risk_mood":
        return _join_clause(base_clause, "sits against", quote_clause)
    if interaction_type == "inflation_vs_growth":
        return _join_clause(base_clause, "differs from", quote_clause)
    if interaction_type == "cyclical_vs_defensive":
        return _join_clause(base_clause, "meets", quote_clause)
    if interaction_type == "risk_mood_vs_rates":
        return _join_clause(base_clause, "is set against", quote_clause)
    if interaction_type == "commodity_vs_safe_haven":
        return _join_clause(base_clause, "sits against", quote_clause)
    if interaction_type == "growth_vs_global_demand":
        return _join_clause(base_clause, "differs from", quote_clause)
    return "These two currencies are being shaped by different macro forces, but the contrast is not clean enough yet."


def _rationale(interaction_type: str, base: str, quote: str) -> str:
    rationales = {
        "policy_vs_growth": "The pair is easier to read through the gap between policy support and growth pressure.",
        "policy_vs_safe_haven": "The pair links rate expectations with defensive market mood.",
        "global_demand_vs_policy": "The pair links global demand sensitivity with policy expectations.",
        "oil_vs_risk_mood": "The pair links oil sensitivity with defensive currency behavior.",
        "inflation_vs_growth": "The pair links inflation pressure with the growth backdrop.",
        "cyclical_vs_defensive": "The pair links cyclical demand sensitivity with defensive positioning.",
        "risk_mood_vs_rates": "The pair links market mood sensitivity with rate expectations.",
        "macro_pressure_transmission": "The pair is being shaped by the same macro pressure through different channels.",
        "commodity_vs_safe_haven": "The pair links commodity sensitivity with defensive currency demand.",
        "growth_vs_global_demand": "The pair links local growth pressure with global demand sensitivity.",
    }
    return rationales.get(interaction_type, f"{base} and {quote} do not show one clean relational theme yet.")


def _mixed_headline(base: str, quote: str, base_driver: str, quote_driver: str) -> str:
    if base_driver == quote_driver:
        return f"{base} and {quote} are being shaped by a similar macro driver."
    interaction_type = _interaction_type(base_driver, quote_driver, None, base, quote)
    if interaction_type != "mixed_macro_forces":
        return _headline(base, quote, base_driver, quote_driver, "Neutral", "Neutral", interaction_type, None)
    return "These two currencies are being shaped by different macro forces, but the contrast is not clean enough yet."


def _what_changes_this(strong: str, weak: str, strong_driver: str, weak_driver: str, mixed: bool) -> str:
    if mixed:
        return "A clearer shift in one side would make the pair easier to read."
    if weak_driver == "growth":
        return f"The imbalance would narrow if {weak} growth improves or {strong} loses support."
    if weak_driver == "global_demand":
        return f"The imbalance would narrow if global demand improves or {strong} loses support."
    if weak_driver == "risk_mood":
        return f"The imbalance would narrow if market mood shifts back toward {weak}."
    if strong_driver in {"policy", "jobs"}:
        return f"The imbalance would narrow if {strong} policy expectations soften."
    return f"The imbalance would narrow if {weak} gains support or {strong} loses support."


def build_pair_narrative(
    pair: str,
    summaries: Dict[str, dict],
    narrative_focus: Optional[dict] = None,
    macro_pressure: Optional[dict] = None,
    risk_environment=None,
) -> dict:
    base, quote = pair.split("/")
    base_summary = summaries.get(base, {})
    quote_summary = summaries.get(quote, {})
    base_label = base_summary.get("label", "Neutral")
    quote_label = quote_summary.get("label", "Neutral")
    base_driver = _driver_key(base_summary)
    quote_driver = _driver_key(quote_summary)

    base_score = _label_score(base_label)
    quote_score = _label_score(quote_label)
    interaction_type = _interaction_type(base_driver, quote_driver, macro_pressure, base, quote)
    mixed = base_score == quote_score or abs(base_score - quote_score) < 2
    relational = interaction_type != "mixed_macro_forces"
    directional_state = "Mixed" if mixed else "Clear backdrop contrast"
    theme = _theme_for_interaction(interaction_type, base_driver, quote_driver)

    if mixed:
        headline = _headline(
            base,
            quote,
            base_driver,
            quote_driver,
            base_label,
            quote_label,
            interaction_type,
            macro_pressure,
        ) if relational else _mixed_headline(base, quote, base_driver, quote_driver)
        narrative = PairNarrative(
            pair=pair,
            theme=theme,
            directional_state=directional_state,
            interaction_type=interaction_type,
            headline=headline,
            rationale=_rationale(interaction_type, base, quote),
            what_changes_this=_what_changes_this(base, quote, base_driver, quote_driver, mixed=True),
            dominant_currency=None,
            tension_summary=headline,
            left_reason=_driver_phrase(base, base_driver, base_label),
            right_reason=_driver_phrase(quote, quote_driver, quote_label),
            interaction_reason=(
                _rationale(interaction_type, base, quote)
                if relational
                else "The relationship is still mixed rather than one-sided."
            ),
        )
    else:
        base_dominates = base_score > quote_score
        strong = base if base_dominates else quote
        weak = quote if base_dominates else base
        strong_driver = base_driver if base_dominates else quote_driver
        weak_driver = quote_driver if base_dominates else base_driver
        headline = _headline(
            base,
            quote,
            base_driver,
            quote_driver,
            base_label,
            quote_label,
            interaction_type,
            macro_pressure,
        )
        narrative = PairNarrative(
            pair=pair,
            theme=theme,
            directional_state=directional_state,
            interaction_type=interaction_type,
            headline=headline,
            rationale=_rationale(interaction_type, base, quote),
            what_changes_this=_what_changes_this(strong, weak, strong_driver, weak_driver, mixed=False),
            dominant_currency=strong,
            tension_summary=headline,
            left_reason=_driver_phrase(base, base_driver, base_label),
            right_reason=_driver_phrase(quote, quote_driver, quote_label),
            interaction_reason=_rationale(interaction_type, base, quote),
        )

    result = asdict(narrative)
    result = {key: clean_lucid_text(value) if isinstance(value, str) else value for key, value in result.items()}
    assert_lucid_object_clean(result)
    return result
