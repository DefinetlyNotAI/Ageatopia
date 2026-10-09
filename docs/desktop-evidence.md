# Desktop rebuild evidence and learning evaluation

## Implementation decisions

The desktop application uses a native Windows WebView2 window and existing responsive
Next.js renderer. The host is written in C using the Windows API and WebView2's
C COM interfaces. Simulation and sandbox authority remain in Python and Docker.
This is a desktop host, not a replacement of all UI elements with native OS widgets.
Textual supplies distinct application-control and maintenance consoles.

Persistence is identity-scoped. Own experiences, notebook entries and short expressed
reflections are durable SQLite state. Context retrieval supplies relevant excerpts;
the full notebook is retained. Outcome observations never copy private grader answers
or impossible-task labels. A replacement identity does not inherit a terminated
identity's private memory. A recovered identity retains its development and history.

## Ollama measurements, 2026-10-09

Real local qwen3.5:2b, context 8192, application base context and actual tool schema,
986 prompt tokens, one tool call per response; three requests in each series:

| Measurement | Explicit cold unload then preload | Already resident |
| --- | ---: | ---: |
| Preload wall time | 6615 ms | 156 ms |
| Median first output | 533 ms | 527 ms |
| Median generation request wall time | 549 ms | 544 ms |
| Median reported load | 103 ms | 110 ms |
| Median prompt evaluation | 34 ms | 32 ms |
| Median generation | 394 ms | 381 ms |

Afterward `ollama ps` reported 2.5 GB, 100% GPU, context 8192 and indefinite residency.
Empty-chat preload did not return server timing counters on this Ollama version;
its measured wall time is recorded, and absent counters remain null. Tool calls may
arrive as a complete call near the end, so first output is not necessarily the first
natural-language token. These measurements cover initial requests, not a sustained
population throughput benchmark. Cold loading still costs time; preloading moves it
before charged agent work, and residency avoids repeated idle unloads where resources
permit. See [Ollama preload/residency documentation](https://docs.ollama.com/faq).

## Practical learning options

| Technique | Runtime behavior | Practical decision |
| --- | --- | --- |
| Notebook and outcome retrieval | Persistent identity-specific experience influences later context | Enabled; deterministic retrieval and restart persistence tested |
| Semantic retrieval | Batch Ollama embeddings rerank 64 candidate notes | Optional separate model; GPU allocation remains Ollama-managed |
| Offline/periodic LoRA | Freeze base weights and train small adapters from public verified trajectories | Implemented explicit command; adapter continuation and lineage supported |
| Continuous full fine-tuning | Train base weights during active inference | Not enabled; competing inference/training allocations and unverified quality make this unsuitable for the baseline |

The LoRA command uses rank 8, gradient accumulation and checkpointing. Periodic
updates must run in a paused/offline window, retain prior adapters, mix past validated
experience with new successes, and evaluate held-out tasks and existing tool behavior
before selecting a new Ollama model. Lower training loss alone does not demonstrate
improved simulation performance. Individual adaptation remains in each identity's
memory unless separate per-identity adapters are trained and selected explicitly.

Model architecture determines whether an adapter can be imported. Use a compatible
base checkpoint and follow the backend's import support rather than applying an
adapter to a mismatched quantized model. Manifests record parent adapters, iteration,
model version, dataset hash, identities, contexts, device, metrics and peak allocation.
No automatic adapter activation or unmeasured claim of improved weights is made.
See [PEFT LoRA](https://huggingface.co/docs/peft/main/package_reference/lora) and
[Ollama model import](https://docs.ollama.com/import).

## GPU adapter verification, 2026-10-09

Installed the optional training dependencies and the official CUDA 13.0 PyTorch
wheel. The actual training environment used torch 2.14.1+cu130, transformers 5.19.0
and peft 0.21.2 on the NVIDIA RTX 3050 6 GB laptop GPU. The test checkpoint was
[Qwen/Qwen3-0.6B](https://huggingface.co/Qwen/Qwen3-0.6B), whose tokenizer template
preserves tool calls. A smaller inspected checkpoint's template omitted those calls,
so it was not used for this experiment.

The dataset was one public trajectory exported from a real verified task, totaling
3,008 tokens after the checkpoint's chat template. Each iteration performed one
optimizer update with the existing rank-8 LoRA training command:

| Iteration | Training runtime | Training loss | Saved lineage |
| --- | ---: | ---: | --- |
| 1 | 5.207 s | 1.6129 | No parent |
| 2 | 4.609 s | 1.4680 | Parent lora-1 |

The second command used `--resume-adapter` and a separate output directory. All 392
saved adapter tensors changed, both adapters were retained, and the second manifest
records the first manifest as its parent. Invalid base/iteration arguments are now
rejected before loading training dependencies or weights. Peak CUDA allocation was
6,896,579,584 bytes, exceeding physical VRAM; this run does not establish that a
larger training workload will fit without Windows shared-memory spillover.

Importing PyTorch concurrently with a resident 4B inference model exhausted Windows
committed memory and caused a Docker CLI process to fail. After releasing that
experiment's inference model, training and continuation succeeded. Pause the world
and release competing inference allocations before training on this machine.
Docker process-creation failures and failed pause cleanup now become structured
events, with regression coverage, rather than an unobserved clock-task exception.

These checks prove weight updates, persistence and adapter continuation. Lower
training loss on one trajectory does not prove improved held-out task performance.
No adapter was activated in the simulation, and these Qwen3 weights must not be
applied to the different Qwen3.5 base used by the default inference model.

## Verification ledger

- Python suite: 91 passed, three real-provider tests skipped in the standard run.
  This includes task-request guidance and adding the event lookup index to an
  existing database without losing its world, resource-error reporting and early
  adapter validation, model-server ownership, cache configuration and TUI configuration
  persistence. Ruff check and formatting passed. The remaining FastAPI
  TestClient warning comes from its current httpx compatibility layer.
- Dashboard: lint and production build passed. Rendered inspection covered desktop
  and 390-pixel layouts, agent reports and notebook content, link navigation, live
  sample updates, zoom, pan, hover inspection and keyboard sample selection. A
  labeled isolated fixture verified headings, bullet/numbered lists, tables,
  highlighted Python, inline math and display equations. Browser errors were empty.
- Native: MSVC x64 `/TC /W4` compiled without warnings. The actual Win32 window
  rendered the observatory with native menus and status. A COM callback ownership
  defect discovered during launch was fixed. Normal close stopped both owned
  services; the verified follow-up shutdown logged a graceful backend exit. The
  updated launcher also started its own Ollama server. Closing the actual native
  window stopped all three services, released their ports and exited successfully.
- TUIs: Textual pilots verified controls at 120 by 40, 80 by 40 and 80 by 24,
  editable configuration, exact-ID deletion confirmation, progress widgets and
  bounded logging. Narrow control panes scroll, all actions remain keyboard
  reachable, and the world table retains eight rows of height. Maintenance
  diagnostics include the native executable, Docker, Ollama, GPU and disk.
- Explorer: the updated native inspector opened the actual shared sandbox tmpfs
  volume in Windows File Explorer. A separate action opens a recorded snapshot.
  Host snapshots remain inspection copies; live directory edits affect Docker files.
- Real providers: Docker isolation/process/CTF verification passed separately;
  the real Ollama tool-call test passed. Batch embedding retrieval produced 768-value
  vectors and retrieved the relevant latency note in a semantic search.
- Open acceptance issue: the initial three five-minute six-agent runs failed the
  existing full acceptance test (three, then four, then four original agents executed
  a successful workspace command). Recorded activity included autonomous calls, context summaries,
  expressions and notebook entries, but did not meet the verified-completion
  threshold. The second run recorded an incorrect submission. A raw Ollama stream
  check confirmed distinct tool IDs/indexes were assembled correctly; logs also
  showed answers sent through `request` and code sent through `task`. The request
  path now explicitly rejects misplaced answers with submission guidance. That
  follow-up has targeted regression coverage; its live acceptance run still failed
  because two original agents did not execute a successful workspace command.
  Two subsequent preserved 2B runs reached three and five original agents with
  successful workspace commands, with one verified task in each. The public
  mathematical prompt now explains the required expression format, and tool
  descriptions distinguish submission from requesting work. An initial 4B run
  completed seven tasks before the concurrent-import memory failure described above.
  An isolated 4B follow-up completed ten tasks before another committed-memory
  failure at 266.9 simulated seconds, with training stopped. Reducing prompt batches
  to 256 then completed the full five-minute observation with nine verified tasks,
  no runtime error, and one retryable empty response. Five original agents performed
  successful workspace commands, so the existing acceptance assertion still failed.
  The default model remains 2B.
- `npm audit --omit=dev`: zero vulnerabilities. Nine advisory entries remain in
  existing development tooling; unrelated major upgrades were not forced.

Actual GPU LoRA training and adapter continuation have passed as described above.
Held-out math evaluation below found no improvement; full simulation improvement
and adapter activation remain unverified.
Memory-based adaptation changes no model weights. The overall goal remains active
pending acceptance work.

## Bounded host-cache verification

The live runner log recorded 19 cached prompts totaling 6,086.672 MiB against an
8,192 MiB host-cache limit. Runner committed memory grew with repeated prompt
switches. This matches the mechanism in the
[upstream Ollama report](https://github.com/ollama/ollama/issues/18264) and the
[llama.cpp host-cache option](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md).
The documented runner environment variable `LLAMA_ARG_CACHE_RAM` can bound this
cache separately from keeping model weights resident. The desktop supervisor now
starts local Ollama when absent, setting a configurable process-local 1,024 MiB
budget. An existing server is identified as external and is never adopted, changed
or stopped. The control room saves this setting, and maintenance reports the budget.

The preserved `.data/acceptance-default-cache1024` six-agent run completed its full
five-minute observation with two verified tasks, 211 completed inferences, 12 notebook
writes and seven expressed states. Its runner log showed cache eviction and a peak
recorded cache state of 1,011.338 MiB. No warning or error events were recorded.
Four original agents performed successful workspace commands; two did not, so the
existing acceptance gate still failed. The owned server shut down after the run.
The four-service status panel also fits the 80-by-24 control room.

A separate 4B attempt under the same cache budget failed during preload with CUDA
shared-object initialization error, before simulation started. A subsequent default
2B preload succeeded in 5,353 ms. The cache cap is verified for the default model;
this does not establish that all larger model allocations fit or resolve that 4B
startup failure. Full live acceptance and broader adapter quality remain open.

## Held-out adapter evaluation, 2026-10-09

`utopia.evaluate_adapter` compares the unchanged base with its saved adapter on
identical generated math tasks. It validates base and training-dataset lineage before
loading weights, excludes recorded training prompts, and uses deterministic decoding
with thinking disabled and a 128-token limit. Only public prompts enter the model;
the existing private grader verifies the resulting answers. Reports preserve actual
answers and grading outcomes, without private validators or automatic activation.

The saved Qwen3-0.6B lora-2 adapter was evaluated on CUDA with two task seeds:

| Seed | Problems | Base correct | Adapter correct | Evaluation time for both |
| --- | ---: | ---: | ---: | ---: |
| 20261009 | 12 | 1 | 1 | 14.579 s |
| 20261010 | 12 | 0 | 0 | 17.531 s |

Both seeds produced identical base/adapter answers. The first had seven malformed,
four incorrect and one correct. These measurements contradict any claim that the two
single-update training iterations demonstrated improved answers. Reports are
retained in `.data/training-verification/held-out-math*.json`. This limited evaluation
does not exercise tools, cyber tasks, agent memory or a full simulation. The adapter
has not been activated; a larger, better-curated dataset and further evaluation are
still needed before claiming improvement. The standard suite now passes 90 tests,
with three real-provider tests skipped; Ruff check and formatting pass.

## Live Explorer directory verification

A real temporary Docker sandbox verified that WSL can read a changing workspace
through the daemon's process/mount namespace. Windows UNC access to that path was
inaccessible, and a bind-mount exposure attempt failed. All temporary containers and
mount directories were removed. A standard Docker local tmpfs volume subsequently
provided the live workspace through Docker Desktop's exposed WSL volume filesystem,
without namespace surgery or copying files. `/workspace` now mounts the exact
run-owned `utopia-RUN_ID-workspace` volume with the configured size limit and nosuid.

Direct verification confirmed container-to-Windows reads, Windows-to-container
edits, a 16 MiB quota rejecting further writes, and an agent UID being denied access
to another agent's mode-600 file. The observer recorded modifications without errors.
The existing real Docker integration test passed with live-directory editing and
restoration after closing/recreating the sandbox, alongside isolation, CTF and
process-termination checks.

The actual C/Win32 inspector displayed **Open sandbox directory** and **Open snapshot**.
Explorer opened the live `_data` directory containing `explorer-live-check.txt`; the
API reported `workspace-live`. The explicit snapshot action separately returned
`workspace-snapshot` and the same recorded contents. Path escape was rejected with
409. The paused verification world's container remained available, so its live
directory could still be inspected.

Confirmed deletion of that world removed its exact container, named volume and
run-owned snapshot directory; subsequent API lookup returned 404. Regression coverage
also checks removal of an orphan volume when no container remains. Standard tests
passed 91 with three integration tests skipped; the real Docker test passed separately.
Ruff check/format, dashboard lint and production build passed. Live access depends on
the tested Docker Desktop WSL volume path; other layouts report an explicit error
and retain the separate snapshot action. No live mapping is claimed on untested
Docker backends.
