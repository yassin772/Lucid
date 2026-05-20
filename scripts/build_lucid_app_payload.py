from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.refresh_lucid_live import refresh_payload
from scripts.validate_lucid_payload import validate_payload


def _pick_macro_context(path: str, use_sample: bool) -> str | None:
    candidate = Path(path)
    if candidate.exists():
        return str(candidate)
    if use_sample:
        sample = Path("examples/macro_context_sample.json")
        if sample.exists():
            return str(sample)
    return None


def build_app_payload(
    *,
    macro_context_path: str = "api/macro_context.json",
    use_sample_context: bool = False,
    output_path: str = "api/lucid_payload.json",
    raw_json_paths: list[str] | None = None,
) -> dict:
    candidate = Path(macro_context_path)
    narratives_json_path = _pick_macro_context(macro_context_path, use_sample_context)
    auto_macro_context = macro_context_path == "api/macro_context.json" and not candidate.exists()
    payload = refresh_payload(
        raw_json_paths=raw_json_paths,
        narratives_json_path=narratives_json_path,
        output_path=Path(output_path),
        auto_macro_context=auto_macro_context,
    )
    validate_payload(payload)
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Build and validate the payload consumed by the Lucid web app.")
    parser.add_argument("--macro-context", default="api/macro_context.json")
    parser.add_argument("--use-sample-context", action="store_true", help="Use the sample macro context if no real context exists.")
    parser.add_argument("--raw-json", action="append", help="Use existing ForexFactory raw JSON instead of downloading.")
    parser.add_argument("--output", default="api/lucid_payload.json")
    args = parser.parse_args()

    payload = build_app_payload(
        macro_context_path=args.macro_context,
        use_sample_context=args.use_sample_context,
        output_path=args.output,
        raw_json_paths=args.raw_json,
    )
    print(
        "Built "
        f"{args.output} in {payload['summary_mode']} mode "
        f"with {len(payload['lucid_events'])} Lucid events."
    )


if __name__ == "__main__":
    main()
