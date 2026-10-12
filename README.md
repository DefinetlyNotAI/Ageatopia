<img src="src/gui/native/ageatopia.svg" width="48" height="48" alt="Ageatopia icon">

# Ageatopia

A local desktop observatory for autonomous, tool-using language-model agents.
Agents work in a shared Docker environment with limited lifetimes, task rewards,
and private grading. The operator observes their activity through a native Windows
window, live charts, notebooks, replay, and research exports.

Agent programs execute only inside Docker. The Python backend owns simulation
state; a Next.js dashboard renders the recorded experience through WebView2.

## Get started

| Installation | Start here |
| --- | --- |
| Compiled Windows package | Extract the Windows ZIP and launch `Ageatopia.exe`. Python and Node.js are bundled. |
| Source checkout | Follow the setup below, or the complete [installation guide](docs/getting-started.md). |
| Python wheel | Install with pip for the backend and Python modules; it excludes the native host. See [package formats](docs/releases.md#package-formats). |

For a source checkout, install Python 3.11+, Node.js 22+, PowerShell 7, Docker
Desktop with Linux containers, Ollama, and the Visual Studio C++ build tools.
Windows requires the Microsoft Edge WebView2 Runtime.

From the repository root:

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File .\src\scripts\maintain.ps1
```

In maintenance, build the dashboard, native host, and sandbox image; pull the
default `qwen3.5:2b` model if needed, then run diagnostics. Launch the desktop:

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File .\src\scripts\run.ps1
```

Create a world in the observatory and start it. The compiled package can open the
observatory without Docker or Ollama, but agent execution requires Docker and a
working inference provider.

**Desktop shutdown also stops Docker Desktop and all WSL distributions.**
See [service ownership and shutdown](docs/operations.md#closing-the-desktop-app).

## Documentation

| Guide | Contents |
| --- | --- |
| [Getting started](docs/getting-started.md) | Prerequisites, source setup, packages, and the first world |
| [Operations](docs/operations.md) | Launchers, lifecycle controls, storage, logs, and deletion |
| [Configuration](docs/configuration.md) | Desktop settings, world defaults, providers, and resource limits |
| [Architecture](docs/architecture.md) | Components, isolation, persistence, inference, and observation |
| [Research and training](docs/research.md) | Exports, replay, comparisons, notebooks, and offline LoRA |
| [Development](docs/development.md) | Repository layout, builds, verification, and icon assets |
| [Releases](docs/releases.md) | Automated packaging, validation, artifact names, and publication |
| [Troubleshooting](docs/troubleshooting.md) | Startup, providers, memory, sandbox access, and release failures |

The [documentation index](docs/README.md) includes the historical archive. Dated
plans and measurements are retained there; the guides above describe current
workflows and point to their implementation sources.
