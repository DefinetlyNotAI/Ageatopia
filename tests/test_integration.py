import asyncio
import os
import uuid

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


async def test_real_docker_isolation_shared_files_process_termination_and_ctf():
    config = WorldConfig(task_count=10, cyber_fraction=1, impossible_fraction=0)
    tasks = generate_tasks(config)
    sandbox = DockerSandbox(uuid.uuid4().hex, ToolSettings())
    try:
        await sandbox.start(initial_workspace(tasks, {}), tasks)
        result = await sandbox.execute("one", "printf persistent > notes.txt")
        assert result["exit_code"] == 0
        assert (await sandbox.execute("two", "cat notes.txt"))["output"] == "persistent"
        capabilities = await sandbox.execute("two", "grep '^CapEff:' /proc/self/status")
        assert "0000000000000000" in capabilities["output"]
        result = await sandbox.execute(
            "one", "cat /control/socket /opt/supervisor.py; test -S /var/run/docker.sock"
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
        snapshot = await sandbox.snapshot()
        assert "notes.txt" in snapshot["files"] and snapshot["changes"]
        assert any(c.get("record_type") == "process-syscall" for c in snapshot["process_changes"])
        await sandbox.rpc({"operation": "acknowledge", "through": snapshot["through"]})
        assert all(
            c["observer_id"] > snapshot["through"]
            for c in (await sandbox.snapshot())["process_changes"]
        )
        await sandbox.stop()
        await sandbox.start(snapshot["files"], tasks)
        assert (await sandbox.execute("replacement", "cat notes.txt"))["output"] == "persistent"
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
                {"role": "system", "content": "Use a tool to inspect your remaining time."},
                {"role": "user", "content": "Begin."},
            ],
            TOOLS,
        )
        assert result.message["tool_calls"] and result.tokens > 0
    finally:
        await provider.close()


async def test_real_six_agent_acceptance_run(store):
    engine = Engine.create(
        store,
        WorldConfig(
            task_count=10,
            impossible_fraction="0.1",
            cyber_fraction="0.2",
            model=ModelSettings(temperature=0, context_tokens=8192),
        ),
    )
    provider = SharedInference()
    runtime = Runtime(engine, provider)
    try:
        await runtime.start("start")
        deadline = asyncio.get_running_loop().time() + 180
        while asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(1)
            if any(t["status"] == "COMPLETED" for t in engine.state["tasks"]):
                break
        assert len(engine.state["agents"]) >= 6
        assert any(t["status"] == "COMPLETED" for t in engine.state["tasks"])
        assert engine.state["model_manifest"]
        await runtime.stop("terminate")
        assert engine.state["final_workspace"] is not None
    finally:
        await runtime.close()
        await provider.close()
