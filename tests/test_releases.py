import json
import threading

import pytest
from packaging.utils import parse_wheel_filename
from textual.widgets import LoadingIndicator

from gui.releases import ReleaseApp, ReleaseBuilder, validate_version, wheel_name


@pytest.mark.parametrize("version", ["0.1.0", "1.2.3-beta", "2.0.0-rc1"])
def test_release_version_accepts_exact_dashboard_version(version):
    assert validate_version(version, version) == version


@pytest.mark.parametrize("version", ["", "1.2", "v1.2.3", "1.2.3-", "../1.2.3", "1.2.3/a"])
def test_release_version_rejects_invalid_input(version):
    with pytest.raises(ValueError, match="version"):
        validate_version(version, version)


def test_release_version_rejects_dashboard_mismatch():
    with pytest.raises(ValueError, match="package.json"):
        validate_version("1.2.3", "0.1.0")


def release_root(tmp_path):
    dashboard = tmp_path / "src/gui/dashboard"
    dashboard.mkdir(parents=True)
    (dashboard / "package.json").write_text(json.dumps({"version": "0.1.0"}))
    return tmp_path


def test_preflight_preserves_even_empty_existing_release(tmp_path):
    root = release_root(tmp_path)
    destination = root / "dist/releases/0.1.0"
    destination.mkdir(parents=True)
    builder = ReleaseBuilder(root, "0.1.0")
    with pytest.raises(FileExistsError, match="already exists"):
        builder.preflight()
    assert destination.is_dir()
    assert list(destination.iterdir()) == []


def test_preflight_preserves_dirty_staging(tmp_path):
    root = release_root(tmp_path)
    staging = root / "dist/releases/.staging"
    staging.mkdir(parents=True)
    marker = staging / "another-build.txt"
    marker.write_text("owned by another build")
    builder = ReleaseBuilder(root, "0.1.0")
    with pytest.raises(FileExistsError, match="staging"):
        builder.preflight()
    assert marker.read_text() == "owned by another build"


def test_preflight_rejects_missing_native_build_before_creating_staging(tmp_path):
    builder = ReleaseBuilder(release_root(tmp_path), "0.1.0")
    with pytest.raises(FileNotFoundError, match="native host"):
        builder.preflight()
    assert not builder.workspace.parent.exists()


def test_failed_build_removes_only_owned_staging(tmp_path, monkeypatch):
    root = release_root(tmp_path)
    builder = ReleaseBuilder(root, "0.1.0")
    monkeypatch.setattr(builder, "preflight", lambda: None)
    monkeypatch.setattr(builder, "build_wheel", lambda: None)
    monkeypatch.setattr(builder, "build_sandbox", lambda: None)

    def fail():
        (builder.workspace / "partial.txt").write_text("partial")
        raise RuntimeError("compiler failed")

    monkeypatch.setattr(builder, "build_dashboard", fail)
    with pytest.raises(RuntimeError, match="compiler failed"):
        builder.build()
    assert not builder.destination.exists()
    assert not builder.workspace.exists()


def test_publish_cannot_replace_a_destination_created_during_build(tmp_path):
    root = release_root(tmp_path)
    builder = ReleaseBuilder(root, "0.1.0")
    builder.artifacts.mkdir(parents=True)
    builder.destination.mkdir()
    marker = builder.destination / "keep.txt"
    marker.write_text("existing release")
    with pytest.raises(FileExistsError):
        builder.publish()
    assert marker.read_text() == "existing release"
    assert builder.artifacts.exists()


def test_cancelled_release_is_not_published_after_validation(tmp_path):
    builder = ReleaseBuilder(release_root(tmp_path), "0.1.0")
    builder.artifacts.mkdir(parents=True)
    builder.cancelled.set()
    with pytest.raises(RuntimeError, match="cancelled"):
        builder.publish()
    assert not builder.destination.exists()
    assert builder.artifacts.exists()


@pytest.mark.parametrize("version", ["0.1.0", "1.2.3-beta", "2.0.0-rc1", "2.0.0-preview"])
def test_wheel_filename_is_installable_for_stable_and_suffix_versions(version):
    name, _, _, _ = parse_wheel_filename(wheel_name(version))
    assert name == "ageatopia"


async def test_release_tui_uses_existing_output_style_and_cancels(tmp_path, monkeypatch):
    builder = ReleaseBuilder(release_root(tmp_path), "0.1.0")
    config = tmp_path / ".data/desktop.json"
    config.parent.mkdir()
    config.write_text(json.dumps({"maintenance_theme": "tokyo-night", "backend_port": 8000}))
    finished = threading.Event()

    def wait_for_cancel():
        builder.report("Native x64 and Python build tools verified", "green")
        builder.report("Building · Standalone Windows application", "cyan")
        builder.cancelled.wait(timeout=10)
        finished.set()
        raise RuntimeError("Release cancelled")

    monkeypatch.setattr(builder, "build", wait_for_cancel)
    app = ReleaseApp(builder)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        assert app.screen.has_class("compact")
        assert app.query_one("#maintenance-log").border_title == "Release output"
        assert app.query_one("#busy", LoadingIndicator).display
        await pilot.press("ctrl+t")
        await pilot.pause()
        saved = json.loads(config.read_text())
        assert saved["maintenance_theme"] == app.theme
        assert saved["maintenance_theme"] != "tokyo-night"
        assert saved["backend_port"] == 8000
        await pilot.press("ctrl+c")
        await pilot.pause()
    assert finished.is_set()
    assert app.failure == "Release cancelled"
