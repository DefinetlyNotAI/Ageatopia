"""Transactional Windows releases, presented in the existing maintenance TUI."""

import argparse
import importlib.metadata
import json
import logging
import os
import queue
import re
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time
import tomllib
import urllib.request
import zipfile
from pathlib import Path

from packaging.requirements import Requirement
from packaging.version import InvalidVersion, Version
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import Footer, Header, Label, LoadingIndicator, Static

from gui.maintenance import MaintenanceApp, SelectableRichLog

REPOSITORY = Path(__file__).resolve().parents[2]
VERSION_PATTERN = r"[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9][A-Za-z0-9.-]*)?"


def validate_version(version, dashboard_version):
    if not re.fullmatch(VERSION_PATTERN, version):
        raise ValueError("Invalid release version; use #.#.# with an optional -suffix")
    if version != dashboard_version:
        raise ValueError(f"Release version must exactly match package.json ({dashboard_version})")
    return version


def python_version(version):
    try:
        return str(Version(version))
    except InvalidVersion:
        base, suffix = version.split("-", 1)
        return str(Version(base + "+" + re.sub(r"[-.]+", ".", suffix).strip(".")))


def wheel_name(version):
    # Stable requested names are valid wheel names; hyphenated suffixes are not.
    if "-" in version:
        return f"ageatopia-{python_version(version)}-py3-none-any.whl"
    return f"Ageatopia-v{version}-py3-none-any.whl"


def assert_x64(path):
    with path.open("rb") as file:
        if file.read(2) != b"MZ":
            raise ValueError(f"Not a Windows executable: {path}")
        file.seek(60)
        offset = struct.unpack("<I", file.read(4))[0]
        file.seek(offset)
        if file.read(4) != b"PE\0\0" or struct.unpack("<H", file.read(2))[0] != 0x8664:
            raise ValueError(f"An x64 Windows binary is required: {path}")


class ReleaseBuilder:
    def __init__(self, root, version, report=None):
        self.root = Path(root).resolve()
        self.version = version
        self.release_root = self.root / "dist/releases"
        self.destination = self.release_root / version
        self.workspace = self.release_root / ".staging/build"
        self.artifacts = self.workspace / "artifacts"
        self.dashboard = self.workspace / "dashboard"
        self.report = report or (lambda message, style="": None)
        self.cancelled = threading.Event()
        self.process = None
        self.node = None
        self.npm = None
        self.owned = False
        self.results = []
        self.timings = []

    def command(self, arguments, cwd=None, env=None, capture=False):
        if self.cancelled.is_set():
            raise RuntimeError("Release cancelled")
        self.report("$ " + subprocess.list2cmdline([str(x) for x in arguments]), "cyan")
        process = subprocess.Popen(
            [str(x) for x in arguments],
            cwd=cwd or self.root,
            env=env or os.environ.copy(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        self.process = process
        lines = queue.Queue()

        def read_output():
            for line in process.stdout:
                lines.put(line.rstrip())
            lines.put(None)

        reader = threading.Thread(target=read_output, daemon=True)
        reader.start()
        output = []
        try:
            while True:
                if self.cancelled.is_set():
                    raise RuntimeError("Release cancelled")
                try:
                    line = lines.get(timeout=0.2)
                except queue.Empty:
                    continue
                if line is None:
                    break
                output.append(line)
                self.report(line)
            code = process.wait()
            if code:
                raise RuntimeError(
                    f"{Path(arguments[0]).name} failed (exit {code}): " + "\n".join(output[-12:])
                )
            return "\n".join(output) if capture else None
        finally:
            if process.poll() is None:
                if os.name == "nt":
                    subprocess.run(
                        ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                        capture_output=True,
                        check=False,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                    )
                else:
                    process.terminate()
                process.wait(timeout=15)
            reader.join(timeout=2)
            process.stdout.close()
            self.process = None

    def preflight(self):
        manifest = json.loads((self.root / "src/gui/dashboard/package.json").read_text())
        validate_version(self.version, manifest["version"])
        if self.destination.exists():
            raise FileExistsError(f"Release directory already exists: {self.destination}")
        staging = self.workspace.parent
        if self.release_root.resolve() != self.root / "dist/releases":
            raise ValueError("Release staging must remain inside this checkout's dist/releases")
        if staging.resolve() != self.root / "dist/releases/.staging":
            raise ValueError("Release staging must not redirect outside this checkout")
        if staging.exists() and (not staging.is_dir() or any(staging.iterdir())):
            raise FileExistsError(f"Release staging must be empty: {staging}")
        self.report("Version, destination and clean staging verified", "green")
        if os.name != "nt" or struct.calcsize("P") != 8:
            raise RuntimeError("Build Windows x64 releases using 64-bit Python on Windows")
        assert_x64(Path(sys.executable))
        for filename in ("ageatopia.exe", "WebView2Loader.dll"):
            path = self.root / "dist/native/build" / filename
            if not path.is_file():
                raise FileNotFoundError(f"Build the native host before releasing: {path}")
            assert_x64(path)
        project = tomllib.loads((self.root / "pyproject.toml").read_text())
        requirements = (
            project["build-system"]["requires"]
            + project["project"]["dependencies"]
            + project["project"]["optional-dependencies"]["release"]
        )
        for requirement_text in requirements:
            requirement = Requirement(requirement_text)
            if requirement.marker and not requirement.marker.evaluate():
                continue
            try:
                installed = importlib.metadata.version(requirement.name)
            except importlib.metadata.PackageNotFoundError as error:
                raise RuntimeError(
                    "Install release dependencies: python -m pip install -e .[release]"
                ) from error
            if Version(installed) not in requirement.specifier:
                raise RuntimeError(
                    f"{requirement} is required; found {installed}. "
                    "Install release dependencies: python -m pip install -e .[release]"
                )
        self.node = shutil.which("node")
        self.npm = shutil.which("npm.cmd")
        if not self.node or not self.npm:
            raise RuntimeError("Node.js and npm are required to build releases")
        assert_x64(Path(self.node))
        node_info = json.loads(
            self.command(
                [
                    self.node,
                    "-p",
                    "JSON.stringify({version:process.versions.node,arch:process.arch})",
                ],
                capture=True,
            )
        )
        if int(node_info["version"].split(".")[0]) < 22 or node_info["arch"] != "x64":
            raise RuntimeError("Node.js 22 or newer, Windows x64, is required")
        self.node_version = node_info["version"]
        license_path = Path(self.node).parent / "LICENSE"
        if license_path.is_file():
            self.node_license = license_path.read_bytes()
        else:
            license_url = (
                f"https://raw.githubusercontent.com/nodejs/node/v{self.node_version}/LICENSE"
            )
            with urllib.request.urlopen(license_url, timeout=15) as response:
                self.node_license = response.read()
        vswhere = (
            Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)"))
            / "Microsoft Visual Studio/Installer/vswhere.exe"
        )
        if not shutil.which("cl"):
            if (
                not vswhere.is_file()
                or not self.command(
                    [
                        str(vswhere),
                        "-latest",
                        "-products",
                        "*",
                        "-requires",
                        "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
                        "-property",
                        "installationPath",
                    ],
                    capture=True,
                ).strip()
            ):
                raise RuntimeError(
                    "Visual Studio Build Tools with the x64 C++ workload are required"
                )
        if not (self.root / "src/hooks/sandbox/Dockerfile").is_file():
            raise FileNotFoundError("Sandbox Dockerfile is missing")
        package_size = sum(
            p.stat().st_size
            for directory in (
                Path(sys.prefix) / "Lib/site-packages",
                self.root / "src",
            )
            for p in directory.rglob("*")
            if p.is_file()
        )
        needed = max(8 * 1024**3, package_size * 5 + 2 * 1024**3)
        free = shutil.disk_usage(self.root).free
        if free < needed:
            raise RuntimeError(
                f"Insufficient disk space: need {needed / 1024**3:.1f} GiB; "
                f"available {free / 1024**3:.1f} GiB"
            )
        self.report(
            f"Native x64, Python build tools, MSVC and Node {self.node_version} verified", "green"
        )
        self.report(
            f"Disk: {free / 1024**3:.1f} GiB free; {needed / 1024**3:.1f} GiB required", "green"
        )

    def step(self, title, action):
        start = time.monotonic()
        self.report(f"Building · {title}", "cyan")
        try:
            action()
            if self.cancelled.is_set():
                raise RuntimeError("Release cancelled")
        except Exception as error:
            elapsed = time.monotonic() - start
            raise RuntimeError(f"{title} failed after {elapsed:.1f}s: {error}") from error
        elapsed = time.monotonic() - start
        self.timings.append((title, elapsed))
        self.report(f"Completed · {title} · {elapsed:.1f}s", "green")

    def build(self):
        self.preflight()
        self.workspace.parent.mkdir(parents=True, exist_ok=True)
        self.workspace.mkdir()  # Exclusive ownership; never reuse another build's files.
        self.owned = True
        try:
            self.artifacts.mkdir()
            self.step("Python wheel", self.build_wheel)
            self.step("Docker sandbox", self.build_sandbox)
            self.step("Standalone dashboard", self.build_dashboard)
            self.step("Standalone Windows application", self.build_application)
            self.step("Development application", self.build_development)
            self.step("Validate all four packages", self.validate_artifacts)
            self.publish()
            self.results = sorted(self.destination.iterdir())
            return self.results
        finally:
            if self.owned:
                if self.workspace.resolve().parent != (self.release_root / ".staging").resolve():
                    raise RuntimeError("Refusing cleanup outside release staging")
                shutil.rmtree(self.workspace)
                if not any(self.workspace.parent.iterdir()):
                    self.workspace.parent.rmdir()

    def build_dashboard(self):
        shutil.copytree(
            self.root / "src/gui/dashboard",
            self.dashboard,
            ignore=shutil.ignore_patterns("node_modules", ".next", "*.tsbuildinfo"),
        )
        (self.dashboard / "next.config.ts").write_text(
            'import type { NextConfig } from "next";\n'
            'const config: NextConfig = { poweredByHeader: false, output: "standalone", '
            "outputFileTracingRoot: process.cwd() };\nexport default config;\n"
        )
        env = os.environ.copy()
        env["NEXT_TELEMETRY_DISABLED"] = "1"
        self.command([os.environ["COMSPEC"], "/d", "/c", self.npm, "ci"], self.dashboard, env)
        self.command([self.node, "node_modules/next/dist/bin/next", "build"], self.dashboard, env)
        standalone = self.dashboard / ".next/standalone"
        if not (standalone / "server.js").is_file():
            raise RuntimeError("Next.js did not emit a standalone server")
        shutil.copytree(self.dashboard / ".next/static", standalone / ".next/static")
        if (self.dashboard / "public").exists():
            shutil.copytree(self.dashboard / "public", standalone / "public")

    def archive(self, folder, filename):
        path = self.artifacts / filename
        with zipfile.ZipFile(path, "x", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for file in sorted(folder.rglob("*")):
                if file.is_file():
                    archive.write(file, file.relative_to(folder.parent).as_posix())

    def build_application(self):
        output = self.workspace / "compiled"
        env = os.environ.copy()
        env["PYTHONPATH"] = str(self.root / "src")
        env["NUITKA_CACHE_DIR"] = str(self.workspace / "nuitka-cache")
        numeric = self.version.split("-", 1)[0]
        self.command(
            [
                sys.executable,
                "-m",
                "nuitka",
                "--mode=standalone",
                "--msvc=latest",
                "--assume-yes-for-downloads",
                "--windows-console-mode=attach",
                "--windows-icon-from-ico=" + str(self.root / "src/gui/native/ageatopia.ico"),
                "--product-name=Ageatopia",
                "--file-version=" + numeric,
                "--product-version=" + numeric,
                "--output-filename=Ageatopia.exe",
                "--output-dir=" + str(output),
                "--include-package=backend",
                "--include-package=hooks",
                "--include-package=gui",
                "--nofollow-import-to=gui.releases",
                "--include-module=uvicorn.logging",
                "--include-module=uvicorn.loops.auto",
                "--include-module=uvicorn.protocols.http.auto",
                "--include-module=uvicorn.protocols.websockets.auto",
                "--include-module=uvicorn.lifespan.on",
                "--jobs=8",
                str(self.root / "src/gui/release_entry.py"),
            ],
            env=env,
        )
        folder = self.workspace / "portable/Ageatopia"
        folder.parent.mkdir()
        shutil.move(str(output / "release_entry.dist"), folder)
        shutil.copytree(
            self.root / "dist/native/build",
            folder / "native",
            ignore=shutil.ignore_patterns("*.obj", "*.res", "*.pdb"),
        )
        shutil.copytree(self.dashboard / ".next/standalone", folder / "dashboard")
        shutil.copytree(
            self.root / "src/hooks/sandbox",
            folder / "sandbox",
            ignore=shutil.ignore_patterns("__pycache__"),
        )
        (folder / "runtime").mkdir()
        shutil.copy2(self.node, folder / "runtime/node.exe")
        (folder / "runtime/NODE-LICENSE.txt").write_bytes(self.node_license)
        (folder / "README.txt").write_text(
            f"Ageatopia {self.version}\n\nLaunch Ageatopia.exe. Python and Node.js are bundled.\n"
            "Logs and configuration are stored in %LOCALAPPDATA%/Ageatopia.\n"
            "Docker Desktop and Ollama are checked at startup; without them the observatory\n"
            "remains available, but local agent execution requires Docker and a model provider.\n"
            "Install the Microsoft Edge WebView2 Runtime if Windows does not have it.\n"
            "Training extras are optional and are not included in this package.\n",
            encoding="utf-8",
        )
        self.portable = folder

    def build_development(self):
        folder = self.workspace / "development/Ageatopia"
        folder.mkdir(parents=True)
        for directory in ("src", "db"):
            shutil.copytree(
                self.root / directory,
                folder / directory,
                ignore=shutil.ignore_patterns("__pycache__", "node_modules", ".next"),
            )
        for filename in ("pyproject.toml", "setup.cfg", "requirements.lock", "README.md"):
            shutil.copy2(self.root / filename, folder / filename)
        metadata_path = folder / "pyproject.toml"
        metadata = metadata_path.read_text()
        original = tomllib.loads(metadata)["project"]["version"]
        metadata_path.write_text(
            metadata.replace(
                f'version = "{original}"', f'version = "{python_version(self.version)}"', 1
            )
        )
        shutil.copytree(
            self.root / "dist/native/build",
            folder / "dist/native/build",
            ignore=shutil.ignore_patterns("*.obj", "*.res", "*.pdb"),
        )
        shutil.copytree(self.dashboard / ".next/standalone", folder / "dist/gui/dashboard")
        (folder / "Start.ps1").write_text(
            "& (Join-Path $PSScriptRoot 'src/scripts/run.ps1') @args\nexit $LASTEXITCODE\n"
        )
        (folder / "Setup.ps1").write_text(
            "$ErrorActionPreference = 'Stop'\nPush-Location $PSScriptRoot\ntry {\n"
            "python -m venv dist/python/venv\n"
            "if ($LASTEXITCODE) { throw 'Python environment creation failed' }\n"
            "& ./dist/python/venv/Scripts/python.exe -m pip install -r requirements.lock\n"
            "if ($LASTEXITCODE) { throw 'Dependency installation failed' }\n"
            "& ./dist/python/venv/Scripts/python.exe -m pip install -e .\n"
            "if ($LASTEXITCODE) { throw 'Application installation failed' }\n"
            "} finally { Pop-Location }\n"
        )
        self.archive(folder, f"Ageatopia-v{self.version}-Windows-x64-dev.zip")

    def build_wheel(self):
        project = self.workspace / "python"
        project.mkdir()
        for name in ("backend", "hooks", "gui"):
            shutil.copytree(
                self.root / "src" / name,
                project / "src" / name,
                ignore=lambda directory, files: [
                    name
                    for name in files
                    if name == "__pycache__"
                    or (not (Path(directory) / name).is_dir() and not name.endswith(".py"))
                ],
            )
        # No native/window/dashboard resources belong in a platform-independent wheel.
        for directory in (project / "src/gui/native", project / "src/gui/dashboard"):
            if directory.exists():
                shutil.rmtree(directory)
        metadata = (self.root / "pyproject.toml").read_text()
        original_version = tomllib.loads(metadata)["project"]["version"]
        metadata = metadata.replace(
            f'version = "{original_version}"', f'version = "{python_version(self.version)}"', 1
        )
        (project / "pyproject.toml").write_text(metadata)
        self.command(
            [
                sys.executable,
                "-m",
                "build",
                "--wheel",
                "--no-isolation",
                "--outdir",
                str(project / "wheels"),
                str(project),
            ]
        )
        wheels = list((project / "wheels").glob("*.whl"))
        if len(wheels) != 1:
            raise RuntimeError("The Python build must emit exactly one wheel")
        self.wheel = self.artifacts / wheel_name(self.version)
        shutil.copy2(wheels[0], self.wheel)
        venv = self.workspace / "wheel-install"
        self.command([sys.executable, "-m", "venv", str(venv)])
        python = venv / "Scripts/python.exe"
        self.command([python, "-m", "pip", "install", "--no-deps", str(self.wheel)])
        self.command(
            [
                python,
                "-I",
                "-c",
                "import backend, pathlib, sys; "
                "assert pathlib.Path(backend.__file__).is_relative_to(pathlib.Path(sys.prefix))",
            ]
        )

    def build_sandbox(self):
        folder = self.workspace / "sandbox/Ageatopia-Sandbox"
        shutil.copytree(
            self.root / "src/hooks/sandbox", folder, ignore=shutil.ignore_patterns("__pycache__")
        )
        (folder / "README.txt").write_text(
            "Build from this folder:\ndocker build -t ageatopia-world:1 .\n"
            "Docker Desktop with Linux containers and internet access are required.\n"
        )
        self.archive(folder, f"Ageatopia-v{self.version}-Docker-Sandbox.zip")

    def validate_artifacts(self):
        # Move before probing: the compiled runtime must work after relocation.
        relocated = self.workspace / "relocated/Ageatopia"
        relocated.parent.mkdir()
        shutil.move(str(self.portable), relocated)
        env = os.environ.copy()
        for name in (
            "PYTHONPATH",
            "PYTHONHOME",
            "UTOPIA_DATA",
            "UTOPIA_API_URL",
            "UTOPIA_OPERATOR_TOKEN",
        ):
            env.pop(name, None)
        env["PATH"] = str(Path(os.environ["SystemRoot"]) / "System32")
        env["LOCALAPPDATA"] = str(self.workspace / "validation-user")
        exe = relocated / "Ageatopia.exe"
        assert_x64(exe)
        self.command([exe, "--self-test"], relocated, env)
        config = self.workspace / "smoke.json"
        with socket.socket() as backend, socket.socket() as dashboard:
            backend.bind(("127.0.0.1", 0))
            dashboard.bind(("127.0.0.1", 0))
            ports = (backend.getsockname()[1], dashboard.getsockname()[1])
        config.write_text(
            json.dumps(
                {
                    "data_dir": str(self.workspace / "smoke-data"),
                    "backend_port": ports[0],
                    "dashboard_port": ports[1],
                    "manage_ollama": False,
                }
            )
        )
        self.command([exe, "--smoke-test", "--config", config], relocated, env)
        self.archive(relocated, f"Ageatopia-v{self.version}-Windows-x64.zip")
        expected = {
            f"Ageatopia-v{self.version}-Windows-x64-dev.zip": (
                "Ageatopia/dist/native/build/ageatopia.exe",
                "Ageatopia/dist/native/build/WebView2Loader.dll",
                "Ageatopia/src/backend/api.py",
                "Ageatopia/dist/gui/dashboard/server.js",
                "Ageatopia/Start.ps1",
            ),
            f"Ageatopia-v{self.version}-Windows-x64.zip": (
                "Ageatopia/Ageatopia.exe",
                "Ageatopia/runtime/node.exe",
                "Ageatopia/native/WebView2Loader.dll",
                "Ageatopia/dashboard/server.js",
                "Ageatopia/sandbox/Dockerfile",
            ),
            f"Ageatopia-v{self.version}-Docker-Sandbox.zip": (
                "Ageatopia-Sandbox/Dockerfile",
                "Ageatopia-Sandbox/supervisor.py",
                "Ageatopia-Sandbox/process_observer.py",
                "Ageatopia-Sandbox/busybox-awk.patch",
                "Ageatopia-Sandbox/busybox-wget.patch",
                "Ageatopia-Sandbox/busybox-ash-recursion.patch",
            ),
            self.wheel.name: ("backend/api.py", "gui/desktop.py", "hooks/sandbox.py"),
        }
        if {p.name for p in self.artifacts.iterdir()} != set(expected):
            raise RuntimeError("Release must contain exactly the four expected packages")
        for filename, required in expected.items():
            with zipfile.ZipFile(self.artifacts / filename) as archive:
                if archive.testzip() is not None or not set(required) <= set(archive.namelist()):
                    raise RuntimeError(f"Invalid or incomplete release archive: {filename}")
                if filename.endswith(".whl") and any(
                    "/native/" in name for name in archive.namelist()
                ):
                    raise RuntimeError("Python wheel must not contain the native application")
            self.report("Validated · " + filename, "green")

    def publish(self):
        if self.cancelled.is_set():
            raise RuntimeError("Release cancelled")
        if self.destination.exists():
            raise FileExistsError(f"Release directory already exists: {self.destination}")
        # Windows rename is atomic on the same volume and refuses an existing destination.
        self.artifacts.rename(self.destination)
        self.report(f"Ready for release · {self.destination}", "green")


class ReleaseApp(App):
    TITLE = "Ageatopia · Releases"
    CSS = (
        MaintenanceApp.CSS
        + """
    #health { height: 3; min-height: 3; max-height: 3; padding: 0 2; }
    #activity { width: 1fr; }
    .actions { padding: 0 2; }
    """
    )
    BINDINGS = [
        Binding("ctrl+c", "cancel", "Copy / Cancel", priority=True),
        Binding("ctrl+t", "toggle_theme", "Theme"),
    ]

    def __init__(self, builder):
        super().__init__()
        self.builder = builder
        self.failure = None
        config_path = builder.root / ".data/desktop.json"
        self.config = {"config_path": str(config_path)}
        self.logger = logging.getLogger(__name__)
        if config_path.is_file():
            theme = json.loads(config_path.read_text(encoding="utf-8")).get("maintenance_theme")
            if theme in self.available_themes:
                self.theme = theme

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(
            f"Release {self.builder.version}\nPreflight → Build → Validate → Publish", id="health"
        )
        with Horizontal(classes="actions"):
            yield Label("Checking build requirements…", id="activity")
            yield LoadingIndicator(id="busy")
        yield SelectableRichLog(
            id="maintenance-log",
            auto_scroll=True,
            wrap=True,
            max_lines=3000,
            markup=False,
            highlight=True,
        )
        yield Footer()

    def on_mount(self):
        self.query_one("#maintenance-log").border_title = "Release output"
        self.screen.set_class(self.size.width < 100 or self.size.height < 30, "compact")
        if Path(self.config["config_path"]).is_file():
            self.watch(self, "theme", self._persist_theme, init=False)
        self.run_release()

    def _persist_theme(self, theme):
        MaintenanceApp._persist_theme(self, theme)

    def on_resize(self, event):
        self.screen.set_class(event.size.width < 100 or event.size.height < 30, "compact")

    def action_toggle_theme(self):
        themes = list(self.available_themes)
        self.theme = themes[(themes.index(self.theme) + 1) % len(themes)]

    def action_cancel(self):
        selected = self.screen.get_selected_text()
        if selected:
            self.copy_to_clipboard(selected)
            return
        self.builder.cancelled.set()
        self.query_one("#activity", Label).update("Cancelling; cleaning owned staging files…")

    def write_output(self, message, style=""):
        content = Text.from_ansi(message)
        if style:
            content.stylize(style)
            self.query_one("#activity", Label).update(content)
        elif re.search(r"error|failed|fatal", message, re.I):
            content.stylize("red")
        elif "warning" in message.lower():
            content.stylize("yellow")
        self.query_one("#maintenance-log").write(content)

    @work(thread=True)
    def run_release(self):
        self.builder.report = lambda message, style="": self.call_from_thread(
            self.write_output, message, style
        )
        try:
            self.builder.build()
        except Exception as error:
            self.failure = str(error)
            self.call_from_thread(self.write_output, "ERROR: " + self.failure, "red")
        finally:
            self.call_from_thread(self.exit, 1 if self.failure else 0)


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Build and validate Ageatopia release packages")
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    builder = ReleaseBuilder(REPOSITORY, args.version)
    console = Console()
    if sys.stdout.isatty() and sys.stdin.isatty():
        app = ReleaseApp(builder)
        status = app.run()
        error = app.failure
    else:
        builder.report = lambda message, style="": console.print(Text(message, style=style))
        try:
            builder.build()
            status, error = 0, None
        except (Exception, KeyboardInterrupt) as failure:
            status, error = 1, str(failure)
            if isinstance(failure, KeyboardInterrupt):
                error = "Release cancelled; owned staging files were removed"
    if error:
        console.print(Panel(Text(error, style="red"), title="Release failed", border_style="red"))
    else:
        table = Table("Artifact", "Size", title=f"Ageatopia {args.version} · Ready for release")
        for path in builder.results:
            table.add_row(path.name, f"{path.stat().st_size / 1024**2:.1f} MiB")
        console.print(table)
        for title, elapsed in builder.timings:
            console.print(f"{title}: {elapsed:.1f}s", style="dim")
        console.print(str(builder.destination), style="green")
    raise SystemExit(status)


if __name__ == "__main__":
    main()
