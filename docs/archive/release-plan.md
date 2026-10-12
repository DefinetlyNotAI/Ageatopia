> Historical record, archived on 2026-10-12. Paths, versions, measurements,
> and completion claims describe their original snapshots. Use the
> [current documentation](../README.md) for supported workflows.

# Ageatopia release implementation plan

Goal: automate the four requested release packages through `releases.ps1`, using
the existing maintenance TUI, staging every build and publishing only after validation.

1. Add strict early version/destination checks and a Textual release runner that
   reuses maintenance typography, theme, borders, selectable output and error colors.
   Guard staging ownership, disk space, build tools and native artifacts before work.
2. Build the dashboard in staging with Next.js standalone output; bundle the x64
   Node executable. Compile the Python launcher/backend with Nuitka standalone and
   attach-console behavior. Keep source launches unchanged and allow a packaged
   observatory without Docker/Ollama, with agent execution still Docker-only.
3. Build the source Windows ZIP, compiled Windows ZIP, Python wheel and sandbox ZIP.
   Validate archives, wheel metadata/installability, compiled imports and isolated
   service startup before atomically moving the artifact directory into its version.
4. Verify invalid/mismatched versions, existing destinations, dirty staging, failure
   cleanup, TUI behavior, package relocation and terminal/no-console launch behavior.
   Inspect the final diff and commit the coherent release feature without pushing.

The compiled folder uses `Ageatopia.exe`, `native/`, `dashboard/`, `runtime/node.exe`
and `sandbox/`; writable state belongs in the user's local application data.
The source package retains the existing repository layout and external setup flow.
All build products, temporary virtual environments and validation copies stay under
`dist/releases/.staging/`; final destinations must never be overwritten.

Wheel filenames for suffix versions need a pip-valid normalized version: the
requested literal hyphenated suffix conflicts with pip's wheel filename grammar.
The exact stable-version name will be validated with pip during the release build.
