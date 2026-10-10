"""Textual installation, repair, and system diagnostics console."""

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

import httpx
from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal
from textual.widgets import Button, Footer, Header, Input, Label, LoadingIndicator, RichLog, Static

from utopia.desktop import DEFAULT_CONFIG, ROOT, load_config, setup_logging


class MaintenanceApp(App):
    TITLE = "Agent Utopia · Maintenance"
    CSS = """
    Screen { layout: vertical; }
    #health { height: auto; min-height: 5; padding: 1 2; color: $text-muted; }
    .actions { height: auto; padding: 1 2; }
    .actions Button { margin-right: 1; }
    #model-name { width: 40; }
    #activity { height: 3; padding: 1 2; }
    #maintenance-log { height: 1fr; border: round $primary-darken-2; }
    """

    BINDINGS = [("ctrl+c", "quit", "Exit")]

    def __init__(self, config_path=DEFAULT_CONFIG):
        super().__init__()
        self.config = load_config(config_path)
        self.logger = setup_logging(self.config["data_dir"], "maintenance")
        self.busy = False

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("System checks have not run yet.", id="health")
        with Horizontal(classes="actions"):
            yield Button("Install dependencies", id="install", variant="primary")
            yield Button("Build dashboard", id="dashboard-build")
            yield Button("Build native host", id="native-build")
        with Horizontal(classes="actions"):
            yield Button("Build sandbox image", id="sandbox-build")
            yield Button("Repair setup", id="repair", variant="warning")
            yield Button("Run diagnostics", id="diagnostics")
        with Horizontal(classes="actions"):
            yield Input("qwen3.5:2b", placeholder="Ollama model tag", id="model-name")
            yield Button("Pull model", id="model-pull")
            yield LoadingIndicator(id="busy")
        yield Label("Commands and diagnostic output", id="activity")
        yield RichLog(id="maintenance-log", auto_scroll=True, max_lines=1000, wrap=True)
        yield Footer()

    def on_mount(self):
        self.query_one("#busy", LoadingIndicator).display = False
        self.query_one("#maintenance-log").border_title = "Maintenance output"

    def _write(self, message, level=logging.INFO):
        style = "red" if level >= logging.ERROR or message.startswith("ERROR") else ""
        if not style and message.startswith(("WARNING", "WARNING:")):
            style = "yellow"
        elif not style and message.startswith(("Successfully", "Requirement already satisfied")):
            style = "green"
        elif not style and message.startswith(
            ("[", "Collecting", "Downloading", "Installing", "Building")
        ):
            style = "cyan"
        self.query_one("#maintenance-log", RichLog).write(Text(message, style=style))
        self.logger.log(level, message)

    def _finish(self, message, failed=False):
        self.busy = False
        self.query_one("#busy", LoadingIndicator).display = False
        self.query_one("#activity", Label).update(message)
        self.notify(message, severity="error" if failed else "information", timeout=8)

    def on_button_pressed(self, event):
        try:
            self._handle_maintenance_action(event.button.id)
        except (OSError, RuntimeError) as error:
            self._finish(str(error), failed=True)

    def _handle_maintenance_action(self, action):
        if action == "install":
            self._run_steps(
                "Installing project dependencies",
                [
                    [sys.executable, "-m", "pip", "install", "-r", "requirements.lock"],
                    [sys.executable, "-m", "pip", "install", "-e", "."],
                    self._npm_command("ci", "--prefix", str(ROOT / "dashboard")),
                ],
            )
        elif action == "dashboard-build":
            self._run_steps(
                "Building dashboard",
                [self._npm_command("run", "build", "--prefix", str(ROOT / "dashboard"))],
            )
        elif action == "sandbox-build":
            self._run_steps(
                "Building sandbox image",
                [["docker", "build", "-t", "agent-utopia-world:1", "sandbox"]],
            )
        elif action == "native-build":
            self._run_steps("Building C Win32 host", [self._native_build_command()])
        elif action == "repair":
            self._run_steps(
                "Repairing dependencies and application build",
                [
                    [sys.executable, "-m", "pip", "install", "-r", "requirements.lock"],
                    [sys.executable, "-m", "pip", "install", "-e", "."],
                    self._npm_command("ci", "--prefix", str(ROOT / "dashboard")),
                    self._npm_command("run", "build", "--prefix", str(ROOT / "dashboard")),
                    self._native_build_command(),
                ],
            )
        elif action == "diagnostics":
            self.run_diagnostics()
        elif action == "model-pull":
            model = self.query_one("#model-name", Input).value.strip()
            if not model or any(char.isspace() for char in model):
                self._finish("Enter a valid Ollama model tag", failed=True)
            else:
                self._run_steps(f"Pulling Ollama model {model}", [["ollama", "pull", model]])

    @staticmethod
    def _native_build_command():
        return ["powershell.exe", "-NoProfile", "-File", str(ROOT / "native" / "build.ps1")]

    @staticmethod
    def _npm_command(*args):
        npm = shutil.which("npm")
        node = shutil.which("node")
        if not npm or not node:
            raise RuntimeError("Node.js and npm are required")
        if os.name == "nt":
            cli = Path(npm).parent / "node_modules" / "npm" / "bin" / "npm-cli.js"
            if not cli.is_file():
                raise RuntimeError("Could not locate npm-cli.js beside the Node.js installation")
            return [node, str(cli), *args]
        return [npm, *args]

    def _run_steps(self, label, commands):
        if self.busy:
            self.notify("A maintenance task is already running", severity="warning")
            return
        self.busy = True
        self.query_one("#busy", LoadingIndicator).display = True
        self.query_one("#activity", Label).update(label)
        self.run_commands(label, commands)

    @work(thread=True, exclusive=True)
    def run_commands(self, label, commands):
        for index, command in enumerate(commands, 1):
            if isinstance(command, Exception):
                self.app.call_from_thread(self._finish, str(command), True)
                return
            description = " ".join(str(part) for part in command)
            self.app.call_from_thread(self._write, f"[{index}/{len(commands)}] {description}")
            try:
                process = subprocess.Popen(
                    command,
                    cwd=ROOT,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
                for line in process.stdout:
                    stripped = line.rstrip()
                    if stripped:
                        self.app.call_from_thread(self._write, stripped)
                result = process.wait()
            except (OSError, RuntimeError) as error:
                self.app.call_from_thread(self._write, f"ERROR · {error}", logging.ERROR)
                self.app.call_from_thread(self._finish, f"Task stopped: {error}", True)
                return
            if result:
                self.app.call_from_thread(
                    self._finish, f"Command failed with exit code {result}", True
                )
                return
        self.app.call_from_thread(self._finish, f"{label} completed")

    def run_diagnostics(self):
        if self.busy:
            self.notify("A maintenance task is already running", severity="warning")
            return
        self.busy = True
        self.query_one("#busy", LoadingIndicator).display = True
        self.query_one("#activity", Label).update("Checking Docker, Ollama, GPU and disk")
        self._diagnostics_worker()

    @work(thread=True, exclusive=True)
    def _diagnostics_worker(self):
        self.app.call_from_thread(self._write, "System diagnostics")
        outcomes = []
        executable = ROOT / "native" / "build" / "ageatopia.exe"
        outcomes.append(
            f"Native host: {'built' if executable.is_file() else 'missing; use Build native host'}"
        )
        outcomes.append(
            f"Ollama host-cache budget: {self.config['ollama_cache_ram_mb']} MiB "
            "(applies when the application starts its own server)"
        )
        for label, command in (("Docker", ["docker", "info"]), ("NVIDIA GPU", ["nvidia-smi"])):
            path = shutil.which(command[0])
            if not path:
                outcomes.append(f"{label}: command not installed")
                continue
            try:
                result = subprocess.run(
                    [path, *command[1:]],
                    capture_output=True,
                    text=True,
                    timeout=15,
                    check=False,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
                detail = result.stdout.strip() or result.stderr.strip()
                state = "ready" if result.returncode == 0 else "unavailable"
                outcomes.append(f"{label}: {state} · {detail[:300]}")
            except (OSError, subprocess.TimeoutExpired) as error:
                outcomes.append(f"{label}: unavailable · {error}")
        try:
            response = httpx.get("http://127.0.0.1:11434/api/tags", timeout=4)
            response.raise_for_status()
            models = [model.get("name", "unknown") for model in response.json().get("models", [])]
            outcomes.append(
                f"Ollama: ready · {', '.join(models) if models else 'no models pulled'}"
            )
        except (httpx.HTTPError, ValueError) as error:
            outcomes.append(f"Ollama: unavailable · {error}")
        disk = shutil.disk_usage(ROOT)
        outcomes.append(f"Disk: {disk.free // (1024**3)} GiB free of {disk.total // (1024**3)} GiB")
        message = "\n".join(outcomes)
        self.app.call_from_thread(self._write, message)
        self.app.call_from_thread(self._set_health, message)
        self.app.call_from_thread(self._finish, "Diagnostics completed")

    def _set_health(self, message):
        self.query_one("#health", Static).update(message)


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Install, repair, and diagnose Agent Utopia")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    args = parser.parse_args()
    MaintenanceApp(args.config).run()


if __name__ == "__main__":
    main()
