#!/usr/local/bin/python
"""Private Unix policy-proxy lifecycle for ``browser_worker.py``.

Only the isolated browser namespace receives this socket. Its loopback relay
uses it for every HTTP(S) request. Destination resolution and pinned socket
connection enforcement live in the signed Bridge package's
``BrowserPolicyProxy`` implementation; this tiny App-owned wrapper only gives
the fixed worker a synchronous lifecycle boundary.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from threading import Event, Thread

from codex_bridge_service.browser_egress import BrowserPolicyProxy


class BrowserPolicyError(RuntimeError):
    """The private egress policy could not be started or stopped safely."""


class UnixPolicyProxy:
    """Run the fixed policy proxy on a private asyncio thread.

    No caller can choose the listen address, destination resolver, headers, or
    upstream transport. The socket is bound into the browser's isolated mount
    namespace; the parent service opens no browser-facing TCP listener.
    """

    def __init__(self, socket_path: Path) -> None:
        self.socket_path = socket_path
        self._started = Event()
        self._stopped = Event()
        self._stop_requested = Event()
        self._thread: Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._proxy: BrowserPolicyProxy | None = None
        self._failure: BaseException | None = None

    def start(self) -> None:
        if self._thread is not None:
            raise BrowserPolicyError("browser egress policy already started")
        self._thread = Thread(
            target=self._run,
            name="codex-bridge-browser-policy",
            daemon=True,
        )
        self._thread.start()
        if not self._started.wait(timeout=5):
            self.close()
            raise BrowserPolicyError("browser egress policy did not start")
        if self._failure is not None:
            self.close()
            raise BrowserPolicyError("browser egress policy is unavailable") from self._failure

    def close(self) -> None:
        self._stop_requested.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=5)
            if thread.is_alive():
                raise BrowserPolicyError('browser egress policy did not stop')
        self._thread = None
        self._loop = None
        self._proxy = None

    def _run(self) -> None:
        asyncio.run(self._serve())

    async def _serve(self) -> None:
        self._loop = asyncio.get_running_loop()
        try:
            proxy = BrowserPolicyProxy()
            await proxy.start_unix(self.socket_path)
            self._proxy = proxy
        except BaseException as exc:
            self._failure = exc
            self._started.set()
            self._stopped.set()
            return
        self._started.set()
        try:
            while not self._stop_requested.is_set():
                await asyncio.sleep(0.1)
        finally:
            try:
                await proxy.close()
            finally:
                self._stopped.set()
