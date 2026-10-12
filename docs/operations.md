# Operations

[Documentation index](README.md)

## Entry points

For a source checkout, run these from the repository root:

| Interface | Command | Purpose |
| --- | --- | --- |
| Desktop | `pwsh -NoProfile -ExecutionPolicy Bypass -File .\src\scripts\run.ps1` | Supervise services and open the native window |
| Maintenance | `pwsh -NoProfile -ExecutionPolicy Bypass -File .\src\scripts\maintain.ps1` | Install, build, repair, pull models, and diagnose |
| Control console | `python -m gui.control` | Configure worlds and manage application/world lifecycle |

Activate the project environment before using Python directly:

```powershell
.\dist\python\venv\Scripts\Activate.ps1
```

Editable installation registers `ageatopia`, `ageatopia-desktop`,
`ageatopia-control`, and `ageatopia-maintenance`. The desktop and both consoles
accept `--config PATH`; see [desktop configuration](configuration.md#desktop-configuration).

Maintenance provides selectable output, progress, and errors. Ctrl+T changes its
theme; Ctrl+C copies selected text or exits when nothing is selected. Releases
use the same conventions, with Ctrl+C copying a selection or cancelling the build.

## World lifecycle

| Action | Effect |
| --- | --- |
| Start / Resume | Prepare the sandbox and required models, then run agent work |
| Pause | Stop requests and agent processes while retaining durable state |
| Terminate | End the simulation and release runtime resources |
| Repeat conditions | Create a fresh experiment from the same configuration and initial conditions |
| Delete world | Permanently erase owned state after confirmation |

Recovery restores durable state in a paused world without charging offline wall
time. Interrupted tools receive an interrupted result and are not automatically
replayed. Existing identities retain their experience; replacements follow the
[memory inheritance policy](configuration.md#memory-and-context).

Only one world can actively run at a time. Pause or terminate it before starting
another. Replay does not start inference.

## Storage and logs

| Data | Source checkout default | Compiled Windows default |
| --- | --- | --- |
| Desktop settings | `.data/desktop.json` | `%LOCALAPPDATA%/Ageatopia/desktop.json` |
| World state, credentials, snapshots | `.data/`, or configured `data_dir` | `%LOCALAPPDATA%/Ageatopia`, or configured `data_dir` |
| Service and launcher logs | `dist/logs/` | `%LOCALAPPDATA%/Ageatopia/logs/` |
| Research/training outputs | `dist/` | Configured world data directory's `outputs/` for backend work |
| Dependencies, builds, caches | `dist/` | Bundled app files; writable state stays outside the install folder |

`backend.log`, `dashboard.log`, and launcher JSONL records help diagnose failures.
Treat `operator.token` as an operator credential; it is not agent-visible data.
Back up the configured world data directory with services stopped, and keep
downloaded exports and adapters separately. Deleting a checkout does not remove
external data directories or uninstall Docker/Ollama.

## Inspecting workspace files

**Open sandbox directory** opens the live shared Docker workspace when its
container is available, including a retained paused container. Edits affect the
experiment. Windows live access depends on Docker Desktop's WSL backend and its
exposed Linux filesystem.

**Open sandbox snapshot** refreshes a recorded host-side inspection copy. Editing
that copy does not change the sandbox. **Open snapshot** selects a recorded
version; notebooks are available beside inspection files. If live access fails,
use the explicit snapshot action.

## Permanent deletion

Pause or terminate first, then confirm the exact world ID. Deletion removes its
runtime/container, run-owned host files, database records, notebooks, and events.
There is no restore action. Other worlds remain intact. Downloaded exports,
separately created adapters, and external models are outside world ownership.

## Closing the desktop app

The full desktop launcher stops owned services and persists state, then stops
Docker Desktop and its backend before running `wsl --shutdown`.
**This stops all WSL distributions and affects shared Docker Desktop sessions.**
World termination alone does not trigger that global shutdown.

An existing Ollama server is reused without taking ownership of its process.
Models warmed by Ageatopia are unloaded during backend shutdown; launcher-owned
Ollama processes are stopped with their descendants.

Occupied backend/dashboard ports cause a visible startup error. Unrelated servers
are not adopted. Implementation: [desktop.py](../src/gui/desktop.py).
