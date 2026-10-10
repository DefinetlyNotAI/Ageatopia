import asyncio
import json
import time
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

from utopia.resources import MemoryGuard, MemoryPressure


class ContextOverflow(RuntimeError):
    pass


@dataclass
class InferenceResult:
    message: dict
    tokens: int
    prompt_tokens: int
    latency_ms: int
    model_version: str | None = None
    first_token_ms: int | None = None
    load_ms: int | None = None
    prompt_eval_ms: int | None = None
    eval_ms: int | None = None


class SharedInference:
    def __init__(self, concurrency=1):
        self.semaphore = asyncio.Semaphore(concurrency)
        self.waiting = 0
        self.active = 0
        self.client = httpx.AsyncClient(timeout=180, trust_env=False)
        self.model_sizes = {}
        self.memory_needed_mb = 0

    async def check_model_memory(self, settings):
        try:
            await self._check_model_memory(settings)
            self.memory_needed_mb = 0
        except MemoryPressure as error:
            self.memory_needed_mb = error.allocation_mb
            raise

    async def _check_model_memory(self, settings):
        guard = MemoryGuard()
        guard.check()
        key = (settings.base_url.rstrip("/"), settings.model)
        if (
            settings.provider != "ollama"
            or urlparse(settings.base_url).hostname not in {"localhost", "127.0.0.1", "::1"}
            or key not in self.model_sizes
        ):
            return
        response = await self.client.get(f"{key[0]}/api/ps")
        response.raise_for_status()
        loaded = next(
            (
                model
                for model in response.json().get("models", [])
                if model.get("name") == settings.model
            ),
            None,
        )
        if loaded and loaded.get("context_length", 0) >= settings.context_tokens:
            guard.check(256)
            return
        # Ollama chooses GPU offloading and context allocation using its own scheduler.
        # Only explicit CPU execution warrants budgeting the whole model in host RAM.
        if settings.gpu_layers == 0:
            guard.check(self.model_sizes[key] + max(512, settings.context_tokens // 8))
        else:
            guard.check(256)

    @staticmethod
    def _ollama_options(settings):
        options = {
            "num_ctx": settings.context_tokens,
            "num_batch": settings.prompt_batch_tokens,
            "temperature": settings.temperature,
            "seed": settings.seed,
            "num_predict": settings.max_output_tokens,
        }
        if settings.gpu_layers is not None:
            options["num_gpu"] = settings.gpu_layers
        return options

    @classmethod
    def _ollama_chat_payload(cls, settings, messages, tools, stream):
        return {
            "model": settings.model,
            "messages": messages,
            "tools": tools,
            "stream": stream,
            "think": settings.thinking,
            "keep_alive": settings.keep_alive,
            "options": cls._ollama_options(settings),
        }

    @staticmethod
    def _duration_ms(data, name):
        value = data.get(name)
        return int(value / 1_000_000) if value is not None else None

    @staticmethod
    def _merge_ollama_tool_calls(message, partial_message):
        partial_calls = partial_message.get("tool_calls", [])
        if not partial_calls:
            return
        calls = message.setdefault("tool_calls", [])
        call_indexes = message.setdefault("_tool_call_indexes", {})
        for position, partial in enumerate(partial_calls):
            function = partial.get("function", {})
            identity = partial.get("id")
            if identity is None:
                identity = function.get("index", partial.get("index", position))
                key = f"index:{identity}"
            else:
                key = f"id:{identity}"
            if key not in call_indexes:
                call_indexes[key] = len(calls)
                calls.append({"function": {"name": "", "arguments": {}}})
            call = calls[call_indexes[key]]
            if partial.get("id"):
                call["id"] = partial["id"]
            if function.get("name"):
                call["function"]["name"] = function["name"]
            arguments = function.get("arguments")
            if isinstance(arguments, dict):
                call["function"]["arguments"].update(arguments)
            elif isinstance(arguments, str):
                call["_arguments_text"] = call.get("_arguments_text", "") + arguments
            elif arguments is not None:
                raise ValueError("Ollama tool call arguments must be an object or JSON text")

    @staticmethod
    def _finish_ollama_tool_calls(message):
        indexes = message.pop("_tool_call_indexes", None)
        if indexes is None:
            return
        for index, call in enumerate(message.get("tool_calls", [])):
            arguments_text = call.pop("_arguments_text", "")
            if arguments_text:
                arguments = json.loads(arguments_text)
                if not isinstance(arguments, dict):
                    raise ValueError("Ollama tool call arguments must decode to an object")
                call["function"]["arguments"].update(arguments)
            call.setdefault("id", f"call-{time.time_ns()}-{index}")
            call.setdefault("type", "function")

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
            self.model_sizes[(settings.base_url.rstrip("/"), settings.model)] = (
                model.get("size", 0) + 1048575
            ) // 1048576
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
        await self.check_model_memory(settings)
        self.context_bound(messages, tools, settings)
        started = time.perf_counter()
        base = settings.base_url.rstrip("/")
        if settings.provider == "ollama":
            first_token_ms = None
            result_message = {"role": "assistant", "content": ""}
            content_parts = []
            thinking_parts = []
            final = None
            async with self.client.stream(
                "POST",
                f"{base}/api/chat",
                json=self._ollama_chat_payload(settings, messages, tools, stream=True),
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    data = json.loads(line)
                    if "error" in data:
                        raise RuntimeError(str(data["error"]))
                    chunk = data.get("message", {})
                    if not isinstance(chunk, dict):
                        raise ValueError("Ollama chat stream message must be an object")
                    content = chunk.get("content", "")
                    thinking = chunk.get("thinking", "")
                    if (content or thinking or chunk.get("tool_calls")) and first_token_ms is None:
                        first_token_ms = int((time.perf_counter() - started) * 1000)
                    if isinstance(content, str):
                        content_parts.append(content)
                    if isinstance(thinking, str):
                        thinking_parts.append(thinking)
                    if chunk.get("role"):
                        result_message["role"] = chunk["role"]
                    self._merge_ollama_tool_calls(result_message, chunk)
                    if data.get("model"):
                        result_message["_model"] = data["model"]
                    if data.get("done"):
                        final = data
            if final is None:
                raise ValueError("Ollama chat stream ended before its final metrics chunk")
            result_message["content"] = "".join(content_parts)
            if any(thinking_parts):
                result_message["thinking"] = "".join(thinking_parts)
            self._finish_ollama_tool_calls(result_message)
            model_version = result_message.pop("_model", final.get("model"))
            return InferenceResult(
                result_message,
                final.get("eval_count", 0),
                final.get("prompt_eval_count", 0),
                int((time.perf_counter() - started) * 1000),
                model_version,
                first_token_ms,
                self._duration_ms(final, "load_duration"),
                self._duration_ms(final, "prompt_eval_duration"),
                self._duration_ms(final, "eval_duration"),
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

    async def warm(self, settings):
        await self.check_model_memory(settings)
        if settings.provider != "ollama":
            raise ValueError("Model warming is supported only by Ollama")
        started = time.perf_counter()
        response = await self.client.post(
            f"{settings.base_url.rstrip('/')}/api/chat",
            json=self._ollama_chat_payload(settings, [], [], stream=False),
        )
        response.raise_for_status()
        data = response.json()
        if "error" in data:
            raise RuntimeError(data["error"])
        return {
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "load_ms": self._duration_ms(data, "load_duration"),
            "prompt_eval_ms": self._duration_ms(data, "prompt_eval_duration"),
            "eval_ms": self._duration_ms(data, "eval_duration"),
        }

    async def embeddings(self, settings, texts):
        self.waiting += 1
        try:
            await self.semaphore.acquire()
        finally:
            self.waiting -= 1
        self.active += 1
        try:
            while True:
                try:
                    await self.check_model_memory(settings)
                    break
                except MemoryPressure:
                    await asyncio.sleep(0.5)
            return await self._embeddings(settings, texts)
        finally:
            self.active -= 1
            self.memory_needed_mb = 0
            self.semaphore.release()

    async def _embeddings(self, settings, texts):
        if settings.provider != "ollama":
            raise ValueError("Embeddings are supported only by Ollama")
        options = {
            "num_ctx": settings.context_tokens,
            "num_batch": settings.prompt_batch_tokens,
        }
        if settings.gpu_layers is not None:
            options["num_gpu"] = settings.gpu_layers
        response = await self.client.post(
            f"{settings.base_url.rstrip('/')}/api/embed",
            json={
                "model": settings.model,
                "input": texts,
                "truncate": False,
                "keep_alive": settings.keep_alive,
                "options": options,
            },
        )
        response.raise_for_status()
        data = response.json()
        if "error" in data:
            raise RuntimeError(data["error"])
        return data["embeddings"]

    async def close(self):
        await self.client.aclose()
