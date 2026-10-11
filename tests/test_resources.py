import os
import subprocess
import sys

import httpx
import pytest

from backend.config import ModelSettings
from backend.runtime import Runtime
from hooks.providers import SharedInference
from hooks.resources import MemoryGuard, MemoryPressure, WindowsMemoryJob, host_memory


def test_memory_guard_admits_once_and_ignores_later_host_pressure(monkeypatch):
    state = {"total_mb": 16000, "available_mb": 4000, "commit_available_mb": 5000}
    monkeypatch.setattr("hooks.resources.host_memory", lambda: state.copy())
    guard = MemoryGuard()
    assert guard.check()["ram_budget_mb"] == 4000
    state.update(available_mb=1, commit_available_mb=1)
    assert guard.check(64)["ram_budget_mb"] == 4000
    with pytest.raises(MemoryPressure, match="fixed startup budget"):
        guard.check(4001)


def test_startup_fails_below_minimum(monkeypatch):
    monkeypatch.setattr(
        "hooks.resources.host_memory",
        lambda: {"total_mb": 16000, "available_mb": 511, "commit_available_mb": 4000},
    )
    with pytest.raises(MemoryPressure, match="Startup requires 512"):
        MemoryGuard().check()


async def test_running_world_never_pauses_for_host_headroom(engine, monkeypatch):
    inference = SharedInference()
    runtime = Runtime(engine, inference)
    monkeypatch.setattr("hooks.resources.host_memory", lambda: pytest.fail("Live headroom polled"))
    before = engine.state["sim_ms"]
    runtime.wall_anchor_ns -= 10_000_000_000
    runtime.sync_clock()
    assert engine.state["sim_ms"] > before
    assert not engine.state["metrics"]["memory_pressure"]
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


async def test_restarted_runtime_clears_saved_memory_hold(engine, monkeypatch):
    monkeypatch.setattr(
        "hooks.resources.host_memory",
        lambda: {"total_mb": 16024, "available_mb": 1000, "commit_available_mb": 2000},
    )
    inference = SharedInference()
    engine.state["metrics"]["memory_pressure"] = True
    runtime = Runtime(engine, inference)
    assert runtime.engine is engine
    assert not engine.state["metrics"]["memory_pressure"]
    await inference.close()


async def test_cold_cpu_model_must_fit_startup_budget(monkeypatch):
    monkeypatch.setattr(
        "hooks.resources.host_memory",
        lambda: {"total_mb": 16000, "available_mb": 3000, "commit_available_mb": 4000},
    )
    provider = SharedInference()
    await provider.client.aclose()
    provider.client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"models": []}))
    )
    settings = ModelSettings(gpu_layers=0)
    provider.model_sizes[(settings.base_url, settings.model)] = 4096
    try:
        with pytest.raises(MemoryPressure, match="fixed startup budget"):
            await provider.check_model_memory(settings)
    finally:
        await provider.close()


async def test_gpu_admission_is_fixed_and_rejects_below_minimum(monkeypatch):
    monkeypatch.setattr(
        "hooks.resources.host_memory",
        lambda: {"total_mb": 16000, "available_mb": 4000, "commit_available_mb": 5000},
    )
    gpu = {"total_mb": 6144, "available_mb": 511}
    monkeypatch.setattr("hooks.providers.gpu_memory", lambda: gpu.copy())
    provider = SharedInference()
    await provider.client.aclose()
    provider.client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"models": []}))
    )
    try:
        with pytest.raises(MemoryPressure, match="512 MiB usable NVIDIA VRAM"):
            await provider.admit([ModelSettings()])
        gpu["available_mb"] = 3000
        await provider.admit([ModelSettings()])
        gpu["available_mb"] = 1
        await provider.admit([ModelSettings()])
        assert provider.vram_budget_mb == 3000
        with pytest.raises(MemoryPressure, match="could not keep"):
            await provider.verify_residency([ModelSettings()])
    finally:
        await provider.close()


async def test_shutdown_unloads_every_warmed_model_even_after_one_failure():
    unloaded = []

    def handler(request):
        import json

        model = json.loads(request.content)["model"]
        unloaded.append(model)
        return httpx.Response(500 if len(unloaded) == 1 else 200, json={})

    provider = SharedInference()
    await provider.client.aclose()
    provider.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider.loaded_models.update(
        {("http://127.0.0.1:11434", "one"), ("http://127.0.0.1:11434", "two")}
    )
    await provider.close()
    assert set(unloaded) == {"one", "two"}
    assert provider.client.is_closed
