import json

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
