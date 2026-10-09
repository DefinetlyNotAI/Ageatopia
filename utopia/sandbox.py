import asyncio
import base64
import json
import shutil

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
        self.config = config
        self.available = False
        self.started = False
        self.observe_lock = asyncio.Lock()

    async def _docker(self, *arguments, stdin=None, deadline_seconds=30):
        if not shutil.which("docker"):
            raise SandboxUnavailable("Docker is required. Host execution is disabled.")
        process = await asyncio.create_subprocess_exec(
            "docker",
            *arguments,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(stdin), deadline_seconds)
        except (TimeoutError, asyncio.CancelledError):
            process.kill()
            await process.wait()
            raise
        if process.returncode:
            raise SandboxUnavailable(stderr.decode(errors="replace")[:2000])
        return stdout

    async def start(self, files, tasks):
        # Remove only this run's specifically named prior container on recovery.
        try:
            await self._docker("rm", "-f", self.name)
        except SandboxUnavailable:
            pass
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
            "--tmpfs",
            f"/workspace:rw,nosuid,size={self.config.workspace_mb}m,mode=0777",
            "--tmpfs",
            "/control:rw,noexec,nosuid,size=4m,mode=0700",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=8m,mode=1777",
            self.config.image,
        )
        self.started = True
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
        await self._docker("rm", "-f", self.name)
        self.started = False


def initial_workspace(tasks, existing):
    result = dict(existing)
    for task in tasks:
        for name, text in task.get("files", {}).items():
            result[name] = {"data": base64.b64encode(text.encode()).decode(), "mode": 0o644}
    return result
