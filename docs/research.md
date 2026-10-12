# Research and training

[Documentation index](README.md)

The observatory records actions and outcomes for analysis. It separates observed
behavior, provider-returned reasoning, and agent self-reports from interpretations
about intent or learning.

## Inspect and export

World views connect agents, tasks, events, and workspace evidence. The inspector
shows lifecycle, dialogue, reported state, learning, and notebooks. Markdown,
tables, highlighted code, and LaTeX render in supported content views. Charts
provide sample inspection, zoom, pan, and keyboard navigation; Alt+1 through Alt+6
switch world sections.

| Output | Contents |
| --- | --- |
| Research JSON | Registry, configuration, state snapshots, and events |
| CSV / Parquet | Event records for external analysis |
| Public training trajectories | Eligible agent-visible experiences ending in verified submissions |

Research exports are operator data and can contain private grading state. Use
**Public training trajectories**, not a private research JSON export, for training.
Plotted history may be downsampled; exports retain complete records.

Replay reads recorded events with time and speed controls. It does not regenerate
answers or execute tools. **Repeat conditions** creates a fresh run with the same
configuration, seed, bank, and initial artifacts. Model answers can still differ.
Ollama model digests are recorded when available; unavailable provider hashes are
represented explicitly rather than invented.

## Notebooks and adaptation

Agents can write/search notebooks and record short reflections. Relevant experience
is retrieved into subsequent contexts, with optional semantic ranking. This changes
available context without changing model weights. Recovery preserves identity and
experience; parent-to-replacement notebook inheritance follows world configuration.

`RELAX` is intentional downtime: lifetime still counts down and an assigned task
remains assigned. Agents can journal, reflect, explore, or resume work. Nonempty
feeling reports can earn a rate-limited 15-second lifetime reward; every third
verified task earns a separate five-minute bonus. The lifetime ledger records
these bonuses separately.

## Provider measurements

Run these in the activated source environment. A benchmark uses real models and
Docker execution; install the selected models and build the sandbox first.

```powershell
python -m backend.latency --repeat 5
python -m backend.benchmark --models qwen3.5:2b qwen3.5:0.8b --seconds 300 --output dist/benchmarks
```

`backend.latency --cold --repeat 5` explicitly unloads the selected model before
measuring warmup. Keep cold loading, prompt processing, first output, cached
requests, and full simulation throughput separate. Compare equivalent contexts,
tools, decoding settings, and seeds. Archive reports include their original
hardware/backend conditions; they are not performance guarantees.

## Offline training

Training is optional, source-based, and explicitly invoked. The compiled Windows
package does not bundle its dependencies. It requires a compatible Hugging Face
base checkpoint; an Ollama model tag alone is not a training checkpoint.

From the repository root in the project environment:

```powershell
python -m pip install -e ".[training]"
python -m backend.train --dataset trajectories.jsonl --model C:/Models/base-checkpoint --output dist/training/adapter-1 --iteration 1
```

Replace the dataset and checkpoint paths with existing files. Export trajectories
from the observatory first. The trainer supervises verified assistant submissions,
rejects invalid targets, and rejects over-length samples without silently truncating
them. `--max-length` defaults to 1024. Increase it for longer trajectories only
when the model and available memory can accommodate the full sequence.

Use a new output directory for each update. Training writes `manifest.json` and
an adjacent `adapter-1.training-events.jsonl`. The manifest records dataset hash,
model/context/identity lineage, precision, optimizer steps, changed adapter tensors,
device, and measured training resources. Missing/non-finite gradients or unchanged
adapter weights prevent a completed checkpoint.

Continuation uses the same base and precision/quantization settings, a saved parent,
and a higher iteration:

```powershell
python -m backend.train --dataset trajectories.jsonl --model C:/Models/base-checkpoint --resume-adapter dist/training/adapter-1 --output dist/training/adapter-2 --iteration 2
```

For supported CUDA hardware, install `.[training,training-4bit]` and explicitly
select the relevant options:

| Option | Effect |
| --- | --- |
| `--load-in-4bit` | Frozen NF4 base with adapter-only training; CUDA is required |
| `--bf16` | BF16 compute for four-bit training on supported hardware |
| `--fp16-frozen-embeddings` | Keep frozen input embeddings in FP16 in four-bit mode |

Unsupported modes fail instead of silently switching to CPU. Keep continuation
settings consistent with the parent's manifest. Training code and CLI:
[train.py](../src/backend/train.py).

## Evaluation and deployment

Compare a candidate with its unchanged base before selecting it for inference:

```powershell
python -m backend.evaluate_adapter --adapter dist/training/adapter-1 --training-data trajectories.jsonl --model C:/Models/base-checkpoint --output dist/training/adapter-1-evaluation.json
```

Supply all training datasets in a continued adapter's lineage after `--training-data`.
The evaluator checks hashes and excludes their recorded prompts from held-out tasks.
Modes are `answers`, `submissions`, and `assigned-submissions`; the latter two grade
explicit task submissions with tool definitions and execute no generated tools.
The assigned mode uses a labeled public assignment fixture.

A completed adapter or reduced loss does not establish improved simulation quality.
Evaluate held-out answers, tool-call validity, and deployed world behavior separately.
Deployment needs a format supported by the inference server; conversion can change
numerics, so reevaluate the deployed candidate. Select the model explicitly in world
configuration. Training and evaluation never automatically activate it.

See [evaluate_adapter.py](../src/backend/evaluate_adapter.py) for the evaluation
contract and [historical verification](archive/desktop-evidence.md) for dated
conversion recipes and measured results, including unsuccessful experiments.
