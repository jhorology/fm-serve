"""Start fm directly in Terminal.app, preserving its PCC execution context."""

from __future__ import annotations

import json
import os
import shlex
import signal
import subprocess
import tempfile
import time
from pathlib import Path


class TerminalFmProcess:
    """Process handle for an fm owned by Terminal rather than this Python process."""

    stdout = None  # fm output remains in its Terminal window.

    def __init__(self, pid: int, window_id: int):
        self.pid = pid
        self.window_id = window_id

    def poll(self) -> int | None:
        try:
            os.kill(self.pid, 0)
        except ProcessLookupError:
            return 0  # Terminal owns waitpid; the actual exit status is unavailable.
        return None

    def terminate(self) -> None:
        self._signal(signal.SIGTERM)

    def kill(self) -> None:
        self._signal(signal.SIGKILL)

    def _signal(self, sig: signal.Signals) -> None:
        try:
            os.kill(self.pid, sig)
        except ProcessLookupError:
            pass

    def wait(self, timeout: float | None = None) -> int:
        deadline = None if timeout is None else time.monotonic() + timeout
        while self.poll() is None:
            if deadline is not None and time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired("Terminal fm serve", timeout or 0)
            time.sleep(0.05)
        self.close_window()
        return 0

    def close_window(self) -> None:
        # Close only the window we created, and only after its process has exited.
        script = (
            'tell application "Terminal"\n'
            f'if exists window id {self.window_id} then\n'
            f'if not busy of window id {self.window_id} then '
            f'close window id {self.window_id}\n'
            'end if\nend tell'
        )
        try:
            subprocess.run(
                ["/usr/bin/osascript", "-e", script],
                capture_output=True, timeout=5, check=False,
            )
        except subprocess.TimeoutExpired:
            pass


def start_terminal_fm(*, host: str, port: int, fm_bin: str) -> TerminalFmProcess:
    with tempfile.TemporaryDirectory(prefix="fm-serve-terminal-") as directory:
        pid_file = Path(directory) / "fm.pid"
        # exec replaces Terminal's shell, so the recorded PID remains fm's PID.
        command = (
            f"echo $$ > {shlex.quote(str(pid_file))} && exec "
            + shlex.join([fm_bin, "serve", "--host", host, "--port", str(port)])
        )
        script = (
            'tell application "Terminal"\n'
            f'set fmTab to do script {json.dumps(command, ensure_ascii=False)}\n'
            'repeat with fmWindow in windows\n'
            'if tty of selected tab of fmWindow is tty of fmTab then '
            'return id of fmWindow\n'
            'end repeat\nend tell'
        )
        result = subprocess.run(
            ["/usr/bin/osascript", "-e", script],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode:
            raise RuntimeError(f"Terminal.app launch failed: {result.stderr.strip()}")
        window_id = int(result.stdout.strip())
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                pid = int(pid_file.read_text().strip())
                return TerminalFmProcess(pid, window_id)
            except (FileNotFoundError, ValueError):
                time.sleep(0.05)
        raise RuntimeError("Terminal.app did not start fm within 15 seconds")
