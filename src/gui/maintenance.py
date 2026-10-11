"""Textual installation, repair, and system diagnostics console."""

import json
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
from textual.binding import Binding
from textual.containers import Horizontal
from textual.selection import Selection
from textual.strip import Strip
from textual.widgets import Button, Footer, Header, Input, Label, LoadingIndicator, RichLog, Static
from textual.worker import Worker, WorkerState

from gui.desktop import DEFAULT_CONFIG, ROOT, load_config, setup_logging


class SelectableRichLog(RichLog):
    def get_selection(self, selection: Selection):
        return selection.extract("\n".join(line.text.rstrip() for line in self.lines)), "\n"

    def selection_updated(self, selection):
        self.refresh()

    def render_line(self, y):
        scroll_x, scroll_y = self.scroll_offset
        row = scroll_y + y
        if row >= len(self.lines):
            return super().render_line(y)
        line = self.lines[row]
        selection = self.text_selection
        if selection is not None and (span := selection.get_span(row)) is not None:
            text = Text.assemble(*[(segment.text, segment.style or "") for segment in line])
            start, end = span
            text.stylize(
                self.screen.get_component_rich_style("screen--selection"),
                start,
                len(text) if end == -1 else end,
            )
            line = Strip(text.render(self.app.console))
        return (
            line.crop_extend(
                scroll_x, scroll_x + self.scrollable_content_region.width, self.rich_style
            )
            .apply_style(self.rich_style)
            .apply_offsets(scroll_x, row)
        )


class MaintenanceApp(App):
    TITLE = "Agent Utopia · Maintenance"
    CSS = """
    Screen { layout: vertical; }
    #health {
        height: auto; min-height: 5; max-height: 8;
        overflow-y: auto; padding: 1 2; color: $text-muted;
    }
    .actions { height: auto; padding: 1 2; }
    .actions Button { margin-right: 1; }
    #model-name { width: 40; }
    #busy { width: 6; height: 3; }
    Screen.compact .actions { padding: 0 1; }
    Screen.compact .actions Button { min-width: 0; width: 1fr; }
    Screen.compact #model-name { width: 1fr; }
    Screen.compact #health { min-height: 2; max-height: 5; padding: 0 1; }
    #activity { height: 3; padding: 1 2; }
    #maintenance-log { height: 1fr; border: round $primary-darken-2; }
    """

    BINDINGS = [
        Binding("ctrl+c", "copy_or_quit", "Copy / Exit", priority=True),
        Binding("ctrl+t", "toggle_theme", "Theme"),
    ]

    def __init__(self, config_path=DEFAULT_CONFIG):
        super().__init__()
        self.config = load_config(config_path)
        self.logger = setup_logging(self.config["data_dir"], "maintenance")
        self.busy = False
        saved_theme = self.config.get("maintenance_theme")
        if saved_theme in self.available_themes:
            self.theme = saved_theme

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
        yield SelectableRichLog(
            id="maintenance-log",
            auto_scroll=True,
            max_lines=1000,
            wrap=True,
            markup=True,
            highlight=True,
        )
        yield Footer()

    def on_mount(self):
        self.query_one("#busy", LoadingIndicator).display = False
        self.query_one("#maintenance-log").border_title = "Maintenance output"
        self.watch(self, "theme", self._persist_theme, init=False)
        self.screen.set_class(self.size.width < 100 or self.size.height < 30, "compact")

    def on_resize(self, event):
        self.screen.set_class(event.size.width < 100 or event.size.height < 30, "compact")

    def _write(self, message, level=logging.INFO):
        style = "red" if level >= logging.ERROR or message.startswith("ERROR") else ""
        if not style and message.startswith(("WARNING", "WARNING:")):
            style = "yellow"
        elif not style and message.startswith(("Successfully", "Requirement already satisfied")):
            style = "green"
        elif not style and message.startswith(
            ("[", "\n[", "Collecting", "Downloading", "Installing", "Building")
        ):
            style = "cyan"
        content = Text.from_ansi(message)
        if style:
            content.stylize(style)
        self.query_one("#maintenance-log", RichLog).write(content)
        self.logger.log(level, message)

    def _persist_theme(self, theme):
        config_path = Path(self.config["config_path"])
        try:
            values = json.loads(config_path.read_text(encoding="utf-8"))
            values["maintenance_theme"] = theme
            config_path.write_text(json.dumps(values, indent=2) + "\n", encoding="utf-8")
        except (OSError, ValueError) as error:
            self.logger.error("Could not persist maintenance theme: %s", error)
            self.notify("Could not save the maintenance theme", severity="warning")

    def action_toggle_theme(self):
        themes = list(self.available_themes)
        self.theme = themes[(themes.index(self.theme) + 1) % len(themes)]

    def action_copy_or_quit(self):
        selected_text = self.screen.get_selected_text()
        if selected_text:
            self.copy_to_clipboard(selected_text)
        else:
            self.exit()

    def _finish(self, message, failed=False):
        self.busy = False
        self.query_one("#busy", LoadingIndicator).display = False
        self.query_one("#activity", Label).update(message)
        for button in self.query(Button):
            button.disabled = False
        self._write(message, logging.ERROR if failed else logging.INFO)
        self.notify(message, severity="error" if failed else "information", timeout=8)

    def on_worker_state_changed(self, event: Worker.StateChanged):
        if event.state == WorkerState.ERROR:
            self._finish(f"Task failed: {event.worker.error}", failed=True)

    def on_button_pressed(self, event):
        try:
            self._handle_maintenance_action(event.button.id)
        except (OSError, RuntimeError) as error:
            self._finish(str(error), failed=True)

    def _handle_maintenance_action(self, action):
        if self.busy:
            self.notify("A maintenance task is already running", severity="warning")
            return
        if action == "install":
            self._run_steps(
                "Installing project dependencies",
                [
                    [sys.executable, "-m", "pip", "install", "-r", "requirements.lock"],
                    [sys.executable, "-m", "pip", "install", "-e", "."],
                    [
                        shutil.which("node") or "node",
                        str(ROOT / "src" / "scripts" / "gui.mjs"),
                        "install",
                    ],
                ],
            )
        elif action == "dashboard-build":
            self._run_steps(
                "Building dashboard",
                [
                    self._npm_command(
                        "run", "build", "--prefix", str(ROOT / "src" / "gui" / "dashboard")
                    )
                ],
            )
        elif action == "sandbox-build":
            self._run_steps(
                "Building sandbox image",
                [["docker", "build", "-t", "agent-utopia-world:1", "src/hooks/sandbox"]],
            )
        elif action == "native-build":
            self._run_steps("Building C Win32 host", [self._native_build_command()])
        elif action == "repair":
            self._run_steps(
                "Repairing dependencies and application build",
                [
                    [sys.executable, "-m", "pip", "install", "-r", "requirements.lock"],
                    [sys.executable, "-m", "pip", "install", "-e", "."],
                    [
                        shutil.which("node") or "node",
                        str(ROOT / "src" / "scripts" / "gui.mjs"),
                        "install",
                    ],
                    self._npm_command(
                        "run", "build", "--prefix", str(ROOT / "src" / "gui" / "dashboard")
                    ),
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
        return [
            "powershell.exe",
            "-NoProfile",
            "-File",
            str(ROOT / "src" / "gui" / "native" / "build.ps1"),
        ]

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
        for button in self.query(Button):
            button.disabled = True
        self.query_one("#busy", LoadingIndicator).display = True
        self.query_one("#activity", Label).update(label)
        self._write(f"\n[{label}]")
        self.run_commands(label, commands)

    @work(thread=True, exclusive=True, exit_on_error=False)
    def run_commands(self, label, commands):
        for index, command in enumerate(commands, 1):
            if isinstance(command, Exception):
                self.app.call_from_thread(self._finish, str(command), True)
                return
            description = " ".join(str(part) for part in command)
            if index > 1:
                self.app.call_from_thread(self._write, "")
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
        for button in self.query(Button):
            button.disabled = True
        self.query_one("#busy", LoadingIndicator).display = True
        self.query_one("#activity", Label).update("Checking Docker, Ollama, GPU and disk")
        self._write("\n[System diagnostics]")
        self._diagnostics_worker()

    @work(thread=True, exclusive=True, exit_on_error=False)
    def _diagnostics_worker(self):
        outcomes = []
        executable = ROOT / "dist" / "native" / "build" / "ageatopia.exe"
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
        summary = "\n".join(outcome.splitlines()[0].split(" · ")[0] for outcome in outcomes)
        self.app.call_from_thread(self._set_health, summary)
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
