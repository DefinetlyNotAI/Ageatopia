"""Optional, explicitly invoked offline LoRA training. Never imported by the runtime."""

import argparse
import hashlib
import json
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Offline LoRA from exported public trajectories")
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--model", required=True, help="Hugging Face model ID or local directory")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--epochs", type=float, default=1)
    parser.add_argument("--iteration", type=int, default=1)
    parser.add_argument("--max-length", type=int, default=1024)
    args = parser.parse_args()
    if args.output.exists() and any(args.output.iterdir()):
        raise SystemExit("Output directory must be empty; existing adapters are preserved")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    event_path = args.output.parent / f"{args.output.name}.training-events.jsonl"

    def record(kind, payload):
        with event_path.open("a", encoding="utf-8") as stream:
            stream.write(
                json.dumps(
                    {
                        "schema_version": 1,
                        "wall_ms": int(time.time() * 1000),
                        "kind": kind,
                        "payload": payload,
                    }
                )
                + "\n"
            )

    record(
        "training.started",
        {
            "model": args.model,
            "iteration": args.iteration,
            "dataset": str(args.dataset),
            "output": str(args.output),
        },
    )
    try:
        manifest = train(args)
    except BaseException as error:
        record("training.failed", {"error": str(error)})
        raise
    record("training.completed", manifest)


def train(args):
    import torch
    from datasets import Dataset
    from peft import LoraConfig, get_peft_model
    from transformers import (
        AutoModelForCausalLM,
        AutoTokenizer,
        DataCollatorForLanguageModeling,
        Trainer,
        TrainingArguments,
    )

    rows = [
        json.loads(line) for line in args.dataset.read_text(encoding="utf-8").splitlines() if line
    ]
    if not rows or any(
        set(row) != {"agent_id", "model_version", "context_version", "messages"} for row in rows
    ):
        raise SystemExit("Use a public training-data export, never a private research export")
    tokenizer = AutoTokenizer.from_pretrained(args.model, trust_remote_code=False)
    tokenizer.pad_token = tokenizer.pad_token or tokenizer.eos_token
    samples = []
    for row in rows:
        text = tokenizer.apply_chat_template(row["messages"], tokenize=False)
        encoded = tokenizer(text, truncation=False)
        if len(encoded["input_ids"]) > args.max_length:
            raise SystemExit("Trajectory exceeds max-length; no silent training truncation")
        samples.append(encoded)
    model = AutoModelForCausalLM.from_pretrained(
        args.model,
        trust_remote_code=False,
        torch_dtype=torch.float16 if torch.cuda.is_available() else torch.float32,
    )
    model = get_peft_model(
        model, LoraConfig(r=8, lora_alpha=16, target_modules="all-linear", task_type="CAUSAL_LM")
    )
    model.config.use_cache = False
    model.enable_input_require_grads()
    trainer = Trainer(
        model=model,
        args=TrainingArguments(
            output_dir=str(args.output),
            num_train_epochs=args.epochs,
            per_device_train_batch_size=1,
            gradient_accumulation_steps=8,
            gradient_checkpointing=True,
            report_to="none",
            fp16=torch.cuda.is_available(),
            seed=42,
        ),
        train_dataset=Dataset.from_list(samples),
        data_collator=DataCollatorForLanguageModeling(tokenizer=tokenizer, mlm=False),
    )
    trainer.train()
    model.save_pretrained(args.output)
    tokenizer.save_pretrained(args.output)
    manifest = {
        "model_version": args.model,
        "adapter_version": "lora-1",
        "dataset_version": hashlib.sha256(args.dataset.read_bytes()).hexdigest(),
        "training_iteration": args.iteration,
        "agent_identities": sorted({r["agent_id"] for r in rows}),
        "context_versions": sorted({r["context_version"] for r in rows}),
        "continuous_training": False,
    }
    (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


if __name__ == "__main__":
    main()
