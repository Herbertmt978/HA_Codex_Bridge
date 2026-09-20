"""Start the optional privileged App without S6 (which cannot use host PID)."""

from __future__ import annotations

import ipaddress
import json
import os
from pathlib import Path
import secrets
import stat
import subprocess
import sys
import threading
import time

import httpx
import uvicorn

from .host_access_contract import HostIdentity
from .host_worker import HostWorker, create_host_worker_app


def private_value(path: Path, size: int) -> str:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError:
        pass
    else:
        try:
            value = secrets.token_hex(size // 2).encode()
            os.write(descriptor, value)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != 0
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_nlink != 1 or metadata.st_size != size
        ):
            raise RuntimeError("Host Access private state is unsafe")
        value = os.read(descriptor, size + 1).decode("ascii")
        if len(value) != size or any(char not in "0123456789abcdef" for char in value):
            raise RuntimeError("Host Access private state is invalid")
        return value
    finally:
        os.close(descriptor)


def _supervisor_json(client: httpx.Client, method: str, path: str, **kwargs) -> dict:
    with client.stream(method, f"http://supervisor/{path}", **kwargs) as response:
        response.raise_for_status()
        payload = bytearray()
        for chunk in response.iter_bytes():
            payload.extend(chunk)
            if len(payload) > 65536:
                raise ValueError("Oversized Supervisor response")
    value = json.loads(payload)
    if not isinstance(value, dict) or value.get("result") != "ok":
        raise ValueError("Supervisor request failed")
    return value["data"]


def publish_discovery(supervisor_token: str, token: str, companion_id: str) -> None:
    # No credential appears in process arguments, a log, or a child environment.
    publication_id = secrets.token_hex(16)
    with httpx.Client(
        headers={"Authorization": f"Bearer {supervisor_token}"},
        trust_env=False, follow_redirects=False, timeout=10,
    ) as client:
        for _ in range(60):
            time.sleep(5)
            try:
                info = _supervisor_json(client, "GET", "addons/self/info")
                address = ipaddress.ip_address(info["ip_address"])
                if not address.is_private or address.is_loopback or address.is_link_local:
                    raise ValueError("Invalid Supervisor network")
                _supervisor_json(client, "POST", "discovery", json={
                    "service": "codex_bridge",
                    "config": {
                        "kind": "host_access", "host": str(address), "port": 8767,
                        "token": token, "api": {"minimum": 1, "maximum": 1},
                        "companion_id": companion_id,
                        "publication_id": publication_id,
                    },
                })
                print("Codex Host Access discovery published.", flush=True)
                return
            except Exception:
                # Remote errors can contain private request/response data.
                continue
    print("Host Access discovery failed. Restart this App to retry.", flush=True)


def main() -> int:
    try:
        if os.geteuid() != 0:
            raise RuntimeError("Host Access must start as root")
        os.umask(0o077)
        directory = Path("/data/host-access")
        directory.mkdir(mode=0o700, exist_ok=True)
        metadata = directory.lstat()
        if (
            not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != 0
            or stat.S_IMODE(metadata.st_mode) != 0o700
        ):
            raise RuntimeError("Host Access state directory is unsafe")
        token = private_value(directory / "token", 64)
        companion_id = private_value(directory / "identity", 32)
        supervisor_token = os.environ.pop("SUPERVISOR_TOKEN", "")
        proof = subprocess.run(
            [sys.executable, "-m", "codex_bridge_service.host_entry", "--probe"],
            check=True, capture_output=True, timeout=15,
            env={"PATH": "/usr/local/bin:/usr/bin:/bin"},
        )
        identity = HostIdentity(companion_id=companion_id, **json.loads(proof.stdout))
        worker = HostWorker(identity)
        application = create_host_worker_app(worker, token)
        if supervisor_token:
            threading.Thread(
                target=publish_discovery,
                args=(supervisor_token, token, companion_id), daemon=True,
            ).start()
            supervisor_token = ""
        # Host commands use a fresh allowlisted environment. The worker itself
        # also retains no inherited Supervisor/HA/Codex environment credential.
        os.environ.clear()
        os.environ["PATH"] = "/usr/local/bin:/usr/bin:/bin"
        uvicorn.run(application, host="0.0.0.0", port=8767, access_log=False, timeout_graceful_shutdown=5)
        return 0
    except Exception:
        print(
            "Host Access could not verify HAOS root access. Check that this is "
            "the optional Host Access App and its Protection mode is disabled.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
