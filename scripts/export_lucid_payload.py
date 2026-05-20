from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from modules.forexfactory_scraper import (
    COUNTRY_TO_CURRENCY,
    FF_ENDPOINTS,
    ForexFactoryScraper,
    IMPACT_FILTER,
)
from modules.lucid_compliance import DISCLAIMER, assert_lucid_object_clean, clean_lucid_text
from modules.lucid_event_engine import LucidEventEngine
from modules.lucid_macro_evolution_engine import build_macro_evolution
from modules.lucid_macro_shock_engine import detect_macro_shock
from modules.lucid_narrative_orchestrator import build_narrative_focus
from modules.lucid_pair_narrative_engine import build_pair_narrative
from modules.lucid_price_alignment_engine import build_price_alignments
from modules.lucid_summary_engine import build_lucid_summaries


CURRENCIES = ["USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD"]
DEFAULT_MACRO_CONTEXT_PATH = Path("api/macro_context.json")

PAIR_UNIVERSE = [
    "EUR/USD",
    "GBP/USD",
    "USD/JPY",
    "USD/CHF",
    "AUD/USD",
    "USD/CAD",
    "NZD/USD",
    "EUR/GBP",
    "EUR/JPY",
    "GBP/JPY",
    "EUR/AUD",
    "EUR/CAD",
    "GBP/AUD",
    "AUD/JPY",
    "NZD/JPY",
    "GBP/CHF",
    "EUR/CHF",
    "AUD/NZD",
    "CAD/JPY",
    "GBP/NZD",
]

CB_BY_CURRENCY = {
    "USD": "The Fed",
    "EUR": "The ECB",
    "GBP": "The BOE",
    "JPY": "The BOJ",
    "CHF": "The SNB",
    "CAD": "The BOC",
    "AUD": "The RBA",
    "NZD": "The RBNZ",
}

EVENT_HEADLINES = {
    "CPI": "Inflation is the main thing to watch",
    "PPI": "Price pressure is the main thing to watch",
    "NFP": "Jobs data is the main thing to watch",
    "UNEMPLOYMENT": "Labor market data is the main thing to watch",
    "GDP": "Growth is the main thing to watch",
    "PMI_COMPOSITE": "Business activity is the main thing to watch",
    "PMI_MFG": "Factory activity is the main thing to watch",
    "PMI_SERVICES": "Services activity is the main thing to watch",
    "ISM": "US business activity is the main thing to watch",
    "RETAIL_SALES": "Consumer spending is the main thing to watch",
    "CONSUMER_CONFIDENCE": "Consumer confidence is the main thing to watch",
    "TRADE_BALANCE": "Trade flows are the main thing to watch",
    "INDUSTRIAL_PRODUCTION": "Industrial output is the main thing to watch",
}

CB_EVENT_TYPES = {"FOMC", "ECB", "BOE", "BOJ", "SNB", "BOC", "RBA", "RBNZ", "INTEREST_RATE"}


def _load_raw_events(paths: Optional[Sequence[str]]) -> List[dict]:
    if paths:
        raw_events: List[dict] = []
        for path in paths:
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    data = json.load(handle)
            except json.JSONDecodeError:
                print(f"Skipping non-JSON raw event file: {path}")
                continue
            if isinstance(data, list):
                raw_events.extend(data)
        return raw_events

    scraper = ForexFactoryScraper()
    raw_events: List[dict] = []
    for key in ("thisweek", "nextweek"):
        fetched = scraper._fetch_json(FF_ENDPOINTS[key]) or []
        raw_events.extend(fetched)
    return raw_events


def _load_raw_shocks(paths: Optional[Sequence[str]]) -> List[dict]:
    raw_items: List[dict] = []
    for path in paths or []:
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except FileNotFoundError:
            print(f"Macro shock file not found: {path}")
            continue
        except json.JSONDecodeError:
            print(f"Skipping non-JSON macro shock file: {path}")
            continue
        if isinstance(data, list):
            raw_items.extend(item for item in data if isinstance(item, (dict, str)))
        elif isinstance(data, dict):
            items = data.get("headlines") or data.get("macro_shocks") or data.get("news") or []
            scenarios = data.get("scenarios")
            if isinstance(items, list):
                raw_items.extend(item for item in items if isinstance(item, (dict, str)))
            if isinstance(scenarios, dict):
                for scenario_items in scenarios.values():
                    if isinstance(scenario_items, list):
                        raw_items.extend(item for item in scenario_items if isinstance(item, (dict, str)))
    return raw_items


def _load_price_data(path: Optional[str]) -> Optional[object]:
    if not path:
        return None
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"Price data file not found: {path}")
        return None
    except json.JSONDecodeError:
        print(f"Skipping non-JSON price data file: {path}")
        return None


def _load_previous_payload(path: Optional[str]) -> Optional[dict]:
    if not path:
        return None
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"Previous payload file not found: {path}")
        return None
    except json.JSONDecodeError:
        print(f"Skipping non-JSON previous payload file: {path}")
        return None
    return data if isinstance(data, dict) else None


def _namespace(value):
    if isinstance(value, dict):
        return SimpleNamespace(**{key: _namespace(item) for key, item in value.items()})
    if isinstance(value, list):
        return [_namespace(item) for item in value]
    return value


def _load_macro_context(path: Optional[str]) -> Tuple[Dict[str, SimpleNamespace], Optional[SimpleNamespace], List[dict]]:
    if not path:
        return {}, None, []

    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"Macro context file not found: {path}")
        return {}, None, []
    except json.JSONDecodeError:
        print(f"Macro context file is not JSON: {path}")
        return {}, None, []

    raw_narratives = payload.get("narratives") if isinstance(payload, dict) else None
    if raw_narratives is None and isinstance(payload, dict):
        raw_narratives = {
            key: value
            for key, value in payload.items()
            if key in CURRENCIES and isinstance(value, dict)
        }

    narratives = {
        currency: _namespace(value)
        for currency, value in (raw_narratives or {}).items()
        if currency in CURRENCIES and isinstance(value, dict)
    }

    risk_environment = None
    raw_risk = payload.get("risk_environment") if isinstance(payload, dict) else None
    if isinstance(raw_risk, dict):
        risk_environment = _namespace(raw_risk)

    raw_shocks = payload.get("macro_shock_headlines") or payload.get("macro_shocks") or payload.get("news") or []
    macro_shock_items = raw_shocks if isinstance(raw_shocks, list) else []

    return narratives, risk_environment, macro_shock_items


def resolve_macro_context_path(path: Optional[str] = None) -> Optional[str]:
    """Return an explicit macro context path, or the default one when present."""
    if path:
        return path
    if DEFAULT_MACRO_CONTEXT_PATH.exists():
        return str(DEFAULT_MACRO_CONTEXT_PATH)
    return None


def warn_if_macro_context_not_used(path: Optional[str], payload: dict) -> None:
    """Surface accidental calendar-only exports when a macro context was available."""
    if not path:
        return
    if payload.get("summary_mode") == "Calendar-derived":
        print(
            "WARNING: macro context was provided or detected but the payload is Calendar-derived. "
            f"Check that {path} contains valid narratives."
        )


def _normalise_live_events(raw_events: Iterable[dict]):
    scraper = ForexFactoryScraper()
    normalised = []
    for raw in raw_events:
        event = scraper._parse_raw(raw)
        if not event:
            continue
        if event.impact not in IMPACT_FILTER:
            continue
        if event.country not in COUNTRY_TO_CURRENCY:
            continue
        item = scraper._normalize(event, is_upcoming=(event.actual in (None, "")))
        if item:
            normalised.append((event, item))
    return normalised


def _to_lucid_event_inputs(normalised_pairs):
    inputs = []
    for raw, item in normalised_pairs:
        inputs.append(SimpleNamespace(
            date=item.date,
            currency=item.currency,
            event_type=item.event_type,
            title=item.title,
            importance=raw.impact.lower(),
        ))
    return inputs


def _event_label(event) -> str:
    timing = getattr(event, "timing_label", "This week")
    title = getattr(event, "title", "")
    importance = getattr(event, "importance", "unknown")
    impact = f" · {importance.capitalize()} impact" if importance in {"high", "medium", "low"} else ""
    timing_text = "" if timing == "Upcoming event" else f" {timing.lower()}"
    return clean_lucid_text(f"{title}{timing_text}{impact}") or None


def _dedupe_lucid_events(lucid_events: List) -> List:
    result = []
    seen = set()
    for event in lucid_events:
        key = (event.currency, event.title, event.timing_label)
        if key in seen:
            continue
        seen.add(key)
        result.append(event)
    return result


def _summaries_to_payload(summaries: Dict[str, object]) -> Dict[str, dict]:
    return {
        currency: asdict(summary) if hasattr(summary, "__dataclass_fields__") else dict(summary)
        for currency, summary in summaries.items()
    }


def _summary_for_currency(currency: str, lucid_events: List) -> dict:
    events = [event for event in lucid_events if event.currency == currency]
    key_event = _event_label(events[0]) if events else None
    cb = CB_BY_CURRENCY.get(currency, "The central bank")

    if not events:
        return {
            "currency": currency,
            "label": "Neutral",
            "confidence": "Low",
            "timeframe": "Mixed",
            "headline": "No major event in focus",
            "reasons": [
                "The calendar is quiet for this currency",
                "The market is waiting for clearer economic data",
            ],
            "invalidation": "This view changes when a new major event appears",
            "insight": "There is no single fresh driver standing out right now",
            "key_event": None,
        }

    first = events[0]
    event_type = first.event_type
    if event_type in CB_EVENT_TYPES or first.is_cb_speech:
        headline = f"{cb} is in focus"
        reasons = [
            "Central bank language can change how investors read the economy",
            "The market is watching whether policy sounds more careful or more patient",
        ]
        insight = "The key is how the central bank explains growth and inflation"
    else:
        headline = EVENT_HEADLINES.get(event_type, "Fresh data is in focus")
        reasons = [
            first.why_it_matters,
            first.market_focus,
        ]
        insight = first.insight

    summary = {
        "currency": currency,
        "label": "Neutral",
        "confidence": "Medium" if events else "Low",
        "timeframe": "Short-term" if key_event else "Mixed",
        "headline": clean_lucid_text(headline),
        "reasons": [clean_lucid_text(reason) for reason in reasons if clean_lucid_text(reason)][:3],
        "invalidation": clean_lucid_text("This view changes if the event message is different from what the market expects"),
        "insight": clean_lucid_text(insight),
        "key_event": key_event,
    }
    assert_lucid_object_clean(summary)
    return summary


def _market_story(lucid_events: List) -> dict:
    if not lucid_events:
        return {
            "title": "The market is waiting for new data",
            "body": "No major high or medium importance event stands out in the current calendar.",
            "drivers": [
                "The calendar is quiet",
                "Currencies need clearer economic data",
                "Central bank language remains important",
            ],
        }

    currencies = []
    for event in lucid_events:
        if event.currency not in currencies:
            currencies.append(event.currency)
    first = lucid_events[0]
    title = f"{first.currency} is the first macro focus"
    if first.event_type in CB_EVENT_TYPES or first.is_cb_speech:
        title = f"{first.currency} central bank communication is in focus"
    elif first.event_type in EVENT_HEADLINES:
        title = f"{first.currency} {EVENT_HEADLINES[first.event_type].lower()}"

    drivers = []
    for event in lucid_events[:3]:
        text = clean_lucid_text(f"{event.currency}: {event.title}")
        if text and text not in drivers:
            drivers.append(text)

    return {
        "title": clean_lucid_text(title),
        "body": clean_lucid_text(
            f"The current calendar puts {', '.join(currencies[:4])} in focus. "
            "Lucid keeps the view simple: what matters, why it matters, and what the market is watching."
        ),
        "drivers": drivers[:3],
    }


def _market_story_from_summaries(summaries: Dict[str, dict]) -> dict:
    supported = [currency for currency, summary in summaries.items() if summary.get("label") == "Supported"]
    weak = [currency for currency, summary in summaries.items() if summary.get("label") == "Weak"]

    def _join_currencies(currencies: List[str]) -> str:
        selected = currencies[:3]
        if len(selected) <= 1:
            return "".join(selected)
        if len(selected) == 2:
            return " and ".join(selected)
        return f"{', '.join(selected[:-1])}, and {selected[-1]}"

    def _verb(currencies: List[str], singular: str, plural: str) -> str:
        return singular if len(currencies[:3]) == 1 else plural

    if supported or weak:
        lead = (supported or weak)[0]
        title = summaries[lead].get("headline") or f"{lead} is the main macro focus"
        if supported and weak:
            body = (
                f"{_join_currencies(supported)} {_verb(supported, 'remains', 'remain')} supported, "
                f"while {_join_currencies(weak)} {_verb(weak, 'stays', 'stay')} under pressure."
            )
        elif supported:
            body = f"{_join_currencies(supported)} {_verb(supported, 'remains', 'remain')} supported."
        else:
            body = f"{_join_currencies(weak)} {_verb(weak, 'stays', 'stay')} under pressure."
    else:
        title = "The market is waiting for clearer macro data"
        body = "No currency has a strong enough macro story to stand out clearly right now."

    drivers = []
    for currency, summary in summaries.items():
        if summary.get("label") == "Neutral":
            continue
        text = clean_lucid_text(f"{currency}: {summary.get('headline', '')}")
        if text:
            drivers.append(text)

    return {
        "title": clean_lucid_text(title),
        "body": clean_lucid_text(body),
        "drivers": drivers[:3],
    }


def _label_score(label: str) -> int:
    if label == "Supported":
        return 1
    if label == "Weak":
        return -1
    return 0


def _pair_context(
    pair: str,
    summaries: Dict[str, dict],
    narrative_focus: Optional[dict] = None,
    macro_pressure: Optional[dict] = None,
    risk_environment: Optional[object] = None,
) -> dict:
    base, quote = pair.split("/")
    base_summary = summaries.get(base, {})
    quote_summary = summaries.get(quote, {})
    base_label = base_summary.get("label", "Neutral")
    quote_label = quote_summary.get("label", "Neutral")
    difference = _label_score(base_label) - _label_score(quote_label)

    if difference >= 2:
        state = f"{base} has the firmer macro backdrop"
        takeaway = f"The macro backdrop is firmer for {base} than {quote}"
    elif difference <= -2:
        state = f"{quote} has the firmer macro backdrop"
        takeaway = f"The macro backdrop is firmer for {quote} than {base}"
    else:
        state = "Mixed macro picture"
        takeaway = "The two currencies do not show a clean macro contrast right now"

    drivers = [
        f"{base}: {base_summary.get('headline', 'No clear driver')}",
        f"{quote}: {quote_summary.get('headline', 'No clear driver')}",
    ]
    narrative = build_pair_narrative(
        pair,
        summaries,
        narrative_focus=narrative_focus,
        macro_pressure=macro_pressure,
        risk_environment=risk_environment,
    )
    result = {
        "pair": pair,
        "base": base,
        "quote": quote,
        "state": clean_lucid_text(state),
        "directional_state": clean_lucid_text(narrative.get("directional_state", "Mixed")),
        "base_label": base_label,
        "quote_label": quote_label,
        "base_key_event": base_summary.get("key_event"),
        "quote_key_event": quote_summary.get("key_event"),
        "has_key_event": bool(base_summary.get("key_event") or quote_summary.get("key_event")),
        "narrative_focus_match": bool(
            narrative_focus
            and (
                narrative_focus.get("focus_currency") in {base, quote}
                or narrative_focus.get("theme") == narrative.get("theme")
            )
        ),
        "drivers": [clean_lucid_text(driver) for driver in drivers],
        "takeaway": clean_lucid_text(takeaway),
        "narrative": narrative,
        "tension_summary": narrative["tension_summary"],
        "interaction_reason": narrative["interaction_reason"],
        "what_changes_this": narrative["what_changes_this"],
    }
    assert_lucid_object_clean(result)
    return result


def _build_lucid_pairs(
    summaries: Dict[str, dict],
    narrative_focus: Optional[dict] = None,
    macro_pressure: Optional[dict] = None,
    risk_environment: Optional[object] = None,
) -> List[dict]:
    return [
        _pair_context(
            pair,
            summaries,
            narrative_focus=narrative_focus,
            macro_pressure=macro_pressure,
            risk_environment=risk_environment,
        )
        for pair in PAIR_UNIVERSE
    ]


def build_payload(
    raw_events: Optional[List[dict]] = None,
    narratives: Optional[Dict[str, object]] = None,
    risk_environment: Optional[object] = None,
    macro_shock_items: Optional[List[dict]] = None,
    price_data: Optional[object] = None,
    previous_payload: Optional[dict] = None,
) -> dict:
    raw_events = raw_events if raw_events is not None else _load_raw_events(None)
    normalised = _normalise_live_events(raw_events)
    lucid_inputs = _to_lucid_event_inputs(normalised)
    today_str = datetime.now().strftime("%Y-%m-%d")
    lucid_events = LucidEventEngine().build_lucid_events(lucid_inputs, today_str=today_str)
    lucid_events = _dedupe_lucid_events(lucid_events)
    macro_shock = detect_macro_shock(macro_shock_items or [])

    summary_mode = "Narrative-derived" if narratives else "Calendar-derived"
    if narratives:
        lucid_summaries = build_lucid_summaries(
            narratives=narratives,
            lucid_events=lucid_events,
            risk_environment=risk_environment,
        )
        summaries = _summaries_to_payload(lucid_summaries)
        market_story = _market_story_from_summaries(summaries)
    else:
        summaries = {
            currency: _summary_for_currency(currency, lucid_events)
            for currency in CURRENCIES
        }
        market_story = _market_story(lucid_events)
    narrative_focus = build_narrative_focus(
        summaries=summaries,
        lucid_events=lucid_events,
        risk_environment=risk_environment,
        macro_pressure=macro_shock,
    )
    macro_evolution = build_macro_evolution(
        narrative_focus=narrative_focus,
        macro_pressure=macro_shock,
        summaries=summaries,
        lucid_events=lucid_events,
        market_mood=risk_environment,
        previous_payload=previous_payload,
    )

    lucid_pairs = _build_lucid_pairs(
        summaries,
        narrative_focus=narrative_focus,
        macro_pressure=macro_shock,
        risk_environment=risk_environment,
    )
    price_alignment = build_price_alignments(lucid_pairs, price_data)

    payload = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "source": (
            "Macro context and ForexFactory calendar via Lucid payload export"
            if narratives
            else "ForexFactory calendar via Lucid payload export"
        ),
        "summary_mode": summary_mode,
        "disclaimer": DISCLAIMER,
        "market_story": market_story,
        "macro_shock": macro_shock,
        "narrative_focus": narrative_focus,
        "macro_evolution": macro_evolution,
        "lucid_summaries": summaries,
        "lucid_pairs": lucid_pairs,
        "lucid_events": [asdict(event) for event in lucid_events],
    }
    if price_alignment:
        payload["price_alignment"] = price_alignment
    assert_lucid_object_clean(payload)
    return payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--raw-json",
        action="append",
        help="Path to a ForexFactory raw JSON file. Can be passed more than once.",
    )
    parser.add_argument(
        "--narratives-json",
        help="Optional macro context JSON. Defaults to api/macro_context.json when that file exists.",
    )
    parser.add_argument(
        "--shock-json",
        action="append",
        help="Optional JSON file containing macro shock headlines. Can be passed more than once.",
    )
    parser.add_argument(
        "--price-json",
        help="Optional local FX price JSON for backend-only Price Alignment development.",
    )
    parser.add_argument(
        "--previous-payload",
        help="Optional previous Lucid payload used only for conservative macro evolution context.",
    )
    parser.add_argument("--output", default="api/lucid_payload.json")
    args = parser.parse_args()

    raw_events = _load_raw_events(args.raw_json)
    macro_context_path = resolve_macro_context_path(args.narratives_json)
    narratives, risk_environment, context_shocks = _load_macro_context(macro_context_path)
    macro_shock_items = context_shocks + _load_raw_shocks(args.shock_json)
    price_data = _load_price_data(args.price_json)
    previous_payload = _load_previous_payload(args.previous_payload)
    payload = build_payload(
        raw_events,
        narratives=narratives,
        risk_environment=risk_environment,
        macro_shock_items=macro_shock_items,
        price_data=price_data,
        previous_payload=previous_payload,
    )
    warn_if_macro_context_not_used(macro_context_path, payload)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {output_path} with {len(payload['lucid_events'])} Lucid events.")


if __name__ == "__main__":
    main()
