# Getting started

[Documentation index](README.md)

Use a compiled Windows release to run the observatory without installing Python
or Node.js, or a source checkout for development, maintenance, and optional training.

## Prerequisites

| Component | Compiled Windows package | Source checkout |
| --- | --- | --- |
| Windows x64 | Required | Required for the native desktop host |
| Microsoft Edge WebView2 Runtime | Required | Required |
| Python 3.11+ | Bundled | Required |
| Node.js 22+ and npm | Node runtime and dashboard bundled | Required |
| PowerShell 7 | Not needed for double-click launch | Used by setup and launch commands |
| Visual Studio C++ tools and Windows SDK | Not needed to run | Required to build the native host |
| Docker Desktop, Linux containers | Required for agent execution | Required for agent execution |
| Ollama or an OpenAI-compatible provider | Required for inference | Required for inference |

Local GPU-backed model startup checks RAM, commit headroom, and NVIDIA VRAM.
Run diagnostics before choosing model sizes and concurrency; see
[resource configuration](configuration.md#resource-limits).

## Compiled Windows package

1. Extract `Ageatopia-v<version>-Windows-x64.zip` into a writable location.
2. Open the extracted `Ageatopia` folder and launch `Ageatopia.exe`.
3. Check the Infrastructure panel before starting a world.

The package includes a Nuitka standalone backend, native host, Node runtime,
dashboard, and sandbox build context. Settings and logs default to
`%LOCALAPPDATA%\Ageatopia`. Terminal invocation attaches logs to that terminal;
double-click launch opens no terminal.

Docker and Ollama are checked at launch. When unavailable, the observatory can
still open, but agent work needs the corresponding infrastructure. The launcher
can start installed services and build the default sandbox image when Docker is
ready. For local inference, install the configured model with Ollama:

```powershell
ollama pull qwen3.5:2b
```

Training dependencies are not bundled. Use a source environment for
[offline training](research.md#offline-training).

## Source checkout

Open PowerShell in the repository root and run:

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File .\src\scripts\maintain.ps1
```

The wrapper creates `dist/python/venv` when needed, installs `requirements.lock`,
installs Ageatopia as an editable package, and opens maintenance. The minimum
Python version is defined in [pyproject.toml](../pyproject.toml).

Complete these maintenance actions:

1. **Build dashboard**.
2. **Build native host**. The build retrieves the pinned WebView2 SDK.
3. **Build sandbox image** with Docker Desktop running in Linux-container mode.
4. **Pull model** for `qwen3.5:2b`, unless it is already installed.
5. **Run diagnostics** and resolve failed checks.

**Install dependencies** is also available for subsequent maintenance. Launch:

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File .\src\scripts\run.ps1
```

Services use configured loopback ports. Occupied ports are rejected rather than
adopting another application's servers.

## Development ZIP and Python wheel

Extract the development ZIP, run `Setup.ps1`, then `Start.ps1`. It contains source,
native binaries, a built dashboard, and setup scripts. It needs external Python
and Node installations plus the same agent execution infrastructure as a checkout.
The included native host does not need rebuilding just to run.

Install the wheel into your chosen virtual environment with pip. It provides
Python modules and the backend, but no native host or dashboard assets. It is not
a complete desktop installation. See [release formats](releases.md#package-formats).

## First world

Create a world in the observatory. Defaults are six agents, 80 tasks,
`qwen3.5:2b`, and one concurrent inference request. Check that the selected model
is installed and the sandbox image exists, then start the world.

The inspector exposes activity, delivered context, notebooks, task results, and
recorded expressions. Pause to inspect without ongoing inference. Use
[configuration](configuration.md) to adjust the experiment and
[operations](operations.md) for lifecycle controls.

Closing the full desktop app stops its services, Docker Desktop, and all WSL
distributions. Review [shutdown behavior](operations.md#closing-the-desktop-app)
if you also use WSL or Docker for other work.
