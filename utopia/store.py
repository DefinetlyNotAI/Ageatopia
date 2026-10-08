import json
from pathlib import Path

from sqlalchemy import Column, Integer, MetaData, String, Table, Text, create_engine, event, select

metadata = MetaData()
runs = Table(
    "runs",
    metadata,
    Column("id", String, primary_key=True),
    Column("state", Text, nullable=False),
)
events = Table(
    "events",
    metadata,
    Column("sequence", Integer, primary_key=True, autoincrement=True),
    Column("run_id", String, nullable=False, index=True),
    Column("schema_version", Integer, nullable=False),
    Column("sim_ms", Integer, nullable=False),
    Column("wall_ms", Integer, nullable=False),
    Column("kind", String, nullable=False),
    Column("agent_id", String),
    Column("task_id", String),
    Column("severity", String, nullable=False),
    Column("payload", Text, nullable=False),
)
presets = Table(
    "presets",
    metadata,
    Column("name", String, primary_key=True),
    Column("config", Text, nullable=False),
)


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


class Store:
    """SQLAlchemy handles Python transactions; dashboard declares the same schema in Drizzle."""

    def __init__(self, url: str):
        self.engine = create_engine(url)
        if url.startswith("sqlite"):

            @event.listens_for(self.engine, "connect")
            def configure(connection, _):
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("PRAGMA busy_timeout=10000")
                connection.execute("PRAGMA foreign_keys=ON")

        metadata.create_all(self.engine)
        if url.startswith("sqlite"):
            with self.engine.begin() as connection:
                for action in ("UPDATE", "DELETE"):
                    connection.exec_driver_sql(
                        f"CREATE TRIGGER IF NOT EXISTS events_no_{action.lower()} "
                        f"BEFORE {action} ON events BEGIN "
                        "SELECT RAISE(ABORT, 'events are immutable'); END"
                    )

    @classmethod
    def local(cls, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        return cls(f"sqlite:///{(directory / 'utopia.db').as_posix()}")

    def save(self, state: dict, pending: list[dict]):
        with self.engine.begin() as connection:
            present = connection.execute(select(runs.c.id).where(runs.c.id == state["id"])).first()
            if present:
                connection.execute(
                    runs.update().where(runs.c.id == state["id"]).values(state=encode(state))
                )
            else:
                connection.execute(runs.insert().values(id=state["id"], state=encode(state)))
            for record in pending:
                connection.execute(
                    events.insert().values({**record, "payload": encode(record["payload"])})
                )

    def load(self, run_id):
        with self.engine.connect() as connection:
            row = connection.execute(select(runs.c.state).where(runs.c.id == run_id)).first()
            return json.loads(row[0]) if row else None

    def all_runs(self):
        with self.engine.connect() as connection:
            return [json.loads(row[0]) for row in connection.execute(select(runs.c.state))]

    def event_page(
        self, run_id, after=0, limit=1000, agent_id=None, kind=None, task_id=None, severity=None
    ):
        query = select(events).where(events.c.run_id == run_id, events.c.sequence > after)
        for column, value in (
            (events.c.agent_id, agent_id),
            (events.c.kind, kind),
            (events.c.task_id, task_id),
            (events.c.severity, severity),
        ):
            if value:
                query = query.where(column == value)
        query = query.order_by(events.c.sequence).limit(limit)
        with self.engine.connect() as connection:
            return [
                dict(row, payload=json.loads(row["payload"]))
                for row in connection.execute(query).mappings()
            ]

    def put_preset(self, name, config):
        with self.engine.begin() as connection:
            connection.execute(presets.delete().where(presets.c.name == name))
            connection.execute(presets.insert().values(name=name, config=encode(config)))

    def get_presets(self):
        with self.engine.connect() as connection:
            return {
                row["name"]: json.loads(row["config"])
                for row in connection.execute(select(presets)).mappings()
            }
