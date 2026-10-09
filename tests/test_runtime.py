import asyncio
import copy

import httpx
import pytest

from utopia.config import ModelSettings
from utopia.exports import public_trajectories
from utopia.providers import ContextOverflow, InferenceResult, SharedInference
from utopia.runtime import Runtime
from utopia.sandbox import DockerSandbox, SandboxUnavailable
from utopia.tools import TOOLS, ToolExecutor


class FakeSandbox:
    available = True

    def __init__(self):
        self.killed = []

    async def snapshot(self):
        return {"files": {}, "changes": [], "process_changes": []}

    async def rpc(self, request):
        return {}

    async def kill(self, identity, permanent=False):
        self.killed.append(identity)


class ScriptedInference:
    context_bound = staticmethod(SharedInference.context_bound)

    def __init__(self, responses):
        self.responses = responses
        self.semaphore = asyncio.Semaphore(1)
        self.waiting = 0
        self.active = 0
        self.peak = 0
        self.histories = []

    async def chat(self, settings, messages, tools):
        self.peak = max(self.peak, self.active)
        self.histories.append(copy.deepcopy(messages))
        await asyncio.sleep(0)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return InferenceResult(response, 10, 100, 100)


def runtime_with(engine, responses):
    provider = ScriptedInference(responses)
    runtime = Runtime(engine, provider)
    runtime.sandbox = FakeSandbox()
    runtime.tools = ToolExecutor(engine, runtime.sandbox)
    return runtime, provider


def call(operation, answer=None):
    args = {"operation": operation}
    if answer:
        args["answer"] = answer
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": f"call-{operation}",
                "type": "function",
                "function": {"name": "task", "arguments": args},
            }
        ],
    }


async def test_autonomous_tool_loop_requests_and_solves_real_engine_task(engine):
    identity = next(iter(engine.state["agents"]))
    answer = engine.state["tasks"][0]["answer"]
    runtime, provider = runtime_with(
        engine,
        [call("request"), call("submit", answer), {"role": "assistant", "content": "Finished"}],
    )
    await runtime.agent_loop(identity)
    assert engine.state["tasks"][0]["status"] == "COMPLETED"
    assert provider.peak == 1
    assert engine.agent(identity)["pending_tools"] == []
    assert len(engine.store.event_page(engine.state["id"], kind="tool.invoked")) == 2
    rows = public_trajectories(engine.store, engine.state)
    assert len(rows) == 1
    assert rows[0]["messages"][0]["role"] == "system"
    assert rows[0]["messages"][-1]["role"] == "tool"
    assert "verified" in rows[0]["messages"][-1]["content"].lower()
    assert all(
        set(message) <= {"role", "content", "tool_calls", "name", "tool_call_id"}
        for message in rows[0]["messages"]
    )


async def test_shared_inference_serializes_six_independent_histories(engine):
    runtime, provider = runtime_with(engine, [{"role": "assistant", "content": "Done"}] * 6)
    await asyncio.gather(*(runtime.agent_loop(identity) for identity in engine.state["agents"]))
    assert provider.peak == 1 and len(provider.histories) == 6
    assert len({history[0]["content"] for history in provider.histories}) == 6
    assert provider.active == 0 and provider.waiting == 0


async def test_inference_failure_backoff_does_not_kill_agent(engine):
    identity = next(iter(engine.state["agents"]))
    runtime, provider = runtime_with(engine, [httpx.ConnectError("Ollama disconnected")])
    await runtime.agent_loop(identity)
    assert engine.agent(identity)["state"] != "TERMINATED"
    assert engine.agent(identity)["retry_after_ms"] - engine.state["sim_ms"] == 2000
    assert provider.active == 0 and provider.semaphore._value == 1


async def test_cancelled_queue_wait_releases_waiting_flag(engine):
    identity = next(iter(engine.state["agents"]))
    runtime, provider = runtime_with(engine, [])
    await provider.semaphore.acquire()
    pending = asyncio.create_task(runtime.infer(identity, [], []))
    await asyncio.sleep(0)
    assert engine.agent(identity)["queued"]
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert not engine.agent(identity)["queued"] and provider.waiting == 0
    provider.semaphore.release()


async def test_context_overflow_is_visible_without_silent_truncation(engine):
    identity = next(iter(engine.state["agents"]))
    engine.conversation(identity, {"role": "user", "content": "x" * 10000})
    history = copy.deepcopy(engine.agent(identity)["history"])
    runtime, provider = runtime_with(engine, [])
    assert not await runtime.manage_context(identity)
    assert engine.agent(identity)["history"] == history
    assert engine.store.event_page(engine.state["id"], kind="context.capacity")
    assert provider.histories == []
    with pytest.raises(ContextOverflow):
        SharedInference.context_bound(history, TOOLS, ModelSettings())


async def test_docker_missing_never_executes_agent_code_on_host(monkeypatch):
    monkeypatch.setattr("utopia.sandbox.shutil.which", lambda _: None)
    from utopia.config import ToolSettings

    with pytest.raises(SandboxUnavailable, match="Host execution is disabled"):
        await DockerSandbox("test", ToolSettings()).execute("agent", "echo unsafe")


async def test_unknown_tools_and_malformed_calls_are_natural_errors(engine):
    identity = next(iter(engine.state["agents"]))
    executor = ToolExecutor(engine, FakeSandbox())
    assert "error" in await executor.execute(identity, "unknown", {})
    assert "error" in await executor.execute(identity, "task", None)
    assert engine.agent(identity)["state"] != "TERMINATED"


async def test_wall_queue_transitions_settle_subtick_intervals(engine, monkeypatch):
    now = [0]
    monkeypatch.setattr("utopia.runtime.time.perf_counter_ns", lambda: now[0])
    identity = next(iter(engine.state["agents"]))
    runtime, provider = runtime_with(engine, [{"role": "assistant", "content": "Done"}])
    await provider.semaphore.acquire()
    now[0] = 100_000_000
    pending = asyncio.create_task(runtime.infer(identity, [], []))
    await asyncio.sleep(0)
    assert engine.agent(identity)["total_charge_ms"] == 100
    now[0] = 350_000_000
    provider.semaphore.release()
    await pending
    assert engine.agent(identity)["queue_ms"] == 250
    assert engine.agent(identity)["total_charge_ms"] == 100


async def test_observer_commits_before_acknowledging_and_deduplicates(engine, monkeypatch):
    runtime, _ = runtime_with(engine, [])
    acknowledgements = []

    async def snapshot():
        return {
            "files": {},
            "epoch": "test-epoch",
            "through": 2,
            "changes": [{"path": "notes.txt", "observer_id": 2}],
            "process_changes": [
                {
                    "uid": 10001,
                    "event": "clone",
                    "child_pid": 2,
                    "observer_id": 1,
                    "record_type": "process-lifecycle",
                }
            ],
        }

    async def rpc(request):
        if request["operation"] == "acknowledge":
            acknowledgements.append(request["through"])
        return {}

    runtime.sandbox.snapshot = snapshot
    runtime.sandbox.rpc = rpc
    save = engine.store.save

    def fail(*args):
        raise RuntimeError("disk failure")

    monkeypatch.setattr(engine.store, "save", fail)
    with pytest.raises(RuntimeError, match="disk failure"):
        await runtime.observe()
    assert acknowledgements == [] and "observer_cursor" not in engine.state
    monkeypatch.setattr(engine.store, "save", save)
    await runtime.observe()
    await runtime.observe()
    records = engine.store.event_page(engine.state["id"])
    observed = [r for r in records if r["kind"] in {"process.observed", "workspace.modified"}]
    assert [r["payload"]["observer_id"] for r in observed] == [1, 2]
    assert acknowledgements == [2, 2]


async def test_runtime_error_pause_stops_processes_and_resolves_pending_tools(engine):
    runtime, _ = runtime_with(engine, [])
    identity = next(iter(engine.state["agents"]))
    engine.agent(identity)["pending_tools"] = call("request")["tool_calls"]
    engine.config.tick_ms = 1

    async def broken_observer():
        raise SandboxUnavailable("observer failed")

    runtime.observe = broken_observer
    await runtime.clock_loop()
    assert engine.state["status"] == "PAUSED"
    assert set(runtime.sandbox.killed) == set(engine.state["agents"])
    assert engine.agent(identity)["pending_tools"] == []
    assert engine.store.event_page(engine.state["id"], kind="runtime.error")


def test_context_bound_includes_provider_thinking_and_tool_calls():
    with pytest.raises(ContextOverflow):
        SharedInference.context_bound(
            [{"role": "assistant", "content": "", "thinking": "x" * 10000}], [], ModelSettings()
        )
