from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Iterable, List, Optional, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.forexfactory_scraper import FF_ENDPOINTS, HTTP_HEADERS, REQUEST_TIMEOUT_SEC
from scripts.export_lucid_payload import (
    _load_macro_context,
    build_payload,
    resolve_macro_context_path,
    warn_if_macro_context_not_used,
)


DEFAULT_ENDPOINTS = ("thisweek", "nextweek")


def _load_raw_json(paths: Optional[Sequence[str]]) -> List[dict]:
    raw_events: List[dict] = []
    for path in paths or []:
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print(f"Skipped non-JSON file: {path}")
            continue
        except FileNotFoundError:
            print(f"Skipped missing file: {path}")
            continue
        if isinstance(data, list):
            raw_events.extend(data)
        else:
            print(f"Skipped JSON file that is not an event list: {path}")
    return raw_events


def _download_endpoint(name: str, raw_dir: Path) -> List[dict]:
    url = FF_ENDPOINTS.get(name)
    if not url:
        print(f"Skipped unknown endpoint: {name}")
        return []

    request = Request(url, headers=HTTP_HEADERS)
    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT_SEC) as response:
            body = response.read().decode("utf-8")
    except HTTPError as error:
        print(f"Skipped {name}: HTTP {error.code}")
        return []
    except URLError as error:
        print(f"Skipped {name}: {error.reason}")
        return []

    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        print(f"Skipped {name}: response was not JSON")
        return []

    if not isinstance(data, list):
        print(f"Skipped {name}: response was not an event list")
        return []

    raw_dir.mkdir(parents=True, exist_ok=True)
    raw_path = raw_dir / f"ff_calendar_{name}.json"
    raw_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Downloaded {name}: {len(data)} raw events -> {raw_path}")
    return data


def _download_raw_events(endpoint_names: Iterable[str], raw_dir: Path) -> List[dict]:
    raw_events: List[dict] = []
    for name in endpoint_names:
        raw_events.extend(_download_endpoint(name, raw_dir))
    return raw_events


def refresh_payload(
    *,
    raw_json_paths: Optional[Sequence[str]] = None,
    narratives_json_path: Optional[str] = None,
    endpoint_names: Sequence[str] = DEFAULT_ENDPOINTS,
    raw_dir: Path = Path("api/raw"),
    output_path: Path = Path("api/lucid_payload.json"),
    auto_macro_context: bool = True,
) -> dict:
    raw_events = (
        _load_raw_json(raw_json_paths)
        if raw_json_paths is not None
        else _download_raw_events(endpoint_names, raw_dir)
    )
    macro_context_path = resolve_macro_context_path(narratives_json_path) if auto_macro_context else narratives_json_path
    narratives, risk_environment, macro_shock_items = _load_macro_context(macro_context_path)
    payload = build_payload(
        raw_events,
        narratives=narratives,
        risk_environment=risk_environment,
        macro_shock_items=macro_shock_items,
    )
    warn_if_macro_context_not_used(macro_context_path, payload)
    payload["source"] = (
        "Macro context and ForexFactory live calendar via Lucid refresh"
        if narratives
        else "ForexFactory live calendar via Lucid refresh"
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Refresh the Lucid frontend payload from live calendar data.")
    parser.add_argument(
        "--raw-json",
        action="append",
        help="Use an existing ForexFactory raw JSON file instead of downloading. Can be passed more than once.",
    )
    parser.add_argument(
        "--narratives-json",
        help="Optional macro context JSON. Defaults to api/macro_context.json when that file exists.",
    )
    parser.add_argument(
        "--endpoint",
        action="append",
        choices=sorted(FF_ENDPOINTS.keys()),
        help="ForexFactory endpoint key to download. Defaults to thisweek and nextweek.",
    )
    parser.add_argument("--raw-dir", default="api/raw", help="Directory where downloaded raw JSON is archived.")
    parser.add_argument("--output", default="api/lucid_payload.json", help="Payload path served by the web app.")
    args = parser.parse_args()

    payload = refresh_payload(
        raw_json_paths=args.raw_json,
        narratives_json_path=args.narratives_json,
        endpoint_names=tuple(args.endpoint or DEFAULT_ENDPOINTS),
        raw_dir=Path(args.raw_dir),
        output_path=Path(args.output),
    )
    print(
        "Wrote "
        f"{args.output} with {len(payload['lucid_summaries'])} currency summaries "
        f"and {len(payload['lucid_events'])} Lucid events."
    )


if __name__ == "__main__":
    main()
