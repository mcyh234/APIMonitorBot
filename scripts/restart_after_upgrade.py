from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import time
from pathlib import Path

if os.name == "nt":
    from ctypes import wintypes


def process_is_running(pid: int) -> bool:
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True

    process_query_limited_information = 0x1000
    still_active = 259
    kernel32 = ctypes.windll.kernel32
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        return False
    try:
        exit_code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return False
        return exit_code.value == still_active
    finally:
        kernel32.CloseHandle(handle)


def append_log(path: Path, message: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n")


def main() -> int:
    if len(sys.argv) != 4:
        return 2
    old_pid = int(sys.argv[1])
    root = Path(sys.argv[2]).resolve()
    executable = Path(sys.argv[3]).resolve()
    log_path = root / "data" / "upgrades" / "restart.log"

    deadline = time.monotonic() + 90
    while process_is_running(old_pid) and time.monotonic() < deadline:
        time.sleep(0.25)
    if process_is_running(old_pid):
        append_log(log_path, f"旧进程 {old_pid} 在 90 秒内没有退出，取消自动重启。")
        return 1

    command = [str(executable), str(root / "run.py")]
    kwargs = {
        "cwd": root,
        "stdin": subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    else:
        kwargs["start_new_session"] = True
    with log_path.open("ab") as log:
        subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, **kwargs)
    append_log(log_path, "升级完成，已启动新进程。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
