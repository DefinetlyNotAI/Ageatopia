"""Textual control room for local services and simulation worlds."""

import json
import os
from pathlib import Path

from textual import work
from textual.app import App, ComposeResult
from textual.containers import Grid, Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    LoadingIndicator,
    Log,
    Static,
    TextArea,
)

from utopia.config import WorldConfig
from utopia.desktop import (
    DEFAULT_CONFIG,
    OperatorApi,
    ProcessSupervisor,
    load_config,
    setup_logging,
    validate_config,
)


class DeleteWorldScreen(ModalScreen[str | None]):
    CSS = """
    DeleteWorldScreen { align: center middle; }
    #delete-dialog {
        width: 72;
        height: auto;
        max-height: 80%;
        padding: 1 2;
        border: round $error;
        background: $surface;
    }
    #delete-id { margin: 1 0; }
    #delete-actions { height: auto; align-horizontal: right; }
    #delete-actions Button { margin-left: 1; }
    """

    def __init__(self, world_id):
        super().__init__()
        self.world_id = world_id

    def compose(self) -> ComposeResult:
        with Vertical(id="delete-dialog"):
            yield Label("Permanently delete this world?", id="delete-title")
            yield Static(f"Type the full world ID to erase its records and files:\n{self.world_id}")
            yield Input(placeholder="World ID", id="delete-id")
            with Horizontal(id="delete-actions"):
                yield Button("Cancel", id="delete-cancel")
                yield Button(
                    "Permanently delete", id="delete-confirm", variant="error", disabled=True
                )

    def on_input_changed(self, event):
        if event.input.id == "delete-id":
            self.query_one("#delete-confirm", Button).disabled = event.value != self.world_id

    def on_button_pressed(self, event):
        if event.button.id == "delete-cancel":
            self.dismiss(None)
        elif event.button.id == "delete-confirm":
            entered = self.query_one("#delete-id", Input).value
            self.dismiss(entered if entered == self.world_id else None)


class ControlApp(App):
    TITLE = "Agent Utopia · Control Room"
    CSS = """
    Screen { layout: vertical; }
    #service-status { width: 1fr; height: 3; padding: 0 2; color: $text-muted; }
    #service-bar { height: 3; }
    #service-progress { width: 4; height: 1; }
    #main { height: 1fr; }
    #world-list { width: 48%; }
    #config-panel { width: 52%; padding: 0 1; }
    #worlds { height: 1fr; min-height: 8; }
    #selected-status { height: 2; padding: 0 1; color: $text-muted; }
    #world-config { height: 1fr; border: round $primary-darken-2; }
    #app-config { height: 1fr; border: round $primary-darken-2; }
    .actions { height: auto; }
    .actions Button { margin-right: 1; width: 1fr; min-width: 0; }
    #service-actions { grid-size: 2; grid-gutter: 0 1; height: 6; }
    #operator-log { height: 10; border: round $primary-darken-2; }
    """

    BINDINGS = [("r", "refresh", "Refresh")]

    def __init__(self, config_path=DEFAULT_CONFIG):
        super().__init__()
        self.config_path = Path(config_path)
        self.config = load_config(self.config_path)
        self.supervisor = ProcessSupervisor(self.config)
        self.logger = setup_logging(self.config["data_dir"], "control")
        self.runs = []
        self.selected_run = None
        self.last_api_error = None
        self.service_busy = False

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="service-bar"):
            yield Label(self._stopped_text(), id="service-status")
            yield LoadingIndicator(id="service-progress")
        with Horizontal(id="main"):
            with VerticalScroll(id="world-list"):
                yield DataTable(id="worlds", cursor_type="row")
                yield Label("No world selected", id="selected-status")
                with Grid(classes="actions", id="service-actions"):
                    yield Button("Start services", id="services-start", variant="success")
                    yield Button("Stop services", id="services-stop", variant="error")
                    yield Button("Launch desktop", id="desktop", variant="primary")
                with Horizontal(classes="actions"):
                    yield Button("Start", id="start")
                    yield Button("Pause", id="pause")
                    yield Button("Resume", id="resume")
                with Horizontal(classes="actions"):
                    yield Button("Terminate", id="terminate", variant="error")
                    yield Button("Delete world", id="delete", variant="error")
                with Horizontal(classes="actions"):
                    yield Button("Refresh worlds", id="refresh")
            with VerticalScroll(id="config-panel"):
                yield Label("Application configuration · saved to JSON")
                yield TextArea(
                    json.dumps(
                        {
                            "data_dir": self.config["data_dir"],
                            "backend_port": self.config["backend_port"],
                            "dashboard_port": self.config["dashboard_port"],
                            "manage_ollama": self.config["manage_ollama"],
                            "ollama_cache_ram_mb": self.config["ollama_cache_ram_mb"],
                        },
                        indent=2,
                    ),
                    id="app-config",
                    language="json",
                )
                yield Button("Save application config", id="save-app-config")
                yield Label("New world configuration · JSON", id="config-label")
                yield TextArea('{\n  "name": "New world"\n}', id="world-config", language="json")
                yield Button("Save and create world", id="create-config", variant="primary")
        yield Log(id="operator-log", auto_scroll=True, max_lines=500)
        yield Footer()

    def on_mount(self):
        self.query_one("#service-progress", LoadingIndicator).display = False
        table = self.query_one("#worlds", DataTable)
        table.add_columns("World", "Status", "Time", "Agents")
        self.refresh_runs()
        self.set_interval(5, self.refresh_runs)
        self._log("Control room ready. Services start only when requested.")

    def _log(self, message):
        self.query_one("#operator-log", Log).write_line(message)

    def _service_label(self):
        statuses = self.supervisor.status()
        if statuses:
            text = " · ".join(f"{name}: {status}" for name, status in statuses.items())
        else:
            text = self._stopped_text()
        self.query_one("#service-status", Label).update(text)

    def _stopped_text(self):
        return (
            "Services: stopped · backend "
            f"127.0.0.1:{self.config['backend_port']} · dashboard "
            f"127.0.0.1:{self.config['dashboard_port']}"
        )

    def _selected_id(self):
        table = self.query_one("#worlds", DataTable)
        if self.selected_run and any(run["id"] == self.selected_run for run in self.runs):
            return self.selected_run
        if table.row_count and 0 <= table.cursor_row < len(self.runs):
            self.selected_run = self.runs[table.cursor_row]["id"]
        return self.selected_run

    def on_data_table_row_highlighted(self, event):
        if event.data_table.id == "worlds" and event.cursor_row < len(self.runs):
            self.selected_run = self.runs[event.cursor_row]["id"]
            self._update_selected_status()

    def refresh_runs(self):
        self._refresh_worker()

    @work(thread=True, exclusive=True)
    def _refresh_worker(self):
        if self.supervisor.status().get("backend") != "running":
            return
        try:
            api = OperatorApi(self.config)
            runs = api.request("GET", "/runs")
            self.last_api_error = None
            self.app.call_from_thread(self._render_runs, runs)
        except Exception as error:
            message = str(error)
            if message != self.last_api_error:
                self.last_api_error = message
                self.app.call_from_thread(self._show_error, message)

    def _render_runs(self, runs):
        old = self._selected_id()
        self.runs = runs
        table = self.query_one("#worlds", DataTable)
        table.clear()
        for run in runs:
            agents = run.get("agents", [])
            live = sum(a.get("state") not in {"TERMINATED"} for a in agents)
            table.add_row(
                run.get("name", "Unnamed"),
                run.get("status", "Unknown"),
                self._duration(run.get("sim_ms", 0)),
                str(live),
                key=run["id"],
            )
        if old:
            self.selected_run = old if any(run["id"] == old for run in runs) else None
        if self.selected_run:
            index = next(i for i, run in enumerate(runs) if run["id"] == self.selected_run)
            table.move_cursor(row=index)
        elif runs:
            self.selected_run = runs[0]["id"]
            table.move_cursor(row=0)
        self._update_selected_status()
        self._service_label()

    def _update_selected_status(self):
        run = next((item for item in self.runs if item["id"] == self.selected_run), None)
        if not run:
            summary = "No world selected"
        else:
            agents = run.get("agents", [])
            living = sum(agent.get("state") != "TERMINATED" for agent in agents)
            queued = sum(bool(agent.get("queued")) for agent in agents)
            metrics = run.get("metrics", {})
            gpu = metrics.get("gpu")
            gpu_text = (
                f"GPU {gpu['utilization_percent']}% · {gpu['used_mb']}/{gpu['total_mb']} MiB"
                if gpu
                else "GPU unavailable"
            )
            completed = sum(task.get("state") == "COMPLETED" for task in run.get("tasks", []))
            summary = (
                f"{run.get('name', 'World')} · {run.get('status', 'Unknown')} · "
                f"{living} agents · {completed}/{len(run.get('tasks', []))} tasks · "
                f"queue {queued} · {gpu_text}"
            )
        self.query_one("#selected-status", Label).update(summary)

    @staticmethod
    def _duration(milliseconds):
        seconds = max(0, int(milliseconds or 0) // 1000)
        return f"{seconds // 3600:02}:{seconds % 3600 // 60:02}:{seconds % 60:02}"

    def _show_error(self, message):
        self._log(f"ERROR · {message}")
        self.logger.error(message)
        self.notify(message, severity="error", timeout=8)

    @work(thread=True, exclusive=True)
    def _perform(self, method, path, json_body=None, success=None):
        try:
            result = OperatorApi(self.config).request(method, path, json=json_body)
            self.logger.info(json.dumps({"action": path, "result": "success"}))
            self.app.call_from_thread(self._show_success, success or "Request completed")
            if path == "/runs" or path.startswith("/runs/"):
                self.refresh_runs()
            return result
        except Exception as error:
            self.app.call_from_thread(self._show_error, str(error))

    def _show_success(self, message):
        self._log(f"OK · {message}")
        self.logger.info(message)
        self.notify(message, severity="information")

    def action_refresh(self):
        self.refresh_runs()

    def on_button_pressed(self, event):
        button = event.button.id
        if button == "refresh":
            self.refresh_runs()
        elif button == "services-start":
            self._begin_service_task("start")
        elif button == "services-stop":
            self._begin_service_task("stop")
        elif button == "desktop":
            self._begin_service_task("desktop")
        elif button in {"create", "create-config"}:
            self._create_run()
        elif button == "save-app-config":
            self._save_app_config()
        elif button == "delete":
            run_id = self._selected_id()
            if run_id:
                self.push_screen(
                    DeleteWorldScreen(run_id),
                    lambda typed: self._delete_world(run_id, typed),
                )
            else:
                self.notify("Select a world first", severity="warning")
        elif button in {"start", "pause", "resume", "terminate"}:
            self._control_run(button)

    def _begin_service_task(self, action):
        if self.service_busy:
            return
        self.service_busy = True
        self.query_one("#service-progress", LoadingIndicator).display = True
        verb = "Starting" if action in {"start", "desktop"} else "Stopping"
        self.query_one("#service-status", Label).update(f"Services: {verb.lower()}…")
        self._service_action(action)

    @work(thread=True, exclusive=True)
    def _service_action(self, action):
        try:
            if action == "start":
                self.supervisor.start_services()
            elif action == "stop":
                self.supervisor.stop_services()
            else:
                self.supervisor.start_services()
                self.supervisor.launch_window()
            self.app.call_from_thread(self._show_success, f"Services {action}ed")
            self.app.call_from_thread(self._finish_service_task)
        except Exception as error:
            self.app.call_from_thread(self._finish_service_task)
            self.app.call_from_thread(self._show_error, str(error))

    def _finish_service_task(self):
        self.service_busy = False
        self.query_one("#service-progress", LoadingIndicator).display = False
        self._service_label()

    def _create_run(self):
        try:
            config = json.loads(self.query_one("#world-config", TextArea).text)
            WorldConfig.model_validate(config)
            self._perform("POST", "/runs", config, "World created")
        except Exception as error:
            self._show_error(f"World JSON is invalid: {error}")

    def _save_app_config(self):
        try:
            values = json.loads(self.query_one("#app-config", TextArea).text)
            updated = validate_config(values, self.config_path)
            saved = {
                key: updated[key]
                for key in (
                    "data_dir",
                    "backend_port",
                    "dashboard_port",
                    "manage_ollama",
                    "ollama_cache_ram_mb",
                )
            }
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.config_path.with_suffix(self.config_path.suffix + ".tmp")
            temporary.write_text(json.dumps(saved, indent=2) + "\n", encoding="utf-8")
            os.replace(temporary, self.config_path)
            self.supervisor.stop_services()
            self.config = load_config(self.config_path)
            self.supervisor = ProcessSupervisor(self.config)
            self.logger = setup_logging(self.config["data_dir"], "control")
            self._service_label()
            self._show_success("Application config saved. Relaunch services to apply it.")
        except Exception as error:
            self._show_error(f"Application config is invalid: {error}")

    def _delete_world(self, world_id, typed_id):
        if typed_id != world_id:
            return
        from urllib.parse import quote

        encoded_id = quote(world_id, safe="")
        self._perform(
            "DELETE",
            f"/runs/{encoded_id}?confirm={encoded_id}",
            success="World and its stored data permanently deleted",
        )

    def _control_run(self, action):
        run_id = self._selected_id()
        if not run_id:
            self.notify("Select a world first", severity="warning")
            return
        self._perform(
            "POST", f"/runs/{run_id}/control/{action}", success=f"World {action} requested"
        )

    def on_unmount(self):
        self.supervisor.stop_services()


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Open the Agent Utopia control room")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    args = parser.parse_args()
    ControlApp(args.config).run()


if __name__ == "__main__":
    main()
