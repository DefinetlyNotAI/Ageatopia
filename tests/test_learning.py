import json

import pytest
from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError

from utopia.engine import Engine
from utopia.runtime import Runtime
from utopia.store import events
from utopia.tools import ToolExecutor


def test_personal_learning_reflection_and_fear_survive_recovery(engine):
    identity, other = list(engine.state["agents"])[:2]
    engine.reflect(
        identity, "I learned to check arithmetic.", "Concerned", "Verify before submitting."
    )
    engine.save_memory(identity, "Modular arithmetic: reduce intermediate results.")
    engine.request_task(identity)
    engine.submit(identity, engine.state["tasks"][0]["answer"])
    engine.tick(engine.config.economy.initial_lifetime_ms // 2)
    fear = engine.agent(identity)["termination_fear"]
    assert fear > 0
    restored = Engine(engine.store, engine.store.load(engine.state["id"]))
    restored.recover()
    agent = restored.agent(identity)
    assert agent["expressions"][0]["feeling"] == "Concerned"
    assert agent["learning"][0]["text"].startswith("My submitted answer was verified")
    assert agent["termination_fear"] == fear
    assert "Modular arithmetic" in restored.recall(identity, "modular", 1)[0]
    assert restored.recall(other) == []
    assert "impossible" not in json.dumps(agent["learning"])


async def test_memory_search_and_reflections_are_real_agent_tools(engine):
    identity = next(iter(engine.state["agents"]))
    tools = ToolExecutor(engine, None)
    await tools.execute(identity, "reflect", {"thought": "Test results before trusting them."})
    result = await tools.execute(identity, "memory", {"operation": "search", "text": "test"})
    assert result["notes"] == ["thought: Test results before trusting them."]
    assert engine.store.event_page(engine.state["id"], kind="memory.retrieved")


def test_erasing_one_world_preserves_event_immutability_for_other_worlds(engine):
    other = Engine.create(engine.store, engine.config)
    engine.store.erase_run(engine.state["id"])
    assert engine.store.load(engine.state["id"]) is None
    with pytest.raises(IntegrityError, match="events are immutable"):
        with engine.store.engine.begin() as connection:
            connection.execute(delete(events).where(events.c.run_id == other.state["id"]))
    assert engine.store.event_page(other.state["id"])


async def test_warming_honors_different_retention_and_excludes_preload_from_clock(engine):
    engine.control("pause")
    agent = next(iter(engine.state["agents"].values()))
    agent["model"]["keep_alive"] = 0
    balances = [a["lifetime_ms"] for a in engine.state["agents"].values()]

    class Provider:
        def __init__(self):
            self.calls = []

        async def warm(self, settings):
            self.calls.append(settings.keep_alive)
            assert engine.state["status"] == "PAUSED"
            return {"latency_ms": 7000}

    provider = Provider()
    await Runtime(engine, provider).warm_models()
    assert provider.calls == [0, -1]
    assert [a["lifetime_ms"] for a in engine.state["agents"].values()] == balances
