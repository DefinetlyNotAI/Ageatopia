import base64
import json
from pathlib import Path

from fastapi.testclient import TestClient

from utopia.api import create_app

HEADERS = {"Authorization": "Bearer test-operator"}


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
