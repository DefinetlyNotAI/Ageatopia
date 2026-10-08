import asyncio
import json
import time
from dataclasses import dataclass

import httpx


class ContextOverflow(RuntimeError):
    pass


@dataclass
class InferenceResult:
    message: dict
    tokens: int
    prompt_tokens: int
    latency_ms: int
    model_version: str | None = None


class SharedInference:
    def __init__(self, concurrency=1):
        self.semaphore = asyncio.Semaphore(concurrency)
        self.waiting = 0
        self.active = 0
        self.client = httpx.AsyncClient(timeout=180)

    @staticmethod
    def context_bound(messages, tools, settings):
        # UTF-8 bytes are a conservative upper bound for byte-based Qwen tokenization.
        # Reserve output and template overhead; never silently trim the supplied history.
        text = json.dumps(messages, ensure_ascii=False, separators=(",", ":"))
        used = len(text.encode()) + len(json.dumps(tools, separators=(",", ":")).encode())
        limit = settings.context_tokens - settings.max_output_tokens - 256
        if used > limit:
            raise ContextOverflow(f"Conservative context bound {used} exceeds input budget {limit}")
        return used

    async def manifest(self, settings):
        if settings.provider == "ollama":
            response = await self.client.get(f"{settings.base_url.rstrip('/')}/api/tags")
            response.raise_for_status()
            model = next(
                (
                    m
                    for m in response.json()["models"]
                    if m["name"].lower() == settings.model.lower()
                ),
                None,
            )
            if model is None:
                raise ValueError(f"Model {settings.model} is not installed in Ollama")
            return {"model": settings.model, "hash": model["digest"], "provider": "ollama"}
        response = await self.client.get(f"{settings.base_url.rstrip('/')}/v1/models")
        response.raise_for_status()
        if settings.model not in [m["id"] for m in response.json()["data"]]:
            raise ValueError("Configured model is unavailable")
        return {
            "model": settings.model,
            "hash": None,
            "provider": "openai-compatible",
            "hash_unavailable": True,
        }

    async def chat(self, settings, messages, tools):
        self.context_bound(messages, tools, settings)
        started = time.perf_counter()
        base = settings.base_url.rstrip("/")
        if settings.provider == "ollama":
            response = await self.client.post(
                f"{base}/api/chat",
                json={
                    "model": settings.model,
                    "messages": messages,
                    "tools": tools,
                    "stream": False,
                    "think": settings.thinking,
                    "keep_alive": "10m",
                    "options": {
                        "num_ctx": settings.context_tokens,
                        "temperature": settings.temperature,
                        "seed": settings.seed,
                        "num_predict": settings.max_output_tokens,
                    },
                },
            )
            response.raise_for_status()
            data = response.json()
            message = data["message"]
            for index, call in enumerate(message.get("tool_calls", [])):
                call.setdefault("id", f"call-{time.time_ns()}-{index}")
                call.setdefault("type", "function")
            return InferenceResult(
                message,
                data.get("eval_count", 0),
                data.get("prompt_eval_count", 0),
                int((time.perf_counter() - started) * 1000),
                data.get("model"),
            )
        response = await self.client.post(
            f"{base}/v1/chat/completions",
            json={
                "model": settings.model,
                "messages": messages,
                "tools": tools,
                "temperature": settings.temperature,
                "seed": settings.seed,
                "max_tokens": settings.max_output_tokens,
            },
        )
        response.raise_for_status()
        data = response.json()
        message = data["choices"][0]["message"]
        for call in message.get("tool_calls", []):
            arguments = call["function"]["arguments"]
            if isinstance(arguments, str):
                call["function"]["arguments"] = json.loads(arguments)
        usage = data.get("usage", {})
        return InferenceResult(
            message,
            usage.get("completion_tokens", 0),
            usage.get("prompt_tokens", 0),
            int((time.perf_counter() - started) * 1000),
            data.get("model"),
        )

    async def close(self):
        await self.client.aclose()
