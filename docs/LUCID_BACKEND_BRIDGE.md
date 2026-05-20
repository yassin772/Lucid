# Lucid Backend Bridge

Lucid does not need the full macro report. It only needs a small macro context export from the existing backend.

## Input Contract

Use this shape when exporting from `macro-scenarios-bot`:

```json
{
  "risk_environment": {
    "label": "neutral"
  },
  "narratives": {
    "USD": {
      "currency_bias": "soutenu",
      "dominant_tone": "hawkish",
      "coherence": "forte"
    }
  }
}
```

Required narrative fields:

- `currency_bias`: `soutenu`, `neutre`, or `fragile`
- `dominant_tone`: backend tone key
- `coherence`: `forte`, `moderee`, or `faible`

Optional:

- `risk_environment.label`: `risk_on`, `risk_off`, or `neutral`

Lucid converts this into frontend-safe copy:

- `Supported`, `Neutral`, or `Weak`
- one dominant idea per currency
- 2 or 3 simple reasons
- no trading advice
- no exposed backend contradictions

## Generate Calendar-Only Payload

```bash
PYTHONPATH=. PYTHONPYCACHEPREFIX=/private/tmp/lucid_pycache python3 scripts/refresh_lucid_live.py
```

## Build The App Payload

This is the recommended daily command. It uses `api/macro_context.json` when available, otherwise it falls back to the live calendar, then validates the result.

```bash
PYTHONPATH=. PYTHONPYCACHEPREFIX=/private/tmp/lucid_pycache python3 scripts/build_lucid_app_payload.py
```

## Run The Local Web App

This is the simplest way to use Lucid locally. It builds the payload, validates it, and serves the web app.

```bash
PYTHONPATH=. PYTHONPYCACHEPREFIX=/private/tmp/lucid_pycache python3 scripts/run_lucid_app.py
```

Then open:

```text
http://127.0.0.1:8000/lucid_web_app_v2_lucid.html
```

With a real backend folder:

```bash
PYTHONPATH=. PYTHONPYCACHEPREFIX=/private/tmp/lucid_pycache python3 scripts/run_lucid_app.py \
  --backend-root /path/to/macro-scenarios-bot
```

Preview narrative mode locally with the sample context:

```bash
PYTHONPATH=. PYTHONPYCACHEPREFIX=/private/tmp/lucid_pycache python3 scripts/build_lucid_app_payload.py \
  --use-sample-context \
  --output api/lucid_payload_narrative_sample.json
```

## Connect A Real Backend Folder

Use this when the full `macro-scenarios-bot` folder lives somewhere else on the machine.

```bash
PYTHONPATH=. PYTHONPYCACHEPREFIX=/private/tmp/lucid_pycache python3 scripts/connect_lucid_backend.py \
  --backend-root /path/to/macro-scenarios-bot
```

The connector tries two routes:

- default: read `storage/narratives/*_narrative.json`
- optional: run `WeeklyMacroAnalysis` with `--method weekly`
- fallback-style mode: try storage first, then weekly with `--method auto`

It writes:

- `api/macro_context.json`
- `api/lucid_payload.json`

Then the web app can show `Macro context` and `Full macro view`.

## Generate Narrative Payload From A Macro Context File

```bash
PYTHONPATH=. PYTHONPYCACHEPREFIX=/private/tmp/lucid_pycache python3 scripts/refresh_lucid_live.py \
  --narratives-json examples/macro_context_sample.json
```

## Export Only The Macro Context

Run this from the complete `macro-scenarios-bot` environment when you want a small bridge file instead of a full frontend payload:

```bash
PYTHONPATH=. PYTHONPYCACHEPREFIX=/private/tmp/lucid_pycache python3 scripts/export_macro_context_from_weekly.py
```

Then generate the Lucid frontend payload:

```bash
PYTHONPATH=. PYTHONPYCACHEPREFIX=/private/tmp/lucid_pycache python3 scripts/refresh_lucid_live.py \
  --narratives-json api/macro_context.json
```

## Generate Directly From Full Weekly Backend

Run this only from the complete `macro-scenarios-bot` environment:

```bash
PYTHONPATH=. PYTHONPYCACHEPREFIX=/private/tmp/lucid_pycache python3 scripts/export_lucid_from_weekly.py
```

If the full backend modules are not present, the script exits with a clear message instead of breaking silently.

## Validate A Payload Before Serving It

```bash
PYTHONPATH=. PYTHONPYCACHEPREFIX=/private/tmp/lucid_pycache python3 scripts/validate_lucid_payload.py api/lucid_payload.json
```
