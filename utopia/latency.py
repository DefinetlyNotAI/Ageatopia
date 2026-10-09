import argparse
import asyncio
import json
import statistics
from pathlib import Path

from utopia.config import ModelSettings, WorldConfig
from utopia.providers import SharedInference
from utopia.tools import TOOLS


def parser():
    result = argparse.ArgumentParser(description="Measure Ollama chat latency")
    result.add_argument("--model", default=ModelSettings().model)
    result.add_argument("--base-url", default=ModelSettings().base_url)
    result.add_argument("--context-tokens", type=int, default=ModelSettings().context_tokens)
    result.add_argument(
        "--prompt-batch-tokens", type=int, default=ModelSettings().prompt_batch_tokens
    )
    result.add_argument("--repeat", type=int, default=5)
    result.add_argument(
        "--cold",
        action="store_true",
        help="Unload the selected model before the warmup request",
    )
    result.add_argument("--output", type=Path, help="Write the JSON measurements to this path")
    return result


def summarize(measurements, field):
    values = [measurement[field] for measurement in measurements if measurement[field] is not None]
    return round(statistics.median(values), 2) if values else None


async def run_benchmark(args):
    if args.repeat < 1:
        raise ValueError("--repeat must be at least 1")
    settings = ModelSettings(
        model=args.model,
        base_url=args.base_url,
        context_tokens=args.context_tokens,
        prompt_batch_tokens=args.prompt_batch_tokens,
        temperature=0,
    )
    messages = [
        {"role": "system", "content": WorldConfig().base_context},
        {"role": "user", "content": "Begin your work. Available tools are listed."},
    ]
    provider = SharedInference()
    try:
        if args.cold:
            response = await provider.client.post(
                f"{settings.base_url.rstrip('/')}/api/chat",
                json={
                    "model": settings.model,
                    "messages": [],
                    "stream": False,
                    "keep_alive": 0,
                },
            )
            response.raise_for_status()
        warmup = await provider.warm(settings)
        measurements = []
        for request_number in range(1, args.repeat + 1):
            result = await provider.chat(
                settings,
                messages,
                TOOLS,
            )
            measurements.append(
                {
                    "request": request_number,
                    "first_token_ms": result.first_token_ms,
                    "load_ms": result.load_ms,
                    "prompt_eval_ms": result.prompt_eval_ms,
                    "eval_ms": result.eval_ms,
                    "total_ms": result.latency_ms,
                    "prompt_tokens": result.prompt_tokens,
                    "output_tokens": result.tokens,
                    "tool_calls": len(result.message.get("tool_calls", [])),
                }
            )
        return {
            "model": settings.model,
            "base_url": settings.base_url,
            "context_tokens": settings.context_tokens,
            "prompt_batch_tokens": settings.prompt_batch_tokens,
            "cold_unload_requested": args.cold,
            "warmup": warmup,
            "measurements": measurements,
            "median": {
                field: summarize(measurements, field)
                for field in ("first_token_ms", "load_ms", "prompt_eval_ms", "eval_ms", "total_ms")
            },
        }
    finally:
        await provider.close()


def main(argv=None):
    args = parser().parse_args(argv)
    result = asyncio.run(run_benchmark(args))
    output = json.dumps(result, indent=2)
    if args.output:
        args.output.write_text(output + "\n", encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
