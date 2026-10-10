# Desktop rebuild plan

Goal: deliver the requested local desktop observatory and operator controls while
preserving deterministic economics, private grading and Docker-only agent execution.
The existing Python engine and Next observatory remain authoritative. As requested
and confirmed, a C Win32 window with native menus embeds WebView2 for the rich
observatory content; it is not a rewrite of every renderer widget into C.

## Deliverables

- [x] Native desktop launcher owns local services, logs and shutdown; centralized
  Textual application TUI and separate installation/repair/health TUI.
- [x] Live interactive graphs, Markdown/GFM/code/LaTeX rendering, self-report
  inspector, sandbox explorer action, entity navigation and keyboard controls.
- [x] Durable personal experiences and reflections, relevant notebook retrieval,
  configurable termination pressure influencing agent context; no invented emotions
  or undisclosed reasoning. Existing identities survive pause/restart; respawns
  remain new identities and do not inherit another agent's private notebook.
- [x] Explicit permanent deletion confirmation, stopped sandbox/processes,
  removed database records and run-owned host files; unrelated runs remain intact.
- [x] Same-settings model preloading before charging simulation time, persistent
  keep-alive, streamed first-token/load/prompt/evaluation timing, real benchmarks.
- [x] Optional batch embedding retrieval using Ollama; resource-aware concurrency
  remains operator-configured and GPU placement remains backend-controlled.
- [x] Evaluate and document offline/periodic LoRA from public trajectories; preserve
  lineage and keep automatic weight updates disabled until actually validated.

## Verification and commit boundaries

Use existing Python tests and add focused regression coverage for erasure, durable
learning, retrieval and streamed inference. Run Ruff and the full engine suite
because changes touch lifecycle context. Run dashboard typecheck/lint/build and
inspect rendered desktop/mobile behavior. Verify TUI widgets with Textual's pilot
and launch the real native window. Measure the installed Ollama model; record actual
cold/warm measurements and separate hardware limits from implemented improvements.
Commit simulation, desktop controls and observatory changes as coherent themes.
Do not push. Record unverified training/hardware paths explicitly.

GPU adapter training, continuation and bounded inference host-cache configuration
are verified. The earlier Qwen3 adapter gained one held-out answer over its base
on each of two task seeds. Corrected Qwen3.5 BF16 continuation subsequently gained
three and one correct submissions on two twelve-task seeds, versus zero for its
base. Overall accuracy remains low. A subsequent deployed Qwen3.5 paired
five-minute, six-agent benchmark produced six correct tasks versus four for the
base, with four incorrect tasks each. This single-seed result is a limited live
benefit and does not justify automatic activation.
Live Explorer access is verified with a quota-limited Docker tmpfs volume.
Full live acceptance passed after correcting workspace-write validation and adding
failed-action guidance. See desktop-evidence.md for the retained training,
deployment and live comparison evidence. The full requirement audit completed on
2026-10-10, including current native startup/shutdown, responsive renderer and
graph inspection, both TUI layouts, GPU semantic retrieval and durable notebook
reopening. No requested product behavior remains open. Physical cold loading,
qwen35's backend parallelism restriction and limited model accuracy are recorded
practical limits; automatic weight activation remains disabled.
