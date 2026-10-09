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

## Verification ledger

- Python suite: 75 passed, three real-provider tests skipped in the standard run.
  The subsequently added task-request guidance regression passed with all 15 runtime
  tests. Ruff passed. The remaining FastAPI TestClient warning comes from its current
  httpx compatibility layer.
- Dashboard: lint and production build passed. Rendered inspection covered desktop
  and 390-pixel layouts, agent reports and notebook content, link navigation, live
  sample updates, zoom, pan, hover inspection and keyboard sample selection. A
  labeled isolated fixture verified headings, bullet/numbered lists, tables,
  highlighted Python, inline math and display equations. Browser errors were empty.
- Native: MSVC x64 `/TC /W4` compiled without warnings. The actual Win32 window
  rendered the observatory with native menus and status. A COM callback ownership
  defect discovered during launch was fixed. Normal close stopped both owned
  services; the verified follow-up shutdown logged a graceful backend exit.
- TUIs: Textual pilots verified visible controls at 120 by 40, editable configuration,
  exact-ID deletion confirmation, progress widgets and bounded logging. Maintenance
  diagnostics include the native executable, Docker, Ollama, GPU and disk.
- Explorer: the UI action opened the agent's world-owned snapshot directory in
  Windows File Explorer. Host snapshots are inspection copies of the shared Docker
  workspace, not editable mounts of the live sandbox.
- Real providers: Docker isolation/process/CTF verification passed separately;
  the real Ollama tool-call test passed. Batch embedding retrieval produced 768-value
  vectors and retrieved the relevant latency note in a semantic search.
- Open acceptance issue: two five-minute six-agent runs failed the existing full
  acceptance test (three, then four original agents executed a successful workspace
  command). Recorded activity included autonomous calls, context summaries,
  expressions and notebook entries, but did not meet the verified-completion
  threshold. The second run recorded an incorrect submission. A raw Ollama stream
  check confirmed distinct tool IDs/indexes were assembled correctly; logs also
  showed answers sent through `request` and code sent through `task`. The request
  path now explicitly rejects misplaced answers with submission guidance. That
  follow-up has targeted regression coverage; its live acceptance result is pending.
- `npm audit --omit=dev`: zero vulnerabilities. Nine advisory entries remain in
  existing development tooling; unrelated major upgrades were not forced.

Actual LoRA weight training has not been run: this environment does not currently
contain torch, transformers or peft, or an unquantized training checkpoint. The
continued-adapter command and evaluation procedure enable explicit offline updates;
model improvement from trained weights remains unverified. Memory-based adaptation
changes no model weights. The overall goal remains active pending acceptance work.
