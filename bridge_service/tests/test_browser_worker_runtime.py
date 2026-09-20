"""Exercise the fixed worker's protocol, navigation and process supervision."""

from collections import deque
import importlib
import io
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace

import pytest

LIBEXEC = (
    Path(__file__).resolve().parents[2]
    / "codex_bridge_app/rootfs/usr/local/libexec/codex-bridge"
)


@pytest.fixture
def worker(monkeypatch):
    monkeypatch.syspath_prepend(str(LIBEXEC))
    return importlib.import_module("browser_worker")


def test_navigation_waits_for_its_own_loader(worker):
    events = [
        {
            "method": "Page.lifecycleEvent",
            "sessionId": "page",
            "params": {"loaderId": "old", "name": "load"},
        },
        {
            "method": "Page.lifecycleEvent",
            "sessionId": "other-page",
            "params": {"loaderId": "new", "name": "load"},
        },
        {
            "method": "Page.lifecycleEvent",
            "sessionId": "page",
            "params": {"loaderId": "new", "name": "load"},
        },
    ]
    pipe = worker.CdpPipe.__new__(worker.CdpPipe)
    pipe._events = deque(events[:1], maxlen=256)
    pipe._session_id = "page"
    remaining = iter(events[1:])
    consumed = []

    def next_message(_deadline):
        event = next(remaining)
        consumed.append(event)
        return event

    pipe._next_message = next_message
    pipe.wait_for_event(
        "Page.lifecycleEvent", params={"loaderId": "new", "name": "load"}, timeout=1
    )
    assert len(consumed) == 2


def test_buffered_event_flood_cannot_extend_the_deadline(worker):
    pipe = worker.CdpPipe.__new__(worker.CdpPipe)
    pipe._buffer = b'{"method":"Page.lifecycleEvent"}\0'
    with pytest.raises(worker.WorkerError, match="timed out"):
        pipe._next_message(time.monotonic() - 1)


def test_navigation_failure_does_not_capture_an_old_page(worker):
    calls = []
    cdp = SimpleNamespace(
        call=lambda *args, **kwargs: {"errorText": "net::ERR_TUNNEL_CONNECTION_FAILED"},
        wait_for_event=lambda *args, **kwargs: calls.append(args),
    )
    session = worker.Session("brs_test", Path("/tmp/test"), None, cdp, time.monotonic())
    with pytest.raises(worker.NavigationBlocked):
        session.navigate(
            worker.parse_browser_action(
                {"action": "open", "url": "https://example.com"}
            )
        )
    assert calls == []


def test_failed_page_setup_closes_the_browser_and_proxy(worker, monkeypatch, tmp_path):
    events = []

    class Proxy:
        def __init__(self, path):
            self.socket_path = path

        def start(self):
            events.append("proxy-start")

        def close(self):
            events.append("proxy-close")

    class Pipe:
        def __init__(self, **kwargs):
            pass

        def call(self, *args, **kwargs):
            raise worker.CdpError("failed")

        def close(self):
            events.append("browser-close")

    monkeypatch.setattr(worker, "UnixPolicyProxy", Proxy)
    monkeypatch.setattr(worker, "CdpPipe", Pipe)
    monkeypatch.setattr(worker.tempfile, "mkdtemp", lambda **kwargs: str(tmp_path))
    monkeypatch.setattr(
        worker, "_remove_profile", lambda path: events.append("profile-removed")
    )
    with pytest.raises(worker.CdpError):
        worker.Session.create("brs_test")
    assert events == ["proxy-start", "browser-close", "proxy-close", "profile-removed"]


def test_oversized_unterminated_request_is_read_with_a_bound(worker, monkeypatch):
    sizes = []

    class Input(io.BytesIO):
        def readline(self, size=-1):
            sizes.append(size)
            assert size > 0
            return super().readline(size)

    incoming = Input(b"x" * (worker.MAX_LINE_BYTES * 2))
    outgoing = io.BytesIO()
    monkeypatch.setattr(worker, "browser_worker_attestation_ready", lambda: True)
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(buffer=incoming))
    monkeypatch.setattr(sys, "stdout", SimpleNamespace(buffer=outgoing))
    assert worker.main() == 0
    assert sizes == [worker.MAX_LINE_BYTES + 2]
    assert json.loads(outgoing.getvalue())["error"]["code"] == "worker_failed"


def test_resource_count_includes_children_owned_by_other_threads(worker, tmp_path):
    resources = importlib.import_module("browser_resources")
    for pid, threads, rss in [
        (10, {10: "", 11: "20"}, 100),
        (20, {20: "30"}, 200),
        (30, {30: ""}, 300),
    ]:
        directory = tmp_path / str(pid)
        directory.mkdir()
        parent = {10: 1, 20: 10, 30: 20}[pid]
        (directory / "status").write_text(
            f"PPid:\t{parent}\nVmRSS:\t{rss} kB\nVmSwap:\t5 kB\n"
        )
        for tid, children in threads.items():
            task = directory / "task" / str(tid)
            task.mkdir(parents=True)
            (task / "children").write_text(children)
    assert resources.process_tree_usage(10, tmp_path) == (3, 615 * 1024)


def test_resource_counter_stops_at_process_limit(worker, tmp_path):
    resources = importlib.import_module("browser_resources")
    for pid in range(1, 200):
        directory = tmp_path / str(pid)
        directory.mkdir()
        (directory / "status").write_text("PPid: 1\nVmRSS: 1 kB\n")
    count, _ = resources.process_tree_usage(1, tmp_path)
    assert count == resources.MAX_BROWSER_PROCESSES + 1


@pytest.mark.parametrize("operation_fails", [False, True])
def test_navigation_releases_its_old_context_without_masking_the_result(
    worker, operation_fails
):
    calls = []

    def call(method, params, **kwargs):
        calls.append(method)
        if method == "Runtime.evaluate":
            return {"result": {"objectId": "old-page"}}
        if method == "Runtime.callFunctionOn":
            if operation_fails:
                raise worker.CdpError("action failed")
            return {"result": {"value": True}}
        raise worker.CdpError("context destroyed by navigation")

    if operation_fails:
        with pytest.raises(worker.CdpError, match="action failed"):
            worker._function_value(SimpleNamespace(call=call), worker._CLICK, ["a"])
    else:
        assert (
            worker._function_value(SimpleNamespace(call=call), worker._CLICK, ["a"])
            is True
        )
    assert calls[-1] == "Runtime.releaseObject"


def test_failed_fixed_operation_keeps_the_session_available_for_retry(worker):
    browser = worker.BrowserWorker()
    session = SimpleNamespace(
        session_id="brs_0123456789abcdef", actions=0, check_limits=lambda: None
    )
    browser._session = session

    def fail(*args):
        raise worker.CdpError("context changed")

    browser._handle_action = fail
    response = browser.handle(
        {
            "session_id": session.session_id,
            "action": {"action": "inspect", "session_id": session.session_id},
        }
    )
    assert response["error"] == {"code": "navigation_failed", "retryable": True}
    assert browser._session is session
