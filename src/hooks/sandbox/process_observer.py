"""Trusted Linux process-event observer; never reads tracee strings or executes agent Python."""

import ctypes
import json
import os
import signal
import sys
import time

LIBC = ctypes.CDLL(None, use_errno=True)
LIBC.ptrace.argtypes = [ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]
LIBC.ptrace.restype = ctypes.c_long
TRACEME, CONT, SETOPTIONS, GETEVENTMSG = 0, 7, 0x4200, 0x4201
EVENTS = {1: "fork", 2: "vfork", 3: "clone", 4: "exec", 6: "exiting"}
OPTIONS = 0x2 | 0x4 | 0x8 | 0x10 | 0x40 | 0x100000  # process events and EXITKILL


def ptrace(request, pid, data=None):
    if LIBC.ptrace(request, pid, None, data) == -1:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code))


def observe(path, command):
    traced = set()
    with open(path, "w", buffering=1) as stream:

        def record(pid, event, **values):
            stream.write(
                json.dumps({"pid": pid, "event": event, "wall_ns": time.time_ns(), **values}) + "\n"
            )

        try:
            root = os.fork()
            if root == 0:
                try:
                    ptrace(TRACEME, 0)
                    os.kill(os.getpid(), signal.SIGSTOP)
                    os.execvp(command[0], command)
                except BaseException:
                    os._exit(127)
            traced.add(root)
            _, status = os.waitpid(root, 0)
            if not os.WIFSTOPPED(status):
                raise RuntimeError("Process observation could not initialize")
            ptrace(SETOPTIONS, root, ctypes.c_void_p(OPTIONS))
            record(root, "started")
            ptrace(CONT, root)
            first_stops = set()
            result = 1
            while traced:
                pid, status = os.waitpid(-1, 0x40000000)  # __WALL includes traced threads
                if os.WIFEXITED(status) or os.WIFSIGNALED(status):
                    code = os.waitstatus_to_exitcode(status)
                    record(pid, "exit", exit_code=code)
                    traced.discard(pid)
                    if pid == root:
                        result = code if code >= 0 else 128 - code
                    continue
                if not os.WIFSTOPPED(status):
                    continue
                event = status >> 16
                delivered = os.WSTOPSIG(status)
                if event in EVENTS:
                    message = ctypes.c_ulong()
                    ptrace(GETEVENTMSG, pid, ctypes.byref(message))
                    if event in {1, 2, 3}:
                        child = message.value
                        traced.add(child)
                        first_stops.add(child)
                        record(pid, EVENTS[event], child_pid=child)
                    else:
                        if event == 4 and message.value and message.value != pid:
                            traced.discard(message.value)
                            first_stops.discard(message.value)
                            traced.add(pid)
                            record(pid, EVENTS[event], former_tid=message.value)
                        else:
                            record(pid, EVENTS[event])
                    delivered = 0
                elif pid in first_stops and delivered == signal.SIGSTOP:
                    first_stops.discard(pid)
                    delivered = 0
                ptrace(CONT, pid, ctypes.c_void_p(delivered))
            return result
        except BaseException as error:
            record(os.getpid(), "observer.error", error=str(error))
            for pid in traced:
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            raise


if __name__ == "__main__":
    sys.exit(observe(sys.argv[1], sys.argv[2:]))
