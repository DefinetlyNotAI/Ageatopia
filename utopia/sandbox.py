import asyncio
import base64
import json
import os
import shutil
import subprocess
from pathlib import Path

from utopia.resources import MemoryGuard, MemoryPressure

CLIENT = (
    "import socket,sys; s=socket.socket(socket.AF_UNIX); "
    "s.connect('/control/socket'); s.sendall(sys.stdin.buffer.read()+b'\\n'); "
    "f=s.makefile('rb'); sys.stdout.buffer.write(f.readline())"
)


class SandboxUnavailable(RuntimeError):
    pass


class DockerSandbox:
    def __init__(self, run_id, config):
        self.name = f"utopia-{run_id}"
        self.volume = f"{self.name}-workspace"
        self.config = config
        self.available = False
        self.started = False
        self.observe_lock = asyncio.Lock()
        self.command_limit = asyncio.Semaphore(2)

    async def _docker(self, *arguments, stdin=None, deadline_seconds=30):
        if not shutil.which("docker"):
            raise SandboxUnavailable("Docker is required. Host execution is disabled.")
        async with self.command_limit:
            if arguments[0] not in {"rm", "stop", "kill"} and arguments[:2] != ("volume", "rm"):
                try:
                    MemoryGuard().check(64)
                except MemoryPressure as error:
                    raise SandboxUnavailable(str(error)) from error
            try:
                process = await asyncio.create_subprocess_exec(
                    "docker",
                    *arguments,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
            except OSError as error:
                raise SandboxUnavailable(self._docker_start_error(error)) from error
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(stdin), deadline_seconds
                )
            except (TimeoutError, asyncio.CancelledError):
                process.kill()
                await process.wait()
                raise
        if process.returncode:
            message = stderr.decode(errors="replace")[:2000]
            if "runtime: cannot allocate memory" in message.lower():
                message = (
                    "Docker's Go CLI could not allocate host memory. Close memory-heavy "
                    "applications or increase the Windows paging file, then automatic "
                    f"recovery will retry this sandbox. Details: {message}"
                )
            raise SandboxUnavailable(message)
        return stdout

    @staticmethod
    def _docker_start_error(error):
        message = str(error)
        if (
            getattr(error, "winerror", None) == 1455
            or "paging file is too small" in message.lower()
        ):
            return (
                "Docker's CLI could not start because host memory or the Windows paging file "
                "is exhausted. Close memory-heavy applications or increase the paging file. "
                f"Details: {message}"
            )
        return f"Docker could not start: {message}"

    async def start(self, files, tasks):
        # Remove only this run's specifically named prior container on recovery.
        try:
            await self._docker("rm", "-f", self.name)
        except SandboxUnavailable:
            pass
        await self.remove_volume()
        await self._docker(
            "volume",
            "create",
            "--driver",
            "local",
            "--label",
            "agent-utopia.world=true",
            "--opt",
            "type=tmpfs",
            "--opt",
            "device=tmpfs",
            "--opt",
            f"o=rw,nosuid,size={self.config.workspace_mb}m,mode=0777",
            self.volume,
        )
        self.started = True
        await self._docker(
            "run",
            "-d",
            "--name",
            self.name,
            "--label",
            "agent-utopia.world=true",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--cap-add",
            "SETUID",
            "--cap-add",
            "SETGID",
            "--cap-add",
            "CHOWN",
            "--cap-add",
            "FOWNER",
            "--cap-add",
            "KILL",
            "--cap-add",
            "DAC_OVERRIDE",
            "--cap-add",
            "SYS_PTRACE",
            "--security-opt",
            "no-new-privileges",
            "--memory",
            f"{self.config.memory_mb}m",
            "--memory-swap",
            f"{self.config.memory_mb}m",
            "--cpus",
            str(self.config.cpu_count),
            "--pids-limit",
            str(self.config.pid_limit),
            "--mount",
            f"type=volume,src={self.volume},dst=/workspace,volume-nocopy",
            "--tmpfs",
            "/control:rw,noexec,nosuid,size=4m,mode=0700",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=8m,mode=1777",
            self.config.image,
        )
        for attempt in range(20):
            try:
                await self.rpc({"operation": "processes"})
                break
            except SandboxUnavailable:
                if attempt == 19:
                    raise
                await asyncio.sleep(0.1)
        await self.rpc({"operation": "restore", "files": files})
        for task in tasks:
            if task.get("service_port"):
                await self.rpc(
                    {"operation": "service", "path": f"challenges/{task['id']}/service.py"}
                )
            if task.get("compile") and task["compile"] in files:
                path = task["compile"]
                # Generated challenge path, never interpolated agent input.
                result = await self.execute("setup", f"cc '{path}' -o '{path[:-2]}'")
                if result["exit_code"] != 0:
                    raise SandboxUnavailable("Challenge binary compilation failed")
                await self.execute("setup", f"rm '{path}'")
        self.available = True

    async def rpc(self, payload):
        result = json.loads(
            await self._docker(
                "exec",
                "-i",
                "--user",
                "0",
                self.name,
                "python",
                "-c",
                CLIENT,
                stdin=json.dumps(payload).encode(),
                deadline_seconds=self.config.timeout_seconds + 30,
            )
        )
        if "error" in result:
            raise SandboxUnavailable(result["error"])
        return result

    async def recover(self, files=None, tasks=None):
        """Resume this run's existing container and workspace after Docker recovers."""
        if not self.started:
            raise SandboxUnavailable("This run has no retained sandbox to recover")
        running = await self._docker("inspect", "--format", "{{.State.Running}}", self.name)
        if running.decode(errors="replace").strip().lower() != "true":
            await self._docker("start", self.name)
            for attempt in range(20):
                try:
                    await self.rpc({"operation": "processes"})
                    break
                except SandboxUnavailable:
                    if attempt == 19:
                        raise
                    await asyncio.sleep(0.1)
            # The workspace is a tmpfs volume: a container restart needs the latest snapshot.
            if files is not None:
                await self.rpc({"operation": "restore", "files": files})
            for task in tasks or []:
                if task.get("service_port"):
                    await self.rpc(
                        {"operation": "service", "path": f"challenges/{task['id']}/service.py"}
                    )
        await self.rpc({"operation": "processes"})
        self.available = True

    async def execute(self, identity, command):
        return await self.rpc(
            {
                "operation": "execute",
                "owner": identity,
                "command": command,
                "timeout": self.config.timeout_seconds,
                "output_bytes": self.config.output_bytes,
            }
        )

    async def kill(self, identity, permanent=False):
        return await self.rpc({"operation": "kill", "owner": identity, "permanent": permanent})

    async def launch(self, identity, command):
        return await self.rpc({"operation": "launch", "owner": identity, "command": command})

    async def snapshot(self):
        async with self.observe_lock:
            return await self.rpc({"operation": "snapshot"})

    async def stop(self):
        self.available = False
        try:
            await self._docker("rm", "-f", self.name)
        except SandboxUnavailable as error:
            if "no such container" not in str(error).lower():
                raise
        await self.remove_volume()
        self.started = False

    async def remove_volume(self):
        names = await self._docker("volume", "ls", "-q", "--filter", f"name=^{self.volume}$")
        if self.volume in names.decode().splitlines():
            await self._docker("volume", "rm", self.volume)

    def live_directory(self):
        if not self.available:
            raise SandboxUnavailable("The sandbox is not running; open its recorded snapshot")
        if os.name != "nt":
            raise SandboxUnavailable("Live Explorer access requires Docker Desktop's WSL backend")
        root = (
            Path("//wsl.localhost/docker-desktop/mnt/docker-desktop-disk/data/docker/volumes")
            / self.volume
            / "_data"
        )
        if not root.is_dir():
            raise SandboxUnavailable(
                "Live workspace is unavailable through Docker Desktop's WSL filesystem; "
                "open the recorded snapshot instead"
            )
        return root


def initial_workspace(tasks, existing):
    result = dict(existing)
    for task in tasks:
        for name, text in task.get("files", {}).items():
            result[name] = {"data": base64.b64encode(text.encode()).decode(), "mode": 0o644}
    return result
