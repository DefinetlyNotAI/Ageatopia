# Releases

[Documentation index](README.md)

[releases.ps1](../src/scripts/releases.ps1) builds four versioned packages through the existing
maintenance-style Textual/Rich interface. It validates staging before publishing
a release directory, with no manual packaging steps.

## Build requirements

Use Windows x64, 64-bit Python 3.11+, Node.js 22+ with npm, and Visual Studio C++
Build Tools with the x64 workload. Build the native host first; these must exist:

```text
dist/native/build/ageatopia.exe
dist/native/build/WebView2Loader.dll
```

Install release tools in the project environment:

```powershell
.\dist\python\venv\Scripts\python.exe -m pip install -e ".[release]"
```

Preflight checks Python/dependency versions, native and Node x64 binaries, compiler
availability, sandbox context, clean staging, and disk space. Network access is
needed for npm/build-tool downloads and a matching Node license if absent locally.

## Version and destination

The required `-v` value is `major.minor.patch` with an optional hyphenated suffix,
such as `1.2.3-beta` or `2.0.0-rc1`. It must exactly match
[dashboard/package.json](../src/gui/dashboard/package.json).

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File .\src\scripts\releases.ps1 -v 1.2.3
```

Replace `1.2.3` with the matching manifest version. Invalid/mismatched values fail
immediately. Outputs go to `dist/releases/<version>/`. An existing destination,
including an empty directory, is rejected. Choose a new matching version for a new
release; published directories are never updated in place.

## Package formats

| Artifact                                   | Contents and requirements                                                                                                                                      |
| ------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `Ageatopia-v<version>-Windows-x64.zip`     | Nuitka standalone launcher/backend, native host, Node, production dashboard, sandbox context. No external Python/Node installation. WebView2 Runtime required. |
| `Ageatopia-v<version>-Windows-x64-dev.zip` | Python source, native binaries, built dashboard/dependencies, `Setup.ps1`, `Start.ps1`. External Python/Node and first-run setup required.                     |
| `Ageatopia-v<version>-py3-none-any.whl`    | Backend/Python modules, pip-installable, without native host/dashboard assets.                                                                                 |
| `Ageatopia-v<version>-Docker-Sandbox.zip`  | Complete Dockerfile/context, supervisor, observer, patches, and build instructions.                                                                            |

ZIP names retain the exact supplied version. Stable wheel versions retain the name
above; suffix wheels use a pip-valid normalized version. For `1.2.3-beta`, the
wheel is `ageatopia-1.2.3b0-py3-none-any.whl`. Other suffixes are normalized through
Python packaging rules, including local versions when needed.

The compiled folder has `Ageatopia.exe`, its Nuitka runtime files, and `native/`,
`dashboard/`, `runtime/node.exe`, and `sandbox/`. Writable settings/logs default to
`%LOCALAPPDATA%/Ageatopia`. Terminal launch attaches logs; double-click launch opens
no terminal. Docker/Ollama absence allows a reduced observatory, with agent work
dependent on Docker and a working provider. Optional training extras are excluded.

## Build and publication

1. Validate all preflight requirements before creating an owned build workspace.
2. Build/install-check the wheel and package the complete sandbox context.
3. Build Next.js standalone in staging, including static/public assets.
4. Compile the launcher/backend with Nuitka `--mode=standalone`, then assemble both
   Windows folders and bundle Node with its license.
5. Relocate the compiled folder and check its imports/assets and HTTP services with
   developer Python/Node removed from `PATH`.
6. Validate exact artifact names, archive integrity, and required files.
7. Atomically move all four artifacts into the final version directory.

Builds, install-check environments, and validation copies stay under
`dist/releases/.staging/`. Failure or cancellation removes only the build's owned
workspace and leaves existing releases untouched. Occupied staging is rejected;
the script does not remove another build's files.

## Output and diagnostics

The interactive TUI shows checks, command output, active stage, durations, and
errors. Ctrl+T changes the theme; Ctrl+C copies selected text or cancels when no
text is selected. Noninteractive runs stream output and print the same final
artifact table. Exit code 0 means all packages validated and publication completed.

For an extracted compiled package, the internal validation modes are also available:

```powershell
.\Ageatopia.exe --self-test
.\Ageatopia.exe --smoke-test --config C:/AgeatopiaData/verification.json
```

Use an isolated writable `data_dir`, unused backend/dashboard ports, and
`manage_ollama=false` for smoke checks. They start and stop owned services without
opening the native window. See [troubleshooting](troubleshooting.md#release-build-failures).
