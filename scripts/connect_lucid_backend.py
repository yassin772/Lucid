from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.build_lucid_app_payload import build_app_payload
from scripts.export_lucid_payload import CURRENCIES


NARRATIVE_FIELDS = ("currency_bias", "dominant_tone", "coherence")


def _read_field(obj, name: str, fallback=None):
    if isinstance(obj, dict):
        return obj.get(name, fallback)
    return getattr(obj, name, fallback)


def _minimal_narrative(obj) -> dict:
    result = {}
    for field in NARRATIVE_FIELDS:
        value = _read_field(obj, field)
        if value is not None:
            result[field] = value
    return result


def _macro_context_from_weekly_backend(backend_root: Path) -> Optional[dict]:
    exporter = r"""
import json
from datetime import datetime, timezone

from config import SUPPORTED_CURRENCIES
from core.weekly_macro_analysis import WeeklyMacroAnalysis

NARRATIVE_FIELDS = ("currency_bias", "dominant_tone", "coherence")

def read_field(obj, name, fallback=None):
    if isinstance(obj, dict):
        return obj.get(name, fallback)
    return getattr(obj, name, fallback)

def minimal_narrative(obj):
    result = {}
    for field in NARRATIVE_FIELDS:
        value = read_field(obj, field)
        if value is not None:
            result[field] = value
    return result

weekly = WeeklyMacroAnalysis().build_weekly_summary()
narratives = {
    currency: minimal_narrative(weekly.narratives[currency])
    for currency in SUPPORTED_CURRENCIES
    if currency in weekly.narratives and weekly.narratives[currency] is not None
}
risk_environment = {}
if weekly.risk_environment is not None:
    label = read_field(weekly.risk_environment, "label")
    if label:
        risk_environment["label"] = label

payload = {
    "generated_at": datetime.now(timezone.utc).astimezone().isoformat(),
    "source": "macro-scenarios-bot WeeklyMacroAnalysis",
    "risk_environment": risk_environment,
    "narratives": narratives,
}
print(json.dumps(payload, ensure_ascii=False))
"""
    try:
        env = os.environ.copy()
        local_deps = ROOT / ".lucid_backend_deps"
        if local_deps.exists():
            existing = env.get("PYTHONPATH")
            env["PYTHONPATH"] = str(local_deps) if not existing else f"{local_deps}{os.pathsep}{existing}"
        result = subprocess.run(
            [sys.executable, "-c", exporter],
            cwd=str(backend_root),
            check=True,
            capture_output=True,
            text=True,
            env=env,
            timeout=120,
        )
        return json.loads(result.stdout)
    except Exception as exc:
        print(f"Weekly backend export failed, trying storage fallback: {exc}")
        return None


def _load_json(path: Path) -> Optional[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _storage_updated_at(data: dict) -> Optional[datetime]:
    raw = data.get("updated_at")
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _macro_context_from_storage(backend_root: Path) -> Optional[dict]:
    storage_dir = backend_root / "storage" / "narratives"
    if not storage_dir.exists():
        return None

    narratives: Dict[str, dict] = {}
    timestamps: list[datetime] = []
    for currency in CURRENCIES:
        candidates = [
            storage_dir / f"{currency}_narrative.json",
            storage_dir / f"{currency}.json",
            storage_dir / f"{currency.lower()}_narrative.json",
            storage_dir / f"{currency.lower()}.json",
        ]
        for path in candidates:
            data = _load_json(path)
            if not data:
                continue
            narrative = data.get("narrative") if isinstance(data.get("narrative"), dict) else data
            minimal = _minimal_narrative(narrative)
            if minimal:
                narratives[currency] = minimal
                updated_at = _storage_updated_at(data)
                if updated_at:
                    timestamps.append(updated_at)
                break

    if not narratives:
        return None

    generated_at = max(timestamps).isoformat() if timestamps else datetime.now(timezone.utc).astimezone().isoformat()
    return {
        "generated_at": generated_at,
        "source": f"{backend_root} storage/narratives",
        "risk_environment": {"label": "neutral"},
        "narratives": narratives,
    }


def build_macro_context_from_backend(backend_root: str, method: str = "storage") -> dict:
    root = Path(backend_root).expanduser().resolve()
    if not root.exists():
        raise FileNotFoundError(f"Backend root does not exist: {root}")

    context = None
    if method in ("weekly", "auto"):
        context = _macro_context_from_weekly_backend(root)
    if context is None and method in ("storage", "auto"):
        context = _macro_context_from_storage(root)
    if context is None:
        raise RuntimeError(
            "Could not read macro context from backend. Expected either a working "
            "WeeklyMacroAnalysis import or storage/narratives JSON files."
        )
    return context


def main() -> None:
    parser = argparse.ArgumentParser(description="Connect Lucid to a real macro-scenarios-bot backend folder.")
    parser.add_argument("--backend-root", required=True, help="Path to the full macro-scenarios-bot folder.")
    parser.add_argument(
        "--method",
        choices=("storage", "weekly", "auto"),
        default="storage",
        help="storage reads stored narratives without running the full backend. weekly runs WeeklyMacroAnalysis.",
    )
    parser.add_argument("--macro-context-output", default="api/macro_context.json")
    parser.add_argument("--payload-output", default="api/lucid_payload.json")
    parser.add_argument("--raw-json", action="append", help="Optional ForexFactory raw JSON file for offline refresh.")
    args = parser.parse_args()

    try:
        context = build_macro_context_from_backend(args.backend_root, method=args.method)
    except Exception as exc:
        print(f"Could not connect Lucid backend: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    context_path = Path(args.macro_context_output)
    context_path.parent.mkdir(parents=True, exist_ok=True)
    context_path.write_text(json.dumps(context, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {context_path} with {len(context['narratives'])} narratives.")

    payload = build_app_payload(
        macro_context_path=str(context_path),
        output_path=args.payload_output,
        raw_json_paths=args.raw_json,
    )
    print(
        "Built "
        f"{args.payload_output} in {payload['summary_mode']} mode "
        f"with {len(payload['lucid_events'])} Lucid events."
    )


if __name__ == "__main__":
    main()
