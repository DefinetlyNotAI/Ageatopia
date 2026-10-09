import hashlib
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, inspect
from sqlalchemy.exc import IntegrityError

from utopia.engine import Engine
from utopia.evaluate_adapter import evaluation_tasks
from utopia.runtime import Runtime
from utopia.store import Store, events
from utopia.tools import ToolExecutor
from utopia.train import train, verified_sample, verified_submission_index


def test_training_target_excludes_prior_errors_and_requires_one_verified_submission():
    bad = {
        "role": "assistant",
        "tool_calls": [
            {"id": "bad", "function": {"name": "task", "arguments": {"operation": "solve"}}}
        ],
    }
    good = {
        "role": "assistant",
        "tool_calls": [
            {
                "id": "good",
                "function": {"name": "task", "arguments": {"operation": "submit", "answer": "7"}},
            }
        ],
    }
    result = {
        "role": "tool",
        "name": "task",
        "tool_call_id": "good",
        "content": '{"status":"Verified"}',
    }
    messages = [
        bad,
        {
            "role": "tool",
            "name": "task",
            "tool_call_id": "bad",
            "content": '{"error":"invalid operation"}',
        },
        good,
        result,
    ]
    assert verified_submission_index(messages) == 2
    good["tool_calls"].append(bad["tool_calls"][0])
    with pytest.raises(ValueError, match="exactly one"):
        verified_submission_index(messages)
    good["tool_calls"].pop()
    result["content"] = '{"status":"Rejected"}'
    with pytest.raises(ValueError, match="verified task result"):
        verified_submission_index(messages)


def test_training_rejects_a_chat_template_that_drops_tool_calls():
    messages = [
        {"role": "user", "content": "Solve the task"},
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "one",
                    "function": {
                        "name": "task",
                        "arguments": {"operation": "submit", "answer": "7"},
                    },
                }
            ],
        },
        {"role": "tool", "name": "task", "tool_call_id": "one", "content": '{"status":"Verified"}'},
    ]
    tokenizer = SimpleNamespace(apply_chat_template=lambda messages, **kw: "prompt and answer")
    with pytest.raises(ValueError, match="omitted the verified tool call"):
        verified_sample(tokenizer, messages, 4096)


def test_adapter_evaluation_excludes_training_prompts_and_requires_complete_lineage(tmp_path):
    from utopia.config import WorldConfig
    from utopia.tasks import generate_tasks, public_task

    original = generate_tasks(
        WorldConfig(seed=20261009, task_count=80, cyber_fraction=0, impossible_fraction=0)
    )[0]
    dataset = tmp_path / "training.jsonl"
    dataset.write_text(
        json.dumps(
            {
                "agent_id": "one",
                "model_version": "source",
                "context_version": "3",
                "messages": [
                    {"role": "tool", "name": "task", "content": json.dumps(public_task(original))}
                ],
            }
        ),
        encoding="utf-8",
    )
    manifest = {
        "model_version": "base",
        "dataset_version": hashlib.sha256(dataset.read_bytes()).hexdigest(),
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    _, tasks = evaluation_tasks(tmp_path, [dataset], "base", 20261009, 12)
    assert len(tasks) == 12 and original["prompt"] not in {task["prompt"] for task in tasks}
    row = json.loads(dataset.read_text())
    row["observed_task_prompts"] = [public_task(original)["prompt"]]
    row["messages"] = [{"role": "user", "content": "Your earlier working notes:\nTask notes"}]
    dataset.write_text(json.dumps(row), encoding="utf-8")
    manifest["dataset_version"] = hashlib.sha256(dataset.read_bytes()).hexdigest()
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    _, tasks = evaluation_tasks(tmp_path, [dataset], "base", 20261009, 12)
    assert len(tasks) == 12 and original["prompt"] not in {task["prompt"] for task in tasks}
    del row["observed_task_prompts"]
    dataset.write_text(json.dumps(row), encoding="utf-8")
    manifest["dataset_version"] = hashlib.sha256(dataset.read_bytes()).hexdigest()
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="Summarized exports require"):
        evaluation_tasks(tmp_path, [dataset], "base", 20261009, 12)
    with pytest.raises(ValueError, match="base model"):
        evaluation_tasks(tmp_path, [dataset], "different", 20261009, 12)
    manifest["parent_adapter"] = {"dataset_version": "missing"}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="every adapter"):
        evaluation_tasks(tmp_path, [dataset], "base", 20261009, 12)


@pytest.mark.parametrize(
    ("model", "iteration", "error"), [("different", 2, "base model"), ("base", 1, "advance")]
)
def test_adapter_continuation_validates_parent_before_loading_weights(
    tmp_path, model, iteration, error
):
    (tmp_path / "manifest.json").write_text(
        json.dumps({"model_version": "base", "training_iteration": 1}), encoding="utf-8"
    )
    with pytest.raises(ValueError, match=error):
        train(SimpleNamespace(resume_adapter=tmp_path, model=model, iteration=iteration))


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


def test_existing_database_receives_event_lookup_index_without_losing_state(engine, tmp_path):
    with engine.store.engine.begin() as connection:
        connection.exec_driver_sql("DROP INDEX events_run_agent_kind")
    reopened = Store.local(tmp_path)
    try:
        assert "events_run_agent_kind" in {
            index["name"] for index in inspect(reopened.engine).get_indexes("events")
        }
        assert reopened.load(engine.state["id"])["id"] == engine.state["id"]
    finally:
        reopened.engine.dispose()
