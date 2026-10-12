"""Portable executable entry point used by the Nuitka release build."""

import argparse
import json
import os
import signal
import sys
import threading
import traceback
from pathlib import Path

from gui import desktop


def is_compiled():
    return bool(
        getattr(sys, "frozen", False)
        or "__compiled__" in globals()
        or Path(sys.executable).name.lower() == "ageatopia.exe"
    )


def configure_runtime(executable=None, local_app_data=None):
    executable = Path(executable or sys.executable).expanduser().resolve()
    app_root = executable.parent
    local_root = local_app_data or os.environ.get("LOCALAPPDATA")
    if not local_root:
        local_root = Path.home() / "AppData" / "Local"
    user_data_root = Path(local_root).expanduser().resolve() / "Ageatopia"
    desktop.configure_portable_runtime(app_root, user_data_root)
    os.environ["AGEATOPIA_OUTPUT_DIR"] = str(user_data_root / "outputs")
    return app_root, user_data_root


def self_test(app_root=None):
    app_root = Path(app_root or desktop.ROOT).expanduser().resolve()
    assets = {
        "native_executable": app_root / "native" / "ageatopia.exe",
        "webview2_loader": app_root / "native" / "WebView2Loader.dll",
        "dashboard_server": app_root / "dashboard" / "server.js",
        "dashboard_static": app_root / "dashboard" / ".next" / "static",
        "node_runtime": app_root / "runtime" / "node.exe",
        "sandbox_dockerfile": app_root / "sandbox" / "Dockerfile",
    }
    asset_status = {name: path.exists() for name, path in assets.items()}
    dependency_names = (
        "backend.cli",
        "backend.api",
        "fastapi",
        "httpx",
        "pydantic",
        "pyarrow",
        "sqlalchemy",
        "sympy",
        "textual",
        "uvicorn",
    )
    try:
        import fastapi
        import httpx
        import pyarrow
        import pydantic
        import sqlalchemy
        import sympy
        import textual
        import uvicorn

        import backend.api
        import backend.cli

        modules = {
            "backend.api": backend.api,
            "backend.cli": backend.cli,
            "fastapi": fastapi,
            "httpx": httpx,
            "pydantic": pydantic,
            "pyarrow": pyarrow,
            "sqlalchemy": sqlalchemy,
            "sympy": sympy,
            "textual": textual,
            "uvicorn": uvicorn,
        }
        import_status = {name: modules[name] is not None for name in dependency_names}
        import_error = None
    except Exception as error:
        import_status = {name: False for name in dependency_names}
        import_error = f"{type(error).__name__}: {error}"
    report = {
        "ok": all(asset_status.values()) and all(import_status.values()),
        "app_root": str(app_root),
        "data_root": str(desktop.data_root()),
        "assets": asset_status,
        "imports": import_status,
    }
    if import_error:
        report["import_error"] = import_error
    return report


def run_backend(port):
    from backend.cli import main as backend_main

    original_argv = sys.argv
    sys.argv = [original_argv[0], "--port", str(port)]
    try:
        backend_main()
    finally:
        sys.argv = original_argv


def smoke_test(config_path=None):
    supervisor = None
    checks = {}
    errors = []
    try:
        config = desktop.load_config(config_path)
        supervisor = desktop.ProcessSupervisor(config)
        supervisor.start_services()
        base_backend = f"http://127.0.0.1:{config['backend_port']}"
        base_dashboard = f"http://127.0.0.1:{config['dashboard_port']}"
        endpoints = {
            "backend_health": f"{base_backend}/health",
            "dashboard": f"{base_dashboard}/",
            "dashboard_system": f"{base_dashboard}/api/system",
        }
        with desktop.httpx.Client(timeout=15, trust_env=False) as client:
            for name, url in endpoints.items():
                response = client.get(url)
                checks[name] = {"status": response.status_code}
                response.raise_for_status()
                if name == "dashboard_system":
                    system = response.json()
                    checks[name]["docker_ready"] = system.get("docker_ready")
                    checks[name]["models"] = len(system.get("models", []))
    except Exception as error:
        errors.append(f"{type(error).__name__}: {error}")
        traceback.print_exc()
    finally:
        if supervisor:
            try:
                supervisor.stop_services()
            except Exception as error:
                errors.append(f"shutdown: {type(error).__name__}: {error}")
    return {
        "ok": not errors and len(checks) == 3,
        "checks": checks,
        "errors": errors,
        "services_stopped": supervisor is not None and not supervisor.processes,
    }


def headless(config_path=None):
    config = desktop.load_config(config_path)
    supervisor = desktop.ProcessSupervisor(config)
    stopping = threading.Event()
    previous_handlers = {}

    def request_stop(_signum, _frame):
        stopping.set()

    for signum in (signal.SIGINT, getattr(signal, "SIGTERM", None)):
        if signum is not None:
            previous_handlers[signum] = signal.signal(signum, request_stop)
    try:
        supervisor.start_services()
        supervisor.logger.info("Headless release services are ready")
        while not stopping.wait(0.5):
            for name in ("backend", "dashboard"):
                entry = supervisor.processes.get(name)
                if entry is None or entry[0].poll() is not None:
                    raise RuntimeError(f"{name.title()} exited during headless verification")
    except KeyboardInterrupt:
        stopping.set()
    finally:
        supervisor.stop_services()
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
    return 0


def _parser():
    parser = argparse.ArgumentParser(description="Launch the Ageatopia portable desktop app")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--backend", action="store_true", help=argparse.SUPPRESS)
    modes.add_argument("--self-test", action="store_true", help="Check bundled imports and assets")
    modes.add_argument(
        "--smoke-test", action="store_true", help="Start and check packaged services"
    )
    modes.add_argument(
        "--headless", action="store_true", help="Run services without the desktop window"
    )
    parser.add_argument("--port", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--window-only", action="store_true")
    return parser


def main():
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass
    parser = _parser()
    args = parser.parse_args()
    backend_output = os.environ.get("AGEATOPIA_OUTPUT_DIR") if args.backend else None
    if is_compiled():
        configure_runtime()
    if backend_output is not None:
        os.environ["AGEATOPIA_OUTPUT_DIR"] = backend_output
    if args.port is not None and not args.backend:
        parser.error("--port can only be used with --backend")
    if args.backend:
        run_backend(args.port if args.port is not None else 8000)
        return 0
    if args.self_test:
        report = self_test()
        print(json.dumps(report, ensure_ascii=False, sort_keys=True), flush=True)
        return 0 if report["ok"] else 1
    if args.smoke_test:
        report = smoke_test(args.config)
        print(json.dumps(report, ensure_ascii=False, sort_keys=True), flush=True)
        return 0 if report["ok"] else 1
    if args.headless:
        return headless(args.config)

    original_argv = sys.argv
    desktop_argv = [original_argv[0]]
    if args.config is not None:
        desktop_argv.extend(("--config", str(args.config)))
    if args.window_only:
        desktop_argv.append("--window-only")
    sys.argv = desktop_argv
    try:
        desktop.main()
    except BaseException as error:
        if isinstance(error, SystemExit):
            raise
        desktop.show_startup_error(error)
        return 1
    finally:
        sys.argv = original_argv
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
