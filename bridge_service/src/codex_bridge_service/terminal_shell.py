"""Give the sandboxed shell its own controlling terminal and job control.

Codex's outer PTY belongs to a session outside the sandbox's PID namespace.
An inner PTY lets Bash interrupt foreground jobs without exiting the shell.
This module runs inside the existing sandbox; it grants no extra permissions.
"""

from __future__ import annotations

import errno
import os
import pty
import select
import signal
import termios
import tty


def _write(fd: int, data: bytes) -> None:
    remaining = memoryview(data)
    while remaining:
        remaining = remaining[os.write(fd, remaining):]


def main() -> int:
    child, master = pty.fork()
    if child == 0:
        os.execv("/bin/bash", ["/bin/bash", "--noprofile", "--norc", "-i"])
    previous = termios.tcgetattr(0)

    def resize(_signum=None, _frame=None):
        termios.tcsetwinsize(master, termios.tcgetwinsize(0))

    signal.signal(signal.SIGWINCH, resize)
    try:
        tty.setraw(0)
        resize()
        ended = False
        while not ended:
            # The outer terminal's foreground group may live outside this PID
            # namespace. Poll its size too; SIGWINCH alone is not reliable here.
            readable, _, _ = select.select([0, master], [], [], 0.2)
            resize()
            for source in readable:
                try:
                    data = os.read(source, 16 * 1024)
                except OSError as error:
                    if source == master and error.errno == errno.EIO:
                        data = b""  # Linux reports EIO when the last slave closes.
                    else:
                        raise
                if not data:
                    ended = True
                    break
                _write(master if source == 0 else 1, data)
    finally:
        signal.signal(signal.SIGWINCH, signal.SIG_IGN)
        termios.tcsetattr(0, termios.TCSANOW, previous)
        os.close(master)
        try:
            os.killpg(child, signal.SIGHUP)
        except ProcessLookupError:
            pass
        _, status = os.waitpid(child, 0)
    return os.waitstatus_to_exitcode(status)


if __name__ == "__main__":
    raise SystemExit(main())
