import os
import subprocess
import sys

import httpx
import pytest

from utopia.config import ModelSettings
from utopia.providers import SharedInference
from utopia.resources import MemoryGuard, MemoryPressure, WindowsMemoryJob, host_memory
from utopia.runtime import Runtime


def test_memory_guard_checks_ram_commit_and_recovery_margin(monkeypatch):
    state = {"total_mb": 16000, "available_mb": 4000, "commit_available_mb": 4000}
    monkeypatch.setattr("utopia.resources.host_memory", lambda: state.copy())
    guard = MemoryGuard(2048)
    assert guard.check()["ram_reserve_mb"] == 2400
    state["available_mb"] = 2399
    with pytest.raises(MemoryPressure, match="Memory safety hold"):
        guard.check()
    state["available_mb"] = 2500
    with pytest.raises(MemoryPressure):
        guard.check(recovery=True)
    state["available_mb"] = 4000
    state["commit_available_mb"] = 2047
    with pytest.raises(MemoryPressure):
        guard.check()
    state["commit_available_mb"] = 4000
    with pytest.raises(MemoryPressure):
        guard.check(2048)


async def test_memory_hold_preserves_agent_lifetimes_and_recovers(engine, monkeypatch):
    inference = SharedInference()
    runtime = Runtime(engine, inference)
    state = {"total_mb": 16000, "available_mb": 2000, "commit_available_mb": 4000}
    monkeypatch.setattr("utopia.resources.host_memory", lambda: state.copy())
    before = engine.state["sim_ms"]
    balances = [agent["lifetime_ms"] for agent in engine.state["agents"].values()]
    assert not runtime.check_memory()
    runtime.wall_anchor_ns -= 10_000_000_000
    runtime.sync_clock()
    assert engine.state["sim_ms"] == before
    assert [agent["lifetime_ms"] for agent in engine.state["agents"].values()] == balances
    assert engine.state["metrics"]["memory_pressure"]
    assert not runtime.check_memory()
    events = engine.store.event_page(engine.state["id"])
    assert sum(event["kind"] == "runtime.memory-pressure" for event in events) == 1
    state["available_mb"] = 4000
    assert runtime.check_memory()
    assert not engine.state["metrics"]["memory_pressure"]
    runtime.sync_clock()
    assert engine.state["sim_ms"] < before + 1000
    await inference.close()


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object integration")
def test_windows_job_denies_allocation_and_caps_descendants():
    # Small real allocations prove OS enforcement without stressing host memory.
    job = WindowsMemoryJob(96)
    try:
        child = job.spawn(
            [
                sys.executable,
                "-c",
                "import sys; "
                "\ntry: data=bytearray(160*1024*1024)"
                "\nexcept MemoryError: sys.exit(42)",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        output, error = child.communicate(timeout=15)
        assert child.returncode == 42, (output, error)
        program = (
            "import subprocess,sys; p=subprocess.run([sys.executable,'-c',"
            + repr(
                "import sys\ntry: data=bytearray(160*1024*1024)\nexcept MemoryError: sys.exit(42)"
            )
            + "]); sys.exit(p.returncode)"
        )
        parent = job.spawn(
            [sys.executable, "-c", program], stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        output, error = parent.communicate(timeout=15)
        assert parent.returncode == 42, (output, error)
    finally:
        job.close()


def test_host_memory_reports_positive_global_values():
    snapshot = host_memory()
    assert snapshot["total_mb"] > 0
    assert 0 <= snapshot["available_mb"] <= snapshot["total_mb"]
    assert snapshot["commit_available_mb"] >= 0


async def test_cold_model_budget_blocks_loading_but_resident_context_can_continue(monkeypatch):
    state = {"total_mb": 16000, "available_mb": 3000, "commit_available_mb": 4000}
    monkeypatch.setattr("utopia.resources.host_memory", lambda: state.copy())
    models = []
    requests = []

    def handler(request):
        requests.append(request.url.path)
        return httpx.Response(200, json={"models": models})

    provider = SharedInference()
    await provider.client.aclose()
    provider.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    settings = ModelSettings()
    provider.model_sizes[(settings.base_url, settings.model)] = 4096
    try:
        with pytest.raises(MemoryPressure) as failure:
            await provider.check_model_memory(settings)
        assert failure.value.allocation_mb == 6144
        assert provider.memory_needed_mb == 6144
        assert requests == ["/api/ps"]
        models.append({"name": settings.model, "context_length": settings.context_tokens})
        await provider.check_model_memory(settings)
        assert provider.memory_needed_mb == 0
    finally:
        await provider.close()
