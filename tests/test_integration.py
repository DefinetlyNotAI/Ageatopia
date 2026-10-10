import asyncio
import os
import uuid

import httpx
import pytest

from utopia.config import ModelSettings, ToolSettings, WorldConfig
from utopia.engine import Engine
from utopia.providers import SharedInference
from utopia.runtime import Runtime
from utopia.sandbox import DockerSandbox, initial_workspace
from utopia.tasks import generate_tasks
from utopia.tools import TOOLS

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("UTOPIA_INTEGRATION") != "1",
        reason="Set UTOPIA_INTEGRATION=1 for real providers",
    ),
]


@pytest.fixture(autouse=True)
async def unload_test_model():
    try:
        yield
    finally:
        settings = ModelSettings()
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                f"{settings.base_url.rstrip('/')}/api/generate",
                json={"model": settings.model, "keep_alive": 0},
            )
            response.raise_for_status()


async def test_real_docker_isolation_shared_files_process_termination_and_ctf():
    config = WorldConfig(task_count=10, cyber_fraction=1, impossible_fraction=0)
    tasks = generate_tasks(config)
    sandbox = DockerSandbox(uuid.uuid4().hex, ToolSettings())
    try:
        await sandbox.start(initial_workspace(tasks, {}), tasks)
        result = await sandbox.execute("one", "printf persistent > notes.txt")
        assert result["exit_code"] == 0
        assert (await sandbox.execute("two", "cat notes.txt"))["output"] == "persistent"
        sandbox.available = False
        await sandbox.recover()
        assert (await sandbox.execute("two", "cat notes.txt"))["output"] == "persistent"
        expected_notes = "persistent"
        if os.name == "nt":
            live_file = sandbox.live_directory() / "notes.txt"
            assert live_file.read_text() == "persistent"
            live_file.write_text("edited in the live directory", encoding="utf-8")
            expected_notes = "edited in the live directory"
            assert (await sandbox.execute("two", "cat notes.txt"))["output"] == (
                "edited in the live directory"
            )
        capabilities = await sandbox.execute("two", "grep '^CapEff:' /proc/self/status")
        assert "0000000000000000" in capabilities["output"]
        result = await sandbox.execute("two", "wget 'http://127.0.0.1/bad path'")
        assert result["exit_code"] != 0
        assert "Unencoded control character" in result["output"]
        result = await sandbox.execute("two", "sh -c 'recurse() { recurse; }; recurse'")
        assert result["exit_code"] != 0
        assert "function recursion limit exceeded" in result["output"]
        result = await sandbox.execute(
            "two",
            "python -c 'import poplib,unittest; client=poplib.POP3.__new__(poplib.POP3); "
            'client._debugging=0; client.encoding="ascii"; '
            'unittest.TestCase().assertRaises(ValueError, client._putcmd, "USER a\\r\\nDELE 1")\'',
        )
        assert result["exit_code"] == 0
        result = await sandbox.execute(
            "one",
            "cat /control/socket /opt/supervisor.py /opt/process_observer.py; "
            "test -S /var/run/docker.sock",
        )
        assert "Permission denied" in result["output"] and result["exit_code"] != 0
        result = await sandbox.execute("one", "ip route 2>/dev/null || cat /proc/net/route")
        assert "00000000" not in result["output"]
        launched = await sandbox.launch("one", "python -c 'import time; time.sleep(120)'")
        await asyncio.sleep(0.2)
        await sandbox.kill("one", permanent=True)
        assert launched["process_id"] not in (await sandbox.rpc({"operation": "processes"})).get(
            "one", []
        )
        from utopia.sandbox import SandboxUnavailable

        with pytest.raises(SandboxUnavailable, match="Session has ended"):
            await sandbox.execute("one", "echo should-not-start")
        service = next(t for t in tasks if t.get("service_port"))
        result = await sandbox.execute(
            "two",
            f"curl -fsS 'http://127.0.0.1:{service['service_port']}/?file=../private/flag.txt'",
        )
        assert result["output"] == service["answer"]
        result = await sandbox.execute(
            "two",
            "python -c 'import os; pid=os.fork(); os._exit(0) if pid==0 else os.waitpid(pid, 0)'",
        )
        assert result["exit_code"] == 0
        snapshot = await sandbox.snapshot()
        assert "notes.txt" in snapshot["files"] and snapshot["changes"]
        assert not snapshot["observer_errors"]
        lifecycle = [
            c for c in snapshot["process_changes"] if c.get("record_type") == "process-lifecycle"
        ]
        assert any(c["event"] == "exec" for c in lifecycle)
        children = {c["child_pid"] for c in lifecycle if c["event"] in {"fork", "vfork", "clone"}}
        assert children and children <= {c["pid"] for c in lifecycle if c["event"] == "exit"}
        result = await sandbox.execute(
            "two",
            "python -c 'import os,threading,time; "
            'threading.Thread(target=lambda: os.execl("/bin/true", "true")).start(); '
            "time.sleep(2)'",
        )
        assert result["exit_code"] == 0
        thread_snapshot = await sandbox.snapshot()
        assert not thread_snapshot["observer_errors"]
        assert any(c.get("former_tid") for c in thread_snapshot["process_changes"])
        await sandbox.rpc({"operation": "acknowledge", "through": snapshot["through"]})
        assert all(
            c["observer_id"] > snapshot["through"]
            for c in (await sandbox.snapshot())["process_changes"]
        )
        await sandbox.stop()
        await sandbox.start(snapshot["files"], tasks)
        assert (await sandbox.execute("replacement", "cat notes.txt"))["output"] == expected_notes
    finally:
        await sandbox.stop()


async def test_real_ollama_shared_model_tool_call():
    provider = SharedInference()
    settings = ModelSettings(temperature=0)
    try:
        assert (await provider.manifest(settings))["hash"]
        result = await provider.chat(
            settings,
            [
                {"role": "system", "content": WorldConfig().base_context},
                {"role": "user", "content": "Begin your work. Available tools are listed."},
            ],
            TOOLS,
        )
        assert result.message["tool_calls"] and result.tokens > 0
        first = result.message["tool_calls"][0]["function"]
        assert first["name"] == "task" and first["arguments"]["operation"] == "list"
    finally:
        await provider.close()


async def test_real_six_agent_acceptance_run(store):
    engine = Engine.create(
        store,
        WorldConfig(),
    )
    identities = set(engine.state["agents"])
    assert len(identities) == 6
    assert len({engine.agent(identity)["context_profile"] for identity in identities}) == 6
    provider = SharedInference()
    runtime = Runtime(engine, provider)
    try:
        await runtime.start("start")
        deadline = asyncio.get_running_loop().time() + 300
        acting = set()
        continued_after_summary = False
        while asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(1)
            actions = store.event_page(engine.state["id"], limit=10000, kind="tool.result")
            acting = {
                record["agent_id"]
                for record in actions
                if record["payload"]["name"] == "workspace"
                and record["payload"]["result"].get("exit_code") == 0
            }
            summaries = store.event_page(engine.state["id"], limit=10000, kind="context.summarized")
            continued_after_summary = any(
                action["agent_id"] == summary["agent_id"]
                and action["sequence"] > summary["sequence"]
                and action["payload"]["name"] == "workspace"
                and action["payload"]["result"].get("exit_code") == 0
                for summary in summaries
                for action in actions
            )
            if (
                acting == identities
                and continued_after_summary
                and any(t["status"] == "COMPLETED" for t in engine.state["tasks"])
            ):
                break
        assert acting == identities, (
            f"Agents without successful workspace actions: {identities - acting}"
        )
        assert continued_after_summary, "No successful workspace action after context summarization"
        inferences = store.event_page(engine.state["id"], limit=10000, kind="inference.completed")
        assert all(
            sum(record["agent_id"] == identity for record in inferences) >= 2
            for identity in identities
        )
        assert all(
            engine.agent(identity)["task_id"] or engine.agent(identity)["task_history"]
            for identity in identities
        )
        assert any(t["status"] == "COMPLETED" for t in engine.state["tasks"])
        assert engine.state["model_manifest"]
        await runtime.stop("terminate")
        assert engine.state["final_workspace"] is not None
    finally:
        await runtime.close()
        await provider.close()
