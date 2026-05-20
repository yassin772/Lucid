from __future__ import annotations

import argparse
import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.build_lucid_app_payload import build_app_payload
from scripts.connect_lucid_backend import build_macro_context_from_backend


def _write_macro_context_from_backend(backend_root: str, method: str, output_path: Path) -> None:
    context = build_macro_context_from_backend(backend_root, method=method)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    import json

    output_path.write_text(json.dumps(context, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {output_path} with {len(context['narratives'])} narratives.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build and serve the Lucid web app locally.")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--backend-root", help="Optional full macro-scenarios-bot folder.")
    parser.add_argument("--backend-method", choices=("storage", "weekly", "auto"), default="storage")
    parser.add_argument("--use-sample-context", action="store_true")
    parser.add_argument("--raw-json", action="append", help="Optional ForexFactory raw JSON file for offline refresh.")
    args = parser.parse_args()

    macro_context = Path("api/macro_context.json")
    if args.backend_root:
        _write_macro_context_from_backend(args.backend_root, args.backend_method, macro_context)

    payload = build_app_payload(
        macro_context_path=str(macro_context),
        use_sample_context=args.use_sample_context,
        raw_json_paths=args.raw_json,
    )
    print(f"Lucid payload ready: {payload['summary_mode']} mode.")

    handler = partial(SimpleHTTPRequestHandler, directory=str(ROOT))
    server = ThreadingHTTPServer((args.host, args.port), handler)
    url = f"http://{args.host}:{args.port}/lucid_web_app_v2_lucid.html"
    print(f"Lucid is running at {url}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped Lucid local server.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
