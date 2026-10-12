import importlib
import io
import json
import os
import sys
from types import SimpleNamespace

import pytest

from backend import paths as backend_paths
from gui import desktop, release_entry


def _use_portable_layout(monkeypatch, app_root, data_root):
    for name in ("ROOT", "DEFAULT_CONFIG", "PORTABLE_MODE", "USER_DATA_ROOT"):
        monkeypatch.setattr(desktop, name, getattr(desktop, name))
    desktop.configure_portable_runtime(app_root, data_root)


def _write_release_assets(app_root):
    for relative in (
        "native/ageatopia.exe",
        "native/WebView2Loader.dll",
        "dashboard/server.js",
        "dashboard/.next/static",
        "runtime/node.exe",
        "sandbox/Dockerfile",
    ):
        path = app_root / relative
        if path.suffix:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("asset", encoding="utf-8")
        else:
            path.mkdir(parents=True, exist_ok=True)


def test_release_runtime_uses_executable_root_and_local_appdata(tmp_path, monkeypatch):
    app_root = tmp_path / "install" / "Ageatopia"
    local_app_data = tmp_path / "profile" / "AppData" / "Local"
    _write_release_assets(app_root)
    _use_portable_layout(monkeypatch, app_root, local_app_data / "Ageatopia")

    release_entry.configure_runtime(
        executable=app_root / "Ageatopia.exe",
        local_app_data=local_app_data,
    )

    assert desktop.ROOT == app_root
    assert desktop.DEFAULT_CONFIG == local_app_data / "Ageatopia" / "desktop.json"
    assert desktop.default_config()["data_dir"] == str(local_app_data / "Ageatopia")
    loaded = desktop.load_config()
    assert loaded["data_dir"] == str(local_app_data / "Ageatopia")
    assert loaded["config_path"] == str(local_app_data / "Ageatopia" / "desktop.json")
    overridden = desktop.validate_config({"data_dir": str(app_root / "writable-data")})
    assert overridden["data_dir"] == str(app_root / "writable-data")
    assert desktop.native_command("http://127.0.0.1:3000", local_app_data)[0] == str(
        app_root / "native" / "ageatopia.exe"
    )


def test_portable_outputs_stay_in_user_data_not_the_install_root(tmp_path, monkeypatch):
    install_root = tmp_path / "Program Files" / "Ageatopia"
    output_root = tmp_path / "LocalAppData" / "Ageatopia" / "outputs"
    monkeypatch.setenv("AGEATOPIA_OUTPUT_DIR", str(output_root))
    monkeypatch.setattr(backend_paths, "DIST", backend_paths.DIST)
    importlib.reload(backend_paths)

    output = backend_paths.output_path(output_root / "exports" / "run.parquet")

    assert backend_paths.DIST == output_root.resolve()
    assert output == output_root.resolve() / "exports" / "run.parquet"
    assert output.parent.is_dir()
    assert not (install_root / "dist").exists()
    with pytest.raises(ValueError, match="inside the repository's dist directory"):
        backend_paths.output_path(install_root / "dist" / "exports" / "run.parquet")


def test_configure_runtime_sets_portable_output_root_before_backend_startup(tmp_path, monkeypatch):
    app_root = tmp_path / "Ageatopia"
    local_app_data = tmp_path / "User" / "AppData" / "Local"
    _use_portable_layout(monkeypatch, app_root, local_app_data / "Ageatopia")
    monkeypatch.delenv("AGEATOPIA_OUTPUT_DIR", raising=False)

    release_entry.configure_runtime(
        executable=app_root / "Ageatopia.exe",
        local_app_data=local_app_data,
    )

    expected = str(local_app_data / "Ageatopia" / "outputs")
    assert os.environ["AGEATOPIA_OUTPUT_DIR"] == expected
    assert desktop.service_environment()["AGEATOPIA_OUTPUT_DIR"] == expected


def test_backend_child_preserves_configured_user_output_root(tmp_path, monkeypatch):
    app_root = tmp_path / "Ageatopia"
    local_app_data = tmp_path / "User" / "AppData" / "Local"
    configured_outputs = tmp_path / "Custom User Data" / "outputs"
    _use_portable_layout(monkeypatch, app_root, local_app_data / "Ageatopia")
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))
    monkeypatch.setenv("AGEATOPIA_OUTPUT_DIR", str(configured_outputs))
    monkeypatch.setattr(release_entry.sys, "executable", str(app_root / "Ageatopia.exe"))
    monkeypatch.setattr(release_entry.sys, "argv", ["Ageatopia.exe", "--backend"])
    monkeypatch.setattr(release_entry, "is_compiled", lambda: True)
    backend_ports = []
    monkeypatch.setattr(release_entry, "run_backend", backend_ports.append)

    assert release_entry.main() == 0

    assert backend_ports == [8000]
    assert os.environ["AGEATOPIA_OUTPUT_DIR"] == str(configured_outputs)


def test_release_entry_encodes_unicode_self_test_output_as_utf8(monkeypatch):
    stdout = io.TextIOWrapper(io.BytesIO(), encoding="ascii")
    stderr = io.TextIOWrapper(io.BytesIO(), encoding="ascii")
    monkeypatch.setattr(release_entry.sys, "stdout", stdout)
    monkeypatch.setattr(release_entry.sys, "stderr", stderr)
    monkeypatch.setattr(release_entry.sys, "argv", ["Ageatopia.exe", "--self-test"])
    monkeypatch.setattr(release_entry, "is_compiled", lambda: True)
    monkeypatch.setattr(release_entry, "configure_runtime", lambda: None)
    monkeypatch.setattr(release_entry, "self_test", lambda: {"ok": True, "path": "雪"})

    assert release_entry.main() == 0
    stdout.flush()

    assert stdout.encoding.lower().replace("-", "") == "utf8"
    assert stderr.encoding.lower().replace("-", "") == "utf8"
    assert "雪" in stdout.buffer.getvalue().decode("utf-8")


def test_packaged_services_route_backend_and_dashboard_through_bundle(tmp_path, monkeypatch):
    app_root = tmp_path / "Ageatopia"
    data_root = tmp_path / "LocalAppData" / "Ageatopia"
    _use_portable_layout(monkeypatch, app_root, data_root)
    dashboard = app_root / "dashboard"
    node = app_root / "runtime" / "node.exe"
    (dashboard / ".next" / "static").mkdir(parents=True)
    (dashboard / "server.js").write_text("", encoding="utf-8")
    node.parent.mkdir(parents=True)
    node.write_text("", encoding="utf-8")
    monkeypatch.setenv("PYTHONPATH", "inherited-python-path")
    monkeypatch.setenv("PYTHONHOME", "inherited-python-home")

    custom_data = tmp_path / "Custom User Data"
    supervisor = desktop.ProcessSupervisor(
        desktop.validate_config({"data_dir": str(custom_data), "manage_ollama": False})
    )
    commands = []
    monkeypatch.setattr(supervisor, "start_docker", lambda: None)
    monkeypatch.setattr(
        supervisor,
        "_spawn",
        lambda name, command, cwd, env: commands.append((name, command, cwd, env)),
    )
    monkeypatch.setattr(supervisor, "wait_ready", lambda **kwargs: None)

    supervisor.start_services()

    backend, dashboard_process = commands
    assert backend[0:2] == (
        "backend",
        [str(app_root / "Ageatopia.exe"), "--backend", "--port", "8000"],
    )
    assert dashboard_process[0] == "dashboard"
    assert dashboard_process[1] == [str(node), "server.js"]
    assert dashboard_process[2] == dashboard
    assert dashboard_process[3]["HOSTNAME"] == "127.0.0.1"
    assert dashboard_process[3]["PORT"] == "3000"
    for _, _, _, env in commands:
        assert "PYTHONPATH" not in env
        assert "PYTHONHOME" not in env
    assert backend[3]["AGEATOPIA_OUTPUT_DIR"] == str(custom_data / "outputs")


def test_source_services_use_standalone_dashboard_when_available(tmp_path, monkeypatch):
    dashboard = tmp_path / "dist" / "gui" / "dashboard"
    dashboard.mkdir(parents=True)
    (dashboard / "server.js").write_text("", encoding="utf-8")
    monkeypatch.setattr(desktop, "ROOT", tmp_path)
    monkeypatch.setattr(desktop, "PORTABLE_MODE", False)
    monkeypatch.setattr(desktop, "USER_DATA_ROOT", None)
    monkeypatch.setattr("gui.desktop.shutil_which", lambda name: name)

    supervisor = desktop.ProcessSupervisor(
        desktop.validate_config({"data_dir": str(tmp_path / ".data"), "manage_ollama": False})
    )
    commands = []
    monkeypatch.setattr(supervisor, "start_docker", lambda: False)
    monkeypatch.setattr(
        supervisor,
        "_spawn",
        lambda name, command, cwd, env: commands.append((name, command, cwd, env)),
    )
    monkeypatch.setattr(supervisor, "wait_ready", lambda **kwargs: None)

    supervisor.start_services()

    assert commands[0][1] == [
        sys.executable,
        "-m",
        "backend.cli",
        "--port",
        "8000",
    ]
    assert commands[1][1] == ["node", "server.js"]
    assert commands[1][2] == dashboard
    assert commands[1][3]["HOSTNAME"] == "127.0.0.1"
    assert commands[1][3]["PORT"] == "3000"


def test_packaged_missing_docker_and_ollama_still_starts_observatory(tmp_path, monkeypatch):
    app_root = tmp_path / "Ageatopia"
    data_root = tmp_path / "LocalAppData" / "Ageatopia"
    _use_portable_layout(monkeypatch, app_root, data_root)
    dashboard = app_root / "dashboard"
    (dashboard / ".next" / "static").mkdir(parents=True)
    (dashboard / "server.js").write_text("", encoding="utf-8")
    node = app_root / "runtime" / "node.exe"
    node.parent.mkdir(parents=True)
    node.write_text("", encoding="utf-8")

    supervisor = desktop.ProcessSupervisor(desktop.validate_config({}))
    commands = []
    monkeypatch.setattr(
        supervisor,
        "start_docker",
        lambda: (_ for _ in ()).throw(RuntimeError("Docker is not installed")),
    )
    monkeypatch.setattr(
        supervisor,
        "start_ollama",
        lambda: (_ for _ in ()).throw(RuntimeError("Ollama is not installed")),
    )
    monkeypatch.setattr(
        supervisor,
        "_spawn",
        lambda name, command, cwd, env: commands.append(name),
    )
    monkeypatch.setattr(supervisor, "wait_ready", lambda **kwargs: None)

    supervisor.start_services()

    assert commands == ["backend", "dashboard"]
    log_path = data_root / "logs" / "desktop.jsonl"
    log = log_path.read_text(encoding="utf-8")
    assert "Docker is not installed" in log
    assert "Ollama is not installed" in log


def test_packaged_builds_sandbox_image_from_bundled_context(tmp_path, monkeypatch):
    app_root = tmp_path / "Ageatopia"
    data_root = tmp_path / "LocalAppData" / "Ageatopia"
    _use_portable_layout(monkeypatch, app_root, data_root)
    dockerfile = app_root / "sandbox" / "Dockerfile"
    dockerfile.parent.mkdir(parents=True)
    dockerfile.write_text("FROM scratch", encoding="utf-8")
    monkeypatch.setattr("gui.desktop.shutil_which", lambda name: "docker")
    outcomes = iter(
        (
            SimpleNamespace(returncode=1, stderr="missing", stdout=""),
            SimpleNamespace(returncode=0, stderr="", stdout="built"),
        )
    )
    commands = []

    def run(command, **options):
        commands.append(command)
        return next(outcomes)

    monkeypatch.setattr("gui.desktop.subprocess.run", run)
    supervisor = desktop.ProcessSupervisor(desktop.validate_config({}))

    supervisor.ensure_sandbox_image()

    assert commands == [
        ["docker", "image", "inspect", "ageatopia-world:1"],
        ["docker", "build", "-t", "ageatopia-world:1", str(app_root / "sandbox")],
    ]


def test_release_self_test_checks_compiled_imports_and_bundle_assets(tmp_path):
    _write_release_assets(tmp_path)

    report = release_entry.self_test(tmp_path)

    assert report["ok"] is True
    assert all(report["imports"].values())
    assert all(report["assets"].values())
    assert json.loads(json.dumps(report))["ok"] is True
