# FlowForm Vitality (Master Suite) — Standalone Founder Build

This is the **standalone** FlowForm Vitality Master Suite for founder testing.

Key goals:
- **BootSafe** on Windows (double-click friendly)
- Deterministic port selection (preferred port + fallback range)
- Usable core journeys: plan wizard → sessions → recovery → analytics → exports/restore
- Coach Assistant: /assistant (LLM optional; safe fallback always works)

---

## Quickstart (Windows)

Run these from the `_BAT` folder (recommended):

1. `1_setup.bat`
2. `2_run.bat`
3. `3_open_browser.bat`
4. `6_run_tests.bat`

Notes:
- `2_run.bat` launches a persistent **FlowForm Server** window so crashes stay visible.
- If scripts finish instantly when double-clicked, they will still show a **Pause** screen (so you can read errors).

---

## Root scripts (advanced)

If you prefer running from a terminal in the repo root:
- `setup.bat`
- `run.bat`
- `open_browser.bat`
- `run_tests.bat`

---

## Configuration

On first setup, `setup.bat` copies `.env.example` → `.env`.

Common settings:
- `PORT` preferred port (default 5410)
- `DB_PATH` database path (default `./data/flowform.db`)
- `ENABLE_AUTH=false` keeps **Founder Mode** (no login)

---

## Key URLs

- Ready page: `http://127.0.0.1:<port>/ready`
- Health: `http://127.0.0.1:<port>/health`
- API Health: `http://127.0.0.1:<port>/api/health`
- Diagnostics: `http://127.0.0.1:<port>/diagnostics`

---

## Folder layout

- `templates/` Flask UI templates
- `tools/` test + structure guard tooling
- `site/` optional static marketing pages (not required for app boot)
- `data/` local SQLite DB location (created at runtime)

