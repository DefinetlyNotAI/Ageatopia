"""Optional, explicitly invoked offline LoRA training. Never imported by the runtime."""

import argparse
import hashlib
import inspect
import json
import time
from pathlib import Path


def verified_submission_index(messages):
    if not messages or messages[-1].get("role") != "tool":
        raise ValueError("Training trajectory must end with a verified task result")
    result = messages[-1]
    outcome = json.loads(result.get("content", "{}"))
    if (
        result.get("name") != "task"
        or not isinstance(outcome, dict)
        or outcome.get("status") != "Verified"
    ):
        raise ValueError("Training trajectory must end with a verified task result")
    index = next(
        (i for i in range(len(messages) - 2, -1, -1) if messages[i].get("role") == "assistant"),
        None,
    )
    if index is None:
        raise ValueError("Verified result has no assistant submission")
    calls = messages[index].get("tool_calls", [])
    if (
        len(calls) != 1
        or not result.get("tool_call_id")
        or calls[0].get("id") != result["tool_call_id"]
    ):
        raise ValueError("Training target must contain exactly one verified submission")
    call = calls[0].get("function", {})
    arguments = call.get("arguments", {})
    if isinstance(arguments, str):
        arguments = json.loads(arguments)
    if (
        call.get("name") != "task"
        or not isinstance(arguments, dict)
        or set(arguments) != {"operation", "answer"}
        or arguments["operation"] != "submit"
        or not isinstance(arguments["answer"], str)
    ):
        raise ValueError("Training target must be a task submit call with an answer")
    return index


def verified_sample(tokenizer, messages, max_length):
    from utopia.tools import TOOLS

    index = verified_submission_index(messages)
    prefix = tokenizer.apply_chat_template(
        messages[:index],
        tokenize=False,
        add_generation_prompt=True,
        enable_thinking=False,
        tools=TOOLS,
    )
    text = tokenizer.apply_chat_template(
        messages[: index + 1],
        tokenize=False,
        enable_thinking=False,
        tools=TOOLS,
    )
    without_calls = messages[:index] + [
        {key: value for key, value in messages[index].items() if key != "tool_calls"}
    ]
    if text == tokenizer.apply_chat_template(
        without_calls, tokenize=False, enable_thinking=False, tools=TOOLS
    ):
        raise ValueError("Chat template omitted the verified tool call")
    encoded = tokenizer(text, add_special_tokens=False, truncation=False)
    prefix_ids = tokenizer(prefix, add_special_tokens=False, truncation=False)["input_ids"]
    if encoded["input_ids"][: len(prefix_ids)] != prefix_ids:
        raise ValueError("Chat template cannot safely separate context from the assistant target")
    if len(encoded["input_ids"]) > max_length:
        raise ValueError("Trajectory exceeds max-length; no silent training truncation")
    if len(prefix_ids) >= len(encoded["input_ids"]):
        raise ValueError("Chat template omitted the verified assistant target")
    encoded["labels"] = [-100] * len(prefix_ids) + encoded["input_ids"][len(prefix_ids) :]
    return encoded


def main():
    parser = argparse.ArgumentParser(description="Offline LoRA from exported public trajectories")
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--model", required=True, help="Hugging Face model ID or local directory")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--epochs", type=float, default=1)
    parser.add_argument("--iteration", type=int, default=1)
    parser.add_argument("--max-length", type=int, default=1024)
    parser.add_argument(
        "--resume-adapter", type=Path, help="Continue a prior adapter in a new output directory"
    )
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
    parent = None
    resume = getattr(args, "resume_adapter", None)
    if resume:
        parent = json.loads((resume / "manifest.json").read_text(encoding="utf-8"))
        if parent["model_version"] != args.model:
            raise ValueError("Adapter base model must match this training model")
        if args.iteration <= parent["training_iteration"]:
            raise ValueError("Training iteration must advance beyond the parent adapter")

    import torch
    from datasets import Dataset
    from peft import LoraConfig, PeftModel, get_peft_model
    from transformers import (
        AutoModelForCausalLM,
        AutoTokenizer,
        DataCollatorForSeq2Seq,
        Trainer,
        TrainingArguments,
    )

    rows = [
        json.loads(line) for line in args.dataset.read_text(encoding="utf-8").splitlines() if line
    ]
    if not rows or any(
        set(row) - {"observed_task_prompts"}
        != {"agent_id", "model_version", "context_version", "messages"}
        for row in rows
    ):
        raise SystemExit("Use a public training-data export, never a private research export")
    tokenizer = AutoTokenizer.from_pretrained(args.model, trust_remote_code=False)
    tokenizer.pad_token = tokenizer.pad_token or tokenizer.eos_token
    samples = []
    for row in rows:
        samples.append(verified_sample(tokenizer, row["messages"], args.max_length))
    model = AutoModelForCausalLM.from_pretrained(
        args.model,
        trust_remote_code=False,
        torch_dtype=torch.float16 if torch.cuda.is_available() else torch.float32,
    )
    if resume:
        model = PeftModel.from_pretrained(model, resume, is_trainable=True)
    else:
        model = get_peft_model(
            model,
            LoraConfig(r=8, lora_alpha=16, target_modules="all-linear", task_type="CAUSAL_LM"),
        )
    model.config.use_cache = False
    model.enable_input_require_grads()
    restrict_logits = (
        "logits_to_keep" in inspect.signature(model.get_base_model().forward).parameters
    )

    class SubmissionTrainer(Trainer):
        def compute_loss(self, model, inputs, return_outputs=False, num_items_in_batch=None):
            labels = inputs.pop("labels")
            positions = torch.nonzero((labels[:, 1:] != -100).any(dim=0), as_tuple=True)[0]
            outputs = model(**inputs, logits_to_keep=positions)
            targets = labels[:, positions + 1]
            loss = torch.nn.functional.cross_entropy(
                outputs.logits.float().reshape(-1, outputs.logits.shape[-1]),
                targets.reshape(-1),
                ignore_index=-100,
            )
            return (loss, outputs) if return_outputs else loss

    trainer = (SubmissionTrainer if restrict_logits else Trainer)(
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
        data_collator=DataCollatorForSeq2Seq(tokenizer=tokenizer, label_pad_token_id=-100),
    )
    if restrict_logits:
        trainer.model_accepts_loss_kwargs = False
    started = time.perf_counter()
    if torch.cuda.is_available():
        torch.cuda.reset_peak_memory_stats()
    metrics = trainer.train().metrics
    model.save_pretrained(args.output)
    tokenizer.save_pretrained(args.output)
    manifest = {
        "model_version": args.model,
        "adapter_version": f"lora-{args.iteration}",
        "dataset_version": hashlib.sha256(args.dataset.read_bytes()).hexdigest(),
        "training_iteration": args.iteration,
        "agent_identities": sorted({r["agent_id"] for r in rows}),
        "context_versions": sorted({r["context_version"] for r in rows}),
        "continuous_training": False,
        "training_objective": "verified-assistant-submission-only",
        "restricted_vocab_projection": restrict_logits,
        "supervised_tokens": sum(
            sum(label != -100 for label in sample["labels"]) for sample in samples
        ),
        "parent_adapter": parent,
        "training_seconds": round(time.perf_counter() - started, 3),
        "training_metrics": metrics,
        "device": torch.cuda.get_device_name(0) if torch.cuda.is_available() else "cpu",
        "peak_gpu_bytes": torch.cuda.max_memory_allocated() if torch.cuda.is_available() else None,
    }
    (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


if __name__ == "__main__":
    main()
