"""
Deterministic macro evolution layer for Lucid.

This module does not create a daily recap. It only describes whether the
current macro regime looks stable, is being tested by a relevant event, is
affected by confirmed macro pressure, or has a clear secondary focus.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable, Optional

from modules.lucid_compliance import assert_lucid_object_clean, clean_lucid_text


ALLOWED_MACRO_EVOLUTION_STATES = frozenset({
    "stable",
    "focus_shifting",
    "pressure_emerging",
    "event_test_ahead",
})

ALLOWED_MACRO_EVOLUTION_CONFIDENCE = frozenset({"low", "medium", "high"})

THEME_LABELS = {
    "us_policy": "US policy expectations",
    "us_inflation": "US inflation",
    "europe_growth": "European growth",
    "uk_inflation": "UK inflation",
    "china_global_demand": "China and global demand",
    "oil_cad": "oil sensitivity",
    "risk_mood": "market mood",
    "safe_haven": "defensive demand",
    "macro_pressure": "macro pressure",
    "central_bank_guidance": "central bank guidance",
    "commodities": "commodity sensitivity",
    "global_macro_backdrop": "the broad macro backdrop",
}

PLURAL_THEME_LABELS = frozenset({
    "US policy expectations",
    "China and global demand",
})


@dataclass(frozen=True)
class MacroEvolution:
    state: str
    summary: str
    primary_theme: Optional[str]
    emerging_theme: Optional[str]
    confidence: str


def _get(obj, key: str, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _theme_label(theme: Optional[str]) -> str:
    if not theme:
        return "the broad macro backdrop"
    return THEME_LABELS.get(str(theme), "the broad macro backdrop")


def _theme_verb(theme: Optional[str]) -> str:
    return "remain" if _theme_label(theme) in PLURAL_THEME_LABELS else "remains"


def _sentence_start(text: str) -> str:
    return text[:1].upper() + text[1:] if text else text


def _event_importance(event) -> str:
    return str(_get(event, "importance", "") or "").lower()


def _event_currency(event) -> str:
    return str(_get(event, "currency", "") or "")


def _event_title(event) -> str:
    return str(_get(event, "title", "") or "")


def _event_timing(event) -> str:
    return str(_get(event, "timing_label", "") or "")


def _event_type(event) -> str:
    return str(_get(event, "event_type", "") or "").upper()


def _summary_text(summary: dict) -> str:
    parts = [
        summary.get("headline", ""),
        summary.get("insight", ""),
        " ".join(summary.get("reasons", []) or []),
    ]
    return " ".join(str(part) for part in parts if part).lower()


def _theme_currencies(theme: Optional[str]) -> set[str]:
    return {
        "us_policy": {"USD"},
        "us_inflation": {"USD"},
        "europe_growth": {"EUR"},
        "uk_inflation": {"GBP"},
        "china_global_demand": {"AUD", "NZD"},
        "oil_cad": {"CAD"},
        "risk_mood": {"JPY", "CHF", "AUD", "NZD"},
        "safe_haven": {"JPY", "CHF"},
        "central_bank_guidance": {"USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD"},
        "commodities": {"CAD", "AUD", "NZD"},
    }.get(str(theme), set())


def _event_matches_theme(event, theme: Optional[str], focus_currency: Optional[str]) -> bool:
    currency = _event_currency(event)
    event_type = _event_type(event)
    title = _event_title(event).lower()
    theme = str(theme or "")

    if focus_currency and currency == focus_currency:
        return True
    if currency and currency in _theme_currencies(theme):
        return True
    if theme == "us_policy" and currency == "USD" and event_type in {"FOMC", "INTEREST_RATE", "NFP", "UNEMPLOYMENT"}:
        return True
    if theme == "us_inflation" and currency == "USD" and event_type in {"CPI", "PPI"}:
        return True
    if theme == "europe_growth" and currency == "EUR" and event_type in {"GDP", "PMI_COMPOSITE", "PMI_MFG", "PMI_SERVICES"}:
        return True
    if theme == "uk_inflation" and currency == "GBP" and event_type in {"CPI", "PPI", "BOE"}:
        return True
    if theme == "china_global_demand" and ("china" in title or currency in {"AUD", "NZD"}):
        return True
    if theme == "oil_cad" and (currency == "CAD" or "oil" in title or "crude" in title):
        return True
    if theme in {"risk_mood", "safe_haven"} and currency in {"JPY", "CHF", "USD"}:
        return True
    return False


def _find_relevant_event(narrative_focus: Optional[dict], lucid_events: Optional[Iterable]):
    if not narrative_focus:
        return None

    theme = _get(narrative_focus, "theme")
    focus_currency = _get(narrative_focus, "focus_currency")
    candidates = []
    for event in lucid_events or []:
        importance = _event_importance(event)
        if importance not in {"high", "medium"}:
            continue
        if not _event_matches_theme(event, theme, focus_currency):
            continue
        priority = 0 if importance == "high" else 1
        timing = _event_timing(event).lower()
        timing_priority = 0 if timing == "today" else 1 if timing == "tomorrow" else 2
        candidates.append((priority, timing_priority, _event_title(event), event))

    return sorted(candidates, key=lambda item: item[:3])[0][3] if candidates else None


def _theme_has_summary_support(theme: Optional[str], summaries: Optional[dict]) -> bool:
    currencies = _theme_currencies(theme)
    if not theme or not currencies:
        return False
    for currency in currencies:
        summary = (summaries or {}).get(currency)
        if not isinstance(summary, dict):
            continue
        text = _summary_text(summary)
        if theme == "china_global_demand" and any(term in text for term in ("china", "global demand", "commodity")):
            return True
        if theme == "oil_cad" and any(term in text for term in ("oil", "energy")):
            return True
        if theme == "europe_growth" and any(term in text for term in ("growth", "europe", "economy")):
            return True
        if theme == "uk_inflation" and any(term in text for term in ("inflation", "boe", "price")):
            return True
        if theme in {"risk_mood", "safe_haven"} and any(term in text for term in ("risk mood", "defensive", "safer")):
            return True
        if theme in {"us_policy", "us_inflation"} and any(term in text for term in ("fed", "policy", "inflation", "rate")):
            return True
    return False


def _first_supported_secondary_theme(narrative_focus: Optional[dict], summaries: Optional[dict]) -> Optional[str]:
    primary = _get(narrative_focus, "theme")
    for theme in _get(narrative_focus or {}, "supporting_themes", []) or []:
        if theme == primary or theme == "global_macro_backdrop":
            continue
        if _theme_has_summary_support(theme, summaries):
            return str(theme)
    return None


def _previous_theme(previous_payload: Optional[dict]) -> Optional[str]:
    if not isinstance(previous_payload, dict):
        return None
    focus = previous_payload.get("narrative_focus")
    if not isinstance(focus, dict):
        return None
    theme = focus.get("theme")
    return str(theme) if theme else None


def build_macro_evolution(
    narrative_focus: Optional[dict],
    macro_pressure: Optional[dict] = None,
    summaries: Optional[dict] = None,
    lucid_events: Optional[Iterable] = None,
    market_mood=None,
    previous_payload: Optional[dict] = None,
) -> dict:
    primary_theme = _get(narrative_focus or {}, "theme") or "global_macro_backdrop"
    previous_theme = _previous_theme(previous_payload)

    if macro_pressure:
        title = macro_pressure.get("title") or "Macro pressure is visible in the current backdrop"
        result = MacroEvolution(
            state="pressure_emerging",
            summary=clean_lucid_text(f"{title}."),
            primary_theme=str(primary_theme),
            emerging_theme="macro_pressure",
            confidence="high" if macro_pressure.get("confidence") == "High" else "medium",
        )
    else:
        relevant_event = _find_relevant_event(narrative_focus, lucid_events)
        if relevant_event:
            title = _event_title(relevant_event) or "A relevant macro event"
            result = MacroEvolution(
                state="event_test_ahead",
                summary=clean_lucid_text(f"{title} is the next test for {_theme_label(primary_theme)}."),
                primary_theme=str(primary_theme),
                emerging_theme=None,
                confidence="medium",
            )
        else:
            secondary_theme = _first_supported_secondary_theme(narrative_focus, summaries)
            if previous_theme and previous_theme != primary_theme and primary_theme != "global_macro_backdrop":
                result = MacroEvolution(
                    state="focus_shifting",
                    summary=clean_lucid_text(f"The current reading is centered on {_theme_label(primary_theme)}."),
                    primary_theme=str(primary_theme),
                    emerging_theme=str(primary_theme),
                    confidence="medium",
                )
            elif secondary_theme:
                result = MacroEvolution(
                    state="focus_shifting",
                    summary=clean_lucid_text(
                        f"{_sentence_start(_theme_label(primary_theme))} {_theme_verb(primary_theme)} the backdrop, "
                        f"with {_theme_label(secondary_theme)} also visible."
                    ),
                    primary_theme=str(primary_theme),
                    emerging_theme=secondary_theme,
                    confidence="medium",
                )
            else:
                result = MacroEvolution(
                    state="stable",
                    summary="The macro regime is largely unchanged.",
                    primary_theme=str(primary_theme) if primary_theme else None,
                    emerging_theme=None,
                    confidence="medium",
                )

    payload = asdict(result)
    if payload["state"] not in ALLOWED_MACRO_EVOLUTION_STATES:
        raise ValueError("Invalid macro evolution state.")
    if payload["confidence"] not in ALLOWED_MACRO_EVOLUTION_CONFIDENCE:
        raise ValueError("Invalid macro evolution confidence.")
    assert_lucid_object_clean(payload)
    return payload
