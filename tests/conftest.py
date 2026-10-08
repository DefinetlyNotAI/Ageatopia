import pytest

from utopia.config import WorldConfig
from utopia.engine import Engine
from utopia.store import Store


@pytest.fixture
def store(tmp_path):
    database = Store.local(tmp_path)
    yield database
    database.engine.dispose()


@pytest.fixture
def engine(store):
    world = Engine.create(
        store, WorldConfig(task_count=10, impossible_fraction=0, cyber_fraction=0)
    )
    world.control("start")
    return world
