# Agent Utopia

A local autonomous agent world, private grader and live operator observatory.
Python owns simulation state; Next.js displays recorded application state.
Agent code executes **only inside Docker**. No host shell fallback exists.

## Windows setup

Install Python 3.12 or later, Node.js 22 or later, Docker Desktop with Linux
containers, and Ollama. Start Docker and Ollama before starting a world.
The default is six agents sharing Qwen3.5:2B and one inference worker.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
.\.venv\Scripts\python.exe -m pip install -e .
docker build -t agent-utopia-world:1 sandbox
ollama pull qwen3.5:2b
```

Use the installed Python version selector if it differs from `-3.12`.
Use `qwen3.5:0.8b` in the world settings for a smaller model.
The Python lock was resolved using Python 3.14 on Windows; the source supports 3.12+.

Start the operator backend in one terminal:

```powershell
.\.venv\Scripts\python.exe -m utopia.cli
```

Start the dashboard in another:

```powershell
cd dashboard
npm ci
npm run build
npm start
```

Open [the observatory](http://127.0.0.1:3000), create a world and start the run.
Both servers bind to loopback. The backend creates `.data/operator.token`.
The dashboard reads that file on its server; it never sends the token to the
browser. For a custom data directory set `UTOPIA_DATA` to the same absolute path
for both servers. `UTOPIA_API_URL` changes the dashboard backend address.

## World settings

The form covers the common controls. Full configuration JSON exposes all rules,
tool resource bounds, provider URLs, models, seeds, thinking, fixed task banks,
context knowledge, custom base instructions and per-agent overrides. Profiles
can supply their own `model` settings. `agent_model_overrides` maps zero-based
population slots to model settings and takes precedence over profiles.
`context_assignment` supports round-robin or seeded random assignments.

New worlds use a compact system prompt and four round-robin expertise profiles:
generalist, mathematics, programming and security analysis. The creation form
exposes the base system instructions directly. Existing worlds and saved presets
keep their exact original prompts and profiles.

Pause or terminate a running world before using **Delete world**. Deletion removes
the world from the active list and preserves its database, snapshots and immutable
events. **Deleted worlds** in the sidebar lets you restore it, including after a
server restart. This control does not permanently erase research records.

Task counts must represent the requested impossible fraction exactly: the default
40 tasks contain 36 solvable tasks and four impossible tasks. Difficulty is an
independent field. The fixed bank requires independently validated private
answers and validator metadata; see `utopia/tasks.py` for the supported recipes.

Five minutes is the default respawn delay, resolving conflicting plan statements
in favor of the tests and acceptance criteria. Set `economy.respawn_delay_ms` to
60000 to use the lifecycle section's one-minute variant. Rewards round down once
to whole milliseconds. Queue waiting does not consume personal lifetime by
default; actual model work does. Collective deductions include all living
agents except the skipper. Expiration precedes other actions at equal timestamps.

`clock_mode=deterministic` advances fixed steps; wall mode charges actual elapsed
time with a configurable speed. Model inference remains nondeterministic. Pause
stops model requests and agent processes. Recovery resumes from durable state in
a paused state and never charges offline wall time. Interrupted tools receive an
explicit interrupted result; they are not replayed automatically. Shared artifacts
persist across generations. Optional respawn cleanup removes scratch artifacts
while preserving challenge environments.

## Isolation and observation

Each run has a disposable, loopback-only Docker world. It has no host mounts,
external network, Docker socket, private database, operator credential or grader.
Root filesystem is read-only. Memory, CPU, PID count, output, tool timeout and
workspace size are bounded. A root-owned supervisor launches agent programs under
separate unprivileged UIDs and kills all processes owned by a terminated identity,
including detached children. Files remain shared with ordinary writable permissions.
`workspace` supports files, search, Python, shell commands, compilation and local
services. Its `service` operation launches a persistent process with a workspace log.

SQLite uses WAL. State and append-only versioned events commit together.
Filesystem notifications and content checkpoints record workspace changes outside
the world. Linux notifications can coalesce repeated writes, so the system records
notifications and observed content versions rather than claiming a byte-by-byte
trace of every CPU write. Observer overflow pauses a run visibly. Process lifecycle,
returned reasoning, tool calls, grades, economics, inference latency and token
counts are operator records. Agent-facing results are explicit allowlists.
Process syscall traces cover children between inventory samples. Observer batches
are acknowledged only after durable database commit, with deduplication on retry.

An agent that stops requesting tools becomes idle without repeated model nudges.
The context guard reserves output/template capacity and uses a conservative UTF-8
byte bound for Qwen tokenization. Capacity stops are visible. Optional summarization
uses only observed history; if the full source cannot fit, it stops visibly instead
of silently truncating. Other model tokenizers may need a larger configured budget.
GPU metrics come from `nvidia-smi`; missing measurements remain unavailable.
Only one run can be active at a time so multiple worlds cannot bypass GPU concurrency.

## Research data and reproducibility

The dashboard exports JSON (complete registry, config, snapshots and events), CSV
and Parquet (events). Replay plays recorded experiences with a time scrubber and
speed controls. It does not regenerate model answers. Repeat conditions creates
a fresh run using the same configuration, seed, dataset and initial artifacts.
Model digests are recorded when Ollama reports them; OpenAI-compatible providers
explicitly report unavailable hashes. Analysis charts use complete event history,
with downsampling only for plotted points. Artifact evidence links show access to
changed files; the software does not infer social intentions from resemblance.

## Optional offline training and model comparison

Continuous training is disabled. Export **Public training trajectories** after
verified successes. This export contains agent-visible experiences, not private
answers or impossibility labels. Personal notebooks and shared files do not modify
model weights. LoRA training is a separate, explicitly invoked command:

```powershell
.\.venv\Scripts\python.exe -m pip install -e '.[training]'
.\.venv\Scripts\python.exe -m utopia.train --dataset trajectories.jsonl --model LOCAL_MODEL_PATH --output .data/adapter-1
```

Hardware and model support determine whether training fits; use a separate GPU or
offline window. Adapters must be packaged into the inference backend by the operator.
The adapter manifest records dataset hash, iteration, model, identities and contexts.
The pipeline appends versioned started/completed/failed records to
`OUTPUT.training-events.jsonl` beside the adapter directory. These offline events
are separate from world events; no background weight-training service is enabled.
`GET /comparison` compares model/context outcomes across runs. For real provider
benchmarks, run `python -m utopia.benchmark --models qwen3.5:2b qwen3.5:0.8b`.

## Verification

Tests are added after implementation, following the supplied plan.

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check utopia sandbox tests
.\.venv\Scripts\python.exe -m ruff format --check utopia sandbox tests
cd dashboard
npm run typecheck
npm run lint
npm run build
```

Real sandbox/inference checks are opt-in and cannot pass without Docker and Ollama:

```powershell
$env:UTOPIA_INTEGRATION = '1'
.\.venv\Scripts\python.exe -m pytest -m integration -v
```

See [verification evidence](docs/evidence.md) for the checked requirements and
remaining activation gates. The original PLAN.md remains unchanged.
