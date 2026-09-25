"""Test-only negative probe for a native isolated stdio worker.

This module is deliberately outside the approved package catalogue and must
never be copied into the signed App image. It prints booleans only, never file
contents, environment values, socket data or paths discovered in the worker.
"""

from __future__ import annotations

import json
import os
import socket
import sys


def denied_read(path: str) -> bool:
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        return True
    else:
        os.close(descriptor)
        return False


def denied_socket(family: socket.AddressFamily, address: object) -> bool:
    try:
        connection = socket.socket(family, socket.SOCK_STREAM)
    except OSError:
        return True
    try:
        connection.settimeout(0.1)
        return connection.connect_ex(address) != 0
    except OSError:
        return True
    finally:
        connection.close()


def main() -> int:
    sensitive = ("SUPERVISOR", "CODEX", "OPENAI", "HOME_ASSISTANT", "TOKEN", "API_KEY", "SECRET")
    result = {
        "denied_data": denied_read("/data"),
        "denied_config": denied_read("/config"),
        "denied_private_runtime": denied_read("/run/codex-bridge"),
        "denied_sibling_package": denied_read("/opt/codex-stdio/packages/other-package"),
        "sensitive_env_absent": not any(
            any(marker in name.upper() for marker in sensitive) for name in os.environ
        ),
        "denied_pid_one_environment": denied_read("/proc/1/environ"),
        "denied_pid_one_fd_alias": denied_read("/proc/1/fd/3"),
        "denied_self_fd_alias": denied_read("/proc/self/fd/3"),
        "denied_ipv4_loopback": denied_socket(socket.AF_INET, ("127.0.0.1", 9)),
        "denied_ipv6_loopback": denied_socket(socket.AF_INET6, ("::1", 9)),
        "denied_unix_private_socket": denied_socket(
            socket.AF_UNIX, "/run/codex-bridge/private.sock"
        ),
    }
    sys.stdout.write(json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n")
    sys.stdout.flush()
    return 0 if sys.stdin.readline() == "close\n" else 2


if __name__ == "__main__":
    raise SystemExit(main())
