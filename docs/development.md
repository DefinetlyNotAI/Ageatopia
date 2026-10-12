# Development

[Documentation index](README.md)

Use [source setup](getting-started.md#source-checkout) first. Commands below run
from the repository root unless stated otherwise.

## Repository layout

| Path | Contents |
| --- | --- |
| `src/backend/` | API, engine, tasks, private grader, store, research tools |
| `src/hooks/` | Inference providers, resource admission, Docker integration |
| `src/hooks/sandbox/` | Dockerfile, trusted observer/supervisor, build patches |
| `src/gui/` | Launcher, control/maintenance/release TUIs, compiled entry point |
| `src/gui/native/` | C Win32/WebView2 host, resources, SVG/ICO, build script |
| `src/gui/dashboard/` | Next.js/TypeScript renderer and package manifest |
| `src/scripts/` | Source launch, maintenance, dashboard routing, verification |
| `db/` | Drizzle schema and migrations |
| `tests/` | Python checks, including optional real-provider integration |
| `docs/` | Current guides and clearly labeled historical records |
| `.data/` | Persistent source-world state and operator credentials |
| `dist/` | Dependencies, builds, caches, logs, exports, training, releases |

Keep source changes in `src/`. The dashboard helper copies source into
`dist/gui/dashboard`, installs there, and routes npm actions there. Editing that
build copy does not replace editing the source directory.

## Python environment

```powershell
.\dist\python\venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
$env:PYTHONPATH = Join-Path (Get-Location) 'src'
$env:PYTHONPYCACHEPREFIX = Join-Path (Get-Location) 'dist/cache/python'
```

PowerShell entry points set those source/cache paths automatically. The runtime
reads `UTOPIA_DATA` for backend data, `UTOPIA_API_URL` for the dashboard proxy,
and `UTOPIA_OPERATOR_TOKEN` for an explicitly supplied operator credential. These
existing environment names remain part of the implementation contract.
`AGEATOPIA_OUTPUT_DIR` selects the allowed generated-output root; the compiled
launcher sets it before backend imports. Never put credentials in documentation,
source fixtures, or agent context.

## Build individual components

Use maintenance actions, or these commands:

```powershell
npm --prefix src/gui/dashboard run build
pwsh -NoProfile -ExecutionPolicy Bypass -File .\src\gui\native\build.ps1
docker build -t ageatopia-world:1 src/hooks/sandbox
```

Native building needs the x64 C++ workload and Windows SDK. The script downloads
the pinned WebView2 SDK. The sandbox Dockerfile includes its supporting patches
and trusted Python programs; retain the entire context when copying it.

For dashboard development, `npm --prefix src/gui/dashboard run dev` uses the same
build routing. A standalone backend can be launched with `python -m backend.cli
--port 8000`. The dashboard proxy needs the corresponding backend URL and data
directory containing its operator token. Stop conflicting services before using
the full desktop launcher; it does not adopt an existing server.

## Verification

Run the relevant checks for the changed area. Python checks:

```powershell
python -m pytest -q
python -m ruff check src tests
python -m ruff format --check src tests
```

Dashboard checks:

```powershell
npm --prefix src/gui/dashboard run typecheck
npm --prefix src/gui/dashboard run lint
npm --prefix src/gui/dashboard run build
```

The verification wrapper supports `Unit`, `Integration`, and `All` (default):

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File .\src\scripts\verify.ps1 -Suite Unit
pwsh -NoProfile -ExecutionPolicy Bypass -File .\src\scripts\verify.ps1 -Suite Integration
```

Integration requires real Docker, the existing `ageatopia-world:1` image, and the
configured Ollama models. It does not build another image. `UTOPIA_INTEGRATION=1`
enables these tests for direct pytest runs. They exercise real providers and remove
their test resources; ordinary tests cannot substitute for that runtime evidence.

For documentation changes, check local links/anchors, examples against schemas and
CLI help, and the diff. Do not rebuild the application solely for prose edits.
Historical pass counts and measurements are in the [archive](archive/README.md),
not a claim about the current working tree.

## Icon assets

The canonical hand-drawn SVG is
[ageatopia.svg](../src/gui/native/ageatopia.svg). The native host embeds its
multi-resolution ICO, and release builds use the same ICO for `Ageatopia.exe`.
After editing the SVG, regenerate with ImageMagick installed:

```powershell
magick -background none src/gui/native/ageatopia.svg -define icon:auto-resize=256,128,64,48,32,24,16 src/gui/native/ageatopia.ico
Copy-Item src/gui/native/ageatopia.svg src/gui/dashboard/app/icon.svg
Copy-Item src/gui/native/ageatopia.ico src/gui/dashboard/app/favicon.ico
```

Rebuild the native host/dashboard to incorporate asset changes. See
[release packaging](releases.md) for distributable builds.
