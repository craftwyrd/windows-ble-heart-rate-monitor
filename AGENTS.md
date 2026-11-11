# Repository Guidelines

## Project structure

PulseDesk is a Windows desktop heart-rate monitor. The application and its BLE/UI logic live in `app.py`; parser tests are in `test_app.py`. `build.ps1` creates the virtual environment, runs tests, and packages the app. `PulseDesk.spec` contains PyInstaller settings. `README.md` describes usage and `DESIGN_BRIEF.md` records product/design context. Build output belongs in `build/` and `dist/`; do not edit generated files as source.

## Build, test, and development

Use Windows PowerShell for the supported build workflow:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\build.ps1
```

This installs `requirements.txt`, runs the test suite, then creates `dist\PulseDesk.exe`. To run tests without packaging, use `python -m pytest` (or `.\.venv\Scripts\python.exe -m pytest` after setup). To run the source app during development, use `.\.venv\Scripts\python.exe app.py`.

## Coding style

Follow the existing Python style: four-space indentation, `snake_case` functions and variables, and `PascalCase` classes. Keep BLE parsing and UI behavior testable in small functions where practical. No formatter or linter is configured, so keep changes consistent with nearby code and avoid unrelated formatting edits. Use PowerShell syntax consistent with `build.ps1` for build changes.

## Testing

Tests use pytest and live in `test_*.py`; test functions use the `test_` prefix. Add or update focused tests for parsing and other behavior that can be exercised without Bluetooth hardware. Run `python -m pytest` before submitting changes; the packaging script also enforces this check before rebuilding.

## Commits and pull requests

Git history is not available in this checkout, so no established commit convention could be verified. Use short, imperative subjects such as `Handle incomplete heart-rate payloads`. Pull requests should explain user-visible behavior and test results, link any related issue, and include screenshots for UI changes. Mention hardware or Windows-specific validation that was not available locally.

## Configuration and generated files

Declare Python dependencies in `requirements.txt`. Do not commit virtual-environment or generated packaging output (`.venv/`, `build/`, `dist/`, or `__pycache__/`) unless a release explicitly requires an artifact.
