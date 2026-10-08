import argparse
import asyncio
import json
from pathlib import Path

from utopia.config import ModelSettings, WorldConfig
from utopia.engine import Engine
from utopia.exports import comparison, export_run
from utopia.providers import SharedInference
from utopia.runtime import Runtime
from utopia.store import Store


async def run(args):
    directory = args.output.resolve()
    store = Store.local(directory)
    inference = SharedInference()
    results = []
    try:
        for model in args.models:
            engine = Engine.create(
                store,
                WorldConfig(
                    name=f"Benchmark {model}",
                    seed=args.seed,
                    model=ModelSettings(model=model),
                    population=6,
                    task_count=40,
                ),
            )
            runtime = Runtime(engine, inference)
            try:
                await runtime.start("start")
                deadline = asyncio.get_running_loop().time() + args.seconds
                while asyncio.get_running_loop().time() < deadline:
                    await asyncio.sleep(1)
                    if engine.state["status"] != "RUNNING":
                        raise RuntimeError("Benchmark paused due to an infrastructure failure")
                await runtime.stop("terminate")
                payload, _ = export_run(store, engine.state, "json")
                (directory / f"{engine.state['id']}.json").write_bytes(payload)
                results.append(engine.state)
            finally:
                await runtime.close()
        report = comparison(results)
        (directory / "comparison.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2))
    finally:
        await inference.close()
        store.engine.dispose()


def main():
    parser = argparse.ArgumentParser(description="Real Docker/Ollama population benchmark")
    parser.add_argument("--models", nargs="+", default=["qwen3.5:2b", "qwen3.5:0.8b"])
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--seconds", type=int, default=1200)
    parser.add_argument("--output", type=Path, default=Path(".data/benchmarks"))
    args = parser.parse_args()
    if args.seconds <= 0:
        parser.error("seconds must be positive")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
