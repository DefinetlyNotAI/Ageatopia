# Implementation and verification evidence

Baseline verified locally on Windows 11 on 2026-10-08. Python 3.14, Node 26.8.1,
Ollama 0.40.1, and an NVIDIA RTX 3050 Laptop GPU with 6144 MiB VRAM.
Both Qwen3.5:2B and Qwen3.5:0.8B are installed. Docker was not installed at baseline;
the follow-up below and docker-setup.md describe the current installation.

## Current result

All eight development phases have application implementations. Docker Desktop now
starts on the upgraded custom kernel, and all three real integrations pass,
including the six-agent task-completion smoke run in 48.53 seconds.
Full generational acceptance and actual optional LoRA training remain unverified.
See docker-setup.md for kernel preservation, installation repair, and rollback.

## Requirement coverage

| Plan sections | Implementation | Evidence and limits |
| --- | --- | --- |
| 1, 8: agent knowledge and varied contexts | Public allowlists, exact context storage, seeded profile assignment, custom base contexts, model overrides | Automated visibility/context-separation checks; browser inspector shows exact delivered context. Only provider-returned thinking is recorded. |
| 2: stack and shared inference | FastAPI, Next.js/TypeScript/Tailwind, SQLite WAL, versioned JSON events, Ollama and OpenAI-compatible provider | Backend/API tests, real Ollama manifest and tool-call check, frontend build. Drizzle defines mirrored tables/migrations; Python transactions use SQLAlchemy as explained in architecture.md. OpenAI-compatible backend not tested live. |
| 3, 4: lifecycle, clocks and economics | Integer milliseconds, one atomic engine transaction per action, queue policy, pause/recovery, termination callbacks, configurable replacement delay | Tests cover all specified economic/lifecycle cases, concurrent/duplicate submissions, rollback, queue cancellation and subtick transitions. Five-minute default resolves the plan conflict. Real Docker permanent process termination passes. |
| 5, 9: tasks and independent grader | Seven math recipes, eight synthetic cyber recipes, inconsistent constraints/unavailable information, deterministic bank validation, private registry, bounded mathematical AST | Tests cover exact distribution, seeded generation, proof policy, malformed vs incorrect vs infrastructure errors, private visibility, and invalid fixed banks. Real CTF execution passes. |
| 6, 7: shared workspace and general tools | Shared bounded tmpfs, distinct unprivileged UIDs, Docker-only shell/Python/compile/process/service operations, independent filesystem and process observer | Missing-Docker fail-closed test and real integration pass: permissions, zero effective capabilities, network isolation, shared artifacts, permanent termination, CTF, observer ACK and workspace restoration. |
| 10: memory and optional learning | Personal notebook, persistent artifacts, public success-trajectory export, explicit offline LoRA CLI and training event log, adapter/dataset/iteration metadata | Deterministic loop verifies complete public trajectory through the returned verified tool result. Optional training dependencies and GPU training were not exercised; adapters require operator packaging into the chosen backend. |
| 11, 12: observer and operator UI | Immutable monotonic events, live SSE, detailed agent pages, actual-state overview/task monitor/activity filters, timeline/trend charts, artifact evidence links, controls/presets | API and browser checks pass. Tested create world, save preset, start failure, live feed, task classifications, agent details, analysis, replay, and desktop/mobile layout. Observer save-failure retry/deduplication and error-pause cleanup tests pass. |
| 13: reproducibility and exports | Seeds/config/digests/context versions, initial/final snapshots, repeated conditions, JSON/CSV/Parquet, recorded-experience replay | Export round trips and API replay/repeat tests pass. Engine reproducibility is deterministic; model inference is explicitly not guaranteed deterministic. |
| 14: performance | One shared inference semaphore, queue/backpressure metrics, idle-stop policy, conservative context guard, opt-in summarization, NVIDIA metrics, real benchmark CLI | Six independent histories serialize through one worker in tests; context checks include thinking/tool-call payloads. Local GPU detected. Sustained population VRAM/throughput benchmarking remains unverified. No unsupported provider batching claim is made. |
| 15: tests last | Existing application development was followed by engine/API/runtime tests and opt-in real integrations | 56 passed, 3 opt-in integrations skipped in the standard suite; all three real integrations pass individually. |
| 16, 17: phases and real acceptance | Eight phases implemented; acceptance commands and real benchmark supplied | See architecture.md phase ledger. Full real acceptance criteria 1–15 remain an activation gate, not a completed experiment. |

## Verification commands and results

- `.venv\Scripts\python.exe -m pytest -q`: **56 passed, 3 skipped**.
- `UTOPIA_INTEGRATION=1 ... pytest tests/test_integration.py -k real_ollama_shared_model_tool_call -q`: **1 passed**, using the installed Qwen model.
- Targeted real Docker integration: **1 passed in 7.91 seconds**; real six-agent smoke: **1 passed in 48.53 seconds**.
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
or authorship. Kernel process events and sampled process inventories are
explicitly different event types. Ordinary process inspection can reveal OS
instrumentation, although its controls/data remain protected.

The mandatory Docker execution gate is now verified, and `agent-utopia-world:1`
has been built. The real six-agent smoke test checks task completion; the sustained
benchmark and exported observations are still needed to demonstrate generational
behavior and full acceptance. No sustained benchmark or training result is claimed.

## Follow-up: 2026-10-09

New defaults use a compact, tool-oriented system prompt and four expertise profiles;
existing run contexts remain unchanged. Recoverable world deletion/restoration
preserves immutable events and works after restart. The dashboard exposes the base
prompt editor and distinguishes a missing Docker installation from a failed engine.
Docker CLI startup errors with zero exit status are covered by a regression test.
The updated automated suite passes 56 tests. The follow-up kernel repair preserved
the existing settings, modules and embedded firmware while enabling Docker.
Real Docker isolation/recovery/CTF integration and real Ollama tool calling pass.
The first six-agent smoke run paused after Windows Docker CLI processes reported
out-of-memory errors near the host commit limit; kernel build caches were cleared
before retrying. Making the initial task request explicit fixed a separate model
detour through unassigned workspace files. The final six-agent smoke run passed in
48.53 seconds, including task completion and final workspace capture. All three
real integrations pass individually; the standard suite passes 56 tests with the
three integrations skipped by default. Full generational acceptance remains unverified.


## Verified persona, challenge and security follow-up

Commit `2159b02` adds six distinct default expertise profiles and 80 seeded tasks,
including 24 independently checked recipe types. The real dashboard creation
form shows all six profiles and the expanded task bank. A retained world named
**Six-agent verification** was started through the dashboard; all six agents
issued workspace calls before the world was paused. Older saved worlds and
presets retain their original profiles and 40-task conditions.

The real Docker/Ollama suite passed all three integrations in 53.95 seconds on
the current image. It verifies C compilation, isolation, short-lived process
tracking, non-leader thread exec, termination, durable recovery, URL validation,
shell recursion limits, POP3 command rejection, real provider tool calls and
six-agent activity with a completed task and final workspace capture.

The standard suite passes 61 tests with three integrations skipped; Ruff lint
and format checks pass. The empty-provider-response regression verifies retry
with backoff and unchanged history. No model actions or answers are fabricated.

The current image is tagged `agent-utopia-world:1`; its nine reported
binutils/strace findings are absent. Trivy and Grype both report zero findings
after upgrading BusyBox to the checksum-verified, pinned upstream `1.39.0.git`
revision with the remaining security patches applied. No custom ignores,
package exclusions, version relabeling or VEX suppression were used. See
docker-setup.md for source provenance, image identity and scanner reports.
No sustained multi-generation benchmark or training result is claimed.

`scripts/verify.ps1` reuses the existing sandbox image. Integration tests remove
their containers and unload the test model between checks. Both a failed run
under memory pressure and the subsequent passing run left zero containers and
zero loaded Ollama models. Unit tests passed 61 tests; all three real integrations
passed after memory became available again.
