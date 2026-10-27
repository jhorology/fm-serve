import subprocess
import sys

import pytest

from fm_serve.proxy import _stop_fm_process
from fm_serve.terminal import TerminalFmProcess, start_terminal_fm


def test_stop_terminal_backend_leaves_other_processes_running(monkeypatch):
    owned = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    handle = TerminalFmProcess(owned.pid, 123)
    closed = []
    monkeypatch.setattr(handle, "close_window", lambda: closed.append(handle.window_id))
    # Terminal normally reaps its child; Popen.poll does that for this test child.
    monkeypatch.setattr(handle, "poll", owned.poll)
    try:
        assert handle.poll() is None
        with pytest.raises(subprocess.TimeoutExpired):
            handle.wait(timeout=0.01)
        _stop_fm_process(handle)
        assert owned.poll() is not None
        assert unrelated.poll() is None
        assert closed and set(closed) == {123}
        _stop_fm_process(handle)  # Repeated cleanup is safe.
    finally:
        for proc in (owned, unrelated):
            if proc.poll() is None:
                proc.terminate()
            proc.wait(timeout=5)


def test_terminal_automation_denial_is_reported(monkeypatch):
    def denied(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 1, "", "Not authorized (-1743)")

    monkeypatch.setattr(subprocess, "run", denied)
    with pytest.raises(RuntimeError, match="Not authorized"):
        start_terminal_fm(host="127.0.0.1", port=1977, fm_bin="/usr/bin/fm")
