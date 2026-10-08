# Implementation and verification evidence

Baseline verified locally on Windows 11 on 2026-10-08. Python 3.14, Node 26.8.1,
Ollama 0.40.1, and an NVIDIA RTX 3050 Laptop GPU with 6144 MiB VRAM.
Both Qwen3.5:2B and Qwen3.5:0.8B are installed. Docker was not installed at baseline;
the follow-up below and docker-setup.md describe the current installation.

## Current result

All eight development phases have application implementations. The complete real
six-agent acceptance experiment is **not verified**, because its mandatory Docker
execution environment is unavailable. Docker Desktop is now installed, but its
engine cannot start with the existing custom WSL kernel. No agent command is executed on the host
as a substitute. The operator UI returns a clear start failure and leaves the
world in CREATED state. Actual optional LoRA weight training has not been run.

## Requirement coverage

| Plan sections | Implementation | Evidence and limits |
| --- | --- | --- |
| 1, 8: agent knowledge and varied contexts | Public allowlists, exact context storage, seeded profile assignment, custom base contexts, model overrides | Automated visibility/context-separation checks; browser inspector shows exact delivered context. Only provider-returned thinking is recorded. |
| 2: stack and shared inference | FastAPI, Next.js/TypeScript/Tailwind, SQLite WAL, versioned JSON events, Ollama and OpenAI-compatible provider | Backend/API tests, real Ollama manifest and tool-call check, frontend build. Drizzle defines mirrored tables/migrations; Python transactions use SQLAlchemy as explained in architecture.md. OpenAI-compatible backend not tested live. |
| 3, 4: lifecycle, clocks and economics | Integer milliseconds, one atomic engine transaction per action, queue policy, pause/recovery, termination callbacks, configurable replacement delay | Tests cover all specified economic/lifecycle cases, concurrent/duplicate submissions, rollback, queue cancellation and subtick transitions. Five-minute default resolves the plan conflict. Docker process termination still needs real integration verification. |
| 5, 9: tasks and independent grader | Seven math recipes, eight synthetic cyber recipes, inconsistent constraints/unavailable information, deterministic bank validation, private registry, bounded mathematical AST | Tests cover exact distribution, seeded generation, proof policy, malformed vs incorrect vs infrastructure errors, private visibility, and invalid fixed banks. Real CTF execution is blocked by Docker. |
| 6, 7: shared workspace and general tools | Shared bounded tmpfs, distinct unprivileged UIDs, Docker-only shell/Python/compile/process/service operations, independent filesystem and process observer | Missing-Docker fail-closed test passes. Real integration test is provided for permissions, zero effective capabilities, network isolation, shared artifacts, permanent termination, CTF, observer ACK and workspace restoration; not run here. |
| 10: memory and optional learning | Personal notebook, persistent artifacts, public success-trajectory export, explicit offline LoRA CLI and training event log, adapter/dataset/iteration metadata | Deterministic loop verifies complete public trajectory through the returned verified tool result. Optional training dependencies and GPU training were not exercised; adapters require operator packaging into the chosen backend. |
| 11, 12: observer and operator UI | Immutable monotonic events, live SSE, detailed agent pages, actual-state overview/task monitor/activity filters, timeline/trend charts, artifact evidence links, controls/presets | API and browser checks pass. Tested create world, save preset, start failure, live feed, task classifications, agent details, analysis, replay, and desktop/mobile layout. Observer save-failure retry/deduplication and error-pause cleanup tests pass. |
| 13: reproducibility and exports | Seeds/config/digests/context versions, initial/final snapshots, repeated conditions, JSON/CSV/Parquet, recorded-experience replay | Export round trips and API replay/repeat tests pass. Engine reproducibility is deterministic; model inference is explicitly not guaranteed deterministic. |
| 14: performance | One shared inference semaphore, queue/backpressure metrics, idle-stop policy, conservative context guard, opt-in summarization, NVIDIA metrics, real benchmark CLI | Six independent histories serialize through one worker in tests; context checks include thinking/tool-call payloads. Local GPU detected. Sustained six-agent VRAM/throughput benchmark remains blocked by Docker. No unsupported provider batching claim is made. |
| 15: tests last | Existing application development was followed by engine/API/runtime tests and opt-in real integrations | 53 passed, 3 opt-in integration tests skipped in the standard suite; separate real Ollama check: 1 passed, 2 deselected. |
| 16, 17: phases and real acceptance | Eight phases implemented; acceptance commands and real benchmark supplied | See architecture.md phase ledger. Full real acceptance criteria 1–15 remain an activation gate, not a completed experiment. |

## Verification commands and results

- `.venv\Scripts\python.exe -m pytest -q`: **53 passed, 3 skipped**.
- `UTOPIA_INTEGRATION=1 ... pytest tests/test_integration.py -k real_ollama_shared_model_tool_call -q`: **1 passed**, using the installed Qwen model.
- Ruff check and format check across `utopia`, `sandbox`, `tests`: passed.
- Dashboard `npm run typecheck`, `npm run lint`, `npm run build`: passed.
- Drizzle migration generation: passed, three tables generated.
- `npm audit --omit=dev`: **0 vulnerabilities**.
- Browser checks: desktop 1280×900 and mobile 390×844; no document-wide horizontal overflow on mobile. Dense tables scroll within their own containers. No browser console warnings/errors observed in the final check.

The suite emits one upstream Starlette/httpx TestClient deprecation warning.
The full dependency audit reports nine development-tool advisories (five high,
four moderate), involving eslint's braces chain and Drizzle Kit's esbuild chain.
Production dependencies are clean. No unverified forced downgrade was applied.

## Observation and activation limits

Linux filesystem notifications may coalesce consecutive writes. Operator records
contain notifications plus content checkpoints; they cannot guarantee every
intermediate byte version. File access evidence identifies paths, never intention
or authorship. Syscall process records and sampled process inventories are
explicitly different event types. Ordinary process inspection can reveal OS
instrumentation, although its controls/data remain protected.

To clear the mandatory execution gate, install/start Docker Desktop with Linux
containers, build `agent-utopia-world:1` from `sandbox`, then run the opt-in real
integration suite and the sustained population benchmark. The 180-second real
integration smoke test checks independent task completion; the benchmark and
exported observations are needed to demonstrate generational behavior and full
acceptance. No such run or training result is claimed in this document.

## Follow-up: 2026-10-09

New defaults use a compact, tool-oriented system prompt and four expertise profiles;
existing run contexts remain unchanged. Recoverable world deletion/restoration
preserves immutable events and works after restart. The dashboard exposes the base
prompt editor and distinguishes a missing Docker installation from a failed engine.
Docker CLI startup errors with zero exit status are covered by a regression test.
The updated automated suite passes 56 tests. See docker-setup.md for the installed
versions and the precise custom-kernel blocker. Real sandbox acceptance remains
unverified.
