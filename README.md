# Lucid

Lucid is a mobile-first macro understanding layer for retail Forex traders.

The current local app is served from:

```text
http://localhost:8000/lucid_web_app_v2_lucid.html
```

## Local Run

```bash
PYTHONPATH=. PYTHONPYCACHEPREFIX=/private/tmp/lucid_pycache python3 scripts/run_lucid_app.py
```

## Build Payload

```bash
PYTHONPATH=. PYTHONPYCACHEPREFIX=/private/tmp/lucid_pycache python3 scripts/build_lucid_app_payload.py
```

## Validate Payload

```bash
PYTHONPATH=. PYTHONPYCACHEPREFIX=/private/tmp/lucid_pycache python3 scripts/validate_lucid_payload.py api/lucid_payload.json
```

## Architecture Notes

The project is being migrated carefully toward a `src/` structure. Existing runtime files remain in place until moving them is proven safe.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
