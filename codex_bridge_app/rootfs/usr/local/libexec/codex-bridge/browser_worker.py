#!/usr/local/bin/python
"""Fixed private Chromium worker for the experimental ``ha_browser`` tool.

The Bridge launches this file directly with an empty, allowlisted environment.
It reads one bounded JSON object per stdin line and writes one bounded JSON
object per stdout line.  The model never sees Chromium's CDP protocol: this
worker translates only the typed high-level actions defined by
``browser_contract.py`` and has no HTTP, WebSocket, Unix-socket, or command
line control plane.

This file is intentionally *not* an availability switch.  The Bridge requires
a separate root-owned sandbox + connection-time-egress proof before starting
it.  In particular, Chromium proxy flags are defence in depth, not evidence
that a browser namespace cannot bypass the proxy.
"""

from __future__ import annotations

import base64
from collections import deque
from dataclasses import dataclass
import json
import os
from pathlib import Path
import resource
import select
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from typing import Any, Final

from browser_policy import BrowserPolicyError, LoopbackPolicyProxy
from codex_bridge_service.browser_contract import (
    BrowserContractError,
    BrowserPageProjection,
    CloseAction,
    NavigateAction,
    OpenAction,
    PdfAction,
    ScreenshotAction,
    normalize_public_url,
    parse_browser_action,
)
from codex_bridge_service.browser_worker_client import browser_worker_attestation_ready


WORKER_PROTOCOL: Final = "browser-worker-v1"
CHROMIUM: Final = "/usr/bin/chromium-browser"
MAX_LINE_BYTES: Final = 64 * 1024
MAX_PAGE_TEXT_CHARS: Final = 32 * 1024
MAX_SCREENSHOT_BYTES: Final = 4 * 1024 * 1024
MAX_PDF_BYTES: Final = 8 * 1024 * 1024
MAX_ACTIONS: Final = 100
MAX_SESSION_SECONDS: Final = 300.0
MAX_BROWSER_MEMORY_BYTES: Final = 1_500 * 1024 * 1024


class WorkerError(RuntimeError):
    """An internal browser failure which must not expose implementation data."""


class CdpError(WorkerError):
    """Chromium rejected a fixed internal CDP operation."""


class NavigationBlocked(WorkerError):
    """The browser reached a URL outside the public-page policy."""


def _strict_json(payload: bytes) -> dict[str, object]:
    if not payload or len(payload) > MAX_LINE_BYTES:
        raise WorkerError("invalid worker request")

    def no_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        document: dict[str, object] = {}
        for key, value in pairs:
            if key in document:
                raise WorkerError("invalid worker request")
            document[key] = value
        return document

    try:
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=no_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WorkerError("invalid worker request") from exc
    if not isinstance(value, dict):
        raise WorkerError("invalid worker request")
    return value


def _response(
    session_id: str,
    *,
    page: BrowserPageProjection | None = None,
    artifact: tuple[str, str, bytes] | None = None,
) -> dict[str, object]:
    document: dict[str, object] = {"status": "ok", "session_id": session_id}
    if page is not None:
        document["page"] = page.model_dump(mode="json")
    if artifact is not None:
        kind, mime_type, data = artifact
        document["artifact"] = {
            "kind": kind,
            "mime_type": mime_type,
            "data_base64": base64.b64encode(data).decode("ascii"),
        }
    return document


def _failure(session_id: str, code: str, *, retryable: bool = False) -> dict[str, object]:
    return {
        "status": "error",
        "session_id": session_id,
        "error": {"code": code, "retryable": retryable},
    }


def chromium_command(*, profile: Path, proxy_port: int) -> list[str]:
    """Return the exact fixed Chromium launch command.

    ``--remote-debugging-pipe`` provides the only CDP transport and does not
    bind a TCP port.  All network-capable browser subsystems that could bypass
    the policy proxy are explicitly disabled.  No caller controls arguments.
    """

    if not profile.is_absolute() or not 1 <= proxy_port <= 65535:
        raise WorkerError("invalid browser launch configuration")
    return [
        CHROMIUM,
        "--headless=new",
        "--remote-debugging-pipe",
        f"--user-data-dir={profile}",
        f"--proxy-server=http://127.0.0.1:{proxy_port}",
        "--proxy-bypass-list=<-loopback>",
        "--disable-quic",
        "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
        "--webrtc-ip-handling-policy=disable_non_proxied_udp",
        "--disable-features=WebRtcHideLocalIpsWithMdns,WebRtcAllowInputVolumeAdjustment,ExtensionsToolbarMenu,DownloadBubble,DownloadBubbleV2",
        "--disable-extensions",
        "--disable-component-extensions-with-background-pages",
        "--disable-background-networking",
        "--disable-sync",
        "--disable-default-apps",
        "--disable-breakpad",
        "--download-restrictions=3",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-gpu",
        "--disable-dev-shm-usage",
        "about:blank",
    ]


def _browser_environment(profile: Path) -> dict[str, str]:
    return {
        "PATH": "/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin",
        "HOME": str(profile),
        "TMPDIR": str(profile),
        "XDG_CONFIG_HOME": str(profile / "config"),
        "XDG_CACHE_HOME": str(profile / "cache"),
        "LANG": "C.UTF-8",
    }


def _browser_preexec(read_fd: int, write_fd: int) -> None:
    """Give Chromium only CDP fds 3/4 and strict per-child rlimits."""

    os.dup2(read_fd, 3)
    os.dup2(write_fd, 4)
    resource.setrlimit(resource.RLIMIT_AS, (MAX_BROWSER_MEMORY_BYTES, MAX_BROWSER_MEMORY_BYTES))
    # A worker turn may be active for five minutes, but one Chromium child has
    # no reason to consume unlimited CPU while the outer worker enforces the
    # same wall-clock session deadline.
    resource.setrlimit(resource.RLIMIT_CPU, (360, 360))


class CdpPipe:
    """Minimal private CDP-over-pipe client for constant worker operations."""

    def __init__(self, *, profile: Path, proxy_port: int) -> None:
        to_browser_read, to_browser_write = os.pipe()
        from_browser_read, from_browser_write = os.pipe()
        self._write_fd = to_browser_write
        self._read_fd = from_browser_read
        self._buffer = b""
        self._events: deque[dict[str, object]] = deque()
        self._next_id = 1
        try:
            self._process = subprocess.Popen(
                chromium_command(profile=profile, proxy_port=proxy_port),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=_browser_environment(profile),
                close_fds=True,
                pass_fds=(to_browser_read, from_browser_write),
                start_new_session=True,
                preexec_fn=lambda: _browser_preexec(to_browser_read, from_browser_write),
            )
        except (OSError, subprocess.SubprocessError) as exc:
            os.close(to_browser_write)
            os.close(from_browser_read)
            raise WorkerError("Chromium could not start") from exc
        finally:
            os.close(to_browser_read)
            os.close(from_browser_write)

    def close(self) -> None:
        for descriptor in (self._write_fd, self._read_fd):
            try:
                os.close(descriptor)
            except OSError:
                pass
        process = self._process
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=2)
            except (OSError, subprocess.SubprocessError, TimeoutError):
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except OSError:
                    pass

    def call(self, method: str, params: dict[str, object], *, timeout: float = 15.0) -> dict[str, object]:
        if not isinstance(method, str) or not method or not isinstance(params, dict):
            raise WorkerError("invalid internal browser operation")
        process = self._process
        if process.poll() is not None:
            raise WorkerError("Chromium stopped")
        command_id = self._next_id
        self._next_id += 1
        payload = json.dumps(
            {"id": command_id, "method": method, "params": params},
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("ascii") + b"\0"
        try:
            os.write(self._write_fd, payload)
        except OSError as exc:
            raise WorkerError("Chromium control pipe failed") from exc
        deadline = time.monotonic() + timeout
        while True:
            message = self._next_message(deadline)
            if message.get("id") != command_id:
                self._events.append(message)
                continue
            if "error" in message:
                raise CdpError("fixed browser operation failed")
            result = message.get("result")
            if not isinstance(result, dict):
                raise CdpError("fixed browser operation returned invalid result")
            return result

    def wait_for_event(self, method: str, *, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        retained: deque[dict[str, object]] = deque()
        while self._events:
            event = self._events.popleft()
            if event.get("method") == method:
                self._events.extendleft(reversed(retained))
                return
            retained.append(event)
        self._events.extendleft(reversed(retained))
        while True:
            event = self._next_message(deadline)
            if event.get("method") == method:
                return
            self._events.append(event)

    def _next_message(self, deadline: float) -> dict[str, object]:
        while b"\0" not in self._buffer:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise WorkerError("fixed browser operation timed out")
            ready, _, _ = select.select([self._read_fd], [], [], remaining)
            if not ready:
                raise WorkerError("fixed browser operation timed out")
            chunk = os.read(self._read_fd, 64 * 1024)
            if not chunk:
                raise WorkerError("Chromium control pipe closed")
            self._buffer += chunk
            if len(self._buffer) > MAX_LINE_BYTES * 8:
                raise WorkerError("Chromium control output exceeded its bound")
        raw, self._buffer = self._buffer.split(b"\0", 1)
        try:
            message = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise WorkerError("Chromium control response was invalid") from exc
        if not isinstance(message, dict):
            raise WorkerError("Chromium control response was invalid")
        return message


_PAGE_PROJECTION = """(() => ({
  url: String(location.href),
  title: String(document.title || '').slice(0, 512),
  text: String(document.body ? document.body.innerText : '').slice(0, 32768)
}))()"""
_CLICK = """(selector => { const element = document.querySelector(selector); if (!element) return false; element.click(); return true; })"""
_TYPE = """((selector, text, clear, submit) => { const element = document.querySelector(selector); if (!element) return false; if (clear) element.value = ''; element.focus(); element.value = text; element.dispatchEvent(new Event('input', {bubbles: true})); element.dispatchEvent(new Event('change', {bubbles: true})); if (submit) { const form = element.form; if (form) form.requestSubmit(); } return true; })"""
_SELECT = """((selector, value) => { const element = document.querySelector(selector); if (!element || element.tagName !== 'SELECT') return false; element.value = value; element.dispatchEvent(new Event('input', {bubbles: true})); element.dispatchEvent(new Event('change', {bubbles: true})); return element.value === value; })"""
_HAS_SELECTOR = """(selector => Boolean(document.querySelector(selector)))"""
_HAS_TEXT = """(text => String(document.body ? document.body.innerText : '').includes(text))"""


def _runtime_value(cdp: CdpPipe, expression: str, *, timeout: float = 10.0) -> object:
    result = cdp.call(
        "Runtime.evaluate",
        {
            "expression": expression,
            "returnByValue": True,
            "awaitPromise": False,
            "userGesture": False,
        },
        timeout=timeout,
    )
    envelope = result.get("result")
    if not isinstance(envelope, dict) or "value" not in envelope:
        raise CdpError("fixed browser operation returned invalid value")
    return envelope["value"]


def _function_value(cdp: CdpPipe, declaration: str, arguments: list[object], *, timeout: float = 10.0) -> object:
    result = cdp.call(
        "Runtime.callFunctionOn",
        {
            "functionDeclaration": declaration,
            "arguments": [{"value": argument} for argument in arguments],
            "returnByValue": True,
            "awaitPromise": False,
            "userGesture": False,
        },
        timeout=timeout,
    )
    envelope = result.get("result")
    if not isinstance(envelope, dict) or "value" not in envelope:
        raise CdpError("fixed browser operation returned invalid value")
    return envelope["value"]


@dataclass(slots=True)
class Session:
    session_id: str
    profile: Path
    policy: LoopbackPolicyProxy
    cdp: CdpPipe
    created_at: float
    actions: int = 0

    @classmethod
    def create(cls, session_id: str) -> "Session":
        profile = Path(tempfile.mkdtemp(prefix="codex-bridge-browser-", dir="/tmp"))
        os.chmod(profile, 0o700)
        policy = LoopbackPolicyProxy()
        try:
            policy.start()
            cdp = CdpPipe(profile=profile, proxy_port=policy.address.port)
            cdp.call("Page.enable", {}, timeout=10)
            cdp.call("Runtime.enable", {}, timeout=10)
            return cls(
                session_id=session_id,
                profile=profile,
                policy=policy,
                cdp=cdp,
                created_at=time.monotonic(),
            )
        except BaseException:
            policy.close()
            _remove_profile(profile)
            raise

    def close(self) -> None:
        try:
            self.cdp.close()
        finally:
            self.policy.close()
            _remove_profile(self.profile)

    def check_limits(self) -> None:
        if self.actions >= MAX_ACTIONS or time.monotonic() - self.created_at > MAX_SESSION_SECONDS:
            raise WorkerError("browser session expired")

    def projection(self) -> BrowserPageProjection:
        main_frame_url = self.public_main_frame_url()
        value = _runtime_value(self.cdp, _PAGE_PROJECTION)
        if not isinstance(value, dict):
            raise CdpError("fixed browser projection was invalid")
        try:
            projection = BrowserPageProjection.model_validate(value)
        except (TypeError, ValueError) as exc:
            raise NavigationBlocked("browser navigation was blocked") from exc
        if projection.url != main_frame_url:
            raise NavigationBlocked("browser navigation was blocked")
        return projection

    def public_main_frame_url(self) -> str:
        """Validate the actual top-level frame after every browser action.

        Action inputs are checked before Chromium sees them, but a public page
        can redirect through a form, script, meta refresh, or an external
        navigation.  CDP's navigation history identifies the browser's current
        top-level document rather than trusting DOM content from a child frame.
        """

        result = self.cdp.call("Page.getNavigationHistory", {}, timeout=10)
        entries = result.get("entries")
        index = result.get("currentIndex")
        if (
            not isinstance(entries, list)
            or type(index) is not int
            or not 0 <= index < len(entries)
            or not isinstance(entries[index], dict)
        ):
            raise CdpError("browser navigation history was invalid")
        url = entries[index].get("url")
        try:
            return normalize_public_url(url)
        except BrowserContractError as exc:
            raise NavigationBlocked("browser navigation was blocked") from exc

    def navigate(self, action: OpenAction | NavigateAction) -> BrowserPageProjection:
        self.cdp.call(
            "Page.navigate",
            {"url": action.url},
            timeout=action.timeout_ms / 1000,
        )
        event = "Page.loadEventFired" if action.wait_until == "load" else "Page.domContentEventFired"
        self.cdp.wait_for_event(event, timeout=action.timeout_ms / 1000)
        return self.projection()


def _remove_profile(profile: Path) -> None:
    try:
        expected_parent = Path("/tmp")
        if profile.parent != expected_parent or not profile.name.startswith("codex-bridge-browser-"):
            return
        metadata = profile.lstat()
        if not stat.S_ISDIR(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != 0o700:
            return
        shutil.rmtree(profile)
    except OSError:
        return


class BrowserWorker:
    def __init__(self) -> None:
        self._session: Session | None = None

    def close(self) -> None:
        session, self._session = self._session, None
        if session is not None:
            session.close()

    def handle(self, request: dict[str, object]) -> dict[str, object]:
        if set(request) == {"close_session"}:
            session_id = request.get("close_session")
            if not isinstance(session_id, str):
                raise WorkerError("invalid close request")
            if self._session is not None and self._session.session_id == session_id:
                self.close()
            return _response(session_id)
        if set(request) != {"session_id", "action"}:
            raise WorkerError("invalid worker request")
        session_id = request.get("session_id")
        if not isinstance(session_id, str):
            raise WorkerError("invalid browser session")
        try:
            action = parse_browser_action(request.get("action"))
        except BrowserContractError:
            return _failure(session_id, "navigation_blocked")
        if isinstance(action, OpenAction):
            if self._session is not None:
                return _failure(session_id, "browser_unavailable")
            try:
                self._session = Session.create(session_id)
                self._session.actions = 1
                return _response(session_id, page=self._session.navigate(action))
            except NavigationBlocked:
                self.close()
                return _failure(session_id, "navigation_blocked")
            except (BrowserPolicyError, OSError, WorkerError, CdpError):
                self.close()
                return _failure(session_id, "navigation_failed", retryable=True)
        session = self._session
        if session is None or session.session_id != session_id:
            return _failure(session_id, "session_closed")
        if isinstance(action, CloseAction):
            self.close()
            return _response(session_id)
        try:
            session.check_limits()
            session.actions += 1
            return self._handle_action(session, action)
        except NavigationBlocked:
            self.close()
            return _failure(session_id, "navigation_blocked")
        except WorkerError:
            self.close()
            return _failure(session_id, "worker_failed")
        except CdpError:
            return _failure(session_id, "navigation_failed", retryable=True)

    def _handle_action(self, session: Session, action: Any) -> dict[str, object]:
        if action.action == "navigate":
            return _response(session.session_id, page=session.navigate(action))
        if action.action == "inspect":
            projection = session.projection()
            text = projection.text[: action.max_chars]
            if action.selector is not None:
                value = _function_value(session.cdp, _HAS_SELECTOR, [action.selector])
                if value is not True:
                    return _failure(session.session_id, "selector_not_found")
            return _response(
                session.session_id,
                page=projection.model_copy(update={"text": text}),
            )
        if action.action == "click":
            if _function_value(session.cdp, _CLICK, [action.selector], timeout=action.timeout_ms / 1000) is not True:
                session.public_main_frame_url()
                return _failure(session.session_id, "selector_not_found")
            return _response(session.session_id, page=session.projection())
        if action.action == "type":
            if _function_value(session.cdp, _TYPE, [action.selector, action.text, action.clear, action.submit], timeout=action.timeout_ms / 1000) is not True:
                session.public_main_frame_url()
                return _failure(session.session_id, "selector_not_found")
            return _response(session.session_id, page=session.projection())
        if action.action == "select":
            if _function_value(session.cdp, _SELECT, [action.selector, action.value], timeout=action.timeout_ms / 1000) is not True:
                session.public_main_frame_url()
                return _failure(session.session_id, "selector_not_found")
            return _response(session.session_id, page=session.projection())
        if action.action == "wait":
            deadline = time.monotonic() + action.timeout_ms / 1000
            while time.monotonic() < deadline:
                if action.selector is not None:
                    matched = _function_value(session.cdp, _HAS_SELECTOR, [action.selector])
                elif action.text is not None:
                    matched = _function_value(session.cdp, _HAS_TEXT, [action.text])
                else:
                    matched = True
                if matched is True:
                    return _response(session.session_id, page=session.projection())
                time.sleep(0.05)
            session.public_main_frame_url()
            return _failure(session.session_id, "page_timeout", retryable=True)
        if isinstance(action, ScreenshotAction):
            session.public_main_frame_url()
            options: dict[str, object] = {
                "format": action.format,
                "captureBeyondViewport": action.full_page,
                "fromSurface": True,
            }
            if action.quality is not None:
                options["quality"] = action.quality
            result = session.cdp.call(
                "Page.captureScreenshot",
                options,
                timeout=30,
            )
            encoded = result.get("data")
            if not isinstance(encoded, str):
                raise CdpError("screenshot response was invalid")
            try:
                data = base64.b64decode(encoded.encode("ascii"), validate=True)
            except (UnicodeEncodeError, ValueError) as exc:
                raise CdpError("screenshot response was invalid") from exc
            if not data or len(data) > MAX_SCREENSHOT_BYTES:
                raise CdpError("screenshot response was invalid")
            mime_type = "image/png" if action.format == "png" else "image/jpeg"
            return _response(session.session_id, artifact=("screenshot", mime_type, data))
        if isinstance(action, PdfAction):
            session.public_main_frame_url()
            result = session.cdp.call(
                "Page.printToPDF",
                {
                    "landscape": action.landscape,
                    "printBackground": action.print_background,
                    "paperWidth": 8.27 if action.format == "A4" else 8.5,
                    "paperHeight": 11.69 if action.format == "A4" else 11.0,
                },
                timeout=30,
            )
            encoded = result.get("data")
            if not isinstance(encoded, str):
                raise CdpError("PDF response was invalid")
            try:
                data = base64.b64decode(encoded.encode("ascii"), validate=True)
            except (UnicodeEncodeError, ValueError) as exc:
                raise CdpError("PDF response was invalid") from exc
            if not data.startswith(b"%PDF-") or len(data) > MAX_PDF_BYTES:
                raise CdpError("PDF response was invalid")
            return _response(session.session_id, artifact=("pdf", "application/pdf", data))
        raise WorkerError("unsupported fixed browser action")


def main() -> int:
    # Defence in depth: the Bridge client verifies this before spawning us, but
    # a manually invoked rootfs executable must not become an alternate route
    # around the namespace/sandbox/egress gate either.
    if not browser_worker_attestation_ready():
        return 1
    worker = BrowserWorker()
    try:
        for raw_line in sys.stdin.buffer:
            session_id = "brs_0000000000000000"
            try:
                if len(raw_line) > MAX_LINE_BYTES + 1 or not raw_line.endswith(b"\n"):
                    raise WorkerError("invalid worker request")
                request = _strict_json(raw_line[:-1])
                candidate = request.get("session_id", request.get("close_session"))
                if isinstance(candidate, str):
                    session_id = candidate
                response = worker.handle(request)
            except BaseException:
                worker.close()
                response = _failure(session_id, "worker_failed")
            encoded = json.dumps(response, separators=(",", ":"), ensure_ascii=True).encode("ascii")
            if len(encoded) > 12 * 1024 * 1024:
                worker.close()
                encoded = json.dumps(_failure(session_id, "worker_failed"), separators=(",", ":")).encode("ascii")
            sys.stdout.buffer.write(encoded + b"\n")
            sys.stdout.buffer.flush()
    finally:
        worker.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
