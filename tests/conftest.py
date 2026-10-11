import pytest

from backend.config import WorldConfig
from backend.engine import Engine
from backend.store import Store


@pytest.fixture(autouse=True)
def stable_host_memory(request, monkeypatch):
    if request.node.get_closest_marker("integration") or request.path.name == "test_resources.py":
        return
    monkeypatch.setattr(
        "hooks.resources.host_memory",
        lambda: {"total_mb": 16000, "available_mb": 12000, "commit_available_mb": 16000},
    )


@pytest.fixture
def store(tmp_path):
    database = Store.local(tmp_path)
    yield database
    database.engine.dispose()


@pytest.fixture
def engine(store):
    world = Engine.create(
        store,
        WorldConfig(task_count=10, impossible_fraction=0, cyber_fraction=0, leisure_fraction=0),
    )
    world.control("start")
    return world
