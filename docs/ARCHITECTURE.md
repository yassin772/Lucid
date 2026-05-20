# Lucid Architecture

## Current State

This repository currently contains:

- `lucid_web_app_v2_lucid.html`: active local web app
- `api/`: generated frontend payloads
- `modules/`: Lucid backend engines and adapters
- `scripts/`: local build, validation, backend connection, and serving commands
- `tests/`: Python unit tests
- `core/`: partial compatibility file from the original macro backend
- `examples/`: sample macro context input
- `lucid_events_dev/`: prototype/reference code

## Target Structure

```text
project/
├── src/
│   ├── app/
│   ├── components/
│   ├── features/
│   ├── services/
│   ├── utils/
│   ├── types/
│   └── config/
├── tests/
├── docs/
├── scripts/
├── README.md
├── .env.example
└── config files at root
```

## Low-Risk Changes Applied

- Created the `src/` skeleton.
- Moved technical documentation into `docs/`.
- Added a root `README.md`.
- Added `.env.example`.

## Files Intentionally Left In Place

- `lucid_web_app_v2_lucid.html`: moving it would change the current local URL.
- `api/`: the app fetches `./api/lucid_payload.json`.
- `modules/`: scripts and tests import `modules.*` directly.
- `scripts/`: commands are already stable and user-facing.
- `core/weekly_macro_analysis.py`: partial compatibility file with imports from the original backend.
- `examples/`: scripts currently use `examples/macro_context_sample.json`.
- `lucid_events_dev/`: prototype/reference folder, not production runtime.

## Next Safe Migration Step

The safest next code migration is to introduce compatibility packages before moving runtime modules. For example:

1. Add imports/tests for a future `src/services` layout.
2. Move one Lucid-only module at a time.
3. Keep old import paths as temporary compatibility wrappers.
4. Remove wrappers only after all scripts and tests are updated.
