# Agent Utopia architecture and execution decisions

The Python engine owns run state, time, allocation and the economy. State and
versioned events commit together in a database transaction. A per-run lock orders
expiration, collective deductions, submissions and assignments. Expiration at a
timestamp occurs before an action at that timestamp. SQLite uses WAL; SQLAlchemy
provides the Python database dialect boundary. The Next.js application declares
the shared tables using Drizzle and reads through the authenticated operator API.
Drizzle cannot execute Python transactions; using it as the Python ORM would
require an additional database service, so Python uses SQLAlchemy.

Inference runs through one shared concurrency semaphore. Agent histories and
exact delivered contexts remain separate. Queue waits are excluded from personal
charges by default. Actual work charges, simulation time and wall time are stored
separately. Context overflow stops inference visibly, or triggers an explicitly
configured summarization without private metadata.

The only execution target for agent code is a disposable Docker container. Each
world shares its bounded tmpfs filesystem, process namespace and loopback-only
services. The container has no host mounts, network, Docker socket, operator token,
database or grader code. A trusted supervisor runs outside the agent UID and
records filesystem and process changes. Workspace snapshots are checkpointed
outside the world and restored after recovery. No host execution fallback exists.

The independent grader operates on private task registries. Agent task views are
explicit allowlists. Math uses bounded symbolic parsing and exact validation.
Cybersecurity uses deterministic synthetic challenge artifacts and private flags.
Infrastructure failures leave agents alive. Observations and classifications are
separate; artifact interactions link to concrete modification events.

The dashboard proxies the backend on the server. The backend token never enters
browser state or agent prompts. Both servers bind to loopback by default.

## Rulings on conflicting plan statements

- Replacement delay defaults to five simulated minutes: section 3 says one minute,
  but section 15 and acceptance criterion 9 both require five. It is configurable.
- Tests are written last, as explicitly required by section 15, overriding the
  test-first skill workflow for this task.
- The supplied PLAN.md is the accepted design and scope. No additional design
  approvals are requested, following the user's explicit instruction.
- Model thinking means only reasoning text actually returned by the provider.
  The observer does not invent or claim access to undisclosed internal reasoning.
- Training is optional and offline. It consumes approved public trajectories,
  never hidden answers or task classifications; memory adaptation changes no weights.

## Phase ledger

1. Configuration, schema and durable events: implemented; automated/API checks pass.
2. Lifecycle, economy and time: implemented; automated checks pass, including subtick queue accounting.
3. Autonomous tools, sandbox and grading: implemented; deterministic loop and real Ollama checks pass. Docker runtime gate remains blocked.
4. Deterministic task bank and impossibility validation: implemented; generated and fixed-bank checks pass.
5. Contexts, notebooks and respawning: implemented; automated separation/recovery checks pass.
6. Dashboard, observation, replay and exports: implemented; browser, API, typecheck, lint and production build checks pass.
7. Recovery, reproducibility and metrics: implemented; recovery/export tests pass and the local NVIDIA GPU is detected. Full population benchmark remains blocked by Docker.
8. Optional offline training and comparison: implemented; public trajectory/comparison checks pass. Actual weight training has not been run.

Real Docker/Ollama acceptance is a separate verification gate. Missing Docker must
be reported as a blocker, never replaced by simulated production functionality.

Observer batches retain monotonic container IDs until the host commits them and
acknowledges delivery. Durable cursors prevent duplicate records on retry. Process
syscall tracing supplements inventory snapshots, including short-lived children;
command-session records are distinguished from process lifecycle observations.
Trace storage is bounded and overflow pauses the world. Filesystem notifications
can coalesce writes; content checkpoints capture observed versions, not every
intermediate byte. Ordinary process visibility can reveal OS-level instrumentation;
observer controls, records, credentials and grader internals are inaccessible to
agent UIDs. See evidence.md for the practical limits of verification.
