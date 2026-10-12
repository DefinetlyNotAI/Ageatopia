# Troubleshooting

[Documentation index](README.md)

Start with **Run diagnostics** in maintenance and the current launcher/service
logs. Source logs are in `dist/logs`; compiled logs default to
`%LOCALAPPDATA%/Ageatopia/logs`. Check the relevant [configuration](configuration.md)
before changing system-wide settings.

## Startup and installation

| Symptom | Check / next action |
| --- | --- |
| Native host is missing | Build it in maintenance; source output is `dist/native/build/ageatopia.exe`. |
| WebView2 cannot initialize | Install the Microsoft Edge WebView2 Runtime. The loader DLL alone is not the browser runtime. |
| Dashboard dependencies/build are missing | Use **Build dashboard** or `npm --prefix src/gui/dashboard run build`. |
| Backend/dashboard port is occupied | Identify its owner. Stop your own conflicting service or choose unused ports in desktop JSON. |
| Python environment is missing | Run `src/scripts/maintain.ps1`; it bootstraps `dist/python/venv`. |
| Installed commands are outdated after an update | Reinstall the editable package with `python -m pip install -e .`. |

Do not terminate unrelated services to free a port. An already-running server is
not adopted by the launcher.

## Docker and sandbox

Run these from the source repository root:

```powershell
docker info
docker image inspect ageatopia-world:1
docker build -t ageatopia-world:1 src/hooks/sandbox
```

Docker Desktop must use Linux containers. `ageatopia-world:1` is a locally built
tag, not an image supplied by Docker Hub. A missing/deleted local image must be
rebuilt from the complete context. The compiled launcher can build that default
image from its bundled `sandbox/` when Docker is ready.

Without Docker, the compiled observatory can open, but there is no host execution
fallback for agents. A source desktop launch requires its execution infrastructure.
If Docker fails because of a custom WSL kernel, diagnose the reported kernel or
filesystem issue separately. The [machine setup archive](archive/docker-setup.md)
records one installation's repairs; it is not a general kernel replacement recipe.

## Models, memory, and concurrency

```powershell
ollama list
ollama ps
ollama pull qwen3.5:2b
```

Check the selected model tag and provider URL, including profile and slot overrides.
All required local models must fit startup residency checks at their actual context
sizes. `manage_ollama=false` permits an externally managed provider; it does not
install a model or make an unavailable provider reachable.

`VirtualAlloc` / Windows error 1455 indicates allocation or commit pressure. Check
available RAM and commit/pagefile headroom. Reduce model/context/concurrency demand
or close your own memory-heavy workloads. The Job Object ceiling is not a private
physical RAM reservation, and unrelated applications can exhaust memory later.

Missing NVIDIA measurements remain unavailable rather than being fabricated.
Client concurrency may become server-side waiting when a model backend cannot run
parallel requests. Measure the actual provider; do not infer throughput from the
configured request limit alone.

## World configuration and files

If creation rejects a task distribution, make `task_count * impossible_fraction`
an integer: 80 tasks at 10% works; 75 does not. Unknown world JSON keys are rejected,
and new agent contexts must be at least 8192 tokens.

An interrupted tool is not automatically replayed after recovery. Resume the world
from paused state and inspect the recorded result. If live sandbox access fails,
check that the container is retained and Docker's WSL filesystem is accessible,
or use **Open sandbox snapshot**. Editing a snapshot copy does not affect the world.

## Release build failures

| Failure | Resolution |
| --- | --- |
| Version is invalid or mismatched | Supply a valid `-v` matching the dashboard manifest exactly. |
| Destination already exists | Keep it intact and choose a new matching release version. Empty directories also count. |
| Staging is occupied | Identify the owner; finish that build or inspect its leftover files before removing anything. |
| Native tools/dependencies are absent | Build the native host, install `.[release]`, and provide the x64 C++ workload and Node/npm. |
| Disk space is insufficient | Free space in storage you own, then rerun preflight. |
| npm/compiler/runtime validation fails | Read the failing stage and output; the final release remains unpublished. |

The release script cleans only its owned staging workspace. Do not bypass archive
or runtime validation to publish a partial package. Never edit an existing release
directory to repair a failed build. See [releases](releases.md).
