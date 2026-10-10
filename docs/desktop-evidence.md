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

## Verified-submission training and allocation

Public successful trajectories can contain earlier invalid operations. Training
now masks their loss, along with all context and tool results, and supervises only
the assistant submission matched to the final verified task result. It rejects
mixed target calls, mismatched result IDs, omitted template tool calls and silent
truncation. Padding preserves the masked labels. Evaluation reports identify the
actual adapter directory, weight hash and training objective.

The preserved Qwen3-0.6B public trajectory contains 3,618 input tokens with the
actual tool definitions; 161 assistant-target tokens are supervised. A first
masked run projecting vocabulary logits for every context position failed during
CUDA backward allocation. Selecting only the causal prediction positions for
supervised tokens retained the full input and completed a fresh update in 5.380 s
at a measured peak allocation of 1,804,917,248 bytes. A separate numerical comparison
on the final 512 tokens found a loss difference of 0.000005126 from the standard
masked loss. This diagnostic used a shorter input; the actual training did not.

Continuation into a fresh directory completed in 5.379 s at the same measured
peak allocation, preserving parent lineage and changing 392 adapter tensors.
The outputs are `adapter-verified-only-2` and `adapter-verified-only-3` under
`.data/training-verification`; older adapters and failed-run events are retained.
Training loss decreased from 0.5361 to 0.4451 on this single repeated example.
That is evidence of weight updates, not held-out improvement.

The fresh adapter's seed-20261009 evaluation still scored 1/12 for both base and
adapter; 11 answers were identical. Its weight SHA-256 is
`13ab5a36adcca9f9388dfb35285ec759a16b6e26acbb469a55e0663b1783d3d0`.
The report is `held-out-verified-only.json`. Neither adapter was activated.
The standard suite passed 93 tests with three real-provider tests skipped, and
Ruff checks passed. Full live acceptance and demonstrated learning improvement
remain open.

## Tool recovery guidance

The preserved cache-budgeted default run showed repeated workspace calls with code
in `text` instead of the required `value`, receiving only the error `'value'`.
Workspace descriptions now give a Python example, explain each operation's value,
and identify text as write content. Missing-value errors give the same guidance
without executing a command. Inspecting without an assigned task now explains
how to request work instead of returning null.

The unchanged six-agent acceptance test ran against real Docker and resident-capable
Ollama for its full five-minute observation. The preserved data is in
`.data/acceptance-tool-guidance/test_real_six_agent_acceptance0`. It recorded two
verified tasks, 207 completed inferences, 37 context summaries, 18 notebook writes
and 11 expressed states. Successful workspace activity continued after summaries;
five original agents performed successful workspace commands. No inference or
runtime error events were recorded. The experimentalist used file operations for
calculations, skipped/re-requested work and terminated after an incorrect answer
before a successful workspace command. The existing gate therefore still failed;
the feedback improvement does not prove full acceptance. Focused regressions and
the standard suite passed, and Ruff check/format passed.

## Training export context fidelity

The export previously retained discarded pre-summary history, omitted the actual
summary, and accumulated ephemeral memory supplements as if they were durable
conversation. Export now reconstructs the history reset and includes only the
supplement used for the successful submission inference. A regression compares the
exported prompt exactly with the provider's recorded input after summarization.
Mixed-tool success turns are omitted rather than fabricated into single-call
targets; a regression verifies the task remains completed while that turn is omitted.

Own observed public task prompts remain separate metadata, so held-out evaluation
can exclude them after their original tool messages have been summarized away.
Legacy full-history exports remain accepted; summarized exports without prompt
metadata are rejected for evaluation. No private validators or answers enter this metadata.
The two verified tasks from the latest live run exported as five-message trajectories
and passed target validation. The versioned dataset is
`.data/training-verification/public-trajectories-tool-guidance-v2.jsonl`.

A fresh Qwen3-0.6B CUDA adapter trained on these two examples in 3.340 s, supervising
55 tokens with peak allocation 1,486,101,504 bytes. Its saved report
`held-out-summarized-1.json` scored base 1/12 and adapter 0/12 for seed 20261009.
This small dataset worsened the held-out result; the adapter was not activated.
Correct context reconstruction and successful weight training do not demonstrate
improved agent performance. Standard verification passed 96 tests with three
real-provider tests skipped; Ruff check/format passed. Full acceptance and learning
quality remain unresolved.

## Voluntary relaxation and recurring success bonus

Agents can choose `RELAX` with the relax tool and continue using memory, reflection
and workspace tools. Text-only replies preserve the state without a forced return
to task work. The runtime schedules relaxing agents even without available tasks.
Lifetime still counts down and assigned work remains reserved. Resume or requesting
work restores the working state; relaxation and its task-time accounting survive recovery.
Relaxation is excluded from reward-eligible elapsed time, preventing multiplied
task rewards from turning deliberate downtime into lifetime gains.

Every third cumulative correct task awards an additional 300,000 ms at 3, 6, 9,
and so on. Durable task history determines progress; no mutable counter is reset
by application restart. The lifetime ledger records `three-task-bonus` separately,
and task reward totals include it. Agent context, the lifetime tool and inspector
show progress toward the next bonus.

Verification passed 100 standard tests with three real-provider tests skipped,
including bonuses at three and six successes across recovery, relaxation charging,
journaling and text replies, and exclusion of downtime from doubled task rewards.
Ruff, dashboard typecheck/lint and production build passed. The actual C/Win32
window rendered a labeled operator fixture showing `RELAX`, three correct tasks,
28:00 remaining and the next +5:00 bonus. This fixture checks rendering and engine
transitions; it does not claim autonomous live-model adoption or full acceptance.

## Held-out submission-format evaluation

Training supervises task submit calls, while the original held-out evaluation
requested plain answers. `utopia.evaluate_adapter --mode submissions` now supplies
the real tool definitions and asks for one mathematical task submit call. A strict
parser rejects surrounding prose, multiple calls, non-task calls, non-submit
operations and non-string answers. Only valid extracted answers reach the existing
private grader. Raw generated outputs and format failures remain in the report.
The original answer mode remains available. Submission mode supports checkpoint
templates using `tool_call` tags and rejects unsupported templates before loading
model weights; no generated tools execute and no adapter is activated.

The saved two-example Qwen3-0.6B adapter was compared with its unchanged base on
CUDA, seed 20261009, 12 held-out mathematical tasks, greedy decoding and a 256-token
limit. Both scored 0/12; all 12 outputs from each model were malformed. Outputs
included argument-only Python requests rather than complete task submit calls.
The comparison took 34.436 s and is preserved in
`.data/training-verification/held-out-submissions-summarized-1.json`. This establishes
failure on the single-turn submission check, not on multi-step tool execution,
cyber tasks or the full simulation. Standard tests passed 101 with three real-provider
tests skipped; Ruff check/format and focused review passed. Learning improvement
and full acceptance remain unresolved.

## Pooled experience training and runtime-aligned submission check

Decoded supervised targets contained complete named submit calls; the earlier
format failure was not caused by missing tool-call structure in training labels.
Thirty-one verified single-call submissions were exported from eight preserved
real acceptance runs, including 15 identities and nine distinct submitted answers.
All fit the 4,096-token input limit without truncation; the source report and dataset
are `.data/training-verification/public-trajectories-pooled-report.json` and
`public-trajectories-pooled.jsonl`. No private grading answers were added.

Twenty-five successful assistant turns contain text alongside their tool call,
which the simulation runtime permits. The submission evaluator now permits that
ordinary text while still requiring exactly one complete named task submit call
with the correct argument structure. The earlier stricter report remains preserved;
its argument-only outputs still fail under the revised parser.

A fresh Qwen3-0.6B adapter trained for three epochs and 12 optimizer steps on the
31 examples. Training took 135.782 s with peak GPU allocation 2,226,686,464 bytes;
the dataset contains 4,389 supervised target tokens before epoch repetition.
The saved adapter is `adapter-pooled-1`. Its CUDA held-out submission comparison,
seed 20261009, 12 tasks and a 256-token limit, took 31.747 s and scored base 0/12
and adapter 0/12. Neither produced a valid submit call. The preserved report is
`held-out-submissions-pooled-1.json`. More examples and updates did not demonstrate
improvement in this check; the adapter remains inactive. Standard tests passed 101
with three real-provider tests skipped, and Ruff check/format passed.

## Four-bit training on the larger checkpoint

The official Qwen3-1.7B checkpoint was downloaded at revision
`70d244cc86ccca08cf5af4e1e306ecf908b1ad5e` into
`.data/training-verification/base-1.7b`. An FP16 attempt completed two optimizer
steps but reported a CUDA allocation retry; another shell could not start because
Windows paging capacity was exhausted. The training process was interrupted before
saving a checkpoint. Its started-only event file and empty output directory remain
preserved; no completed training or saved adapter is claimed for that attempt.

Optional `--load-in-4bit` training now uses bitsandbytes NF4 double quantization
with FP16 computation on CUDA. The base is prepared and frozen before LoRA is
added or resumed. Continuation checks base quantization before loading weights;
evaluation reads it from the manifest and compares disabled/enabled adapters on
the same quantized base. Standard training remains available without bitsandbytes.
The separate `training-4bit` extra was verified using bitsandbytes 0.50.2 on Windows,
PyTorch 2.14.1+cu130 and the RTX 3050 6GB Laptop GPU.

A fresh quantized Qwen3-1.7B adapter trained on all 31 pooled verified submissions
for one epoch and four optimizer steps. It completed without a CUDA allocation
warning, taking 94.050 s including save, with peak GPU allocation 4,043,925,504
bytes and training loss 0.622905. The saved adapter is
`.data/training-verification/adapter-pooled-1.7b-nf4-1`; its manifest and completed
event record preserve dataset hash, 15 identities, 4,389 supervised tokens and
base quantization. This confirms that this workload fits, not that it improves
agent performance. No adapter has been activated.

Paired CUDA submission evaluations used the same quantized base, real tool
definitions, greedy decoding and a 256-token limit on 12 held-out tasks per seed.
Seed 20261009 scored base 0/12 and adapter 1/12 in 142.754 s; valid submit calls
increased from one to three. The gained success was a Boolean conjunction count.
Seed 20261010 scored 0/12 for both models in 99.908 s. The first gain did not
repeat; reliable learning improvement and live simulation acceptance remain
unproven. Reports are `held-out-submissions-pooled-1.7b-nf4-1.json` and
`held-out-submissions-pooled-1.7b-nf4-1-seed20261010.json` in the same evidence
directory. These single-turn checks do not execute generated tools.

Thirteen focused learning tests and Ruff check/format passed. Focused code review
found no actionable issues. The implementation follows the
[PEFT quantized training procedure](https://huggingface.co/docs/peft/developer_guides/quantization)
and the installed release's
[Windows CUDA support](https://huggingface.co/docs/bitsandbytes/v0.50.2/en/installation).

## Workspace feedback and successful live acceptance

The prior run's missing agent sent calculations to shell and file operations.
It also called `workspace write` with code as the path and omitted file content;
the executor previously supplied an empty string and attempted a file write.
Writes now require explicit `text`, while an intentional empty string remains
valid. Missing content returns an error before process execution. Failed workspace
process responses retain their output and exit status and include short guidance
distinguishing paths, Python code, shell commands and search patterns. Successful
responses and running services do not receive that extra field.

The unchanged real six-agent acceptance test passed in 247.49 s with the default
Qwen3.5:2B model and Docker. The preserved run is
`.data/acceptance-workspace-feedback/test_real_six_agent_acceptance0/utopia.db`,
world `a4a7c0c3f26143d7955db44a5380ca67`. It reached 227,671 ms of simulation time,
with successful workspace actions from all six identities, continued successful
actions after summarization, at least two inferences and assigned/history tasks
per identity, and one verified completion. There were 174 completed inferences,
42 summaries, 21 failed-process guidance responses and two missing-content guard
responses. All 5,460 recorded events had info severity. Termination captured the
final workspace; subsequent checks found no remaining sandbox containers or loaded
Ollama models. A passing run does not establish that every agent choice was effective.

Verification passed 105 standard tests with three integration tests deselected,
the real acceptance test, and Ruff check/format. Focused review found no actionable
issues. Reliable held-out weight-learning improvement remains unproven; no adapter
was activated.

## Preserve completed results across observer cancellation

The passing live acceptance run retained its verified task ledger and `tool.result`
event but exported zero training rows. Its worker was cancelled during the
workspace-observation await before the returned tool message reached conversation
history. Recovery consequently substituted an interruption message for an action
that had already completed. The isolated successful submission was present; this
was a persistence-order issue, not a mixed-call exclusion.

The runtime now saves the returned tool message and removes its pending call before
awaiting observation. The terminated-agent check remains before persistence. A
regression reproduces cancellation after a real engine-verified submission, then
reloads and recovers the saved state. It retains the actual `Verified` result, has
no pending call and exports one public training target. Original evidence is
preserved; no missing historic conversation message was fabricated.

Verification passed 106 standard tests with three integration tests deselected,
Ruff check/format and focused review. The previously recorded live acceptance pass
remains evidence for that run; this cancellation fix was checked by its focused
reproduction and the standard suite.

## Four-bit adapter continuation

The saved `adapter-pooled-1.7b-nf4-1` was resumed with iteration two into a fresh
`adapter-pooled-1.7b-nf4-2` directory, retaining the NF4 double-quantized base.
The attempted dataset update added zero rows because of the observer-cancellation
issue above. `public-trajectories-pooled-v2.jsonl` contains the same 31 existing
examples, reserialized with hash
`c0dfd0d77212cac741555293f76524072844f2f219752e5ada5d4a552d967394`.
No new experience or reconstructed historic tool message is claimed for this update.

Two further epochs and eight optimizer steps completed in 184.794 s including
save, with peak GPU allocation 4,079,186,944 bytes and training loss 0.411352.
All 392 saved LoRA tensors changed from the parent. The manifest and completed
event retain the original parent metadata, both dataset versions, identities,
iteration and quantization mode. This verifies periodic weight continuation on
the local GPU; the lower training loss does not establish held-out improvement.

The same paired CUDA submission protocol compared this continued adapter with its
disabled-adapter base on both seeds, using both lineage datasets for held-out
exclusion. Seed 20261009 scored base 0/12 and adapter 1/12 in 178.716 s; the gained
answer was the Boolean conjunction count. Seed 20261010 scored base 0/12 and
adapter 1/12 in 177.115 s; the gained answer was the exact probability `5/26` for
drawing two red balls from six red and seven blue without replacement. This is a
small repeated gain across two task samples, while overall accuracy remains 1/12
per sample. It does not establish improved multi-step simulation behavior or justify
automatic activation. Reports are `held-out-submissions-pooled-1.7b-nf4-2.json`
and `held-out-submissions-pooled-1.7b-nf4-2-seed20261010.json`. Saved weights hash
`8377853ab058d71762eb2d2c8b42945a88dcf630cc41c675149809edd5644fd5` identifies
the actual compared adapter. GPU memory returned to zero after evaluation.

## Continued adapter deployment on Windows

Installed Ollama 0.40.1 explicitly rejects LoRA adapter imports in its create
handler. A direct import of the matching Qwen3-1.7B Safetensors base failed with
MLX dynamic library unavailable. No failed import was treated as a deployed model.

The official llama.cpp v0.6.0 source, commit
`d81235049384534c167caea52b85a694f6103d14`, was built with VS2022/CMake, CPU only,
for its export tool. Both official converters succeeded after installing their
required sentencepiece dependency in the local venv. The base was converted to
F16 GGUF and the continued adapter to F16 GGUF. The merge applied its recorded
alpha/rank scale of 2, modified 196 base tensors and wrote all 311 tensors.
The full merged model imported successfully through Ollama under
`ageatopia-qwen3-continued-verification:latest`; the matching base imported under
`ageatopia-qwen3-base-verification:latest`. Neither is the application default.

The deployed merged model ran through the actual SharedInference client with all
application tools and returned a valid workspace Python call to compute 2 + 3.
It did not directly submit the requested answer. Cold latency was 14,656 ms
(load 7,517 ms, prompt 6,353 ms); the identical warm request took 1,115 ms
(load 16 ms, prompt 322 ms). First returned output was at 14,655/1,113 ms;
these tool-only responses do not measure first natural-language-token latency.
Ollama reported 4.4 GB, 100 percent GPU, 8,192 context and persistent residency.
The verification model was explicitly unloaded afterward.

Ignored local artifact `gguf-deployment-verification.json` records file sizes,
converter revision and hashes. Base GGUF hash is
`0519188ea5be1772defc9b7ed72afbea6315e30c94116672e2de043ff4212cdf`;
merged GGUF hash is
`f52eed6bafba3591bc7a5928a2757d03fc99c02f412352b782efd66a0bc0e160`.
Conversion/import logs and cold/warm client reports remain under
`.data/training-verification`. This verifies a deployable full-model path; F16
deployment differs from the NF4 evaluation base and requires a new paired quality
comparison. No live simulation effectiveness or automatic activation is claimed.

The requested RELAX and recurring-bonus regression surface was rechecked:
70 engine/runtime tests passed.

## Paired deployed-model simulation, 2026-10-10

The existing benchmark command ran the matching F16 base and merged continued
model sequentially for 300 seconds each, with seed 20261010, six default contexts,
40 tasks, identical model settings and the real Docker sandbox. The saved run IDs
are `685fdd5c77bc407696c9e701ec99d152` (base) and
`540c6df9bf534bbe838f11330ea2ad6d` (continued). Both recorded the actual Ollama
model digest. During these runs, automatic placement reported 5.1 GB,
15 percent CPU / 85 percent GPU and 8,192 context for each model; this differs
from the earlier smoke request with explicitly requested GPU layers.

| Observed result | Base | Continued adapter merged into F16 base |
| --- | ---: | ---: |
| Simulation time | 300,783 ms | 300,173 ms |
| Verified task completions | 0 | 0 |
| Incorrect submissions | 4 | 5 |
| Completed inferences | 228 | 285 |
| Saved notebook entries | 5 | 5 |
| Expressed reflections | 1 | 0 |
| Recorded outcome experiences | 4 | 5 |

Each run made only one workspace call; repeated task-tool calls dominated activity.
More inferences therefore do not demonstrate better work or a throughput gain.
These results provide no evidence of improved live simulation behavior and do not
justify selecting the candidate. The small NF4 held-out gains remain evidence for
that offline protocol, not a guarantee for F16 deployment or multi-step tool use.
The application default remains qwen3.5:2b, which passed its separate acceptance gate.

Both runs terminated normally, captured final workspace state and recorded only
info-severity events. A fresh SQLite connection matched each complete saved state
against its export, including the actual memories, expressions and learned outcome
entries. This verifies retained data, not successful behavioral adaptation.
The benchmark left no sandbox containers; its resident candidate was explicitly
unloaded afterward. Database, exports, comparison and paired-summary.json remain
under `.data/benchmarks/deployed-continuation-pair`.

Current verification passed 106 standard tests with three integrations deselected,
Ruff lint and formatting. Live effectiveness of trained weights remains unproven.

## Qwen3.5 training and submission-format support

The actual default model family was investigated using the official
[Qwen3.5-2B checkpoint](https://huggingface.co/Qwen/Qwen3.5-2B), pinned to revision
`15852e8c16360a2fea060d615a32b45270f8a8fc`. Its text checkpoint loads through the
installed Transformers Qwen3_5ForCausalLM support. Its tokenizer renders tool calls
as function/parameter blocks inside tool_call tags rather than Qwen3's JSON body.
The offline evaluator now accepts this real format while preserving exactly one
task submit call, only operation/answer parameters, and duplicate/extra-parameter
rejection. Both protocols remain covered by focused regressions.

All 31 existing verified public examples passed the real tokenizer preflight,
with a maximum length of 2,654 tokens and 4,891 supervised tokens. No new examples
or private answers were introduced. The initial four-bit training attempt was
stopped after a separate diagnostic shell failed with System.OutOfMemoryException
during training; it produced no completed checkpoint. Its started record remains
preserved, without a fabricated completed/failed event.

A subsequent actual-model allocation diagnostic showed PEFT preparation expanded
the frozen tied embedding/head from FP16 to FP32: allocated GPU bytes increased
from 1,726,784,512 to 2,744,994,816. The embedding alone occupied 2,034,237,440 bytes;
the diagnostic's peak allocation was 3,761,021,952 bytes. The explicit
fp16-frozen-embeddings option now preserves FP16 input embeddings after preparation,
with the tied head sharing their weight and normalization still FP32. Defaults
remain unchanged; the manifest records and strictly validates the boolean precision
choice for resumed training, including rejection of numeric JSON values.

With that option, one epoch and four optimizer steps completed on the local RTX
3050 in 139.655 seconds including save. Training loss was 0.245228 and peak PyTorch
allocation during training was 3,431,236,608 bytes. The saved artifact is
adapter-pooled-qwen3.5-2b-nf4-fp16emb-1, weights hash
`fb73cd8233b28c9cfbb76a0491ab3c636eed55dea963ece8e322fd860af055cb`, size
33,688,200 bytes. Its completed event and manifest preserve dataset hash,
identities, precision mode and metrics. Reference attention kernels were used;
this run does not verify optimized Windows attention kernels or quality improvement.

Seventeen focused learning tests and Ruff check/format passed. Static review found
the numeric/boolean resume issue, which was fixed and regression-tested. Held-out
quality evaluation is separate; this adapter has not been deployed or activated.

The paired greedy single-turn submission check subsequently completed in 388.505
seconds on seed 20261009: base 0/12, adapter 0/12, with no valid final submissions
from either model. The generated outputs repeatedly requested work and sometimes
invented tool-response dialogue; the evaluator rejected them rather than treating
that generated dialogue as execution evidence. All twelve decoded outputs were
identical between the two runs. This provides no quality-improvement evidence for
this protocol and does not isolate mathematical ability from task/tool behavior.
The unchanged report is held-out-submissions-qwen3.5-nf4-fp16emb-1.json under
.data/training-verification. GPU allocation returned to zero afterward.

Final verification passed 110 standard tests with three integrations deselected,
plus Ruff lint and formatting. The trained adapter remains inactive because neither
this check nor the earlier deployed-model benchmark demonstrated a live benefit.

## Evaluation turn-boundary correction

An actual configuration diagnostic found that Qwen3.5's config-derived generation
end token was 248044 (endoftext, also its padding token), while the tokenizer's
assistant-turn end token is 248046 (im_end). The pinned official repository has no
generation_config.json, confirmed through its file listing and a 404 retrieval.
The evaluator now explicitly passes tokenizer end/padding token IDs and records
them in the report. A regression covers the differing tokens, missing padding and
missing end token; static review found no actionable issue.

With only this stopping correction, the same twelve-task seed and adapter were
reevaluated in held-out-submissions-qwen3.5-nf4-fp16emb-1-turn-stop.json. Runtime
fell from 388.505 to 69.445 seconds. Invented tool-response continuations disappeared;
all outputs ended after a single request call. Base and adapter remained identical
and scored 0/12. The original report is preserved but its generated continuations
cannot be attributed solely to model behavior: the evaluator allowed generation
past the actual assistant-turn boundary. This corrected check still supplies no
assignment exchange, unlike the verified training contexts; it therefore does not
isolate ability to solve an assigned task. The adapter remains inactive.

Eighteen focused learning tests passed, along with Ruff lint and formatting.

## Public assignment evaluation context

A separate assigned-submissions mode now supplies the public request/response
assignment exchange before generation, matching the shape of real task-tool
assignment results and training histories. Both direct-problem modes remain
available. Reports explicitly identify public-assignment-fixture-v1, so this
constructed context cannot be mistaken for live tool execution. The response uses
public_task exclusively; a regression guards private-answer/validator exclusion
and matching tool call IDs. Nineteen focused learning tests passed, and static
review found no actionable validity, privacy or compatibility issue.

The paired assignment check completed in 122.315 seconds on seed 20261009,
preserved as held-out-assigned-submissions-qwen3.5-nf4-fp16emb-1.json. Both models
attempted submissions instead of requesting work, but scored 0/12: five incorrect
and seven malformed results each. All twelve decoded outputs remained identical.
Several malformed answers used unsupported mathematical syntax; others omitted
the answer or repeated incomplete calls. This demonstrates the assignment context
changes tool behavior, but provides no adapter improvement evidence. It does not
prove full simulation effectiveness, and the adapter remains inactive.

Final standard verification passed 112 tests with three integrations deselected;
Ruff lint/format and diff checks also passed.
