import asyncio
import json

import httpx
import pytest

from utopia.config import ModelSettings
from utopia.providers import InferenceResult, SharedInference


class DelayedLines(httpx.AsyncByteStream):
    def __init__(self, values):
        self.values = values

    async def __aiter__(self):
        for value in self.values:
            await asyncio.sleep(0.02)
            yield (json.dumps(value) + "\n").encode()

    async def aclose(self):
        pass


async def mock_provider(handler):
    provider = SharedInference()
    await provider.client.aclose()
    provider.client = httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False)
    return provider


async def test_ollama_stream_assembles_content_thinking_tool_calls_and_timings():
    chunks = [
        {
            "model": "qwen3.5:2b",
            "message": {"role": "assistant", "thinking": "Check. "},
            "done": False,
        },
        {"model": "qwen3.5:2b", "message": {"content": "Done."}, "done": False},
        {
            "model": "qwen3.5:2b",
            "message": {
                "tool_calls": [
                    {
                        "function": {
                            "index": 0,
                            "name": "task",
                            "arguments": {"operation": "request"},
                        }
                    }
                ]
            },
            "done": False,
        },
        {
            "model": "qwen3.5:2b",
            "message": {
                "tool_calls": [
                    {"function": {"index": 0, "arguments": {"answer": "42"}}},
                    {
                        "function": {
                            "index": 1,
                            "name": "workspace",
                            "arguments": {"operation": "list"},
                        }
                    },
                ]
            },
            "done": False,
        },
        {
            "model": "qwen3.5:2b",
            "message": {"role": "assistant", "content": ""},
            "done": True,
            "load_duration": 2_000_000,
            "prompt_eval_count": 17,
            "prompt_eval_duration": 3_000_000,
            "eval_count": 5,
            "eval_duration": 4_000_000,
        },
    ]

    async def handler(request):
        payload = json.loads(request.content)
        assert request.url.path == "/api/chat"
        assert payload["stream"] is True
        assert payload["keep_alive"] == -1
        assert payload["options"] == {
            "num_ctx": 2048,
            "num_batch": 128,
            "temperature": 0.25,
            "seed": 7,
            "num_predict": 64,
            "num_gpu": 0,
        }
        return httpx.Response(200, stream=DelayedLines(chunks))

    provider = await mock_provider(handler)
    settings = ModelSettings(
        context_tokens=2048,
        prompt_batch_tokens=128,
        temperature=0.25,
        seed=7,
        max_output_tokens=64,
        keep_alive=-1,
        gpu_layers=0,
    )
    try:
        result = await provider.chat(settings, [{"role": "user", "content": "Start"}], [])
    finally:
        await provider.close()

    assert result.message["content"] == "Done."
    assert result.message["thinking"] == "Check. "
    assert [call["function"]["name"] for call in result.message["tool_calls"]] == [
        "task",
        "workspace",
    ]
    assert result.message["tool_calls"][0]["function"]["arguments"] == {
        "operation": "request",
        "answer": "42",
    }
    assert all(
        call["id"].startswith("call-") and call["type"] == "function"
        for call in result.message["tool_calls"]
    )
    assert result.model_version == "qwen3.5:2b"
    assert (result.tokens, result.prompt_tokens) == (5, 17)
    assert 0 <= result.first_token_ms < result.latency_ms
    assert result.latency_ms - result.first_token_ms >= 10
    assert (result.load_ms, result.prompt_eval_ms, result.eval_ms) == (2, 3, 4)


async def test_ollama_stream_http_error_is_propagated():
    async def handler(request):
        return httpx.Response(503, json={"error": "server overloaded"})

    provider = await mock_provider(handler)
    try:
        with pytest.raises(httpx.HTTPStatusError, match="503"):
            await provider.chat(ModelSettings(), [{"role": "user", "content": "hello"}], [])
    finally:
        await provider.close()


async def test_warm_uses_empty_chat_and_returns_server_timings():
    async def handler(request):
        payload = json.loads(request.content)
        assert request.url.path == "/api/chat"
        assert payload["messages"] == []
        assert payload["stream"] is False
        assert payload["keep_alive"] == "3m"
        assert payload["options"]["num_ctx"] == 4096
        assert payload["options"]["num_batch"] == 256
        assert payload["options"]["num_gpu"] == 12
        return httpx.Response(
            200,
            json={
                "model": "qwen3.5:2b",
                "message": {"role": "assistant", "content": ""},
                "done": True,
                "load_duration": 12_000_000,
                "prompt_eval_duration": 4_000_000,
                "eval_duration": 0,
            },
        )

    provider = await mock_provider(handler)
    settings = ModelSettings(context_tokens=4096, keep_alive="3m", gpu_layers=12)
    try:
        timings = await provider.warm(settings)
    finally:
        await provider.close()

    assert timings["latency_ms"] >= 0
    assert (timings["load_ms"], timings["prompt_eval_ms"], timings["eval_ms"]) == (12, 4, 0)


async def test_ollama_embeddings_batch_without_truncation():
    texts = ["first", "second"]

    async def handler(request):
        payload = json.loads(request.content)
        assert request.url.path == "/api/embed"
        assert payload["model"] == "embeddinggemma:latest"
        assert payload["input"] == texts
        assert payload["truncate"] is False
        assert payload["keep_alive"] == -1
        assert payload["options"] == {"num_ctx": 4096, "num_batch": 256}
        return httpx.Response(200, json={"embeddings": [[0.1, 0.2], [0.3, 0.4]]})

    provider = await mock_provider(handler)
    try:
        vectors = await provider.embeddings(
            ModelSettings(model="embeddinggemma:latest", context_tokens=4096), texts
        )
    finally:
        await provider.close()

    assert vectors == [[0.1, 0.2], [0.3, 0.4]]


def test_inference_result_timing_fields_remain_optional_for_existing_fake_providers():
    result = InferenceResult({"role": "assistant", "content": "Ready"}, 1, 2, 3)

    assert result.first_token_ms is None
    assert result.load_ms is None
    assert result.prompt_eval_ms is None
    assert result.eval_ms is None


async def test_embedding_requests_share_the_inference_concurrency_limit():
    active = peak = 0

    async def handler(request):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.02)
        active -= 1
        return httpx.Response(200, json={"embeddings": [[0.1, 0.2]]})

    provider = await mock_provider(handler)
    try:
        settings = ModelSettings(model="embeddinggemma:latest")
        await asyncio.gather(
            provider.embeddings(settings, ["first"]), provider.embeddings(settings, ["second"])
        )
        assert peak == 1
        assert provider.active == provider.waiting == 0
    finally:
        await provider.close()
