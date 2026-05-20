from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.lucid_compliance import DISCLAIMER, assert_lucid_object_clean
from scripts.export_lucid_payload import _market_story_from_summaries


def _dataclass_map(items: dict) -> dict:
    return {
        key: asdict(value) if hasattr(value, "__dataclass_fields__") else dict(value)
        for key, value in items.items()
    }


def build_weekly_lucid_payload() -> dict:
    try:
        from core.weekly_macro_analysis import WeeklyMacroAnalysis
    except ModuleNotFoundError as exc:
        missing = exc.name or "a required backend module"
        raise RuntimeError(
            f"Cannot load the full macro backend here. Missing module: {missing}. "
            "Run this script from the complete macro-scenarios-bot environment."
        ) from exc

    weekly = WeeklyMacroAnalysis().build_weekly_summary()
    summaries = _dataclass_map(weekly.lucid_summaries)
    events = [
        asdict(event) if hasattr(event, "__dataclass_fields__") else dict(event)
        for event in weekly.lucid_events
    ]

    payload = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "source": "macro-scenarios-bot WeeklyMacroAnalysis",
        "summary_mode": "Narrative-derived",
        "disclaimer": DISCLAIMER,
        "market_story": _market_story_from_summaries(summaries),
        "lucid_summaries": summaries,
        "lucid_events": events,
    }
    assert_lucid_object_clean(payload)
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Export a frontend Lucid payload from the full weekly macro backend.")
    parser.add_argument("--output", default="api/lucid_payload.json", help="Payload path served by the web app.")
    args = parser.parse_args()

    try:
        payload = build_weekly_lucid_payload()
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2) from exc
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        "Wrote "
        f"{output_path} with {len(payload['lucid_summaries'])} currency summaries "
        f"and {len(payload['lucid_events'])} Lucid events."
    )


if __name__ == "__main__":
    main()
