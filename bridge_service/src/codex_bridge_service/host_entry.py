"""Fixed child entrypoint for the separately privileged HAOS companion.

Never imported into the web server: namespace changes belong to this child only.
"""

from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import sys

from .host_access_contract import HostCommand, MAX_COMMAND_BYTES


def enter_host() -> None:
    if sys.platform != "linux" or os.geteuid() != 0 or not hasattr(os, "setns"):
        raise RuntimeError("HAOS host privileges are unavailable")
    # Open everything before replacing the mount namespace and root directory.
    root = os.open("/proc/1/root", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    namespaces = []
    try:
        for name in ("net", "uts", "ipc", "mnt"):
            namespaces.append(os.open(f"/proc/1/ns/{name}", os.O_RDONLY | os.O_CLOEXEC))
        # The companion must already share the host PID namespace. Joining just
        # mounts inside an ordinary App must never be accepted as host access.
        if Path("/proc/1/comm").read_text().strip() != "systemd":
            raise RuntimeError("the companion is not in the HAOS PID namespace")
        for descriptor in namespaces:
            os.setns(descriptor, 0)
        os.fchdir(root)
        os.chroot(".")
        os.chdir("/")
    finally:
        for descriptor in namespaces:
            os.close(descriptor)
        os.close(root)


def host_identity() -> dict[str, str]:
    values = {}
    for line in Path("/etc/os-release").read_text().splitlines():
        key, separator, value = line.partition("=")
        if separator:
            values[key] = value.strip('"')
    if values.get("ID") != "haos":
        raise RuntimeError("the host is not Home Assistant OS")
    machine_id = Path("/etc/machine-id").read_text().strip()
    if len(machine_id) != 32 or any(char not in "0123456789abcdef" for char in machine_id):
        raise RuntimeError("the HAOS machine identity is unavailable")
    return {
        "hostname": os.uname().nodename, "os_version": values["VERSION_ID"],
        "machine_fingerprint": hashlib.sha256(machine_id.encode()).hexdigest(),
    }


def main() -> int:
    try:
        probe = sys.argv[1:] == ["--probe"]
        if not probe and sys.argv[1:] != ["--execute"]:
            return 2
        operation = None
        if not probe:
            raw = sys.stdin.buffer.read(MAX_COMMAND_BYTES * 8 + 8193)
            if len(raw) > MAX_COMMAND_BYTES * 8 + 8192:
                return 2
            operation = HostCommand.model_validate_json(raw)
        enter_host()
        identity = host_identity()
        if probe:
            print(json.dumps(identity))
            return 0
        assert operation is not None
        os.chdir(operation.cwd)
        os.execve("/bin/sh", ["sh", "-c", operation.command], {
            "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
            "HOME": "/root", "LANG": "C.UTF-8",
        })
    except Exception:
        # Do not echo a command, path, credential or a provider-controlled error.
        print("Host command could not enter the verified HAOS environment.", file=sys.stderr)
        return 125
    return 125


if __name__ == "__main__":
    raise SystemExit(main())
