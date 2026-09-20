#!/usr/local/bin/python
"""Fixed namespace launcher and private proxy relay for App-owned Chromium."""

from __future__ import annotations

import argparse
import asyncio
import ctypes
import json
import os
from pathlib import Path
import resource
import socket
import stat
import struct

from browser_probe import isolation_checks


BWRAP = "/opt/codex-browser/bin/bwrap"
HELPER = "/usr/local/libexec/codex-bridge/browser_sandbox.py"
CHROMIUM = "/usr/bin/chromium-browser"
PROXY_SOCKET = "/run/browser/egress.sock"
PROXY_PORT = 13377
MAX_CONNECTIONS = 32
MAX_SESSION_SECONDS = 300
MAX_RELAY_BYTES = 32 * 1024 * 1024


def _instruction(code: int, jt: int, jf: int, value: int) -> bytes:
    return struct.pack("<HBBI", code, jt, jf, value)


def boundary_filter() -> bytes:
    """Deny cross-process escape primitives while retaining Chromium namespaces.

    The browser creates its own nested renderer sandbox. Unlike the Codex
    command filter, this filter must permit unshare and clone. No host namespace
    descriptors are inherited, and setns is forbidden regardless of descriptor.
    """
    if os.uname().machine != "x86_64":
        raise RuntimeError("unsupported browser architecture")
    instructions = [
        _instruction(0x20, 0, 0, 4),
        _instruction(0x15, 1, 0, 0xC000003E),
        _instruction(0x06, 0, 0, 0x80000000),
        _instruction(0x20, 0, 0, 0),
    ]
    # ptrace, kexec, keyctl, perf, setns, process_vm, bpf, userfaultfd,
    # open_by_handle_at, pidfd_getfd and the x32 syscall ABI.
    for number in (
        101,
        155,
        165,
        166,
        246,
        250,
        248,
        249,
        298,
        308,
        310,
        311,
        321,
        323,
        304,
        428,
        429,
        430,
        431,
        432,
        433,
        438,
        442,
    ):
        instructions.extend(
            (_instruction(0x15, 0, 1, number), _instruction(0x06, 0, 0, 0x50001))
        )
    instructions.extend(
        (_instruction(0x45, 0, 1, 0x40000000), _instruction(0x06, 0, 0, 0x80000000))
    )
    # Only stream sockets are needed for IPv4/IPv6. Unix-domain IPC remains
    # available, while QUIC, WebRTC UDP and raw IP sockets are denied by the
    # kernel independently of Chromium's proxy configuration.
    instructions.extend(
        (
            _instruction(0x15, 0, 8, 41),
            _instruction(0x20, 0, 0, 16),
            _instruction(0x15, 1, 0, 2),
            _instruction(0x15, 0, 4, 10),
            _instruction(0x20, 0, 0, 24),
            _instruction(0x54, 0, 0, 0xF),
            _instruction(0x15, 1, 0, 1),
            _instruction(0x06, 0, 0, 0x50001),
            _instruction(0x06, 0, 0, 0x7FFF0000),
            _instruction(0x06, 0, 0, 0x7FFF0000),
        )
    )
    return b"".join(instructions)


def launch(
    socket_path: str, read_fd: int, write_fd: int, canary_fd: int | None = None
) -> None:
    parent_pid = os.getppid()
    libc = ctypes.CDLL(None, use_errno=True)
    if parent_pid <= 1 or libc.prctl(1, 9, 0, 0, 0) != 0 or os.getppid() != parent_pid:
        raise RuntimeError("browser parent is unavailable")
    path = Path(socket_path)
    if (
        path.name != "egress.sock"
        or path.parent.parent != Path("/tmp")
        or not path.parent.name.startswith("codex-bridge-browser-")
        or path.is_symlink()
        or not stat.S_ISSOCK(path.lstat().st_mode)
        or path.parent.is_symlink()
        or stat.S_IMODE(path.parent.stat().st_mode) != 0o700
        or path.parent.stat().st_uid != os.getuid()
    ):
        raise RuntimeError("invalid private browser socket")
    if read_fd == write_fd or any(
        fd < 3 or not stat.S_ISFIFO(os.fstat(fd).st_mode) for fd in (read_fd, write_fd)
    ):
        raise RuntimeError("invalid browser control pipes")
    # Duplicate both sources before assigning the fixed Chromium descriptors.
    copies = (os.dup(read_fd), os.dup(write_fd))
    for original, target in zip(copies, (3, 4), strict=True):
        os.dup2(original, target, inheritable=True)
        os.set_inheritable(target, True)
    for descriptor in set((*copies, read_fd, write_fd)) - {3, 4}:
        os.close(descriptor)
    filter_fd = os.memfd_create("browser-boundary-seccomp", flags=0)
    os.write(filter_fd, boundary_filter())
    os.lseek(filter_fd, 0, os.SEEK_SET)
    os.set_inheritable(filter_fd, True)
    arguments = [
        BWRAP,
        "--die-with-parent",
        "--new-session",
        "--unshare-user",
        "--unshare-pid",
        "--unshare-net",
        "--unshare-ipc",
        "--unshare-uts",
        "--cap-drop",
        "ALL",
        "--add-seccomp-fd",
        str(filter_fd),
    ]
    for directory in ("/usr", "/lib", "/bin", "/sbin"):
        arguments.extend(("--ro-bind", directory, directory))
    arguments.extend(("--dir", "/etc"))
    for name in ("fonts", "ssl", "passwd", "group", "nsswitch.conf"):
        source = "/etc/" + name
        if Path(source).exists():
            arguments.extend(("--ro-bind", source, source))
    # HAOS masks parts of procfs and rejects a fresh proc mount. Retain those
    # masks; the dedicated LSM profile denies App-private files and state and
    # permits writes only to the caller-owned user-namespace mapping files.
    arguments.extend(
        (
            "--bind",
            "/proc",
            "/proc",
            "--dev",
            "/dev",
            "--size",
            "134217728",
            "--tmpfs",
            "/tmp",
            "--dir",
            "/run/browser",
            "--ro-bind",
            str(path),
            PROXY_SOCKET,
            "--remount-ro",
            "/",
            "--chdir",
            "/tmp",
            "--clearenv",
            "--setenv",
            "PATH",
            "/usr/local/bin:/usr/bin:/bin",
            "--setenv",
            "HOME",
            "/tmp/profile",
            "--setenv",
            "LANG",
            "C.UTF-8",
            "--",
            "/usr/local/bin/python",
            HELPER,
            "--inside",
            "--parent-pid",
            str(parent_pid),
        )
    )
    if canary_fd is not None:
        arguments.extend(("--canary-fd", str(canary_fd)))
    os.execv(BWRAP, arguments)


def chromium_command() -> list[str]:
    return [
        CHROMIUM,
        "--headless=new",
        "--remote-debugging-pipe",
        "--user-data-dir=/tmp/profile",
        f"--proxy-server=http://127.0.0.1:{PROXY_PORT}",
        "--proxy-bypass-list=<-loopback>",
        "--disable-quic",
        "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
        "--webrtc-ip-handling-policy=disable_non_proxied_udp",
        "--disable-extensions",
        "--disable-component-extensions-with-background-pages",
        "--disable-background-networking",
        "--disable-sync",
        "--disable-default-apps",
        "--disable-breakpad",
        "--disable-gpu-shader-disk-cache",
        "--download-restrictions=3",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-gpu",
        "--disable-software-rasterizer",
        "--disable-setuid-sandbox",
        "about:blank",
    ]


async def run_namespace(proof: dict[str, object]) -> None:
    if os.getuid() == 0:
        raise RuntimeError("browser cannot run as root")
    profile = Path("/proc/self/attr/current").read_text().strip()
    if not profile.endswith("//browser_bwrap (enforce)"):
        raise RuntimeError("browser LSM boundary missing")
    if {name for _, name in socket.if_nameindex()} != {"lo"}:
        raise RuntimeError("browser network namespace missing")
    for descriptor in (3, 4):
        if not stat.S_ISFIFO(os.fstat(descriptor).st_mode):
            raise RuntimeError("browser inherited a non-pipe control descriptor")
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(39, 0, 0, 0, 0) != 1 or libc.prctl(21, 0, 0, 0, 0) != 2:
        raise RuntimeError("browser syscall boundary missing")
    os.write(
        4,
        json.dumps({"method": "Bridge.isolationProof", "params": proof}).encode()
        + b"\0",
    )
    Path("/tmp/profile").mkdir(mode=0o700)
    resource.setrlimit(resource.RLIMIT_CPU, (360, 360))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_DATA, (768 * 1024 * 1024, 768 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_FSIZE, (64 * 1024 * 1024, 64 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_NOFILE, (256, 256))
    resource.setrlimit(resource.RLIMIT_NPROC, (128, 128))
    active: set[asyncio.Task] = set()

    async def relay(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        upstream = None
        if len(active) >= MAX_CONNECTIONS:
            writer.close()
            return
        active.add(task)

        async def copy(source, destination):
            total = 0
            while chunk := await asyncio.wait_for(source.read(65536), timeout=30):
                total += len(chunk)
                if total > MAX_RELAY_BYTES:
                    raise RuntimeError("browser relay limit reached")
                destination.write(chunk)
                await destination.drain()

        try:
            upstream_reader, upstream = await asyncio.open_unix_connection(PROXY_SOCKET)
            tasks = [
                asyncio.create_task(copy(reader, upstream)),
                asyncio.create_task(copy(upstream_reader, writer)),
            ]
            try:
                await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for pending in tasks:
                    pending.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
        except (OSError, RuntimeError, TimeoutError):
            pass
        finally:
            writer.close()
            if upstream is not None:
                upstream.close()
            active.discard(task)

    server = await asyncio.start_server(relay, "127.0.0.1", PROXY_PORT, limit=65536)
    browser = None
    try:
        browser = await asyncio.create_subprocess_exec(
            *chromium_command(),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            pass_fds=(3, 4),
            env={
                "PATH": "/usr/bin:/bin",
                "HOME": "/tmp/profile",
                "TMPDIR": "/tmp",
                "LANG": "C.UTF-8",
            },
        )
        await asyncio.wait_for(browser.wait(), timeout=MAX_SESSION_SECONDS)
    finally:
        server.close()
        for task in tuple(active):
            task.cancel()
        await asyncio.gather(*active, return_exceptions=True)
        await server.wait_closed()
        if browser is not None and browser.returncode is None:
            browser.kill()
            await browser.wait()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inside", action="store_true")
    parser.add_argument("--socket")
    parser.add_argument("--read-fd", type=int)
    parser.add_argument("--write-fd", type=int)
    parser.add_argument("--parent-pid", type=int)
    parser.add_argument("--canary-fd", type=int)
    arguments = parser.parse_args()
    if arguments.canary_fd is not None and not 3 <= arguments.canary_fd < 1048576:
        parser.error("invalid acceptance descriptor")
    if arguments.inside:
        if any(
            value is not None
            for value in (arguments.socket, arguments.read_fd, arguments.write_fd)
        ):
            parser.error("namespace entry takes no arguments")
        if arguments.parent_pid is None or arguments.parent_pid <= 1:
            parser.error("parent process required")
        # Check inherited descriptors before asyncio creates its own epoll and
        # wake-up socket pair inside the namespace.
        proof = isolation_checks(arguments.parent_pid, arguments.canary_fd)
        asyncio.run(run_namespace(proof))
    else:
        if arguments.parent_pid is not None:
            parser.error("parent process is fixed by the launcher")
        if any(
            value is None
            for value in (arguments.socket, arguments.read_fd, arguments.write_fd)
        ):
            parser.error("private launch arguments required")
        launch(
            arguments.socket, arguments.read_fd, arguments.write_fd, arguments.canary_fd
        )


if __name__ == "__main__":
    main()
