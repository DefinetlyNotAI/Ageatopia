"""Optional, explicitly invoked offline LoRA training. Never imported by the runtime."""

import argparse
import hashlib
import inspect
import json
import time
from pathlib import Path


def validate_optimizer_updates(steps, changed_parameters):
    if steps["applied"] < 1:
        raise RuntimeError(
            "Training made no optimizer updates: "
            f"{steps['applied']} applied, {steps['skipped']} skipped; "
            "no completed adapter was saved"
        )
    if changed_parameters < 1:
        raise RuntimeError(
            "Training left all adapter weights unchanged; no completed adapter was saved"
        )


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
        "--load-in-4bit", action="store_true", help="Train LoRA on a CUDA NF4 quantized base"
    )
    parser.add_argument(
        "--bf16", action="store_true", help="Use BF16 compute and training with four-bit CUDA"
    )
    parser.add_argument(
        "--fp16-frozen-embeddings",
        action="store_true",
        help="Keep frozen input embeddings in FP16 during four-bit CUDA training",
    )
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
    bf16 = getattr(args, "bf16", False)
    quantization = (
        f"nf4-double-{'bf16' if bf16 else 'fp16'}"
        if getattr(args, "load_in_4bit", False)
        else "none"
    )
    if bf16 and quantization == "none":
        raise ValueError("BF16 requires four-bit CUDA training")
    fp16_embeddings = getattr(args, "fp16_frozen_embeddings", False)
    if fp16_embeddings and quantization == "none":
        raise ValueError("FP16 frozen embeddings require four-bit CUDA training")
    if resume:
        parent = json.loads((resume / "manifest.json").read_text(encoding="utf-8"))
        if parent["model_version"] != args.model:
            raise ValueError("Adapter base model must match this training model")
        if args.iteration <= parent["training_iteration"]:
            raise ValueError("Training iteration must advance beyond the parent adapter")
        if parent.get("base_quantization", "none") != quantization:
            raise ValueError("Adapter base quantization must match this training run")
        parent_precision = parent.get("fp16_frozen_embeddings", False)
        if not isinstance(parent_precision, bool) or parent_precision != fp16_embeddings:
            raise ValueError("Frozen embedding precision must match this training run")

    import torch
    from datasets import Dataset
    from peft import LoraConfig, PeftModel, get_peft_model, prepare_model_for_kbit_training
    from transformers import (
        AutoModelForCausalLM,
        AutoTokenizer,
        BitsAndBytesConfig,
        DataCollatorForSeq2Seq,
        Trainer,
        TrainerCallback,
        TrainingArguments,
    )

    model_options = {}
    compute_dtype = torch.bfloat16 if bf16 else torch.float16
    if quantization != "none":
        if not torch.cuda.is_available():
            raise ValueError("Four-bit training requires CUDA and the training-4bit dependencies")
        if bf16 and not torch.cuda.is_bf16_supported():
            raise ValueError("BF16 training requires a supported CUDA GPU")
        model_options = {
            "device_map": {"": 0},
            "quantization_config": BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_quant_type="nf4",
                bnb_4bit_use_double_quant=True,
                bnb_4bit_compute_dtype=compute_dtype,
            ),
        }

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
        torch_dtype=compute_dtype if torch.cuda.is_available() else torch.float32,
        **model_options,
    )
    if quantization != "none":
        model = prepare_model_for_kbit_training(model)
        if fp16_embeddings:
            model.get_input_embeddings().to(dtype=torch.float16)
    if resume:
        model = PeftModel.from_pretrained(model, resume, is_trainable=True)
    else:
        model = get_peft_model(
            model,
            LoraConfig(r=8, lora_alpha=16, target_modules="all-linear", task_type="CAUSAL_LM"),
        )
    model.config.use_cache = False
    model.enable_input_require_grads()
    optimizer_steps = {"applied": 0, "skipped": 0}
    initial_weights = {
        name: parameter.detach().cpu().clone()
        for name, parameter in model.named_parameters()
        if parameter.requires_grad
    }

    class UpdateCounter(TrainerCallback):
        def on_pre_optimizer_step(self, args, state, control, **kwargs):
            gradients = [
                parameter.grad
                for parameter in kwargs["model"].parameters()
                if parameter.requires_grad and parameter.grad is not None
            ]
            if not gradients or any(not torch.isfinite(gradient).all() for gradient in gradients):
                raise RuntimeError(
                    "Missing or non-finite adapter gradients; optimizer update aborted"
                )

        def on_optimizer_step(self, args, state, control, **kwargs):
            skipped = kwargs["optimizer"].step_was_skipped
            optimizer_steps["skipped" if skipped else "applied"] += 1

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
            fp16=torch.cuda.is_available() and not bf16,
            bf16=bf16,
            seed=42,
        ),
        train_dataset=Dataset.from_list(samples),
        data_collator=DataCollatorForSeq2Seq(tokenizer=tokenizer, label_pad_token_id=-100),
        callbacks=[UpdateCounter()],
    )
    if restrict_logits:
        trainer.model_accepts_loss_kwargs = False
    started = time.perf_counter()
    if torch.cuda.is_available():
        torch.cuda.reset_peak_memory_stats()
    metrics = trainer.train().metrics
    changed_parameters = sum(
        not torch.equal(initial_weights[name], parameter.detach().cpu())
        for name, parameter in model.named_parameters()
        if name in initial_weights
    )
    validate_optimizer_updates(optimizer_steps, changed_parameters)
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
        "base_quantization": quantization,
        "fp16_frozen_embeddings": fp16_embeddings,
        "restricted_vocab_projection": restrict_logits,
        "supervised_tokens": sum(
            sum(label != -100 for label in sample["labels"]) for sample in samples
        ),
        "parent_adapter": parent,
        "training_seconds": round(time.perf_counter() - started, 3),
        "training_metrics": metrics,
        "optimizer_steps": optimizer_steps,
        "changed_adapter_parameters": changed_parameters,
        "device": torch.cuda.get_device_name(0) if torch.cuda.is_available() else "cpu",
        "peak_gpu_bytes": torch.cuda.max_memory_allocated() if torch.cuda.is_available() else None,
    }
    (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


if __name__ == "__main__":
    main()
