"""Windows Job Objects: drzewo procesów bramki ginie razem z nadzorcą.

Odpowiednik `setsid` + `prctl(PR_SET_PDEATHSIG)` + spisu potomków z `/proc`
(execution.py na Linuksie). Proces przypisany do joba przenosi członkostwo
na wszystkie swoje dzieci, także uruchomione w nowej konsoli albo grupie.
Job ma `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`, a jego jedyny uchwyt trzyma
nadzorca — śmierć nadzorcy (nawet TerminateProcess) zamyka uchwyt i system
zabija całe drzewo bramki.

Tylko `ctypes` — bez pywin32. Na innych systemach moduł się importuje, ale
`Job()` zgłasza `OSError`.
"""

from __future__ import annotations

import ctypes
import os
import time
from ctypes import wintypes

_JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION = 1
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
_PROCESS_TERMINATE = 0x0001
_PROCESS_SET_QUOTA = 0x0100
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_STILL_ACTIVE = 259


class _IoCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in (
        "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
        "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
    )]


class _BasicLimit(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _ExtendedLimit(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BasicLimit),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class _BasicAccounting(ctypes.Structure):
    _fields_ = [
        ("TotalUserTime", ctypes.c_int64),
        ("TotalKernelTime", ctypes.c_int64),
        ("ThisPeriodTotalUserTime", ctypes.c_int64),
        ("ThisPeriodTotalKernelTime", ctypes.c_int64),
        ("TotalPageFaultCount", wintypes.DWORD),
        ("TotalProcesses", wintypes.DWORD),
        ("ActiveProcesses", wintypes.DWORD),
        ("TotalTerminatedProcesses", wintypes.DWORD),
    ]


def _kernel32() -> ctypes.WinDLL:  # type: ignore[name-defined,unused-ignore]
    # `os.name`, nie `sys.platform`: mypy na Linuksie uznałby resztę za martwą.
    if os.name != "nt":
        raise OSError("Job Objects są dostępne tylko na Windows")
    dll = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined,unused-ignore]
    dll.CreateJobObjectW.restype = wintypes.HANDLE
    dll.OpenProcess.restype = wintypes.HANDLE
    return dll


def _check(ok: object, what: str) -> None:
    if not ok:
        error = ctypes.get_last_error()  # type: ignore[attr-defined,unused-ignore]
        raise OSError(error, f"{what} nie powiódł się (WinError {error})")


class Job:
    """Job z `KILL_ON_JOB_CLOSE`; jedyny uchwyt należy do tego obiektu."""

    def __init__(self) -> None:
        self._k32 = _kernel32()
        handle = self._k32.CreateJobObjectW(None, None)
        _check(handle, "CreateJobObjectW")
        self._handle: int | None = handle
        info = _ExtendedLimit()
        info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        _check(
            self._k32.SetInformationJobObject(
                handle, _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                ctypes.byref(info), ctypes.sizeof(info),
            ),
            "SetInformationJobObject",
        )

    def assign(self, pid: int) -> None:
        process = self._k32.OpenProcess(_PROCESS_SET_QUOTA | _PROCESS_TERMINATE, False, pid)
        _check(process, "OpenProcess")
        try:
            _check(self._k32.AssignProcessToJobObject(self._handle, process),
                   "AssignProcessToJobObject")
        finally:
            self._k32.CloseHandle(process)

    def active_processes(self) -> int:
        info = _BasicAccounting()
        _check(
            self._k32.QueryInformationJobObject(
                self._handle, _JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION,
                ctypes.byref(info), ctypes.sizeof(info), None,
            ),
            "QueryInformationJobObject",
        )
        return int(info.ActiveProcesses)

    def terminate(self, wait_s: float = 5.0) -> None:
        """Zabija wszystkie procesy joba i czeka, aż znikną (pliki zwolnione)."""
        if self._handle is None:
            return
        self._k32.TerminateJobObject(self._handle, 1)
        deadline = time.monotonic() + wait_s
        while time.monotonic() < deadline and self.active_processes():
            time.sleep(0.02)

    def close(self) -> None:
        if self._handle is not None:
            self._k32.CloseHandle(self._handle)
            self._handle = None


def process_alive(pid: int) -> bool:
    """Czy proces `pid` jeszcze działa (do testów i diagnostyki)."""
    k32 = _kernel32()
    process = k32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not process:
        return False
    try:
        code = wintypes.DWORD()
        if not k32.GetExitCodeProcess(process, ctypes.byref(code)):
            return False
        return code.value == _STILL_ACTIVE
    finally:
        k32.CloseHandle(process)
