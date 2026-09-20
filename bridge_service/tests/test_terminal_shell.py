"""Real PTY regression for the shell inside the Codex command sandbox."""

import os
from pathlib import Path
import subprocess
import sys
import time

import pytest


@pytest.mark.skipif(sys.platform != "linux", reason="HA App terminal runs on Linux")
def test_nested_terminal_resize_interrupt_and_exit(tmp_path):
    import pty
    import select
    import termios

    master, slave = pty.openpty()
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(Path(__file__).parents[1] / "src")
    environment["PS1"] = "terminal-test> "
    process = subprocess.Popen(
        [sys.executable, "-m", "codex_bridge_service.terminal_shell"],
        cwd=tmp_path, env=environment, stdin=slave, stdout=slave, stderr=slave,
        start_new_session=True,
    )
    os.close(slave)

    def wait_for_file(name):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if (tmp_path / name).exists():
                return (tmp_path / name).read_text()
            assert process.poll() is None
            time.sleep(0.02)
        pytest.fail(f"Shell did not write {name}")

    try:
        # The launcher switches its outer PTY to raw mode. Wait for the shell
        # prompt so that transition cannot flush input sent before startup.
        output = b""
        deadline = time.monotonic() + 5
        while b"terminal-test> " not in output and time.monotonic() < deadline:
            readable, _, _ = select.select([master], [], [], 0.1)
            if readable:
                output += os.read(master, 4096)
        assert b"terminal-test> " in output
        os.write(master, b"printf ready > ready\n")
        assert wait_for_file("ready") == "ready"
        termios.tcsetwinsize(master, (30, 100))
        os.write(master, b"stty size > size\n")
        assert wait_for_file("size").strip() == "30 100"
        os.write(master, b"sleep 120\n")
        time.sleep(0.1)
        os.write(master, b"\x03")
        time.sleep(0.1)
        os.write(master, b"printf resumed > resumed\n")
        assert wait_for_file("resumed") == "resumed"
        os.write(master, b"exit 7\n")
        assert process.wait(timeout=5) == 7
    finally:
        os.close(master)
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)
