import base64
import csv
import io
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from utopia.api import create_app

HEADERS = {"Authorization": "Bearer test-operator"}


def test_room_metadata_is_protected_and_excludes_file_contents(tmp_path):
    app = create_app(tmp_path, token="test-operator")
    with TestClient(app) as client:
        world = client.post("/runs", headers=HEADERS, json={}).json()
        engine = app.state.engines[world["id"]]
        with engine.transaction():
            engine.state["workspace"] = {
                "notes/a.txt": {"data": "c2VjcmV0", "size": 6, "mode": 420, "sha256": "digest"},
                "link": {"symlink": "notes/a.txt"},
            }
        path = f"/runs/{world['id']}/room"
        assert client.get(path).status_code == 401
        metadata = client.get(path, headers=HEADERS).json()
        assert not metadata["sandbox_available"]
        assert metadata["files"] == [
            {"path": "link", "symlink": "notes/a.txt"},
            {"path": "notes/a.txt", "size": 6, "mode": 420, "sha256": "digest"},
        ]
        assert metadata["limits"]["workspace_mb"] == engine.config.tools.workspace_mb
        assert client.get("/runs/missing/room", headers=HEADERS).status_code == 404


def test_live_snapshot_cursor_skips_historical_checkpoints_and_activity_is_recent(tmp_path):
    from utopia.config import WorldConfig
    from utopia.engine import Engine
    from utopia.store import Store

    store = Store.local(tmp_path)
    world = Engine.create(store, WorldConfig())
    for index in range(210):
        world.record("test.observation", {"index": index})
    with TestClient(create_app(tmp_path, store=store, token="test-operator")) as client:
        snapshot = client.get(f"/runs/{world.state['id']}", headers=HEADERS).json()
        recent = client.get(f"/runs/{world.state['id']}/events?recent=true", headers=HEADERS).json()
        assert len(recent) == 200
        assert recent[-1]["sequence"] == snapshot["event_cursor"]
        assert recent[0]["sequence"] > store.event_page(world.state["id"])[0]["sequence"]
        assert store.event_page(world.state["id"], after=snapshot["event_cursor"]) == []
        world.record("tool.invoked", {"name": "workspace"})
        world.record("environment.error", {"error": "Memory pressure"}, severity="warning")
        operator_events = client.get(
            f"/runs/{world.state['id']}/events?operator=true", headers=HEADERS
        ).json()
        assert any(record["kind"] == "environment.error" for record in operator_events)
        assert not any(record["kind"] == "tool.invoked" for record in operator_events)


@pytest.mark.parametrize("target", ["default", "override", "profile"])
def test_world_and_preset_reject_small_agent_context(tmp_path, target):
    with TestClient(create_app(tmp_path, token="test-operator")) as client:
        config = client.get("/system", headers=HEADERS).json()["defaults"]
        model = {**config["model"], "context_tokens": 8191}
        if target == "default":
            config["model"] = model
        elif target == "override":
            config["agent_model_overrides"] = {"0": model}
        else:
            config["contexts"][0]["model"] = model
        for path, payload in (("/runs", config), ("/presets", {"name": "Small", "config": config})):
            result = client.post(path, headers=HEADERS, json=payload)
            assert result.status_code == 422
            assert "at least 8,192" in result.json()["detail"][0]["msg"]


def test_legacy_world_remains_readable_but_cannot_resume_small_context(tmp_path):
    app = create_app(tmp_path, token="test-operator")
    with TestClient(app) as client:
        run = client.post("/runs", headers=HEADERS, json={}).json()
        engine = app.state.engines[run["id"]]
        with engine.transaction():
            engine.state["config"]["model"]["context_tokens"] = 4096
            for agent in engine.state["agents"].values():
                agent["model"]["context_tokens"] = 4096
            engine.state["status"] = "PAUSED"
    with TestClient(create_app(tmp_path, token="test-operator")) as client:
        assert client.get(f"/runs/{run['id']}", headers=HEADERS).status_code == 200
        result = client.post(f"/runs/{run['id']}/control/resume", headers=HEADERS)
        assert result.status_code == 503
        assert "at least 8,192" in result.json()["detail"]


def test_operator_authentication_and_host_restrictions(tmp_path):
    with TestClient(create_app(tmp_path, token="test-operator")) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/runs").status_code == 401
        assert (
            client.get("/runs", headers={**HEADERS, "host": "attacker.example"}).status_code == 403
        )
        assert client.get("/runs", headers=HEADERS).status_code == 200
        assert client.get("/openapi.json").status_code == 404


def test_run_presets_replay_repeat_and_economy_versioning(tmp_path):
    app = create_app(tmp_path, token="test-operator")
    with TestClient(app) as client:
        config = client.get("/system", headers=HEADERS).json()["defaults"]
        result = client.post("/runs", headers=HEADERS, json=config)
        assert result.status_code == 201
        identity = result.json()["id"]
        assert (
            client.post(
                "/presets", headers=HEADERS, json={"name": "Default", "config": config}
            ).status_code
            == 200
        )
        assert client.get("/presets", headers=HEADERS).json()["Default"] == config
        rules = {**config["economy"], "skip_collective_ms": 6000}
        assert (
            client.put(f"/runs/{identity}/economy", headers=HEADERS, json=rules).json()[
                "economy_version"
            ]
            == 2
        )
        replay = client.get(f"/runs/{identity}/replay", headers=HEADERS).json()
        assert replay["state"]["id"] == identity
        assert any(e["kind"] == "economy.changed" for e in replay["events"])
        repeated = client.post(f"/runs/{identity}/repeat", headers=HEADERS).json()
        assert repeated["id"] != identity and len(repeated["agents"]) == 6
        exported = client.get(f"/runs/{identity}/export/json", headers=HEADERS)
        assert json.loads(exported.content)["run"]["config"]["seed"] == 42
        exported_csv = client.get(f"/runs/{identity}/export/csv", headers=HEADERS)
        csv_records = list(csv.DictReader(io.StringIO(exported_csv.content.decode("utf-8-sig"))))
        assert len(csv_records) == len(exported.json()["events"])
        assert json.loads(csv_records[0]["payload"]) == exported.json()["events"][0]["payload"]
        import pyarrow.parquet as pq

        exported_parquet = client.get(f"/runs/{identity}/export/parquet", headers=HEADERS)
        table = pq.read_table(io.BytesIO(exported_parquet.content))
        assert table.num_rows == len(csv_records)
        assert table["sequence"].to_pylist() == [int(row["sequence"]) for row in csv_records]
        assert client.get(f"/runs/{identity}/analytics", headers=HEADERS).status_code == 200
        assert client.get("/comparison", headers=HEADERS).status_code == 200
        assert client.get(f"/runs/{identity}/training-data", headers=HEADERS).text == ""


def test_start_without_docker_fails_closed_and_does_not_terminate(tmp_path, monkeypatch):
    from utopia.sandbox import DockerSandbox, SandboxUnavailable

    async def unavailable(*args):
        raise SandboxUnavailable("Docker unavailable")

    async def manifest(*args):
        return {"hash": "model-digest", "model": "qwen3.5:2b"}

    monkeypatch.setattr(DockerSandbox, "start", unavailable)
    from utopia.providers import SharedInference

    monkeypatch.setattr(SharedInference, "manifest", manifest)
    app = create_app(tmp_path, token="test-operator")
    with TestClient(app) as client:
        identity = client.post("/runs", headers=HEADERS, json={}).json()["id"]
        result = client.post(f"/runs/{identity}/control/start", headers=HEADERS)
        assert result.status_code == 503
        state = client.get(f"/runs/{identity}", headers=HEADERS).json()
        assert state["status"] == "CREATED"
        assert all(a["state"] != "TERMINATED" for a in state["agents"])


def test_world_delete_permanently_erases_records_and_owned_files_after_restart(
    tmp_path, monkeypatch
):
    async def no_container(*args, **kwargs):
        return b""

    monkeypatch.setattr("utopia.api.DockerSandbox._docker", no_container)
    app = create_app(tmp_path, token="test-operator")
    with TestClient(app) as client:
        world = client.post("/runs", headers=HEADERS, json={}).json()
        identity = world["id"]
        other = client.post("/runs", headers=HEADERS, json={}).json()["id"]
        owned = tmp_path / "worlds" / identity / "agents"
        owned.mkdir(parents=True)
        (owned / "notebook.md").write_text("private memory")
        assert client.delete(f"/runs/{identity}", headers=HEADERS).status_code == 409
        assert (
            client.delete(f"/runs/{identity}?confirm={identity}", headers=HEADERS).status_code
            == 200
        )
        assert not owned.exists()
        assert app.state.store.load(identity) is None
        assert app.state.store.event_page(identity) == []
        assert [run["id"] for run in client.get("/runs", headers=HEADERS).json()] == [other]
        assert client.get(f"/runs/{identity}", headers=HEADERS).status_code == 404
        assert client.get("/runs?deleted=true", headers=HEADERS).json() == []
    with TestClient(create_app(tmp_path, token="test-operator")) as client:
        assert client.post(f"/runs/{identity}/restore", headers=HEADERS).status_code == 404
        assert client.get(f"/runs/{identity}/events", headers=HEADERS).status_code == 404
        assert len(client.get(f"/runs/{other}/events", headers=HEADERS).json()) > 0


def test_running_world_must_be_paused_before_delete(tmp_path, monkeypatch):
    async def no_container(*args, **kwargs):
        return b""

    monkeypatch.setattr("utopia.api.DockerSandbox._docker", no_container)
    app = create_app(tmp_path, token="test-operator")
    with TestClient(app) as client:
        identity = client.post("/runs", headers=HEADERS, json={}).json()["id"]
        app.state.engines[identity].control("start")
        assert (
            client.delete(f"/runs/{identity}?confirm={identity}", headers=HEADERS).status_code
            == 409
        )
        assert client.get(f"/runs/{identity}", headers=HEADERS).json()["status"] == "RUNNING"
        client.post(f"/runs/{identity}/control/pause", headers=HEADERS)
        assert (
            client.delete(f"/runs/{identity}?confirm={identity}", headers=HEADERS).status_code
            == 200
        )


def test_sandbox_snapshot_opens_folder_without_host_execution_or_path_escape(tmp_path, monkeypatch):
    opened = []
    monkeypatch.setattr("utopia.api.sys.platform", "win32")
    monkeypatch.setattr("utopia.api.os.startfile", lambda path: opened.append(path), raising=False)
    app = create_app(tmp_path, token="test-operator")
    with TestClient(app) as client:
        world = client.post("/runs", headers=HEADERS, json={}).json()
        identity = world["agents"][0]["id"]
        engine = app.state.engines[world["id"]]
        engine.state["workspace"] = {
            "notes/math.md": {"data": base64.b64encode(b"# Notes").decode(), "mode": 0o644},
            "external-link": {"symlink": "../../operator.token"},
        }
        endpoint = f"/runs/{world['id']}/agents/{identity}/sandbox/open"
        response = client.post(endpoint, headers=HEADERS)
        assert response.status_code == 200
        assert response.json()["kind"] == "workspace-snapshot"
        assert len(opened) == 1
        assert (Path(opened[0]) / "notes/math.md").read_text() == "# Notes"
        assert "inspection copy only" in (Path(opened[0]) / "external-link").read_text()
        assert not (Path(opened[0]) / "external-link").is_symlink()
        assert (
            client.post(endpoint + "?path=../../../../operator.token", headers=HEADERS).status_code
            == 409
        )
        engine.state["workspace"]["../../../../escaped.txt"] = {"data": "Yg==", "mode": 0o644}
        assert client.post(endpoint, headers=HEADERS).status_code == 409
        assert not (tmp_path / "escaped.txt").exists()


def test_delete_removes_orphan_container_even_before_manifest_commit(tmp_path, monkeypatch):
    calls = []

    async def docker(self, *args, **kwargs):
        calls.append((self.name, args))
        return b"orphan-id" if args[0] == "ps" else b""

    monkeypatch.setattr(
        "utopia.api.shutil.which", lambda name: "docker" if name == "docker" else None
    )
    monkeypatch.setattr("utopia.api.DockerSandbox._docker", docker)
    with TestClient(create_app(tmp_path, token="test-operator")) as client:
        world = client.post("/runs", headers=HEADERS, json={}).json()["id"]
        assert client.delete(f"/runs/{world}?confirm={world}", headers=HEADERS).status_code == 200
        assert (f"utopia-{world}", ("rm", "-f", f"utopia-{world}")) in calls
        assert calls[-1][1][:3] == ("volume", "ls", "-q")


def test_delete_removes_orphan_workspace_volume_without_a_container(tmp_path, monkeypatch):
    calls = []

    async def docker(self, *args, **kwargs):
        calls.append(args)
        return self.volume.encode() if args[:2] == ("volume", "ls") else b""

    monkeypatch.setattr("utopia.api.shutil.which", lambda _: "docker")
    monkeypatch.setattr("utopia.api.DockerSandbox._docker", docker)
    with TestClient(create_app(tmp_path, token="test-operator")) as client:
        world = client.post("/runs", headers=HEADERS, json={}).json()["id"]
        assert client.delete(f"/runs/{world}?confirm={world}", headers=HEADERS).status_code == 200
        assert calls[-1] == ("volume", "rm", f"utopia-{world}-workspace")


def test_docker_startup_error_with_zero_cli_exit_is_not_ready(tmp_path, monkeypatch):
    class Process:
        returncode = 0

        async def communicate(self):
            return b"\n", b"Error response from daemon: Docker Desktop is unable to start\n"

    async def create_process(*args, **kwargs):
        return Process()

    monkeypatch.setattr("utopia.api.shutil.which", lambda _: "docker.exe")
    monkeypatch.setattr("utopia.api.asyncio.create_subprocess_exec", create_process)
    with TestClient(create_app(tmp_path, token="test-operator")) as client:
        result = client.get("/system", headers=HEADERS).json()
        assert result["docker_installed"]
        assert not result["docker_ready"]
        assert "unable to start" in result["docker_error"]
