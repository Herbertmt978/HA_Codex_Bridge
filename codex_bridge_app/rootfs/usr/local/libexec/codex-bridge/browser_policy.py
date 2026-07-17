#!/usr/local/bin/python
"""Private loopback policy-proxy lifecycle for ``browser_worker.py``.

The worker never exposes this proxy beyond loopback and Chromium is configured
to use it for every HTTP(S) request.  Destination resolution and pinned socket
connection enforcement live in the signed Bridge package's
``BrowserPolicyProxy`` implementation; this tiny App-owned wrapper only gives
the fixed worker a synchronous lifecycle boundary.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from threading import Event, Thread

from codex_bridge_service.browser_egress import BrowserPolicyProxy


class BrowserPolicyError(RuntimeError):
    """The private egress policy could not be started or stopped safely."""


@dataclass(frozen=True, slots=True)
class PolicyAddress:
    host: str
    port: int


class LoopbackPolicyProxy:
    """Run the fixed policy proxy on a private asyncio thread.

    No caller can choose the listen address, destination resolver, headers, or
    upstream transport.  The browser worker only receives the resulting
    loopback port for Chromium's forced proxy flag.
    """

    def __init__(self) -> None:
        self._started = Event()
        self._stopped = Event()
        self._stop_requested = Event()
        self._thread: Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._proxy: BrowserPolicyProxy | None = None
        self._address: PolicyAddress | None = None
        self._failure: BaseException | None = None

    @property
    def address(self) -> PolicyAddress:
        address = self._address
        if address is None or self._failure is not None:
            raise BrowserPolicyError("browser egress policy is unavailable")
        return address

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
        # BrowserPolicyProxy itself guarantees this exact loopback-only address.
        if self.address.host != "127.0.0.1" or not 1 <= self.address.port <= 65535:
            self.close()
            raise BrowserPolicyError("browser egress policy address is invalid")

    def close(self) -> None:
        self._stop_requested.set()
        loop = self._loop
        proxy = self._proxy
        if loop is not None and proxy is not None and not loop.is_closed():
            try:
                future = asyncio.run_coroutine_threadsafe(proxy.close(), loop)
                future.result(timeout=5)
            except (RuntimeError, TimeoutError):
                pass
        thread = self._thread
        if thread is not None:
            self._stopped.wait(timeout=5)
            thread.join(timeout=1)
        self._thread = None
        self._loop = None
        self._proxy = None
        self._address = None

    def _run(self) -> None:
        asyncio.run(self._serve())

    async def _serve(self) -> None:
        self._loop = asyncio.get_running_loop()
        try:
            proxy = BrowserPolicyProxy()
            await proxy.start()
            host, port = proxy.address
            self._proxy = proxy
            self._address = PolicyAddress(host=host, port=port)
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
