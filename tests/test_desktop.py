import io
import json
import sys
from types import SimpleNamespace

import httpx
import pytest
from textual.widgets import Button, Label, LoadingIndicator, RichLog, TextArea

from gui.control import ControlApp
from gui.desktop import LauncherMonitor, ProcessSupervisor, shutdown_wsl, validate_config
from gui.maintenance import MaintenanceApp


def test_launcher_streams_new_service_errors_and_world_events(tmp_path, monkeypatch):
    from rich.console import Console

    monkeypatch.setattr("gui.desktop.ROOT", tmp_path)
    output = io.StringIO()
    supervisor = ProcessSupervisor(validate_config({"data_dir": str(tmp_path)}))
    monitor = LauncherMonitor(supervisor, Console(file=output, width=200))
    world = {"id": "world-id", "name": "Test world", "status": "RUNNING"}
    requests = []

    def request(method, path, **kwargs):
        requests.append((path, kwargs))
        if path == "/runs":
            return [world]
        if path == "/runs/world-id":
            return {"event_cursor": 42}
        return [
            {
                "sequence": 43,
                "kind": "runtime.recovery-started",
                "severity": "warning",
                "payload": {"error": "Docker host memory exhausted"},
            }
        ]

    monkeypatch.setattr("gui.desktop.OperatorApi", lambda config: SimpleNamespace(request=request))
    monitor.poll()
    (tmp_path / "dist" / "logs" / "backend.log").write_text(
        "normal tool activity\nERROR: service connection failed\n", encoding="utf-8"
    )
    monitor.poll()
    assert "normal tool activity" not in output.getvalue()
    assert "service connection failed" in output.getvalue()
    assert "Docker host memory exhausted" in output.getvalue()
    assert requests[-1][1]["params"] == {"after": 42, "operator": "true"}
    assert monitor.cursors["world-id"] == 43


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
        app.query_one("#model-name").focus()
        await pilot.press("ctrl+c")
        assert app._exit


async def test_maintenance_restores_and_persists_theme(tmp_path):
    path = tmp_path / "desktop.json"
    path.write_text(
        json.dumps({"data_dir": str(tmp_path), "maintenance_theme": "textual-light"}),
        encoding="utf-8",
    )
    app = MaintenanceApp(path)
    async with app.run_test() as pilot:
        assert app.theme == "textual-light"
        app.theme = "textual-dark"
        await pilot.pause()
        assert json.loads(path.read_text(encoding="utf-8"))["maintenance_theme"] == "textual-dark"
        themes = set(app.available_themes)
        assert len(themes) > 2
        visited = set()
        for _ in themes:
            await pilot.press("ctrl+t")
            visited.add(app.theme)
        assert visited == themes
        assert app.theme == "textual-dark"
        assert json.loads(path.read_text(encoding="utf-8"))["maintenance_theme"] == app.theme


async def test_maintenance_ctrl_c_copies_selected_output_without_exiting(tmp_path):
    path = tmp_path / "desktop.json"
    path.write_text(json.dumps({"data_dir": str(tmp_path)}), encoding="utf-8")
    app = MaintenanceApp(path)
    async with app.run_test() as pilot:
        app._write("copyable output")
        log = app.query_one("#maintenance-log", RichLog)
        app.screen._select_all_in_widget(log)
        assert app.screen.get_selected_text() == "copyable output"
        await pilot.press("ctrl+c")
        assert app.clipboard == "copyable output"
        assert not app._exit
        app.screen.clear_selection()
        await pilot.press("ctrl+c")
        assert app._exit


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
            "gui.maintenance.subprocess.Popen",
            lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("process failure")),
        )
        app._run_steps("Checking pip", [[sys.executable, "-m", "pip", "--version"]])
        await pilot.pause(0.3)
        assert not app.busy


async def test_maintenance_diagnostics_keep_controls_and_log_visible(tmp_path):
    path = tmp_path / "desktop.json"
    path.write_text(json.dumps({"data_dir": str(tmp_path)}), encoding="utf-8")
    app = MaintenanceApp(path)
    async with app.run_test(size=(80, 24)) as pilot:
        app._set_health("Docker output\n" * 30)
        await pilot.pause()
        assert app.query_one("#diagnostics", Button).region.bottom < 24
        assert app.query_one("#maintenance-log", RichLog).region.height >= 4


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
    monkeypatch.setattr("gui.desktop.httpx.get", lambda *a, **kw: response)
    supervisor = ProcessSupervisor(validate_config({"data_dir": str(tmp_path)}))
    monkeypatch.setattr(supervisor, "_spawn", lambda *a: pytest.fail("External server adopted"))
    monkeypatch.setattr(
        "gui.desktop.httpx.Client", lambda *a, **kw: pytest.fail("External server stopped")
    )
    supervisor.start_ollama()
    supervisor.stop_services()
    assert supervisor.processes == {}
    assert supervisor.status()["ollama"] == "external (not managed)"


@pytest.mark.parametrize("already_running", [False, True])
def test_docker_start_waits_for_engine_and_retains_running_desktop(
    tmp_path, monkeypatch, already_running
):
    supervisor = ProcessSupervisor(validate_config({"data_dir": str(tmp_path)}))
    commands = []
    probes = iter([0] if already_running else [1, 1, 0])
    monkeypatch.setattr("gui.desktop.os.name", "nt")
    monkeypatch.setattr("gui.desktop.shutil_which", lambda name: name)
    monkeypatch.setattr("gui.desktop.time.sleep", lambda _: None)

    def run(command, **options):
        commands.append(command)
        assert options["timeout"] > 0
        assert options["creationflags"]
        return SimpleNamespace(
            returncode=next(probes) if command[1] == "info" else 0,
            stderr="engine unavailable",
            stdout="",
        )

    monkeypatch.setattr("gui.desktop.subprocess.run", run)
    supervisor.start_docker()
    starts = [command for command in commands if command[1:3] == ["desktop", "start"]]
    assert len(starts) == (0 if already_running else 1)
    assert commands[-1][1] == "info"


def test_launcher_starts_docker_before_backend(tmp_path, monkeypatch):
    next_cli = tmp_path / "dist/gui/dashboard/node_modules/next/dist/bin/next"
    next_cli.parent.mkdir(parents=True)
    next_cli.touch()
    monkeypatch.setattr("gui.desktop.ROOT", tmp_path)
    monkeypatch.setattr("gui.desktop.shutil_which", lambda name: name)
    supervisor = ProcessSupervisor(
        validate_config({"data_dir": str(tmp_path), "manage_ollama": False})
    )
    actions = []
    monkeypatch.setattr(supervisor, "start_docker", lambda: actions.append("docker"))

    def spawn(name, command, cwd, env):
        assert env["OPENBLAS_NUM_THREADS"] == "1"
        actions.append(name)

    monkeypatch.setattr(supervisor, "_spawn", spawn)
    monkeypatch.setattr(supervisor, "wait_ready", lambda **kw: None)
    supervisor.start_services()
    assert actions == ["docker", "backend", "dashboard"]


@pytest.mark.parametrize("start_fails", [False, True])
def test_docker_start_reports_failure_or_bounded_readiness_timeout(
    tmp_path, monkeypatch, start_fails
):
    supervisor = ProcessSupervisor(validate_config({"data_dir": str(tmp_path)}))
    times = iter([0, 0, 2])
    monkeypatch.setattr("gui.desktop.os.name", "nt")
    monkeypatch.setattr("gui.desktop.shutil_which", lambda name: name)
    monkeypatch.setattr("gui.desktop.time.monotonic", lambda: next(times))
    monkeypatch.setattr("gui.desktop.time.sleep", lambda _: None)
    monkeypatch.setattr(
        "gui.desktop.subprocess.run",
        lambda command, **kw: SimpleNamespace(
            returncode=1 if command[1] == "info" or start_fails else 0,
            stderr="engine unavailable",
            stdout="",
        ),
    )
    error = RuntimeError if start_fails else TimeoutError
    with pytest.raises(error, match="engine unavailable"):
        supervisor.start_docker(timeout=1)


def test_process_cap_uses_full_startup_budget(tmp_path, monkeypatch):
    supervisor = ProcessSupervisor(validate_config({"data_dir": str(tmp_path)}))
    caps = []
    monkeypatch.setattr("gui.desktop.os.name", "nt")
    monkeypatch.setattr(
        "hooks.resources.host_memory",
        lambda: {"total_mb": 16024, "available_mb": 900, "commit_available_mb": 1900},
    )
    monkeypatch.setattr(
        "gui.desktop.WindowsMemoryJob",
        lambda cap: caps.append(cap) or SimpleNamespace(spawn=lambda *a, **kw: "spawned"),
    )
    assert supervisor.spawn_limited(["service"]) == "spawned"
    assert caps == [900]


def test_desktop_shutdown_stops_wsl_with_bounded_wait(monkeypatch):
    from rich.console import Console

    commands = []
    monkeypatch.setattr("gui.desktop.os.name", "nt")
    monkeypatch.setattr(
        "gui.desktop.subprocess.run",
        lambda command, **options: (
            commands.append((command, options)) or SimpleNamespace(returncode=0)
        ),
    )
    output = io.StringIO()
    shutdown_wsl(Console(file=output))
    assert commands[-1][0] == ["wsl.exe", "--shutdown"]
    assert commands[-1][1]["timeout"] == 30
    assert any("com.docker.backend.exe" in command for command, _ in commands[:-1])
    assert "WSL stopped" in output.getvalue()


def test_owned_ollama_receives_process_local_cache_budget(tmp_path, monkeypatch):
    response = httpx.Response(200, request=httpx.Request("GET", "http://localhost/api/tags"))
    responses = iter([httpx.ConnectError("Not running"), response])

    def get(*args, **kwargs):
        value = next(responses)
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr("gui.desktop.httpx.get", get)
    monkeypatch.setattr("gui.desktop.shutil_which", lambda _: "ollama")
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
