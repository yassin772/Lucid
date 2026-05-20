from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.export_lucid_payload import PAIR_UNIVERSE


TWELVE_DATA_URL = "https://api.twelvedata.com/time_series"
DEFAULT_OUTPUT = Path("api/raw/fx_prices_twelvedata.json")
DEFAULT_INTERVAL = "1day"
DEFAULT_OUTPUT_SIZE = 20
MIN_BARS = 8
REQUEST_TIMEOUT_SEC = 20


def get_api_key(env: dict[str, str] | None = None) -> str:
    values = env if env is not None else os.environ
    key = (values.get("TWELVE_DATA_API_KEY") or values.get("TWELVEDATA_API_KEY") or "").strip()
    if not key:
        raise RuntimeError("Missing Twelve Data API key. Set TWELVE_DATA_API_KEY.")
    return key


def parse_timestamp(value: object) -> Optional[datetime]:
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


def freshness_limit_hours(latest: datetime, now: datetime) -> int:
    current = now.astimezone(timezone.utc)
    last = latest.astimezone(timezone.utc)
    if current.weekday() in {5, 6} or (current.weekday() == 0 and last.weekday() == 4):
        return 84
    return 36


def is_fresh_daily(latest: datetime, now: datetime) -> bool:
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now = now.astimezone(timezone.utc)
    if latest > now:
        return latest <= now + timedelta(hours=24)
    age_seconds = (now - latest).total_seconds()
    return age_seconds <= freshness_limit_hours(latest, now) * 3600


def as_float(value: object) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def pct_change(newer: float, older: float) -> Optional[float]:
    if older == 0:
        return None
    return round(((newer - older) / older) * 100, 4)


def normalize_twelvedata_pair(pair: str, raw: dict, *, now: Optional[datetime] = None) -> Optional[dict]:
    values = raw.get("values")
    if not isinstance(values, list) or len(values) < MIN_BARS:
        return None

    rows = []
    for item in values:
        if not isinstance(item, dict):
            continue
        timestamp = parse_timestamp(item.get("datetime"))
        close = as_float(item.get("close"))
        if timestamp and close is not None:
            rows.append({"timestamp": timestamp, "close": close})

    rows.sort(key=lambda item: item["timestamp"])
    if len(rows) < MIN_BARS:
        return None

    current = now or datetime.now(timezone.utc)
    latest = rows[-1]["timestamp"]
    if not is_fresh_daily(latest, current):
        return None

    recent = pct_change(rows[-1]["close"], rows[-2]["close"])
    previous = pct_change(rows[-2]["close"], rows[-3]["close"]) if len(rows) >= 3 else None
    if recent is None:
        return None

    return {
        "pair": pair,
        "price_updated_at": latest.isoformat().replace("+00:00", "Z"),
        "recent_change_pct": recent,
        "previous_change_pct": previous,
        "bars_count": len(rows),
        "last_close": rows[-1]["close"],
    }


def fetch_twelvedata_pair(pair: str, api_key: str, *, interval: str = DEFAULT_INTERVAL, outputsize: int = DEFAULT_OUTPUT_SIZE) -> dict:
    query = urlencode({
        "symbol": pair,
        "interval": interval,
        "outputsize": str(outputsize),
        "apikey": api_key,
    })
    request = Request(f"{TWELVE_DATA_URL}?{query}", headers={"User-Agent": "Lucid/1.0"})
    with urlopen(request, timeout=REQUEST_TIMEOUT_SEC) as response:
        return json.loads(response.read().decode("utf-8"))


def build_price_cache(
    *,
    pairs: Iterable[str],
    api_key: str,
    now: Optional[datetime] = None,
    interval: str = DEFAULT_INTERVAL,
    outputsize: int = DEFAULT_OUTPUT_SIZE,
) -> dict:
    normalized = []
    errors = []
    current = now or datetime.now(timezone.utc)
    for pair in pairs:
        try:
            raw = fetch_twelvedata_pair(pair, api_key, interval=interval, outputsize=outputsize)
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            errors.append({"pair": pair, "error": str(exc)})
            continue
        if raw.get("status") == "error":
            errors.append({"pair": pair, "error": raw.get("message", "Twelve Data returned an error")})
            continue
        item = normalize_twelvedata_pair(pair, raw, now=current)
        if item:
            normalized.append(item)

    payload = {
        "source": "twelve_data",
        "generated_at": current.isoformat().replace("+00:00", "Z"),
        "interval": interval,
        "pairs": normalized,
    }
    if errors:
        payload["errors"] = errors
    return payload


def write_payload(payload: dict, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch daily FX prices for Lucid PriceAlignmentEngine.")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--pair", action="append", help="FX pair to fetch. Defaults to Lucid's curated pair universe.")
    parser.add_argument("--outputsize", type=int, default=DEFAULT_OUTPUT_SIZE)
    parser.add_argument("--allow-missing-key", action="store_true", help="Write an empty cache instead of failing when the API key is missing.")
    args = parser.parse_args()

    try:
        api_key = get_api_key()
    except RuntimeError as exc:
        if not args.allow_missing_key:
            print(str(exc), file=sys.stderr)
            raise SystemExit(2) from exc
        payload = {
            "source": "twelve_data",
            "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "interval": DEFAULT_INTERVAL,
            "pairs": [],
            "errors": [{"error": str(exc)}],
        }
        write_payload(payload, Path(args.output))
        print(f"Wrote empty FX price cache to {args.output}: {exc}")
        return

    payload = build_price_cache(
        pairs=args.pair or PAIR_UNIVERSE,
        api_key=api_key,
        outputsize=max(args.outputsize, MIN_BARS),
    )
    write_payload(payload, Path(args.output))
    print(f"Wrote {args.output} with {len(payload['pairs'])} fresh FX pairs.")


if __name__ == "__main__":
    main()
