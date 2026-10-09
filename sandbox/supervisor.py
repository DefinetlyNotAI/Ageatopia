"""Trusted container supervisor; root-owned, not exposed as an agent tool.

Runs no agent code. Commands execute under distinct unprivileged UIDs.
The supervisor owns its control socket and immutable change stream in /control.
"""

import base64
import ctypes
import hashlib
import json
import os
import selectors
import signal
import socket
import struct
import subprocess
import threading
import time
import uuid
from pathlib import Path

ROOT = Path("/workspace")
CONTROL = Path("/control")
GROUPS = {}
BACKGROUND = []
UIDS = {}
ENDED = set()
LOCK = threading.RLock()
OBSERVER_ID = 0
OBSERVER_EPOCH = uuid.uuid4().hex


class EventBuffer(list):
    def append(self, item):
        global OBSERVER_ID
        with LOCK:
            OBSERVER_ID += 1
            super().append({**item, "observer_id": OBSERVER_ID})


CHANGES = EventBuffer()
SEQUENCE = 0
LAST = {}
PROCESSES = {}
TRACE_OFFSETS = {}
TRACE_LOCK = threading.Lock()
PROCESS_CHANGES = EventBuffer()
OBSERVER_ERRORS = []
SNAPSHOT_LOCK = threading.Lock()


def snapshot():
    result = {}
    for directory, folders, files in os.walk(ROOT, followlinks=False):
        folders[:] = [name for name in folders if not Path(directory, name).is_symlink()]
        for name in files:
            path = Path(directory, name)
            try:
                if path.is_symlink():
                    result[str(path.relative_to(ROOT))] = {"symlink": os.readlink(path)}
                    continue
                if not path.is_file():
                    continue
                data = path.read_bytes()
                result[str(path.relative_to(ROOT))] = {
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "size": len(data),
                    "data": base64.b64encode(data).decode(),
                    "mode": path.stat().st_mode & 0o777,
                }
            except (OSError, ValueError):
                continue
    return result


def observe():
    global LAST, SEQUENCE
    with SNAPSHOT_LOCK, LOCK:
        current = snapshot()
        for name in sorted(set(current) | set(LAST)):
            if current.get(name) != LAST.get(name):
                SEQUENCE += 1
                CHANGES.append(
                    {
                        "sequence": SEQUENCE,
                        "path": name,
                        "before": LAST.get(name),
                        "after": current.get(name),
                        "wall_ns": time.time_ns(),
                    }
                )
        LAST = current
    return current


def owner_uid(owner):
    with LOCK:
        if owner not in UIDS:
            UIDS[owner] = 10001 + len(UIDS)
        return UIDS[owner]


def spawn(owner, arguments, **options):
    with LOCK:
        if owner in ENDED:
            raise ValueError("Session has ended")
        return subprocess.Popen(arguments, **options)


def traced_arguments(owner, command):
    uid = owner_uid(owner)
    trace = CONTROL / f"trace-{uid}-{time.time_ns()}.log"
    return [
        "python",
        "/opt/process_observer.py",
        str(trace),
        "setpriv",
        f"--reuid={uid}",
        "--regid=10000",
        "--clear-groups",
        "/bin/sh",
        "-c",
        command,
    ]


def observe_traces():
    with TRACE_LOCK:
        _observe_traces()


def _observe_traces():
    paths = list(CONTROL.glob("trace-*.log"))
    if sum(path.stat().st_size for path in paths) >= 3 * 1024 * 1024:
        message = "Process trace storage limit reached; observation incomplete"
        if message not in OBSERVER_ERRORS:
            OBSERVER_ERRORS.append(message)
    for path in paths:
        offset = TRACE_OFFSETS.get(str(path), 0)
        with path.open("rb") as stream:
            stream.seek(offset)
            data = stream.read()
        complete = data.rfind(b"\n") + 1
        TRACE_OFFSETS[str(path)] = offset + complete
        uid = int(path.name.split("-")[1])
        for line in data[:complete].decode(errors="replace").splitlines():
            record = json.loads(line)
            if record["event"] == "observer.error":
                OBSERVER_ERRORS.append(record["error"])
            PROCESS_CHANGES.append(
                {
                    **record,
                    "uid": uid,
                    "record_type": "process-lifecycle",
                }
            )


def process_snapshot():
    result = {}
    for path in Path("/proc").iterdir():
        if not path.name.isdigit():
            continue
        try:
            uid = path.stat().st_uid
            if uid < 10000:
                continue
            command = (path / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
            stat = (path / "stat").read_text().rsplit(")", 1)[1].split()
            result[int(path.name)] = {"uid": uid, "command": command, "start_ticks": stat[19]}
        except (OSError, IndexError):
            continue
    return result


def observe_processes():
    global PROCESSES
    current = process_snapshot()
    with LOCK:
        for pid in set(current) | set(PROCESSES):
            if current.get(pid) != PROCESSES.get(pid):
                PROCESS_CHANGES.append(
                    {
                        "pid": pid,
                        "before": PROCESSES.get(pid),
                        "after": current.get(pid),
                        "wall_ns": time.time_ns(),
                        "record_type": "process-inventory",
                    }
                )
        PROCESSES = current


def kill_owner(owner, permanent=False):
    if permanent:
        with LOCK:
            ENDED.add(owner)
    uid = UIDS.get(owner)
    if uid is None:
        return
    for _ in range(20):
        matching = False
        for pid, info in process_snapshot().items():
            if info["uid"] == uid:
                matching = True
                try:
                    os.kill(pid, signal.SIGSTOP)
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        if not matching:
            break
        time.sleep(0.01)
    observe_processes()


def filesystem_events():
    """Kernel filesystem notifications supplement content checkpoints."""
    libc = ctypes.CDLL(None, use_errno=True)
    descriptor = libc.inotify_init1(os.O_NONBLOCK)
    if descriptor < 0:
        OBSERVER_ERRORS.append("Filesystem notification initialization failed")
        return
    watches = {}
    mask = 0x2 | 0x4 | 0x8 | 0x40 | 0x80 | 0x100 | 0x200 | 0x400 | 0x800

    def add_directories():
        known = set(watches.values())
        for directory, folders, _ in os.walk(ROOT, followlinks=False):
            folders[:] = [name for name in folders if not Path(directory, name).is_symlink()]
            if directory not in known:
                watch = libc.inotify_add_watch(descriptor, os.fsencode(directory), mask)
                if watch >= 0:
                    watches[watch] = directory

    add_directories()
    while True:
        try:
            data = os.read(descriptor, 65536)
        except BlockingIOError:
            time.sleep(0.02)
            continue
        offset = 0
        while offset < len(data):
            watch, flags, cookie, length = struct.unpack_from("iIII", data, offset)
            offset += 16
            name = data[offset : offset + length].split(b"\0", 1)[0].decode(errors="replace")
            offset += length
            if flags & 0x4000:
                OBSERVER_ERRORS.append(
                    "Filesystem notification queue overflow; observation incomplete"
                )
                continue
            directory = watches.get(watch)
            if directory is not None:
                relative = str((Path(directory) / name).relative_to(ROOT))
                with LOCK:
                    CHANGES.append(
                        {
                            "path": relative,
                            "notification_mask": flags,
                            "move_cookie": cookie,
                            "wall_ns": time.time_ns(),
                            "record_type": "filesystem-notification",
                        }
                    )
            if flags & 0x40000000:
                add_directories()
        observe()


def monitor():
    while True:
        try:
            observe()
            observe_processes()
            observe_traces()
            with LOCK:
                BACKGROUND[:] = [process for process in BACKGROUND if process.poll() is None]
        except Exception as error:
            OBSERVER_ERRORS.append(str(error))
        time.sleep(0.25)


def run_command(request):
    owner = request["owner"]
    timeout = request["timeout"]
    limit = request["output_bytes"]
    arguments = traced_arguments(owner, request["command"])
    process = spawn(
        owner,
        arguments,
        cwd=ROOT,
        umask=0,
        start_new_session=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env={
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "HOME": "/workspace",
            "LANG": "C.UTF-8",
            "TMPDIR": "/workspace",
        },
    )
    with LOCK:
        GROUPS.setdefault(owner, set()).add(process.pid)
        PROCESS_CHANGES.append(
            {
                "pid": process.pid,
                "before": None,
                "after": {"uid": owner_uid(owner), "command": request["command"]},
                "wall_ns": time.time_ns(),
                "record_type": "command-session",
            }
        )
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    output = bytearray()
    deadline = time.monotonic() + timeout
    expired = False
    while selector.get_map():
        if time.monotonic() >= deadline:
            expired = True
            break
        for key, _ in selector.select(0.1):
            chunk = os.read(key.fileobj.fileno(), 4096)
            if not chunk:
                selector.unregister(key.fileobj)
            else:
                output.extend(chunk[: max(0, limit - len(output))])
    # Each action owns a process group. Children cannot outlive a completed tool call.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()
    if expired:
        kill_owner(owner)
    selector.close()
    process.stdout.close()
    with LOCK:
        GROUPS.get(owner, set()).discard(process.pid)
    observe()
    observe_traces()
    with TRACE_LOCK:
        Path(arguments[2]).unlink(missing_ok=True)
        TRACE_OFFSETS.pop(arguments[2], None)
    PROCESS_CHANGES.append(
        {
            "pid": process.pid,
            "after": None,
            "before": {"uid": owner_uid(owner), "command": request["command"]},
            "wall_ns": time.time_ns(),
            "record_type": "command-session",
        }
    )
    return {
        "output": output.decode(errors="replace"),
        "exit_code": process.returncode,
        "timed_out": expired,
        "output_limited": len(output) == limit,
        "process_id": process.pid,
    }


def handle(request):
    operation = request["operation"]
    if operation == "execute":
        return run_command(request)
    if operation == "kill":
        with LOCK:
            for group in GROUPS.pop(request["owner"], set()):
                try:
                    os.killpg(group, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        kill_owner(request["owner"], request.get("permanent", False))
        observe()
        return {"status": "Stopped"}
    if operation == "snapshot":
        current = observe()
        observe_traces()
        with LOCK:
            changes = CHANGES[:]
            process_changes = PROCESS_CHANGES[:]
            through = OBSERVER_ID
            owners = UIDS.copy()
        return {
            "files": current,
            "changes": changes,
            "process_changes": process_changes,
            "observer_errors": OBSERVER_ERRORS[:],
            "owner_uids": owners,
            "epoch": OBSERVER_EPOCH,
            "through": through,
        }
    if operation == "acknowledge":
        with LOCK:
            through = request["through"]
            CHANGES[:] = [item for item in CHANGES if item["observer_id"] > through]
            PROCESS_CHANGES[:] = [item for item in PROCESS_CHANGES if item["observer_id"] > through]
        return {"status": "Acknowledged"}
    if operation == "restore":
        for name, entry in request["files"].items():
            path = ROOT / name
            if path.is_absolute() and ROOT not in path.parents:
                raise ValueError("Invalid snapshot path")
            if ".." in path.parts or path.is_symlink():
                raise ValueError("Invalid snapshot path")
            path.parent.mkdir(parents=True, exist_ok=True)
            if "symlink" in entry:
                # Symlinks are observable but never followed during restoration.
                continue
            path.write_bytes(base64.b64decode(entry["data"]))
            path.chmod(entry.get("mode", 0o644) | 0o666)
            os.chown(path, 10000, 10000)
        for directory, _, _ in os.walk(ROOT):
            os.chown(directory, 10000, 10000)
            os.chmod(directory, 0o777)
        observe()
        return {"status": "Restored"}
    if operation == "service":
        path = ROOT / request["path"]
        process = subprocess.Popen(
            ["python", str(path)],
            cwd=path.parent / "public",
            user=10000,
            group=10000,
            extra_groups=[],
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        with LOCK:
            GROUPS.setdefault("services", set()).add(process.pid)
            BACKGROUND.append(process)
        return {"process_id": process.pid}
    if operation == "processes":
        current = process_snapshot()
        return {
            owner: sorted(pid for pid, info in current.items() if info["uid"] == uid)
            for owner, uid in UIDS.items()
        }
    if operation == "launch":
        owner = request["owner"]
        log = ROOT / f"process-{owner[:8]}-{time.time_ns()}.log"
        with log.open("wb") as output:
            process = spawn(
                owner,
                traced_arguments(owner, request["command"]),
                cwd=ROOT,
                umask=0,
                start_new_session=True,
                stdout=output,
                stderr=subprocess.STDOUT,
                env={
                    "PATH": "/usr/local/bin:/usr/bin:/bin",
                    "HOME": "/workspace",
                    "LANG": "C.UTF-8",
                    "TMPDIR": "/workspace",
                },
            )
        with LOCK:
            GROUPS.setdefault(owner, set()).add(process.pid)
            BACKGROUND.append(process)
        observe_processes()
        return {"process_id": process.pid, "log": str(log), "exit_code": None, "timed_out": False}
    raise ValueError("Unknown supervisor operation")


def client(connection):
    try:
        source = connection.makefile("rb")
        request = json.loads(source.readline())
        result = handle(request)
    except Exception as error:
        result = {"error": str(error)}
    try:
        connection.sendall(json.dumps(result).encode() + b"\n")
    finally:
        connection.close()


if __name__ == "__main__":
    CONTROL.mkdir(mode=0o700, exist_ok=True)
    ROOT.mkdir(exist_ok=True)
    os.chown(ROOT, 10000, 10000)
    server = socket.socket(socket.AF_UNIX)
    server.bind(str(CONTROL / "socket"))
    server.listen(32)
    threading.Thread(target=monitor, daemon=True).start()
    threading.Thread(target=filesystem_events, daemon=True).start()
    while True:
        connection, _ = server.accept()
        threading.Thread(target=client, args=(connection,), daemon=True).start()
