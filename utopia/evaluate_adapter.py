"""Offline base/adapter comparison using held-out, privately graded math tasks."""

import argparse
import hashlib
import json
import time
from pathlib import Path

from utopia.config import WorldConfig
from utopia.grader import GradeKind, Grader
from utopia.tasks import generate_tasks, public_task


def evaluation_tasks(adapter, training_data, model, seed, count):
    manifest = json.loads((adapter / "manifest.json").read_text(encoding="utf-8"))
    if manifest["model_version"] != model:
        raise ValueError("Adapter base model must match the evaluation model")
    digests = {hashlib.sha256(path.read_bytes()).hexdigest() for path in training_data}
    parent = manifest
    while parent:
        if parent["dataset_version"] not in digests:
            raise ValueError("Provide the dataset used by every adapter in this lineage")
        parent = parent.get("parent_adapter")
    rows = [
        json.loads(line)
        for path in training_data
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]
    if not rows or any(
        set(row) != {"agent_id", "model_version", "context_version", "messages"} for row in rows
    ):
        raise ValueError("Use the public training-data export")
    training_prompts = set()
    for row in rows:
        for message in row["messages"]:
            if message.get("role") != "tool" or message.get("name") != "task":
                continue
            try:
                task = json.loads(message.get("content", ""))
            except (ValueError, TypeError):
                continue
            if isinstance(task, dict) and "prompt" in task:
                training_prompts.add(task["prompt"].split(" Submit one plain mathematical")[0])
    bank = generate_tasks(
        WorldConfig(seed=seed, task_count=80, cyber_fraction=0, impossible_fraction=0)
    )
    tasks = [task for task in bank if task["prompt"] not in training_prompts][:count]
    if not 1 <= count <= 80 or len(tasks) != count:
        raise ValueError("Choose 1–80 tasks with enough distinct held-out prompts")
    return manifest, tasks


def evaluate(args):
    manifest, tasks = evaluation_tasks(
        args.adapter, args.training_data, args.model, args.seed, args.count
    )
    if args.output.exists():
        raise ValueError("Evaluation output already exists; preserve prior results")

    import torch
    from peft import PeftModel
    from transformers import AutoModelForCausalLM, AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(args.model, trust_remote_code=False)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    base = AutoModelForCausalLM.from_pretrained(
        args.model,
        trust_remote_code=False,
        torch_dtype=torch.float16 if device == "cuda" else torch.float32,
    ).to(device)
    model = PeftModel.from_pretrained(base, args.adapter).eval()
    grader = Grader()

    def sample(task):
        messages = [
            {
                "role": "system",
                "content": "Solve the problem. Return only the requested expression.",
            },
            {"role": "user", "content": public_task(task)["prompt"]},
        ]
        text = tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True, enable_thinking=False
        )
        inputs = tokenizer(text, return_tensors="pt").to(device)
        with torch.inference_mode():
            tokens = model.generate(
                **inputs,
                do_sample=False,
                max_new_tokens=args.max_output_tokens,
                pad_token_id=tokenizer.eos_token_id,
            )
        answer = tokenizer.decode(
            tokens[0, inputs["input_ids"].shape[1] :], skip_special_tokens=True
        ).strip()
        grade = grader.grade(task, answer)
        if grade.kind == GradeKind.INFRASTRUCTURE:
            raise RuntimeError(f"Grading failed for {task['id']}: {grade.detail}")
        return {
            "task_id": task["id"],
            "prompt": task["prompt"],
            "answer": answer,
            "grade": grade.kind,
        }

    started = time.perf_counter()
    with model.disable_adapter():
        baseline = [sample(task) for task in tasks]
    adapted = [sample(task) for task in tasks]
    report = {
        "schema_version": 1,
        "model_version": args.model,
        "adapter_version": manifest["adapter_version"],
        "training_dataset_version": manifest["dataset_version"],
        "seed": args.seed,
        "task_count": len(tasks),
        "scope": "held-out math answers; no tools, cyber tasks or full simulation",
        "decoding": {"do_sample": False, "max_output_tokens": args.max_output_tokens},
        "device": device,
        "evaluation_seconds": round(time.perf_counter() - started, 3),
        "base_correct": sum(row["grade"] == GradeKind.CORRECT for row in baseline),
        "adapter_correct": sum(row["grade"] == GradeKind.CORRECT for row in adapted),
        "base": baseline,
        "adapter": adapted,
        "automatic_activation": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adapter", type=Path, required=True)
    parser.add_argument("--training-data", type=Path, nargs="+", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=20261009)
    parser.add_argument("--count", type=int, default=12)
    parser.add_argument("--max-output-tokens", type=int, default=128)
    args = parser.parse_args()
    if not 1 <= args.max_output_tokens <= 1024:
        parser.error("max-output-tokens must be between 1 and 1024")
    report = evaluate(args)
    print(
        json.dumps({key: value for key, value in report.items() if key not in {"base", "adapter"}})
    )


if __name__ == "__main__":
    main()
