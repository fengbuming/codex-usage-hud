"""Keep a Windows daemon outside a launcher's kill-on-close Job Object."""

from __future__ import annotations

from collections.abc import Callable, Sequence
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
from multiprocessing.connection import Client, Listener
import secrets
import subprocess
import sys
import threading
from uuid import uuid4

from .process_environment import external_process_environment


JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
BROKER_OPTION = "--daemon-launch-broker"


class _BasicLimits(ctypes.Structure):
    _fields_ = [
        ("process_time", ctypes.c_int64),
        ("job_time", ctypes.c_int64),
        ("flags", wintypes.DWORD),
        ("min_working_set", ctypes.c_size_t),
        ("max_working_set", ctypes.c_size_t),
        ("active_processes", wintypes.DWORD),
        ("affinity", ctypes.c_size_t),
        ("priority", wintypes.DWORD),
        ("scheduling", wintypes.DWORD),
    ]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [
        ("basic", _BasicLimits),
        ("io_counters", ctypes.c_uint64 * 6),
        ("process_memory", ctypes.c_size_t),
        ("job_memory", ctypes.c_size_t),
        ("peak_process_memory", ctypes.c_size_t),
        ("peak_job_memory", ctypes.c_size_t),
    ]


def current_job_limit_flags() -> int:
    if sys.platform != "win32":
        return 0
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.IsProcessInJob.argtypes = [
        wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL),
    ]
    kernel32.IsProcessInJob.restype = wintypes.BOOL
    in_job = wintypes.BOOL()
    if not kernel32.IsProcessInJob(kernel32.GetCurrentProcess(), None, ctypes.byref(in_job)):
        raise ctypes.WinError(ctypes.get_last_error())
    if not in_job.value:
        return 0
    kernel32.QueryInformationJobObject.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p,
    ]
    kernel32.QueryInformationJobObject.restype = wintypes.BOOL
    limits = _ExtendedLimits()
    if not kernel32.QueryInformationJobObject(None, 9, ctypes.byref(limits), ctypes.sizeof(limits), None):
        raise ctypes.WinError(ctypes.get_last_error())
    return int(limits.basic.flags)


def _shell_launch(arguments: Sequence[str]) -> None:
    command = [] if getattr(sys, "frozen", False) else ["-m", "codex_usage_hud"]
    command.extend(arguments)
    executable = Path(sys.executable)
    # python.exe 的隐藏控制台仍可能创建独立的 kill-on-close Job；常驻 GUI 使用同环境 pythonw。
    if not getattr(sys, "frozen", False) and executable.with_name("pythonw.exe").is_file():
        executable = executable.with_name("pythonw.exe")
    def literal(value: str) -> str:
        return "'" + value.replace("'", "''") + "'"
    # 必须取得桌面窗口所属的 Explorer 自动化对象；直接 New-Object Shell.Application
    # 的 ShellExecute 仍可能在调用者进程中执行，继承原来的嵌套 Job。
    # 参数只含随机管道地址和一次性认证值；环境（可能含凭据）不落盘、不写命令行。
    script = (
        "$ErrorActionPreference='Stop'; "
        "$windows=(New-Object -ComObject Shell.Application).Windows(); "
        "$location=0; $root=0; $desktopHwnd=0; "
        "$desktop=$windows.FindWindowSW([ref]$location,[ref]$root,8,[ref]$desktopHwnd,1); "
        "if ($null -eq $desktop) { throw 'Windows desktop shell is unavailable' }; "
        "$shell=$desktop.Document.Application; "
        f"$shell.ShellExecute({literal(str(executable))}, "
        f"{literal(subprocess.list2cmdline(command))}, {literal(os.getcwd())}, 'open', 0)"
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW, timeout=10, check=True,
    )


def _broker_launch(argv: Sequence[str]) -> int:
    address = rf"\\.\pipe\codex-hud-launch-{uuid4().hex}"
    token = secrets.token_bytes(32)
    listener = Listener(address, family="AF_PIPE", authkey=token)
    finished = threading.Event()
    reply: dict[str, object] = {}
    payload = {"argv": list(argv), "environment": external_process_environment(), "cwd": os.getcwd()}

    def handoff() -> None:
        try:
            with listener.accept() as connection:
                connection.send(payload)
                if not connection.poll(10):
                    raise RuntimeError("Independent daemon did not acknowledge launch")
                reply.update(connection.recv())
        except Exception as exc:
            reply["error"] = str(exc)
        finally:
            finished.set()

    threading.Thread(target=handoff, name="hud-launch-handoff", daemon=True).start()
    try:
        _shell_launch([BROKER_OPTION, address, token.hex()])
        if not finished.wait(15):
            raise RuntimeError("Windows desktop did not start the independent HUD")
        if reply.get("error"):
            raise RuntimeError(str(reply["error"]))
        if int(reply.get("jobFlags", JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE)) & JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE:
            raise RuntimeError("Independent HUD still belongs to a kill-on-close job")
        child_pid = int(reply.get("pid", 0))
        if child_pid <= 0:
            raise RuntimeError("Independent HUD did not return its process ID")
        return child_pid
    finally:
        listener.close()


def resume_brokered_daemon(address: str, token: str, run: Callable[[Sequence[str]], int]) -> int:
    with Client(address, family="AF_PIPE", authkey=bytes.fromhex(token)) as connection:
        try:
            if not connection.poll(10):
                raise RuntimeError("Launcher did not send startup context")
            payload = connection.recv()
            flags = current_job_limit_flags()
            if flags & JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE:
                raise RuntimeError("Windows desktop launch retained a kill-on-close job")
            # 使用启动者的配置环境，但保留独立 frozen 子进程自己的 bootloader 生命周期。
            os.environ.update(payload["environment"])
            os.chdir(payload["cwd"])
            sys.argv = [sys.argv[0], *payload["argv"]]
            connection.send({"pid": os.getpid(), "jobFlags": flags})
        except (OSError, RuntimeError) as exc:
            connection.send({"error": str(exc)})
            return 2
    return run(payload["argv"])


def detach_daemon_if_needed(argv: Sequence[str]) -> int | None:
    """Return a launcher exit code, or None when this process can own the daemon."""
    if sys.platform != "win32":
        return None
    try:
        if not current_job_limit_flags() & JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE:
            return None
        child_pid = _broker_launch(argv)
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"codex-usage-hud: cannot start an independent daemon: {exc}. "
              "Start HUD from an external terminal or desktop shortcut.", file=sys.stderr)
        return 2
    print(f"Started independent codex-usage-hud process PID {child_pid}.")
    return 0
