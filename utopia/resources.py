"""Host headroom checks and hard limits for launcher-owned Windows processes."""

import ctypes
import os
import subprocess
from ctypes import wintypes
from pathlib import Path

MIB = 1024 * 1024


class MemoryPressure(RuntimeError):
    def __init__(self, message, allocation_mb=0):
        super().__init__(message)
        self.allocation_mb = allocation_mb


def host_memory():
    if os.name == "nt":

        class Status(ctypes.Structure):
            _fields_ = [("length", wintypes.DWORD), ("load", wintypes.DWORD)] + [
                (name, ctypes.c_ulonglong)
                for name in (
                    "total",
                    "available",
                    "page_total",
                    "page_available",
                    "virtual_total",
                    "virtual_available",
                    "extended",
                )
            ]

        class Performance(ctypes.Structure):
            _fields_ = (
                [("size", wintypes.DWORD)]
                + [
                    (name, ctypes.c_size_t)
                    for name in (
                        "commit",
                        "commit_limit",
                        "commit_peak",
                        "physical",
                        "available",
                        "cache",
                        "kernel",
                        "paged",
                        "nonpaged",
                        "page_size",
                    )
                ]
                + [(name, wintypes.DWORD) for name in ("handles", "processes", "threads")]
            )

        status, performance = Status(), Performance()
        status.length = ctypes.sizeof(status)
        performance.size = ctypes.sizeof(performance)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        if not kernel.GlobalMemoryStatusEx(ctypes.byref(status)) or not psapi.GetPerformanceInfo(
            ctypes.byref(performance), ctypes.sizeof(performance)
        ):
            raise MemoryPressure("Cannot read Windows memory headroom; new work is blocked.")
        return {
            "total_mb": status.total // MIB,
            "available_mb": status.available // MIB,
            "commit_available_mb": (performance.commit_limit - performance.commit)
            * performance.page_size
            // MIB,
        }
    values = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        key, value = line.split(":", 1)
        values[key] = int(value.strip().split()[0]) // 1024
    return {
        "total_mb": values["MemTotal"],
        "available_mb": values["MemAvailable"],
        "commit_available_mb": values["MemAvailable"] + values["SwapFree"],
    }


class MemoryGuard:
    def __init__(self, reserve_mb=None):
        self.reserve_mb = (
            reserve_mb
            if reserve_mb is not None
            else int(os.environ.get("UTOPIA_RAM_RESERVE_MB", "1024"))
        )
        if not 512 <= self.reserve_mb <= 65536:
            raise ValueError("RAM reserve must be between 512 and 65536 MiB")

    def snapshot(self):
        state = host_memory()
        state["ram_reserve_mb"] = self.reserve_mb
        state["commit_reserve_mb"] = max(512, self.reserve_mb // 2)
        return state

    def check(self, allocation_mb=0, recovery=False):
        state = self.snapshot()
        margin = allocation_mb + (128 if recovery else 0)
        if (
            state["available_mb"] < state["ram_reserve_mb"] + margin
            or state["commit_available_mb"] < state["commit_reserve_mb"] + margin
        ):
            raise MemoryPressure(
                f"Memory safety hold: {state['available_mb']} MiB RAM and "
                f"{state['commit_available_mb']} MiB Windows commit headroom available. "
                f"Keep {state['ram_reserve_mb']} MiB RAM and {state['commit_reserve_mb']} MiB "
                f"commit free, plus {margin} MiB for this allocation. "
                "Free memory; work resumes automatically.",
                allocation_mb=allocation_mb,
            )
        return state


class WindowsMemoryJob:
    """One private-commit ceiling shared by all owned services and their descendants."""

    def __init__(self, limit_mb):
        class Basic(ctypes.Structure):
            _fields_ = [
                ("process_time", ctypes.c_longlong),
                ("job_time", ctypes.c_longlong),
                ("flags", wintypes.DWORD),
                ("minimum", ctypes.c_size_t),
                ("maximum", ctypes.c_size_t),
                ("active", wintypes.DWORD),
                ("affinity", ctypes.c_size_t),
                ("priority", wintypes.DWORD),
                ("scheduling", wintypes.DWORD),
            ]

        class Counters(ctypes.Structure):
            _fields_ = [(f"counter{index}", ctypes.c_ulonglong) for index in range(6)]

        class Limits(ctypes.Structure):
            _fields_ = [("basic", Basic), ("io", Counters)] + [
                (name, ctypes.c_size_t)
                for name in ("process_memory", "job_memory", "peak_process", "peak_job")
            ]

        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.kernel.CreateJobObjectW.restype = wintypes.HANDLE
        self.kernel.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        self.kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.handle = self.kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = Limits()
        limits.basic.flags = 0x200 | 0x2000  # JOB_MEMORY and KILL_ON_JOB_CLOSE
        limits.job_memory = limit_mb * MIB
        if not self.kernel.SetInformationJobObject(
            self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)
        ):
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error

    def spawn(self, command, **kwargs):
        # No executable code or descendants may run before the limit is attached.
        kwargs["creationflags"] = kwargs.get("creationflags", 0) | 0x4  # CREATE_SUSPENDED
        process = subprocess.Popen(command, **kwargs)
        try:
            if not self.kernel.AssignProcessToJobObject(
                self.handle, wintypes.HANDLE(int(process._handle))
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            self._resume(process.pid)
            return process
        except BaseException:
            process.kill()
            process.wait()
            raise

    def _resume(self, pid):
        class Thread(ctypes.Structure):
            _fields_ = [
                ("size", wintypes.DWORD),
                ("usage", wintypes.DWORD),
                ("identity", wintypes.DWORD),
                ("owner", wintypes.DWORD),
                ("priority", wintypes.LONG),
                ("delta", wintypes.LONG),
                ("flags", wintypes.DWORD),
            ]

        kernel = self.kernel
        kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        kernel.Thread32First.argtypes = kernel.Thread32Next.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(Thread),
        ]
        kernel.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenThread.restype = wintypes.HANDLE
        kernel.ResumeThread.argtypes = [wintypes.HANDLE]
        kernel.ResumeThread.restype = wintypes.DWORD
        snapshot = kernel.CreateToolhelp32Snapshot(4, 0)
        if snapshot == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            entry = Thread()
            entry.size = ctypes.sizeof(entry)
            found = kernel.Thread32First(snapshot, ctypes.byref(entry))
            while found:
                if entry.owner == pid:
                    thread = kernel.OpenThread(2, False, entry.identity)
                    if not thread:
                        raise ctypes.WinError(ctypes.get_last_error())
                    try:
                        if kernel.ResumeThread(thread) == 0xFFFFFFFF:
                            raise ctypes.WinError(ctypes.get_last_error())
                        return
                    finally:
                        kernel.CloseHandle(thread)
                found = kernel.Thread32Next(snapshot, ctypes.byref(entry))
            raise RuntimeError("Suspended service thread was not found; startup blocked")
        finally:
            kernel.CloseHandle(snapshot)

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None
