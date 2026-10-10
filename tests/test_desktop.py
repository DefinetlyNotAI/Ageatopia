import json
import sys
from types import SimpleNamespace

import httpx
import pytest
from textual.widgets import Button, Label, LoadingIndicator, TextArea

from utopia.control import ControlApp
from utopia.desktop import ProcessSupervisor, validate_config
from utopia.maintenance import MaintenanceApp


async def test_maintenance_command_palette_and_exit(tmp_path):
    path = tmp_path / "desktop.json"
    path.write_text(json.dumps({"data_dir": str(tmp_path)}), encoding="utf-8")
    app = MaintenanceApp(path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("ctrl+p")
        await pilot.pause()
        assert len(app.screen_stack) == 2
        await pilot.press("escape")
        app._write("Successfully installed example [literal]")
        await pilot.press("ctrl+c")


@pytest.mark.parametrize(
    "arguments,failed", [(["-m", "pip", "--version"], False), (["--invalid-option"], True)]
)
async def test_maintenance_real_command_finishes(tmp_path, arguments, failed):
    path = tmp_path / "desktop.json"
    path.write_text(json.dumps({"data_dir": str(tmp_path)}), encoding="utf-8")
    app = MaintenanceApp(path)
    async with app.run_test(size=(80, 24)) as pilot:
        app._run_steps("Checking pip", [[sys.executable, *arguments]])
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert not app.busy
        assert not app.query_one("#busy", LoadingIndicator).display
        assert all(not button.disabled for button in app.query(Button))
        assert ("failed" if failed else "completed") in str(
            app.query_one("#activity", Label).render()
        )


async def test_maintenance_worker_failure_resets_busy(tmp_path, monkeypatch):
    path = tmp_path / "desktop.json"
    path.write_text(json.dumps({"data_dir": str(tmp_path)}), encoding="utf-8")
    app = MaintenanceApp(path)
    async with app.run_test() as pilot:
        monkeypatch.setattr(
            "utopia.maintenance.subprocess.Popen",
            lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("process failure")),
        )
        app._run_steps("Checking pip", [[sys.executable, "-m", "pip", "--version"]])
        await pilot.pause(0.3)
        assert not app.busy


async def test_control_room_saves_model_server_settings(tmp_path):
    path = tmp_path / "desktop.json"
    values = {"data_dir": str(tmp_path), "manage_ollama": False, "ollama_cache_ram_mb": 512}
    path.write_text(json.dumps(values), encoding="utf-8")
    app = ControlApp(path)
    async with app.run_test(size=(80, 24)):
        app.query_one("#app-config", TextArea).load_text(json.dumps(values))
        app._save_app_config()
        saved = json.loads(path.read_text(encoding="utf-8"))
        assert saved["manage_ollama"] is False and saved["ollama_cache_ram_mb"] == 512
        assert app.config["manage_ollama"] is False


@pytest.mark.parametrize("budget", [-1, 16385, True, "1024"])
def test_ollama_cache_budget_rejects_invalid_values(budget):
    with pytest.raises(ValueError, match="ollama_cache_ram_mb"):
        validate_config({"ollama_cache_ram_mb": budget})


def test_managed_ollama_port_must_not_overlap_application():
    with pytest.raises(ValueError, match="Ollama port"):
        validate_config({"backend_port": 11434})


def test_external_ollama_is_never_adopted_or_stopped(tmp_path, monkeypatch):
    response = httpx.Response(200, request=httpx.Request("GET", "http://localhost/api/tags"))
    monkeypatch.setattr("utopia.desktop.httpx.get", lambda *a, **kw: response)
    supervisor = ProcessSupervisor(validate_config({"data_dir": str(tmp_path)}))
    monkeypatch.setattr(supervisor, "_spawn", lambda *a: pytest.fail("External server adopted"))
    monkeypatch.setattr(
        "utopia.desktop.httpx.Client", lambda *a, **kw: pytest.fail("External server stopped")
    )
    supervisor.start_ollama()
    supervisor.stop_services()
    assert supervisor.processes == {}
    assert supervisor.status()["ollama"] == "external (not managed)"


def test_owned_ollama_receives_process_local_cache_budget(tmp_path, monkeypatch):
    response = httpx.Response(200, request=httpx.Request("GET", "http://localhost/api/tags"))
    responses = iter([httpx.ConnectError("Not running"), response])

    def get(*args, **kwargs):
        value = next(responses)
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr("utopia.desktop.httpx.get", get)
    monkeypatch.setattr("utopia.desktop.shutil_which", lambda _: "ollama")
    supervisor = ProcessSupervisor(
        validate_config({"data_dir": str(tmp_path), "ollama_cache_ram_mb": 512})
    )
    commands = []
    monkeypatch.setattr(
        supervisor,
        "_spawn",
        lambda *args: commands.append(args) or SimpleNamespace(poll=lambda: None),
    )
    supervisor.start_ollama()
    name, command, _, env = commands[0]
    assert name == "ollama" and command == ["ollama", "serve"]
    assert env["LLAMA_ARG_CACHE_RAM"] == "512"
    assert env["OLLAMA_HOST"] == "127.0.0.1:11434"
    assert not supervisor.external_ollama
