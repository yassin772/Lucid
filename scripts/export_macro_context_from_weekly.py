from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.export_lucid_payload import CURRENCIES


NARRATIVE_FIELDS = ("currency_bias", "dominant_tone", "coherence")


def _read_field(obj, name: str, fallback=None):
    if isinstance(obj, dict):
        return obj.get(name, fallback)
    return getattr(obj, name, fallback)


def _export_narrative(narrative) -> dict:
    return {
        field: _read_field(narrative, field)
        for field in NARRATIVE_FIELDS
        if _read_field(narrative, field) is not None
    }


def build_macro_context() -> dict:
    try:
        from core.weekly_macro_analysis import WeeklyMacroAnalysis
    except ModuleNotFoundError as exc:
        missing = exc.name or "a required backend module"
        raise RuntimeError(
            f"Cannot load the full macro backend here. Missing module: {missing}. "
            "Run this script from the complete macro-scenarios-bot environment."
        ) from exc

    weekly = WeeklyMacroAnalysis().build_weekly_summary()
    narratives = {
        currency: _export_narrative(weekly.narratives[currency])
        for currency in CURRENCIES
        if currency in weekly.narratives and weekly.narratives[currency] is not None
    }

    risk = weekly.risk_environment
    risk_environment = {}
    if risk is not None:
        label = _read_field(risk, "label")
        if label:
            risk_environment["label"] = label

    return {
        "generated_at": datetime.now().astimezone().isoformat(),
        "source": "macro-scenarios-bot WeeklyMacroAnalysis",
        "risk_environment": risk_environment,
        "narratives": narratives,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Export the minimal macro context needed by Lucid.")
    parser.add_argument("--output", default="api/macro_context.json", help="Macro context JSON output path.")
    args = parser.parse_args()

    try:
        payload = build_macro_context()
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2) from exc

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {output_path} with {len(payload['narratives'])} narratives.")


if __name__ == "__main__":
    main()
