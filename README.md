# Agent Utopia

A local autonomous agent world, private grader and live operator observatory.
Python owns simulation state; a native Windows WebView2 window hosts the responsive
observatory, with Next.js rendering recorded application state. The host is written
in C using Win32 windowing, COM, native menus, DPI-aware resizing and WebView2.
Agent code executes **only inside Docker**. No host shell fallback exists.

## Windows setup

Install Python 3.12 or later, Node.js 22 or later, Docker Desktop with Linux
containers, and Ollama. Start Docker before starting a world. The desktop launcher
starts local Ollama when no server is already listening.
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

For first-time setup, run the maintenance console. Its bootstrap creates the Python
environment; use **Install dependencies**, **Build dashboard**, **Build sandbox image**,
**Build native host**, and **Run diagnostics** before launching the application:

```powershell
.\maintain.ps1
```

Launch the desktop application, which starts and manages its local services:

```powershell
.\run.ps1
```

Closing the standalone desktop window gracefully pauses simulations, persists state,
removes owned Docker environments, and shuts down its servers. Existing servers on
the configured backend/dashboard ports cause a visible error. An existing Ollama
server is reused without changing its settings or taking ownership of it.
The Windows WebView2 runtime is required. Building the C host requires Visual Studio
Build Tools with the C compiler and Windows SDK; `native/build.ps1` retrieves the
pinned Microsoft WebView2 SDK from NuGet. Desktop services bind only to loopback.

The centralized terminal control room configures worlds, launches the desktop,
controls simulation lifecycle, shows status and structured logs, and confirms erasure:

```powershell
.\.venv\Scripts\python.exe -m utopia.control
```

`utopia-maintenance` and `utopia-control` are separate installed console commands.
Desktop ports and the data directory are configured in `.data/desktop.json`; all three
interfaces accept `--config PATH`. World settings remain strict configuration JSON.
Logs are written under the configured data directory's `logs` folder.

Desktop configuration also accepts `manage_ollama` (default `true`) and
`ollama_cache_ram_mb` (default `1024`, range `0`–`16384`). The cache budget bounds
the runner's host prompt cache through a process-local `LLAMA_ARG_CACHE_RAM` setting;
it does not unload resident model weights or change global environment variables.
Zero disables this cache. It applies only when the launcher starts its own Ollama
server. Set `manage_ollama` to `false` when using an independently managed provider.
Closing the standalone desktop unloads models and stops the Ollama server it owns.

For development, the backend can still run separately:

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

New worlds use a compact system prompt and six round-robin expertise profiles:
generalist, mathematician, programmer, security analyst, data analyst and
experimentalist. Agents check tool results, change unproductive approaches, and
request another task after a submission or skip. The creation form
exposes the base system instructions directly. Existing worlds and saved presets
keep their exact original prompts and profiles.

Pause or terminate a running world before using **Delete world**.
After confirmation, deletion removes its sandbox, run-owned snapshot directories,
database state, agent notebooks and event records. Other worlds remain untouched.
Erasure is the explicit exception to append-only observation. There is no restore
operation. Previously downloaded exports and independently created model adapters
remain outside world ownership and are not erased by this operation.

Task counts must represent the requested impossible fraction exactly: the default
80 tasks contain 72 solvable tasks and eight impossible tasks. Difficulty is an
independent field. The fixed bank requires independently validated private
answers and validator metadata; see `utopia/tasks.py` for the supported recipes.
The bank includes 24 recipes across mathematics and cybersecurity, including
linear systems, modular arithmetic, probability, statistics, determinants,
compressed payloads, rotating XOR, log forensics and packet reconstruction.

Five minutes is the default respawn delay, resolving conflicting plan statements
in favor of the tests and acceptance criteria. Set `economy.respawn_delay_ms` to
60000 to use the lifecycle section's one-minute variant. Rewards round down once
to whole milliseconds. Queue waiting does not consume personal lifetime by
default; actual model work does. Collective deductions include all living
agents except the skipper. Expiration precedes other actions at equal timestamps.
Each agent earns an additional five minutes at every third correctly solved task
(3, 6, 9, and so on). This cumulative progress survives recovery; skips and malformed
answers do not count. Task reward totals include the bonus, recorded separately in
the lifetime ledger as `three-task-bonus`.

Agents can choose `RELAX` through the relax tool to take intentional downtime.
They remain free to journal, reflect, explore the workspace or return to tasks.
Lifetime still counts down, and an existing task remains assigned. Requesting work
or choosing relax/resume returns them to work; ordinary idle remains distinct.
Relaxation time does not increase the time-based task reward.

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
Kernel process events cover fork, clone, exec and exit between inventory samples.
The trusted observer reads numeric kernel events rather than tracee strings.
Observer batches
are acknowledged only after durable database commit, with deduplication on retry.

An agent that returns text without tool calls receives a continuation instruction
and retries after five seconds while work remains. With no work it waits idle.
The context guard reserves output/template capacity and uses a conservative UTF-8
byte bound for Qwen tokenization. New worlds summarize observed history by default,
preserving the original messages in the event log. The explicit stop policy remains
available; if the full summary source cannot fit, the agent stops visibly instead
of silently truncating. Other model tokenizers may need a larger configured budget.
GPU metrics come from `nvidia-smi`; missing measurements remain unavailable.
Only one run can be active at a time so multiple worlds cannot bypass GPU concurrency.

## Desktop observation and persistent learning

Charts support hover inspection, zoom, pan, keyboard sample selection, follow-latest,
and sample tables. Alt+1 through Alt+6 switch world sections. Arrow keys navigate
tabs and chart samples. Entity links connect agents, tasks and evidence.
Agent dialogue, reflections and notebooks render Markdown, tables, highlighted code
and inline/display LaTeX. Raw HTML and arbitrary host file links are not executed.
Workspace file links select the corresponding live sandbox file while the container
is available, or a recorded snapshot file after it closes.

The agent inspector separates lifecycle state, model output, reported feelings,
intentions, dialogue, learning and persistent notebooks. These are recorded expressions,
not access to hidden thoughts. `reflect` records short self-reports in the personal
notebook; observed task outcomes are also recorded automatically. Relevant personal
notes are retrieved into subsequent contexts. Set `termination_fear` from 0 (disabled)
to 1 to scale time-dependent survival pressure; the signal falls when rewards add time.
No other identities' private memory or hidden task classifications enter retrieval.

Optional `embedding_model` uses a full Ollama model settings object for batched
semantic ranking of up to 64 lexical/recent candidate experiences. For example,
`{"model":"embeddinggemma:latest","context_tokens":2048}`. It must be installed;
model errors are reported. Defaults use lexical retrieval to avoid loading another
model unnecessarily. Memory and expressions survive pause, resume and application
restart with the same agent identity. Replacement agents are new identities, and
Repeat conditions intentionally creates a fresh experiment.

**Open sandbox directory** opens the actual shared Docker workspace in File Explorer
while its container is available, including a paused world whose container remains
allocated. File edits affect the simulation. This requires Docker Desktop's WSL
backend and its exposed Linux volume filesystem on Windows. The workspace uses a
run-owned, size-limited tmpfs volume, preserving Linux permissions and isolation;
it is removed when the sandbox closes or the world is deleted.
**Open snapshot** remains a separate inspection action. When no container is
available, **Open sandbox snapshot** refreshes recorded files into a run-owned,
per-agent host directory. Editing that copy does not change the sandbox. The complete
notebook is beside its snapshot folder. If the live WSL path is unavailable, the
interface reports that failure and offers the explicit snapshot action.
Inspector windows retain bounded live event and transcript windows; complete data
remains in research exports.

Ollama models are preloaded at their actual configured context size before simulation
time starts or resumes. `model.keep_alive` defaults to `-1`, requesting residency;
Ollama still manages allocation when available memory cannot hold every model.
Default GPU placement remains automatic; `gpu_layers` can explicitly control layers
for a supported Ollama backend. `prompt_batch_tokens` defaults to 256 to bound prompt
processing buffers; increase it only after measuring available memory and throughput.
This setting is used consistently for preloading, chat and embedding requests.
Streamed inference records first output, model load,
prompt evaluation and generation timings. Measure this machine using:

```powershell
.\.venv\Scripts\python.exe -m utopia.latency --repeat 5
# Explicitly unload the selected model before measuring cold warmup:
.\.venv\Scripts\python.exe -m utopia.latency --cold --repeat 5
```

See [desktop verification and learning evaluation](docs/desktop-evidence.md).

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
Training targets only the final assistant submission confirmed by a verified task
result. Earlier errors, dialogue and tool results remain context with their loss
masked. The tokenizer must preserve tool calls; overlong trajectories are rejected
instead of silently truncated. Models supporting selected-position vocabulary
projection avoid allocating logits for masked context tokens while retaining the
full context. Other models use the standard masked loss.
Exports preserve the actual summarized submission context and retain observed
public task prompts separately for held-out exclusion. Successful turns containing
multiple tool calls are omitted because they are not isolated submission targets.
The pipeline appends versioned started/completed/failed records to
`OUTPUT.training-events.jsonl` beside the adapter directory. These offline events
are separate from world events; no background weight-training service is enabled.
Periodic offline updates can continue an adapter using `--resume-adapter PREVIOUS`
and a higher `--iteration`, writing each update into a new `--output` directory.
Manifests retain parent lineage, dataset hash, training metrics, device and peak GPU
allocation. Actual training and quality improvement require a compatible Hugging Face
base checkpoint, training dependencies and evaluation; Ollama inference weights alone are
not a training checkpoint.

For supported CUDA GPUs with limited memory, install `.[training,training-4bit]` and
add `--load-in-4bit` to train LoRA on an NF4 base with double quantization and FP16
computation. Only adapters are trained; base weights remain frozen. The manifest
records this mode, continuation requires the same mode, and evaluation automatically
loads the same quantized base for both comparisons. Four-bit mode requires CUDA;
it does not silently fall back to CPU or enable background training.

Compare an adapter with its unchanged base on held-out math answers before selecting
it for inference:

```powershell
.\.venv\Scripts\python.exe -m utopia.evaluate_adapter --adapter .data/adapter-1 --training-data trajectories.jsonl --model LOCAL_MODEL_PATH --output .data/adapter-1-evaluation.json
```

Supply every training dataset in the adapter's lineage after `--training-data`.
The evaluator checks their hashes, excludes their recorded task prompts, uses the
same deterministic decoding for both models, and grades generated answers without
putting private validators into model context. Existing reports are preserved.
This evaluates mathematical answers; tool use, cyber tasks and full simulation
performance still need separate validation. Evaluation never activates an adapter.
Use `--mode submissions --max-output-tokens 256` for a separate single-turn
mathematical submission check with the real tool definitions. This mode requires
a checkpoint chat template using `<tool_call>` tags. It grades only an explicit
`task` submit call, preserves malformed raw outputs, and executes no generated
tools. Ordinary assistant text can accompany the call, matching the simulation
runtime. Multiple calls and invalid submission arguments are rejected. It does not
replace full simulation validation.
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

The repeatable Windows verification command runs unit tests and real Docker/Ollama
checks against the existing `agent-utopia-world:1` image. It does not build or tag
another image. Each real check removes its sandbox container and unloads the test
model when it finishes, including failures:

```powershell
.\scripts\verify.ps1
# Run only the real checks:
.\scripts\verify.ps1 -Suite Integration
```

See [verification evidence](docs/evidence.md) for the checked requirements and
remaining activation gates. The original PLAN.md remains unchanged.
