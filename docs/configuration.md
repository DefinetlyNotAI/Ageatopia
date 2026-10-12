# Configuration

[Documentation index](README.md)

Desktop settings and world configuration are separate. World JSON is strict:
unknown keys are rejected. The form exposes common controls; full JSON provides
the remaining settings. Schema: [config.py](../src/backend/config.py).

## Desktop configuration

Source defaults to `.data/desktop.json`; compiled Windows defaults to
`%LOCALAPPDATA%/Ageatopia/desktop.json`. Missing files are created with defaults.

```json
{
  "data_dir": "C:/AgeatopiaData",
  "backend_port": 8000,
  "dashboard_port": 3000,
  "manage_ollama": true,
  "ollama_cache_ram_mb": 1024
}
```

Replace the example data path with a writable directory. Pass the file to the
packaged executable with `--config`, or to the source launcher:

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File .\src\scripts\run.ps1 --config C:/AgeatopiaData/desktop.json
```

Ports must be distinct integers from 1024 through 65535. With `manage_ollama=true`,
neither application port can be 11434. This setting controls starting Ollama if
no server exists; it does not select a world's provider or reconfigure an existing
server. `ollama_cache_ram_mb` accepts 0–16384 and applies to launcher-owned Ollama.
Maintenance saves its theme in the same file.
Defaults and validation: [desktop.py](../src/gui/desktop.py).

## World defaults

| Field | Default | Meaning |
| --- | --- | --- |
| `population` | `6` | Population slots |
| `seed` | `42` | Generation and assignment seed |
| `task_count` | `80` | Task bank size |
| `impossible_fraction` | `0.10` | Hidden impossible-task share |
| `cyber_fraction` | `0.25` | Cybersecurity-task share |
| `leisure_fraction` | `0.10` | Leisure/exploration-task share |
| `clock_mode` | `"wall"` | Wall-time or deterministic-step clock |
| `clock_speed` | `1` | Wall clock speed multiplier |
| `tick_ms` | `1000` | Clock step interval |
| `exclude_queue_wait` | `true` | Exclude inference queue waits from personal lifetime charges |
| `inference_concurrency` | `1` | Shared request limit, configurable from 1 to 16 |
| `context_assignment` | `"round-robin"` | Round-robin or seeded random profiles |

The default bank contains 72 solvable and eight impossible tasks. The impossible
share must be exact: 80 tasks at 10% is valid; 75 at 10% is not. Difficulty is
independent of impossibility. Fixed banks need exactly `task_count` entries and
the private validator metadata required by [tasks.py](../src/backend/tasks.py).

A deterministic world clock does not make inference deterministic. Repeat
conditions preserves inputs rather than guaranteeing identical answers.

## Models and providers

| Model field | Default |
| --- | --- |
| `provider` | `"ollama"` |
| `base_url` | `"http://127.0.0.1:11434"` |
| `model` | `"qwen3.5:2b"` |
| `context_tokens` | `16384` |
| `prompt_batch_tokens` | `256` |
| `max_output_tokens` | `512` |
| `temperature` | `0.7` |
| `seed` | `42` |
| `thinking` | `false` |
| `keep_alive` | `-1` |
| `gpu_layers` | `null` |

Providers are `ollama` and `openai-compatible`. New agent configurations need at
least 8192 context tokens. Thinking records only reasoning returned by the
provider. `gpu_layers` is an optional backend-specific placement setting.

Precedence is a zero-based slot's `agent_model_overrides`, then the profile model,
then the world model. A partial world JSON example:

```json
{
  "name": "Mixed models",
  "agent_model_overrides": {
    "0": { "model": "qwen3.5:0.8b", "context_tokens": 8192 }
  }
}
```

Overrides must address existing slots. Install every configured local model.
Required local models must pass startup residency checks. The shared concurrency
limit applies across providers, while the server may impose smaller per-model
limits. Increasing client concurrency alone does not establish server parallelism.

Adapter lineage fields are `adapter_version`, `training_dataset_version`, and
`training_iteration`. Selecting a trained model is explicit; see [research](research.md).

## Lifetime and rewards

The `economy` object uses integer milliseconds.

| Field | Default |
| --- | --- |
| `initial_lifetime_ms` | `1200000` — 20 minutes |
| `respawn_delay_ms` | `300000` — 5 minutes |
| `reward_base_ms` | `60000` — 1 minute |
| `reward_rate_numerator` / `reward_rate_denominator` | `2` / `3` |
| `skip_individual_ms` | `30000` |
| `skip_collective_ms` | `5000` |
| `repeated_skip_multiplication` | `false` |
| `maximum_multiplier` | `2` |

Verified answers add a task's base and eligible work-time reward with its multiplier
applied. Relaxation does not increase the time reward. Incorrect final answers
terminate an identity; skips return the task and apply configured deductions.
Every third correct task adds a separate five-minute bonus. Expiration precedes
other actions at the same timestamp.

## Memory and context

Default profiles cover generalist, mathematics, programming, security, data
analysis, and experimentation. `base_context`, profile instructions, and knowledge
compose initial context. Existing worlds retain saved settings when defaults change.

| Field | Default | Meaning |
| --- | --- | --- |
| `history_policy` | `"summarize"` | Summarize at context capacity; `"stop"` is also available |
| `termination_fear` | `0.5` | Lifetime-pressure scale from 0 to 1 |
| `inherit_memory` | `true` | Replacement inherits its parent's notebook entries |
| `inherited_memory_limit` | `100` | Maximum inherited entries |
| `cleanup_on_respawn` | `false` | Optional scratch cleanup |
| `embedding_model` | `null` | Optional Ollama settings for semantic ranking |

Recovery retains an identity. Respawn creates a new identity with recorded lineage
and the configured inherited notebook entries. Unrelated notebooks and hidden
grader information are not retrieval inputs. Lexical/recent retrieval works
without embeddings; install the chosen embedding model before enabling ranking.

## Resource limits

The `tools` object defaults to:

| Field | Default |
| --- | --- |
| `image` | `"ageatopia-world:1"` |
| `timeout_seconds` | `15` |
| `workspace_mb` | `64` |
| `memory_mb` | `512` |
| `cpu_count` | `1` |
| `pid_limit` | `128` |
| `output_bytes` | `32768` |

These bounds govern Docker and agent tools. Launcher admission separately checks
host RAM/commit and fixes a Windows Job Object budget at startup. It is a commit
ceiling, not an exclusive physical RAM reservation. Models preload before lifetime
charging; allocation failures remain errors.

Implementation: [providers.py](../src/hooks/providers.py) and
[resources.py](../src/hooks/resources.py).
