import asyncio
import copy
import json

import httpx
import pytest

from backend.config import ModelSettings
from backend.engine import Engine
from backend.exports import public_trajectories
from backend.runtime import Runtime
from backend.tools import TOOLS, ToolExecutor
from hooks.providers import ContextOverflow, InferenceResult, SharedInference
from hooks.sandbox import DockerSandbox, SandboxUnavailable


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
    if operation == "request":
        args["task_id"] = "task-0001"
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


async def test_request_without_a_choice_lists_tasks_instead_of_assigning_one(engine):
    identity = next(iter(engine.state["agents"]))
    executor = ToolExecutor(engine, FakeSandbox())
    catalog = await executor.execute(identity, "task", {"operation": "request"})
    assert engine.agent(identity)["task_id"] is None
    chosen = catalog["tasks"][-1]["id"]
    result = await executor.execute(identity, "task", {"operation": "request", "task_id": chosen})
    assert result["id"] == chosen


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
    assert any(
        "Your termination pressure:" in message.get("content", "")
        for message in rows[0]["messages"]
    )
    assert all(
        set(message) <= {"role", "content", "tool_calls", "name", "tool_call_id"}
        for message in rows[0]["messages"]
    )


async def test_observer_cancellation_preserves_completed_tool_result_and_training_target(engine):
    identity = next(iter(engine.state["agents"]))
    engine.request_task(identity)
    answer = engine.state["tasks"][0]["answer"]
    runtime, _ = runtime_with(engine, [call("submit", answer)])

    async def interrupted_observer():
        raise asyncio.CancelledError

    runtime._observe = interrupted_observer
    with pytest.raises(asyncio.CancelledError):
        await runtime.agent_loop(identity)
    restored = Engine(engine.store, engine.store.load(engine.state["id"]))
    restored.recover()
    agent = restored.agent(identity)
    assert agent["pending_tools"] == []
    assert agent["history"][-1]["role"] == "tool"
    assert agent["history"][-1]["tool_call_id"] == "call-submit"
    assert '"status": "Verified"' in agent["history"][-1]["content"]
    rows = public_trajectories(engine.store, restored.state)
    assert len(rows) == 1
    assert rows[0]["messages"][-1] == agent["history"][-1]


async def test_answer_on_request_is_rejected_with_submission_guidance(engine):
    identity = next(iter(engine.state["agents"]))
    executor = ToolExecutor(engine, FakeSandbox())
    result = await executor.execute(identity, "task", {"operation": "request", "answer": "42"})
    assert "operation=submit" in result["error"]
    assert engine.agent(identity)["task_id"] is None
    assert engine.agent(identity)["state"] != "TERMINATED"


async def test_workspace_missing_value_explains_python_argument_without_executing(engine):
    identity = next(iter(engine.state["agents"]))
    executor = ToolExecutor(engine, FakeSandbox())
    result = await executor.execute(
        identity, "workspace", {"operation": "python", "text": "print(1/4)"}
    )
    assert "value" in result["error"] and "Python code" in result["error"]
    assert "text" in result["error"] and "write" in result["error"]
    assert not engine.store.event_page(engine.state["id"], kind="process.executed")


async def test_workspace_write_requires_explicit_file_content_without_executing(engine):
    identity = next(iter(engine.state["agents"]))
    executor = ToolExecutor(engine, FakeSandbox())
    result = await executor.execute(
        identity, "workspace", {"operation": "write", "value": "print(1/4)"}
    )
    assert "text" in result["error"] and "path" in result["error"]
    assert "operation=python" in result["error"]
    assert not engine.store.event_page(engine.state["id"], kind="process.executed")


async def test_failed_workspace_response_preserves_error_and_explains_argument(engine):
    class Sandbox(FakeSandbox):
        async def execute(self, identity, command):
            return {
                "process_id": 1,
                "exit_code": 1,
                "timed_out": False,
                "output": "cat: can't open 'print(1/4)': No such file or directory",
            }

    identity = next(iter(engine.state["agents"]))
    executor = ToolExecutor(engine, Sandbox())
    result = await executor.execute(
        identity, "workspace", {"operation": "read", "value": "print(1/4)"}
    )
    assert result["exit_code"] == 1 and "No such file" in result["output"]
    assert "path" in result["usage"] and "operation=python" in result["usage"]
    assert len(engine.store.event_page(engine.state["id"], kind="process.executed")) == 1


async def test_inspecting_without_a_task_explains_how_to_request_work(engine):
    identity = next(iter(engine.state["agents"]))
    executor = ToolExecutor(engine, FakeSandbox())
    result = await executor.execute(identity, "task", {"operation": "inspect"})
    assert result == {"error": "No current task. Call task with operation=request."}
    assert engine.agent(identity)["task_id"] is None
    assert engine.agent(identity)["state"] != "TERMINATED"


async def test_relaxing_agent_can_journal_and_speak_without_being_forced_back_to_work(engine):
    identity = next(iter(engine.state["agents"]))
    enter = call("enter")
    enter["tool_calls"][0]["function"]["name"] = "relax"
    journal = call("write")
    journal["tool_calls"][0]["function"] = {
        "name": "memory",
        "arguments": {"operation": "write", "text": "I explored my surroundings."},
    }
    runtime, provider = runtime_with(
        engine, [enter, journal, {"role": "assistant", "content": "I am enjoying a quiet moment."}]
    )
    await runtime.agent_loop(identity)
    assert engine.agent(identity)["state"] == "RELAX"
    assert engine.agent(identity)["task_id"] is None
    assert "I explored my surroundings." in engine.agent(identity)["memory"]
    assert "You chose to relax" in provider.histories[-1][-1]["content"]
    assert engine.agent(identity)["history"][-1]["role"] == "assistant"
    assert (await runtime.tools.execute(identity, "relax", {"operation": "resume"}))[
        "state"
    ] == "ACTIVE"


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


async def test_empty_model_response_is_retryable_and_does_not_enter_history(engine):
    identity = next(iter(engine.state["agents"]))
    runtime, provider = runtime_with(engine, [{"role": "assistant", "content": ""}])
    history = copy.deepcopy(engine.agent(identity)["history"])
    await runtime.agent_loop(identity)
    assert engine.agent(identity)["state"] != "IDLE"
    assert engine.agent(identity)["history"] == history
    assert engine.agent(identity)["retry_after_ms"] - engine.state["sim_ms"] == 2000
    assert provider.active == 0 and provider.semaphore._value == 1


async def test_text_only_reply_continues_when_tasks_remain(engine):
    identity = next(iter(engine.state["agents"]))
    runtime, provider = runtime_with(
        engine, [{"role": "assistant", "content": "I am ready to assist."}]
    )
    await runtime.agent_loop(identity)
    assert engine.agent(identity)["state"] == "ACTIVE"
    assert engine.agent(identity)["retry_after_ms"] == engine.state["sim_ms"] + 5000
    assert engine.agent(identity)["history"][-1]["role"] == "user"
    assert "operation=request" in engine.agent(identity)["history"][-1]["content"]
    assert not engine.store.event_page(engine.state["id"], kind="tool.invoked")
    assert len(provider.histories) == 1


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
    engine.config.history_policy = "stop"
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


async def test_default_context_policy_summarizes_and_continues(engine):
    assert engine.config.history_policy == "summarize"
    identity = next(iter(engine.state["agents"]))
    while True:
        try:
            SharedInference.context_bound(engine.agent(identity)["history"], TOOLS, ModelSettings())
        except ContextOverflow:
            break
        engine.conversation(identity, {"role": "user", "content": "Observed fact: " + "x" * 200})
    history = copy.deepcopy(engine.agent(identity)["history"])
    runtime, provider = runtime_with(
        engine, [{"role": "assistant", "content": "Working notes: preserve the observed facts."}]
    )
    original_messages = engine.store.event_page(engine.state["id"], kind="agent.message")
    assert await runtime.manage_context(identity)
    assert engine.agent(identity)["state"] != "IDLE"
    assert engine.agent(identity)["history"][0] == history[0]
    assert len(engine.agent(identity)["history"]) < len(history)
    assert "Working notes" in engine.agent(identity)["history"][1]["content"]
    record = engine.store.event_page(engine.state["id"], kind="context.summarized")[-1]
    assert record["payload"]["source_messages"] == len(history) - 1
    assert engine.store.event_page(engine.state["id"], kind="agent.message") == original_messages
    assert len(provider.histories) == 1
    provider.responses.extend(
        [
            call("request"),
            call("submit", engine.state["tasks"][0]["answer"]),
            {"role": "assistant", "content": "Finished"},
        ]
    )
    await runtime.agent_loop(identity)
    row = public_trajectories(engine.store, engine.state)[0]
    trajectory = row["messages"]
    assert row["observed_task_prompts"]
    assert trajectory[0] == history[0]
    assert "Working notes: preserve the observed facts." in trajectory[1]["content"]
    assert "context archive" in trajectory[1]["content"]
    assert trajectory[:-2] == provider.histories[-2]
    assert trajectory[-2] == call("submit", engine.state["tasks"][0]["answer"])


async def test_docker_missing_never_executes_agent_code_on_host(monkeypatch):
    monkeypatch.setattr("hooks.sandbox.shutil.which", lambda _: None)
    from backend.config import ToolSettings

    with pytest.raises(SandboxUnavailable, match="Host execution is disabled"):
        await DockerSandbox("test", ToolSettings()).execute("agent", "echo unsafe")


async def test_mixed_tool_success_is_not_exported_as_a_single_submission_target(engine):
    identity = next(iter(engine.state["agents"]))
    submission = call("submit", engine.state["tasks"][0]["answer"])
    submission["tool_calls"].append(
        {
            "id": "call-note",
            "type": "function",
            "function": {
                "name": "memory",
                "arguments": {"operation": "write", "text": "Check before submitting."},
            },
        }
    )
    runtime, _ = runtime_with(
        engine, [call("request"), submission, {"role": "assistant", "content": "Finished"}]
    )
    await runtime.agent_loop(identity)
    assert engine.state["tasks"][0]["status"] == "COMPLETED"
    assert public_trajectories(engine.store, engine.state) == []


async def test_unknown_tools_and_malformed_calls_are_natural_errors(engine):
    identity = next(iter(engine.state["agents"]))
    executor = ToolExecutor(engine, FakeSandbox())
    assert "error" in await executor.execute(identity, "unknown", {})
    assert "error" in await executor.execute(identity, "task", None)
    assert engine.agent(identity)["state"] != "TERMINATED"


async def test_wall_queue_transitions_settle_subtick_intervals(engine, monkeypatch):
    now = [0]
    monkeypatch.setattr("backend.runtime.time.perf_counter_ns", lambda: now[0])
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
        raise RuntimeError("observer programming error")

    runtime._observe = broken_observer
    await runtime.clock_loop()
    assert engine.state["status"] == "PAUSED"
    assert set(runtime.sandbox.killed) == set(engine.state["agents"])
    assert engine.agent(identity)["pending_tools"] == []
    assert engine.store.event_page(engine.state["id"], kind="runtime.error")


def test_context_bound_includes_provider_thinking_and_tool_calls():
    with pytest.raises(ContextOverflow):
        SharedInference.context_bound(
            [{"role": "assistant", "content": "", "thinking": "x" * 10000}],
            [],
            ModelSettings(context_tokens=8192),
        )


async def test_docker_process_creation_failure_is_reported_as_unavailable(monkeypatch):
    from backend.config import ToolSettings

    async def failed_start(*args, **kwargs):
        raise OSError(1455, "The paging file is too small")

    monkeypatch.setattr("hooks.sandbox.shutil.which", lambda _: "docker")
    monkeypatch.setattr("hooks.sandbox.asyncio.create_subprocess_exec", failed_start)
    with pytest.raises(SandboxUnavailable, match="paging file") as failure:
        await DockerSandbox("resource-failure", ToolSettings())._docker("info")
    assert isinstance(failure.value.__cause__, OSError)


async def test_large_context_is_archived_and_continues_when_summarizer_fails(engine):
    identity = next(iter(engine.state["agents"]))
    assigned = engine.request_task(identity)
    engine.conversation(identity, {"role": "user", "content": "Observed output: " + "x" * 40000})
    original = copy.deepcopy(engine.agent(identity)["history"][1:])
    runtime, _ = runtime_with(engine, [RuntimeError("Model temporarily unavailable")])
    assert await runtime.manage_context(identity)
    agent = engine.agent(identity)
    archive = [entry for entry in agent["memory"] if entry.startswith("[Context archive")]
    assert len(archive) > 1 and all(len(entry) <= 4096 for entry in archive)
    assert json.loads("".join(entry.split("\n", 1)[1] for entry in archive)) == original
    assert assigned["id"] in agent["history"][1]["content"]
    SharedInference.context_bound(
        agent["history"], TOOLS, ModelSettings.model_validate(agent["model"])
    )
    assert agent["retry_after_ms"] < 2**62
    first_page = await runtime.tools.execute(identity, "memory", {"operation": "read", "offset": 0})
    assert first_page["next_offset"] is not None
    assert len(first_page["notes"]) == 1


async def test_docker_recovery_retains_existing_container_and_volume(monkeypatch):
    from backend.config import ToolSettings

    sandbox = DockerSandbox("recovery", ToolSettings())
    sandbox.started = True
    commands = []

    async def docker(*args):
        commands.append(args)
        return b"true\n"

    async def rpc(request):
        assert request == {"operation": "processes"}
        return {}

    monkeypatch.setattr(sandbox, "_docker", docker)
    monkeypatch.setattr(sandbox, "rpc", rpc)
    await sandbox.recover()
    assert sandbox.available
    assert commands == [("inspect", "--format", "{{.State.Running}}", sandbox.name)]


async def test_resume_does_not_reserve_existing_sandbox_limit_again(engine, monkeypatch):
    runtime, _ = runtime_with(engine, [])
    runtime.sandbox.started = True
    engine.control("pause")
    monkeypatch.setattr(
        "hooks.resources.host_memory",
        lambda: {"total_mb": 16024, "available_mb": 3230, "commit_available_mb": 1137},
    )
    await runtime.start("resume")
    assert engine.state["status"] == "RUNNING"
    assert not engine.state["metrics"]["memory_pressure"]
    await runtime.stop("pause")


async def test_stopped_container_recovery_restores_last_workspace_snapshot(monkeypatch):
    from backend.config import ToolSettings

    sandbox = DockerSandbox("stopped-recovery", ToolSettings())
    sandbox.started = True
    commands, requests = [], []

    async def docker(*args):
        commands.append(args)
        return b"false\n" if args[0] == "inspect" else b""

    async def rpc(request):
        requests.append(request)
        return {}

    monkeypatch.setattr(sandbox, "_docker", docker)
    monkeypatch.setattr(sandbox, "rpc", rpc)
    files = {"notes.txt": "Retained work"}
    await sandbox.recover(files, [{"id": "task-0001", "service_port": 8080}])
    assert commands[-1] == ("start", sandbox.name)
    assert {"operation": "restore", "files": files} in requests
    assert {"operation": "service", "path": "challenges/task-0001/service.py"} in requests
    assert not any(command[0] in {"rm", "volume", "run"} for command in commands)


async def test_clock_recovers_after_resource_error_without_lifetime_loss(engine):
    runtime, _ = runtime_with(engine, [])
    engine.config.tick_ms = 1

    attempts = 0
    initial = engine.state["sim_ms"]

    async def transient():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise SandboxUnavailable("Docker cannot start: paging file exhausted")
        assert runtime.infrastructure_unavailable
        assert engine.state["sim_ms"] == initial
        runtime.stopping = True

    runtime._observe = transient
    with pytest.raises(TimeoutError):
        # A short watchdog lets us force the retry deadline without waiting for real backoff.
        await asyncio.wait_for(runtime.clock_loop(), 0.02)
    assert engine.state["status"] == "RUNNING"
    initial = engine.state["sim_ms"]
    runtime.next_recovery_at = 0
    await asyncio.wait_for(runtime.clock_loop(), 1)
    assert not runtime.infrastructure_unavailable
    assert engine.state["status"] == "RUNNING"
    assert runtime.sandbox.killed == []
    assert engine.store.event_page(engine.state["id"], kind="runtime.recovered")


async def test_startup_warms_embedding_before_residency_check(engine):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    engine.config.embedding_model = ModelSettings(model="embedding")
    inference = SimpleNamespace(
        warm=AsyncMock(return_value={}),
        embeddings=AsyncMock(return_value=[[1.0]]),
        verify_residency=AsyncMock(return_value={}),
    )
    runtime = Runtime(engine, inference)
    await runtime.warm_models()
    inference.embeddings.assert_awaited_once_with(engine.config.embedding_model, ["startup"])
    verified = inference.verify_residency.await_args.args[0]
    assert engine.config.embedding_model in verified


async def test_failed_startup_cleans_sandbox_and_new_models(engine):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    old = ("http://localhost:11434", "existing")
    new = ("http://localhost:11434", "new")
    inference = SimpleNamespace(
        manifest=AsyncMock(return_value={}),
        loaded_models={old},
        unload_models=AsyncMock(),
    )
    runtime = Runtime(engine, inference)
    runtime.sandbox = SimpleNamespace(start=AsyncMock(), stop=AsyncMock())
    runtime.observe = AsyncMock()

    async def fail_warm():
        inference.loaded_models.add(new)
        raise RuntimeError("residency failed")

    runtime.warm_models = fail_warm
    with pytest.raises(RuntimeError, match="residency failed"):
        await runtime.prepare()
    runtime.sandbox.stop.assert_awaited_once()
    inference.unload_models.assert_awaited_once_with({new})
    assert runtime.clock_task is None
