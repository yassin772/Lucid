from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.lucid_compliance import (
    ALLOWED_CONFIDENCE_LEVELS,
    ALLOWED_SUMMARY_LABELS,
    ALLOWED_TIMEFRAMES,
    DISCLAIMER,
    assert_lucid_object_clean,
)
from scripts.export_lucid_payload import CURRENCIES


def validate_payload(payload: dict) -> None:
    if payload.get("disclaimer") != DISCLAIMER:
        raise ValueError("Missing or invalid Lucid disclaimer.")

    summaries = payload.get("lucid_summaries")
    if not isinstance(summaries, dict):
        raise ValueError("lucid_summaries must be an object keyed by currency.")

    if set(summaries) != set(CURRENCIES):
        raise ValueError("lucid_summaries must contain exactly the 8 configured currencies.")

    for currency, summary in summaries.items():
        if summary.get("currency") != currency:
            raise ValueError(f"{currency} summary has an invalid currency field.")
        if summary.get("label") not in ALLOWED_SUMMARY_LABELS:
            raise ValueError(f"{currency} summary has an invalid label.")
        if summary.get("confidence") not in ALLOWED_CONFIDENCE_LEVELS:
            raise ValueError(f"{currency} summary has an invalid confidence.")
        if summary.get("timeframe") not in ALLOWED_TIMEFRAMES:
            raise ValueError(f"{currency} summary has an invalid timeframe.")
        reasons = summary.get("reasons")
        if not isinstance(reasons, list) or not 2 <= len(reasons) <= 3:
            raise ValueError(f"{currency} summary must have 2 or 3 reasons.")
        for field in ("headline", "invalidation", "insight"):
            if not summary.get(field):
                raise ValueError(f"{currency} summary is missing {field}.")

    events = payload.get("lucid_events")
    if not isinstance(events, list):
        raise ValueError("lucid_events must be a list.")

    narrative_focus = payload.get("narrative_focus")
    if not isinstance(narrative_focus, dict):
        raise ValueError("narrative_focus must be an object.")
    for field in ("theme", "focus_currency", "headline", "supporting_themes", "rationale"):
        if field not in narrative_focus:
            raise ValueError(f"narrative_focus is missing {field}.")
    if not isinstance(narrative_focus.get("supporting_themes"), list):
        raise ValueError("narrative_focus supporting_themes must be a list.")

    macro_evolution = payload.get("macro_evolution")
    if not isinstance(macro_evolution, dict):
        raise ValueError("macro_evolution must be an object.")
    for field in ("state", "summary", "primary_theme", "emerging_theme", "confidence"):
        if field not in macro_evolution:
            raise ValueError(f"macro_evolution is missing {field}.")
    if macro_evolution.get("state") not in {"stable", "focus_shifting", "pressure_emerging", "event_test_ahead"}:
        raise ValueError("macro_evolution has an invalid state.")
    if macro_evolution.get("confidence") not in {"low", "medium", "high"}:
        raise ValueError("macro_evolution has an invalid confidence.")
    if not macro_evolution.get("summary"):
        raise ValueError("macro_evolution summary is required.")

    pairs = payload.get("lucid_pairs", [])
    if not isinstance(pairs, list):
        raise ValueError("lucid_pairs must be a list when present.")
    for item in pairs:
        if not isinstance(item, dict):
            raise ValueError("Each Lucid pair item must be an object.")
        for field in ("pair", "base", "quote", "state", "drivers", "takeaway"):
            if field not in item:
                raise ValueError(f"Lucid pair item is missing {field}.")
        narrative = item.get("narrative")
        if isinstance(narrative, dict):
            for field in ("theme", "interaction_type", "headline", "rationale", "what_changes_this"):
                if field not in narrative:
                    raise ValueError(f"Lucid pair narrative is missing {field}.")

    price_alignment = payload.get("price_alignment")
    if price_alignment is not None:
        if not isinstance(price_alignment, dict):
            raise ValueError("price_alignment must be an object keyed by pair when present.")
        allowed_states = {"Aligned", "Mixed", "Diverging", "Transitioning"}
        known_pairs = {item.get("pair") for item in pairs if isinstance(item, dict)}
        for pair, item in price_alignment.items():
            if pair not in known_pairs:
                raise ValueError(f"price_alignment contains an unknown pair: {pair}.")
            if not isinstance(item, dict):
                raise ValueError("Each price_alignment item must be an object.")
            for field in ("pair", "state", "summary", "observed_behavior", "caveat", "price_updated_at"):
                if field not in item:
                    raise ValueError(f"price_alignment {pair} is missing {field}.")
            if item.get("pair") != pair:
                raise ValueError(f"price_alignment {pair} has an invalid pair field.")
            if item.get("state") not in allowed_states:
                raise ValueError(f"price_alignment {pair} has an invalid state.")
            if item.get("caveat") != "Price behavior can differ from macro context in the short term.":
                raise ValueError(f"price_alignment {pair} has an invalid caveat.")
            if not item.get("price_updated_at"):
                raise ValueError(f"price_alignment {pair} is missing price_updated_at.")

    macro_shock = payload.get("macro_shock")
    if macro_shock is not None:
        if not isinstance(macro_shock, dict):
            raise ValueError("macro_shock must be an object or null.")
        for field in (
            "shock_type",
            "severity",
            "confidence",
            "title",
            "transmission_chain",
            "supports",
            "pressures",
            "explanation",
            "source_count",
        ):
            if field not in macro_shock:
                raise ValueError(f"macro_shock is missing {field}.")
        if macro_shock.get("severity") not in {"Medium", "High"}:
            raise ValueError("macro_shock severity must be Medium or High.")
        if macro_shock.get("confidence") not in {"Medium", "High"}:
            raise ValueError("macro_shock confidence must be Medium or High.")
        if macro_shock.get("source_count", 0) < 2:
            raise ValueError("macro_shock requires at least two confirming sources.")
        for list_field in ("transmission_chain", "supports", "pressures"):
            if not isinstance(macro_shock.get(list_field), list) or not macro_shock[list_field]:
                raise ValueError(f"macro_shock {list_field} must be a non-empty list.")

    assert_lucid_object_clean(payload)


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate a Lucid frontend payload.")
    parser.add_argument("payload", nargs="?", default="api/lucid_payload.json")
    args = parser.parse_args()

    try:
        payload = json.loads(Path(args.payload).read_text(encoding="utf-8"))
        validate_payload(payload)
    except Exception as exc:
        print(f"Invalid Lucid payload: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    print(f"Valid Lucid payload: {args.payload}")


if __name__ == "__main__":
    main()
