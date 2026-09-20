"""Native App acceptance, run as codexbridge after root startup attestation.

This deliberately uses a fixed local DOM fixture at a verified public page.
No form is submitted to an external service. Test-only CDP setup is never
available through the model's typed browser contract.
"""

from __future__ import annotations

import base64
from pathlib import Path
import sys
import threading
import time

sys.path.insert(0, "/usr/local/libexec/codex-bridge")

from browser_resources import process_tree  # noqa: E402
from browser_worker import BrowserWorker, Session  # noqa: E402
from codex_bridge_service.browser_worker_client import (  # noqa: E402
    BrowserWorkerClient,
    BrowserWorkerClientError,
)

SESSION = "brs_0123456789abcdef"
FIXTURE = """<!doctype html><title>Browser action acceptance</title>
<h1>Browser action acceptance</h1><output id="result">Ready</output>
<form onsubmit="event.preventDefault();result.textContent='Submitted '+namefield.value">
<input id="namefield" aria-label="Name">
<select id="choice" onchange="result.textContent='Choice '+this.value">
<option value="a">A</option><option value="b">B</option></select>
<button type="button" id="click" onclick="result.textContent='Clicked'">Click</button>
<a id="fragment" href="#next">Next</a></form>"""


def wait_removed(pids: set[int]) -> None:
    deadline = time.monotonic() + 5
    while any(Path(f"/proc/{pid}").exists() for pid in pids):
        assert time.monotonic() < deadline, "browser processes survived cleanup"
        time.sleep(0.05)


def actions() -> None:
    worker = BrowserWorker()

    def action(name, **values):
        payload = {"action": name, **values}
        if name != "open":
            payload["session_id"] = SESSION
        response = worker.handle({"session_id": SESSION, "action": payload})
        assert response["status"] == "ok", (name, response)
        return response

    try:
        assert (
            action("open", url="https://example.com")["page"]["title"]
            == "Example Domain"
        )
        session = worker._session
        frame = session.cdp.call("Page.getFrameTree", {})["frameTree"]["frame"]["id"]
        session.cdp.call("Page.setDocumentContent", {"frameId": frame, "html": FIXTURE})
        action("type", selector="#namefield", text="Codex")
        action("type", selector="#namefield", text=" Bridge", clear=False, submit=True)
        assert (
            action("inspect", selector="#result")["page"]["text"]
            == "Submitted Codex Bridge"
        )
        action("select", selector="#choice", value="b")
        assert (
            action("wait", text="Choice b")["page"]["title"]
            == "Browser action acceptance"
        )
        action("click", selector="#click")
        assert (
            action("inspect", selector="#result", max_chars=4)["page"]["text"] == "Clic"
        )
        action("click", selector="#fragment")
        assert action("inspect")["page"]["url"].endswith("#next")
        for name, signature in (("screenshot", b"\x89PNG"), ("pdf", b"%PDF-")):
            data = action(name)["artifact"]["data_base64"]
            assert base64.b64decode(data, validate=True).startswith(signature)
        session.cdp.call("Page.setDocumentContent", {
            "frameId": frame,
            "html": '<output id="result">Pending</output><img src="https://127.0.0.1/private-image" onload="result.textContent=\'Unsafe load\'" onerror="result.textContent=\'Private subresource blocked\'">',
        })
        action("wait", text="Private subresource blocked", timeout_ms=10000)
        response = worker.handle(
            {
                "session_id": SESSION,
                "action": {
                    "action": "click",
                    "session_id": SESSION,
                    "selector": "#missing",
                },
            }
        )
        assert response["error"]["code"] == "selector_not_found"
        assert (
            action("navigate", url="https://example.com")["page"]["title"]
            == "Example Domain"
        )
        pids = process_tree(session.cdp._process.pid)
        profile = session.profile
        action("close")
        wait_removed(pids)
        assert not profile.exists()
    finally:
        worker.close()
    print("Typed navigation, forms, inspection, captures and cleanup passed.")


def cancellation() -> None:
    client = BrowserWorkerClient()
    initial = set(Path("/tmp").glob("codex-bridge-browser-*"))
    errors = []
    try:
        result = client.execute(
            {"action": "open", "url": "https://example.com"}, session_id=SESSION
        )
        assert result["status"] == "ok", result
        pids = process_tree(client._process.pid)

        def pending():
            try:
                client.execute(
                    {
                        "action": "wait",
                        "session_id": SESSION,
                        "selector": "#never-present",
                        "timeout_ms": 10000,
                    },
                    session_id=SESSION,
                )
            except BrowserWorkerClientError:
                errors.append("cancelled")

        thread = threading.Thread(target=pending)
        thread.start()
        time.sleep(0.25)
        started = time.monotonic()
        client.close_session(SESSION)
        thread.join(timeout=8)
        assert not thread.is_alive() and errors == ["cancelled"]
        assert time.monotonic() - started < 8
        wait_removed(pids)
        assert set(Path("/tmp").glob("codex-bridge-browser-*")) == initial
    finally:
        client.close()
    print(
        "Cancellation interrupted a pending action and removed its processes/profile."
    )


def resource_limit() -> None:
    import browser_resources

    session = Session.create(SESSION)
    original = browser_resources.MAX_BROWSER_MEMORY_BYTES
    try:
        pids = process_tree(session.cdp._process.pid)
        # Exercise the real watchdog and cleanup without consuming 1.5 GiB.
        browser_resources.MAX_BROWSER_MEMORY_BYTES = 1
        session.cdp._process.wait(timeout=5)
        assert session.cdp._guard.exceeded
    finally:
        browser_resources.MAX_BROWSER_MEMORY_BYTES = original
        session.close()
    wait_removed(pids)
    assert not session.profile.exists()
    print("Aggregate memory watchdog stopped the actual browser tree.")


def protected_navigation() -> None:
    from browser_worker import CdpError, NavigationBlocked

    session = Session.create(SESSION)
    try:
        # Test behind the public action validator: Chromium itself must fail
        # to reach a protected destination through its forced proxy.
        result = session.cdp.call("Page.navigate", {"url": "https://127.0.0.1/"})
        assert result.get("errorText"), result
        try:
            session.public_main_frame_url()
        except (NavigationBlocked, CdpError):
            pass
        else:
            raise AssertionError("protected navigation remained capturable")
    finally:
        session.close()
    print("Chromium's protected navigation was denied behind the action validator.")


def protected_redirect() -> None:
    from browser_worker import CdpError, NavigationBlocked

    session = Session.create(SESSION)
    try:
        cdp = session.cdp
        cdp.call("Network.enable", {})
        cdp.call("Fetch.enable", {"patterns": [{
            "urlPattern": "https://example.com/browser-redirect-test",
            "requestStage": "Request",
        }]})
        # A fixed synthetic HTTP response exercises Chromium's redirect path
        # without asking an external service to redirect towards the LAN.
        cdp.call("Runtime.evaluate", {
            "expression": "location.href='https://example.com/browser-redirect-test'",
        })
        deadline = time.monotonic() + 10
        while True:
            event = cdp._events.popleft() if cdp._events else cdp._next_message(deadline)
            if event.get("method") == "Fetch.requestPaused":
                paused = event["params"]
                break
            assert time.monotonic() < deadline
        cdp.call("Fetch.fulfillRequest", {
            "requestId": paused["requestId"],
            "responseCode": 302,
            "responseHeaders": [{"name": "Location", "value": "https://127.0.0.1/"}],
            "body": "",
        })
        cdp.wait_for_event("Network.loadingFailed", timeout=10, params={
            "requestId": paused["networkId"], "type": "Document",
        })
        try:
            session.public_main_frame_url()
        except (NavigationBlocked, CdpError):
            pass
        else:
            raise AssertionError("protected redirect remained capturable")
    finally:
        session.close()
    print("Chromium followed a synthetic HTTP redirect and denied its private target.")


if __name__ == "__main__":
    actions()
    cancellation()
    resource_limit()
    protected_navigation()
    protected_redirect()
