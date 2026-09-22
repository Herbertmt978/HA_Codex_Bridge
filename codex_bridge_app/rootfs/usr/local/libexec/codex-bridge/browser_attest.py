#!/usr/local/bin/python
"""Root-owned, boot-local acceptance of the actual Chromium process boundary."""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import pwd
import secrets
import select
import signal
import stat
import subprocess
import sys
import time

from browser_resources import process_tree

from codex_bridge_service.browser_worker_client import (
    BROWSER_WORKER_ATTESTATION_PATH,
    BROWSER_WORKER_PROTOCOL,
    CHROMIUM_VERSION,
)

SELF = Path("/usr/local/libexec/codex-bridge/browser_attest.py")


def worker(canary: str) -> None:
    from browser_worker import Session

    assert Path(canary).parent == Path("/data") and Path(canary).name.startswith(
        ".browser-proof-"
    )
    canary_fd = os.open(canary, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    session = None
    try:
        session = Session.create("brs_startup_acceptance", canary_fd=canary_fd)
        # A fixed offline page proves rendering without depending on a public
        # website at every App boot. Public egress is tested on the target
        # before release; the exact policy's private-denial path is tested here.
        navigation = session.cdp.call(
            "Page.navigate",
            {
                "url": "data:text/html,<title>Browser acceptance</title><h1>Sandbox ready</h1>"
            },
        )
        session.cdp.wait_for_event(
            "Page.lifecycleEvent",
            params={"name": "load", "loaderId": navigation["loaderId"]},
            timeout=10,
        )
        screenshot = session.cdp.call("Page.captureScreenshot", {"format": "png"})
        pdf = session.cdp.call("Page.printToPDF", {})
        assert base64.b64decode(screenshot["data"], validate=True).startswith(
            b"\x89PNG"
        )
        assert base64.b64decode(pdf["data"], validate=True).startswith(b"%PDF-")
        proof = next(
            event["params"]
            for event in session.cdp._events
            if event.get("method") == "Bridge.isolationProof"
        )
        assert proof["checks"] and all(
            value is True for value in proof["checks"].values()
        )
        assert proof["checks"]["private_parent_fd_denied"] is True
        import socket

        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(3)
            connection.connect(str(session.policy.socket_path))
            connection.sendall(
                b"CONNECT 127.0.0.1:443 HTTP/1.1\r\nHost: 127.0.0.1:443\r\n\r\n"
            )
            assert connection.recv(4096).startswith(b"HTTP/1.1 403 ")
        version = session.cdp.call("Browser.getVersion", {})["product"].split("/")[-1]
        renderer_ids = [
            int(process["id"])
            for process in session.cdp.call("SystemInfo.getProcessInfo", {})[
                "processInfo"
            ]
            if process["type"] == "renderer"
        ]
        assert renderer_ids
        print(
            json.dumps(
                {
                    "launcher_pid": session.cdp._process.pid,
                    "namespace_pid": proof["pid"],
                    "checks": proof["checks"],
                    "namespaces": proof["namespaces"],
                    "namespace_pids": proof["namespace_pids"],
                    "chromium_version": version,
                    "renderer_ids": renderer_ids,
                }
            ),
            flush=True,
        )
        if sys.stdin.buffer.readline(16) != b"close\n":
            raise RuntimeError("invalid acceptance completion")
    finally:
        try:
            if session is not None:
                session.close()
        finally:
            os.close(canary_fd)


def inspect_process(
    pid: int,
    parent_profile: str,
    uid: int,
    *,
    renderer: bool = False,
    proc_root: Path = Path("/proc"),
) -> dict[str, object]:
    proc = proc_root / str(pid)
    values = dict(
        line.split(":", 1)
        for line in (proc / "status").read_text().splitlines()
        if ":" in line
    )
    assert (
        proc / "attr/current"
    ).read_text().strip() == parent_profile + "//browser_bwrap (enforce)"
    assert values["NoNewPrivs"].strip() == "1" and values["Seccomp"].strip() == "2"
    assert all(
        int(values[key].strip(), 16) == 0
        for key in ("CapInh", "CapPrm", "CapEff", "CapAmb")
    ), {
        key: values[key].strip()
        for key in ("CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb")
    }
    if not renderer:
        assert int(values["CapBnd"].strip(), 16) == 0
    # Creating Chromium's nested user namespace resets its bounding set.
    # Its usable capability sets are empty, no-new-privileges prevents exec
    # from gaining capabilities, and the inherited boundary filter persists.
    assert all(int(value) == uid for value in values["Uid"].split())
    # HAOS denies cross-namespace readlink without SYS_PTRACE. Do not weaken
    # that boundary. These kernel records remain readable from the trusted
    # parent and independently confirm the PID/UID mapping and syscall filters.
    uid_map = [
        tuple(int(value) for value in line.split())
        for line in (proc / "uid_map").read_text().splitlines()
    ]
    assert uid_map and all(len(row) == 3 and row[2] == 1 for row in uid_map)
    pids = [int(value) for value in values["NSpid"].split()]
    assert pids[0] == pid and len(pids) > 1
    return {"namespace_pids": pids, "seccomp_filters": int(values["Seccomp_filters"])}


def attest() -> None:
    assert os.getuid() == 0
    target = BROWSER_WORKER_ATTESTATION_PATH
    parent = target.parent.lstat()
    assert (
        stat.S_ISDIR(parent.st_mode)
        and parent.st_uid == 0
        and not parent.st_mode & 0o022
    )
    target.unlink(missing_ok=True)
    if os.uname().machine != "x86_64":
        raise RuntimeError("Browser tools are not qualified for this architecture")
    profile = Path("/proc/self/attr/current").read_text().strip()
    assert profile.endswith(" (enforce)") and "//" not in profile
    profile = profile.removesuffix(" (enforce)")
    account = pwd.getpwnam("codexbridge")

    for name in (
        "browser_attest.py",
        "browser_worker.py",
        "browser_sandbox.py",
        "browser_probe.py",
        "browser_resources.py",
        "browser_policy.py",
    ):
        metadata = (SELF.parent / name).lstat()
        assert (
            stat.S_ISREG(metadata.st_mode)
            and metadata.st_uid == 0
            and not metadata.st_mode & 0o022
        )
    canary = Path("/data") / (".browser-proof-" + secrets.token_hex(16))
    child = None
    descendants = set()
    try:
        descriptor = os.open(
            canary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400
        )
        try:
            os.write(descriptor, secrets.token_bytes(32))
            os.fchown(descriptor, account.pw_uid, account.pw_gid)
        finally:
            os.close(descriptor)
        child = subprocess.Popen(
            [sys.executable, str(SELF), "--worker", str(canary)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            user=account.pw_uid,
            group=account.pw_gid,
            extra_groups=[],
            start_new_session=True,
            env={
                "PATH": "/usr/local/bin:/usr/bin:/bin",
                "HOME": "/tmp",
                "LANG": "C.UTF-8",
                "PYTHONDONTWRITEBYTECODE": "1",
            },
        )
        deadline = time.monotonic() + 40
        output = b""
        while not output.endswith(b"\n"):
            remaining = deadline - time.monotonic()
            assert remaining > 0 and select.select([child.stdout], [], [], remaining)[0]
            chunk = os.read(child.stdout.fileno(), 4096)
            assert chunk
            output += chunk
            assert len(output) <= 8192
        report = json.loads(output)
        assert report["chromium_version"] == CHROMIUM_VERSION
        assert report["checks"] and all(
            value is True for value in report["checks"].values()
        )
        assert report["checks"]["private_parent_fd_denied"] is True
        descendants = process_tree(child.pid) - {child.pid}
        assert (
            report["launcher_pid"] in descendants
            and report["namespace_pid"] in descendants
        ), {
            "launcher": report["launcher_pid"],
            "namespace": report["namespace_pid"],
            "descendants": sorted(descendants),
        }
        namespace = inspect_process(report["namespace_pid"], profile, account.pw_uid)
        assert namespace["namespace_pids"] == report["namespace_pids"]
        assert set(report["namespaces"]) == {"user", "net", "mnt", "pid"}
        assert all(
            value != os.readlink("/proc/self/ns/" + name)
            for name, value in report["namespaces"].items()
        )
        renderers = []
        for pid in descendants:
            try:
                command = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
                status_lines = Path(f"/proc/{pid}/status").read_text().splitlines()
                process_pids = next(
                    [int(value) for value in line.split()[1:]]
                    for line in status_lines
                    if line.startswith("NSpid:")
                )
            except FileNotFoundError:
                continue
            # Chromium forks renderers from its zygote; the kernel argv may
            # still describe the zygote. Bind Chromium's fixed process report
            # to the independently observed PID hierarchy instead.
            if (
                len(process_pids) > len(namespace["namespace_pids"])
                and process_pids[len(namespace["namespace_pids"]) - 1]
                in report["renderer_ids"]
            ):
                assert not any(
                    argument in command
                    for argument in (
                        b"--no-sandbox",
                        b"--disable-seccomp-filter-sandbox",
                    )
                )
                renderer = inspect_process(pid, profile, account.pw_uid, renderer=True)
                assert (
                    len(renderer["namespace_pids"]) > len(namespace["namespace_pids"])
                    and renderer["seccomp_filters"] > namespace["seccomp_filters"]
                )
                renderers.append(pid)
        assert renderers
        child.stdin.write(b"close\n")
        child.stdin.flush()
        assert child.wait(timeout=8) == 0
        deadline = time.monotonic() + 3
        while (
            any(Path(f"/proc/{pid}").exists() for pid in descendants)
            and time.monotonic() < deadline
        ):
            time.sleep(0.05)
        assert not any(Path(f"/proc/{pid}").exists() for pid in descendants)
        payload = {
            "schema_version": 1,
            "worker_protocol": BROWSER_WORKER_PROTOCOL,
            "chromium_version": CHROMIUM_VERSION,
            "chromium_sandbox": "ready",
            "egress_boundary": "ready",
        }
        fd = os.open(
            target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644
        )
        with os.fdopen(fd, "w") as stream:
            json.dump(payload, stream)
            stream.flush()
            os.fsync(stream.fileno())
        print("Browser isolation and rendering checks passed.")
    finally:
        canary.unlink(missing_ok=True)
        if child is not None and child.poll() is None:
            os.killpg(child.pid, signal.SIGKILL)
            child.wait(timeout=5)


if __name__ == "__main__":
    try:
        if len(sys.argv) == 3 and sys.argv[1] == "--worker":
            worker(sys.argv[2])
        elif sys.argv[1:] == ["--disabled"] and os.getuid() == 0:
            BROWSER_WORKER_ATTESTATION_PATH.unlink(missing_ok=True)
        elif not sys.argv[1:]:
            attest()
        else:
            raise RuntimeError("invalid acceptance operation")
    except BaseException:
        if os.getuid() == 0:
            BROWSER_WORKER_ATTESTATION_PATH.unlink(missing_ok=True)
        raise
