"""Native Windows desktop launcher and shared local process helpers."""

import json
import logging
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / ".data" / "desktop.json"


def load_config(path=DEFAULT_CONFIG):
    path = Path(path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(json.dumps(default_config(), indent=2) + "\n", encoding="utf-8")
    return validate_config(json.loads(path.read_text(encoding="utf-8")), path)


def default_config():
    return {
        "data_dir": str(ROOT / ".data"),
        "backend_port": 8000,
        "dashboard_port": 3000,
        "manage_ollama": True,
        "ollama_cache_ram_mb": 1024,
    }


def validate_config(values, path=DEFAULT_CONFIG):
    if not isinstance(values, dict):
        raise ValueError("Desktop configuration must be a JSON object")
    config = {**default_config(), **values}
    for key in ("backend_port", "dashboard_port"):
        port = config[key]
        if isinstance(port, bool) or not isinstance(port, int) or not 1024 <= port <= 65535:
            raise ValueError(f"{key} must be an integer between 1024 and 65535")
    if config["backend_port"] == config["dashboard_port"]:
        raise ValueError("backend_port and dashboard_port must be different")
    if not isinstance(config["manage_ollama"], bool):
        raise ValueError("manage_ollama must be true or false")
    cache = config["ollama_cache_ram_mb"]
    if isinstance(cache, bool) or not isinstance(cache, int) or not 0 <= cache <= 16384:
        raise ValueError("ollama_cache_ram_mb must be an integer between 0 and 16384")
    if config["manage_ollama"] and 11434 in (config["backend_port"], config["dashboard_port"]):
        raise ValueError("Application ports must not conflict with local Ollama port 11434")
    config["data_dir"] = str(Path(config["data_dir"]).expanduser().resolve())
    config["config_path"] = str(Path(path).expanduser().resolve())
    return config


def setup_logging(data_dir, name):
    log_dir = Path(data_dir) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(f"utopia.{name}")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    log_path = (log_dir / f"{name}.jsonl").resolve()
    for handler in tuple(logger.handlers):
        if isinstance(handler, logging.FileHandler) and Path(handler.baseFilename) != log_path:
            logger.removeHandler(handler)
            handler.close()
    if not any(
        isinstance(handler, logging.FileHandler) and Path(handler.baseFilename) == log_path
        for handler in logger.handlers
    ):
        handler = logging.FileHandler(log_path, encoding="utf-8")
        handler.setFormatter(JsonLogFormatter())
        logger.addHandler(handler)
    return logger


class JsonLogFormatter(logging.Formatter):
    def format(self, record):
        return json.dumps(
            {
                "timestamp": self.formatTime(record),
                "level": record.levelname,
                "event": record.getMessage(),
            },
            ensure_ascii=False,
        )


class ProcessSupervisor:
    """Own only processes started by this instance; never adopt a listening server."""

    def __init__(self, config):
        self.config = config
        self.data_dir = Path(config["data_dir"])
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.logger = setup_logging(self.data_dir, "desktop")
        self.processes = {}
        self.external_ollama = False

    @staticmethod
    def _assert_port_available(port):
        with socket.socket() as probe:
            if os.name == "nt":
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            try:
                probe.bind(("127.0.0.1", port))
            except OSError as error:
                raise RuntimeError(f"127.0.0.1:{port} is already in use") from error

    def _spawn(self, name, command, cwd, env):
        port = 11434 if name == "ollama" else self.config[f"{name}_port"]
        self._assert_port_available(port)
        log_file = open(self.data_dir / "logs" / f"{name}.log", "ab", buffering=0)
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            process = subprocess.Popen(
                command,
                cwd=cwd,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                creationflags=flags,
            )
        except BaseException:
            log_file.close()
            raise
        self.processes[name] = (process, log_file)
        self.logger.info(json.dumps({"action": "started", "service": name, "pid": process.pid}))
        return process

    def start_ollama(self):
        if "ollama" in self.processes and self.processes["ollama"][0].poll() is None:
            return
        try:
            response = httpx.get("http://127.0.0.1:11434/api/tags", timeout=2, trust_env=False)
            response.raise_for_status()
        except httpx.HTTPError:
            pass
        else:
            self.external_ollama = True
            self.logger.info("Existing Ollama server retained; its cache settings are unchanged")
            return
        executable = shutil_which("ollama")
        if not executable:
            raise RuntimeError("Install Ollama or set manage_ollama=false for an external provider")
        env = os.environ.copy()
        env["OLLAMA_HOST"] = "127.0.0.1:11434"
        env["LLAMA_ARG_CACHE_RAM"] = str(self.config["ollama_cache_ram_mb"])
        self.external_ollama = False
        process = self._spawn("ollama", [executable, "serve"], ROOT, env)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError("Ollama exited before becoming ready; inspect logs/ollama.log")
            try:
                response = httpx.get("http://127.0.0.1:11434/api/tags", timeout=2, trust_env=False)
                response.raise_for_status()
                self.logger.info(
                    f"Managed Ollama ready with {self.config['ollama_cache_ram_mb']} MiB host cache"
                )
                return
            except httpx.HTTPError:
                time.sleep(0.25)
        raise TimeoutError("Ollama did not become ready within 30 seconds")

    def start_services(self):
        if all(
            name in self.processes and self.processes[name][0].poll() is None
            for name in ("backend", "dashboard")
        ):
            return
        if self.processes:
            self.stop_services()
        node = shutil_which("node")
        next_cli = ROOT / "dashboard" / "node_modules" / "next" / "dist" / "bin" / "next"
        if not node or not next_cli.is_file():
            raise RuntimeError("Dashboard dependencies are missing. Run utopia-maintenance first.")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        env["UTOPIA_DATA"] = str(self.data_dir)
        env["UTOPIA_API_URL"] = f"http://127.0.0.1:{self.config['backend_port']}"
        try:
            if self.config["manage_ollama"]:
                self.start_ollama()
            self._spawn(
                "backend",
                [sys.executable, "-m", "utopia.cli", "--port", str(self.config["backend_port"])],
                ROOT,
                env,
            )
            self._spawn(
                "dashboard",
                [
                    node,
                    str(next_cli),
                    "start",
                    "--hostname",
                    "127.0.0.1",
                    "--port",
                    str(self.config["dashboard_port"]),
                ],
                ROOT / "dashboard",
                env,
            )
            self.wait_ready()
        except BaseException:
            self.stop_services()
            raise

    def wait_ready(self, timeout=90):
        base = time.monotonic()
        endpoints = {
            "backend": f"http://127.0.0.1:{self.config['backend_port']}/health",
            "dashboard": f"http://127.0.0.1:{self.config['dashboard_port']}/",
        }
        ready = set()
        with httpx.Client(timeout=2) as client:
            while time.monotonic() - base < timeout:
                for name, url in endpoints.items():
                    if name in ready:
                        continue
                    process = self.processes.get(name, (None, None))[0]
                    if process is None or process.poll() is not None:
                        raise RuntimeError(f"{name.title()} exited before becoming ready")
                    try:
                        response = client.get(url)
                        if response.is_success:
                            ready.add(name)
                            self.logger.info(json.dumps({"action": "ready", "service": name}))
                    except httpx.HTTPError:
                        pass
                if len(ready) == len(endpoints):
                    return
                time.sleep(0.25)
        raise TimeoutError("Application services did not become ready within 90 seconds")

    def stop_services(self):
        for name in ("window", "dashboard", "backend", "ollama"):
            entry = self.processes.pop(name, None)
            if not entry:
                continue
            process, log_file = entry
            if process.poll() is None:
                try:
                    if name == "ollama":
                        try:
                            with httpx.Client(timeout=10, trust_env=False) as client:
                                response = client.get("http://127.0.0.1:11434/api/ps")
                                response.raise_for_status()
                                models = {m["name"] for m in response.json().get("models", [])}
                                for model in models:
                                    response = client.post(
                                        "http://127.0.0.1:11434/api/generate",
                                        json={"model": model, "keep_alive": 0},
                                    )
                                    response.raise_for_status()
                        except (httpx.HTTPError, ValueError, KeyError) as error:
                            self.logger.warning(f"Ollama model release failed: {error}")
                        if os.name == "nt" and process.poll() is None:
                            subprocess.run(
                                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                capture_output=True,
                                timeout=8,
                                check=False,
                                creationflags=subprocess.CREATE_NO_WINDOW,
                            )
                    if name == "backend":
                        try:
                            OperatorApi(self.config).request("POST", "/shutdown")
                            self.logger.info(
                                json.dumps({"action": "graceful-shutdown", "service": name})
                            )
                            process.wait(timeout=30)
                        except Exception as error:
                            self.logger.warning(
                                json.dumps(
                                    {
                                        "action": "graceful-shutdown-failed",
                                        "service": name,
                                        "error": str(error),
                                    }
                                )
                            )
                    if process.poll() is None:
                        process.terminate()
                        process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            log_file.close()
            self.logger.info(json.dumps({"action": "stopped", "service": name}))

    def status(self):
        statuses = {
            name: "running" if process.poll() is None else f"exited ({process.returncode})"
            for name, (process, _) in self.processes.items()
        }
        if self.external_ollama:
            statuses["ollama"] = "external (not managed)"
        return statuses

    def launch_window(self):
        if "window" in self.processes and self.processes["window"][0].poll() is None:
            return
        env = os.environ.copy()
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        log_file = open(self.data_dir / "logs" / "window.log", "ab", buffering=0)
        try:
            process = subprocess.Popen(
                native_command(f"http://127.0.0.1:{self.config['dashboard_port']}", self.data_dir),
                cwd=ROOT,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                creationflags=flags,
            )
        except BaseException:
            log_file.close()
            raise
        self.processes["window"] = (process, log_file)


def shutil_which(command):
    import shutil

    return shutil.which(command)


class OperatorApi:
    def __init__(self, config):
        self.config = config
        self.base = f"http://127.0.0.1:{config['backend_port']}"
        token_path = Path(config["data_dir"]) / "operator.token"
        if not token_path.is_file():
            raise RuntimeError("Operator token is not available. Start the backend first.")
        self.token = token_path.read_text(encoding="utf-8").strip()

    def request(self, method, path, **kwargs):
        response = httpx.request(
            method,
            f"{self.base}/{path.lstrip('/')}",
            headers={"Authorization": f"Bearer {self.token}"},
            timeout=15,
            **kwargs,
        )
        if response.is_error:
            try:
                detail = response.json().get("detail", response.text)
            except ValueError:
                detail = response.text
            raise RuntimeError(f"{response.status_code}: {detail}")
        return response.json()


def native_command(url, data_dir):
    executable = ROOT / "native" / "build" / "ageatopia.exe"
    if not executable.is_file():
        raise RuntimeError("Native C host is missing. Use Build native host in maintenance.")
    return [str(executable), "--url", url, "--data-dir", str(data_dir)]


def open_window(url, data_dir):
    with subprocess.Popen(native_command(url, data_dir), cwd=ROOT) as process:
        try:
            result = process.wait()
            if result:
                raise RuntimeError(f"Native desktop host exited with code {result}")
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=5)


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Launch Agent Utopia as a native desktop app")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--window-only", action="store_true")
    args = parser.parse_args()
    config = load_config(args.config)
    native_command(f"http://127.0.0.1:{config['dashboard_port']}", config["data_dir"])
    if args.window_only:
        open_window(f"http://127.0.0.1:{config['dashboard_port']}", config["data_dir"])
        return
    supervisor = ProcessSupervisor(config)
    try:
        supervisor.start_services()
        open_window(f"http://127.0.0.1:{config['dashboard_port']}", config["data_dir"])
    except KeyboardInterrupt:
        pass
    finally:
        supervisor.stop_services()


if __name__ == "__main__":
    main()
