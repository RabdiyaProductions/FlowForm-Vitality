# GOLDEN BASELINE

## Required files sanity check
- `meta.json`
- `run_server.py`
- `_BAT/1_setup.bat`
- `_BAT/2_run.bat`
- `_BAT/3_open_browser.bat`
- `_BAT/6_run_tests.bat`
- `tools/run_full_tests.py`

## Exact boot steps
1. Run `_BAT/1_setup.bat`.
2. Run `_BAT/2_run.bat`.
3. (Optional) Run `_BAT/3_open_browser.bat`.

## Known good URLs (from `meta.json` port, default `5203`)
- Diagnostics: `http://127.0.0.1:5203/diagnostics`
- Projects: `http://127.0.0.1:5203/projects`

## Exact test steps
1. Run `_BAT/6_run_tests.bat`.
2. Run `python tools/run_full_tests.py`.
