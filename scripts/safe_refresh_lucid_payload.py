from __future__ import annotations

import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.export_lucid_payload import (
    _load_macro_context,
    _load_previous_payload,
    build_payload,
    resolve_macro_context_path,
    warn_if_macro_context_not_used,
)
from scripts.fetch_fx_prices import PAIR_UNIVERSE, build_price_cache, get_api_key
from scripts.refresh_lucid_live import DEFAULT_ENDPOINTS, _download_raw_events
from scripts.validate_lucid_payload import validate_payload


OUTPUT_PATH = Path("api/lucid_payload.json")
MAX_MACRO_CONTEXT_AGE_DAYS = 7


def _parse_datetime(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _fresh_macro_context_path(path: str | None) -> str | None:
    if not path:
        return None
    context_path = Path(path)
    try:
        payload = json.loads(context_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        print(f"Macro context unavailable or invalid: {path}. Falling back to live calendar only.")
        return None

    generated_at = _parse_datetime(payload.get("generated_at"))
    if generated_at is None:
        print(f"Macro context has no generated_at: {path}. Falling back to live calendar only.")
        return None

    age_days = (datetime.now(timezone.utc) - generated_at).total_seconds() / 86400
    if age_days < -1:
        print(f"Macro context generated_at is unexpectedly in the future: {path}. Falling back to live calendar only.")
        return None
    if age_days > MAX_MACRO_CONTEXT_AGE_DAYS:
        print(
            f"Macro context is stale ({age_days:.1f} days old): {path}. "
            "Falling back to live calendar only."
        )
        return None
    return path


def _optional_price_data(tmp_dir: Path) -> dict | None:
    try:
        api_key = get_api_key()
    except RuntimeError:
        print("Twelve Data key not configured. Continuing without price_alignment.")
        return None

    price_data = build_price_cache(pairs=PAIR_UNIVERSE, api_key=api_key)
    price_path = tmp_dir / "fx_prices_twelvedata.json"
    price_path.write_text(json.dumps(price_data, ensure_ascii=False, indent=2), encoding="utf-8")
    fresh_count = len(price_data.get("pairs") or [])
    print(f"Fetched optional Twelve Data daily prices: {fresh_count} fresh pairs.")
    return price_data if fresh_count else None


def main() -> None:
    tmp_root = Path(os.environ.get("RUNNER_TEMP") or "/tmp") / "lucid_payload_refresh"
    if tmp_root.exists():
        shutil.rmtree(tmp_root)
    tmp_root.mkdir(parents=True, exist_ok=True)

    raw_dir = tmp_root / "raw"
    candidate_path = tmp_root / "lucid_payload_candidate.json"

    raw_events = _download_raw_events(DEFAULT_ENDPOINTS, raw_dir)
    if not raw_events:
        raise RuntimeError("No calendar events were downloaded. Keeping the existing payload.")

    macro_context_path = _fresh_macro_context_path(resolve_macro_context_path())
    narratives, risk_environment, macro_shock_items = _load_macro_context(macro_context_path)
    previous_payload = _load_previous_payload(str(OUTPUT_PATH))
    price_data = _optional_price_data(tmp_root)

    payload = build_payload(
        raw_events,
        narratives=narratives,
        risk_environment=risk_environment,
        macro_shock_items=macro_shock_items,
        price_data=price_data,
        previous_payload=previous_payload,
    )
    payload["source"] = (
        "Macro context and ForexFactory live calendar via scheduled Lucid refresh"
        if narratives
        else "ForexFactory live calendar via scheduled Lucid refresh"
    )
    warn_if_macro_context_not_used(macro_context_path, payload)
    validate_payload(payload)

    candidate_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(candidate_path, OUTPUT_PATH)

    generated_at = payload.get("generated_at") or datetime.now(timezone.utc).isoformat()
    print(
        f"Refreshed {OUTPUT_PATH} safely. "
        f"summary_mode={payload.get('summary_mode')} "
        f"events={len(payload.get('lucid_events') or [])} "
        f"generated_at={generated_at}"
    )


if __name__ == "__main__":
    main()
