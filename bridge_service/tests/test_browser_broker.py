from __future__ import annotations

import base64
import struct
from threading import Event, Thread
import zlib

import pytest

from codex_bridge_service.browser_broker import (
    BrowserBroker,
    BrowserInvocationContext,
)


def _png() -> bytes:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        checksum = zlib.crc32(kind + payload) & 0xFFFFFFFF
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", checksum)

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + b"".join(
        (
            chunk(b"IHDR", ihdr),
            chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00\x00")),
            chunk(b"IEND", b""),
        )
    )


class FakeWorker:
    def __init__(self) -> None:
        self.healthy = True
        self.calls: list[tuple[object, str]] = []
        self.closed: list[str] = []
        self.next_response: object | None = None

    def ready(self) -> bool:
        return self.healthy

    def execute(self, action: object, *, session_id: str) -> object:
        self.calls.append((action, session_id))
        if self.next_response is not None:
            response, self.next_response = self.next_response, None
            return response
        return {
            "status": "ok",
            "session_id": session_id,
            "page": {
                "url": "https://example.com/",
                "title": "Example",
                "text": "Example page",
            },
        }

    def close_session(self, session_id: str) -> None:
        self.closed.append(session_id)


def context(*, run_id: str = "run_1", generation: int = 3, turn_id: str = "turn_1") -> BrowserInvocationContext:
    return BrowserInvocationContext(
        run_id=run_id,
        thread_id="thr_local",
        codex_thread_id="thr_codex",
        turn_id=turn_id,
        generation=generation,
    )


def open_session(broker: BrowserBroker, owner: BrowserInvocationContext | None = None) -> str:
    result = broker.invoke(owner or context(), "open", {"url": "https://example.com"})
    assert result["success"] is True
    return broker.session_ids()[0]


def test_broker_is_not_ready_and_never_calls_an_unhealthy_worker() -> None:
    worker = FakeWorker()
    worker.healthy = False
    broker = BrowserBroker(worker)

    assert broker.ready is False
    result = broker.invoke(context(), "open", {"url": "https://example.com"})

    assert result == {
        "success": False,
        "contentItems": [{"type": "inputText", "text": "Browser worker unavailable."}],
    }
    assert worker.calls == []


def test_open_creates_one_owned_ephemeral_session_and_returns_bounded_text() -> None:
    worker = FakeWorker()
    broker = BrowserBroker(worker)

    session_id = open_session(broker)

    assert session_id.startswith("brs_")
    assert len(worker.calls) == 1
    result = broker.invoke(
        context(),
        "inspect",
        {"session_id": session_id, "max_chars": 100},
    )
    assert result["success"] is True
    assert result["contentItems"] == [
        {"type": "inputText", "text": "Example\n\nExample page"}
    ]


@pytest.mark.parametrize(
    "other",
    [
        context(run_id="run_other"),
        context(generation=4),
        context(turn_id="turn_other"),
    ],
)
def test_session_cannot_cross_run_generation_or_turn(other: BrowserInvocationContext) -> None:
    worker = FakeWorker()
    broker = BrowserBroker(worker)
    session_id = open_session(broker)

    result = broker.invoke(other, "inspect", {"session_id": session_id})

    assert result["success"] is False
    assert result["contentItems"][0]["text"] == "Browser session unavailable."
    assert len(worker.calls) == 1


def test_expired_session_is_destroyed_before_worker_execution() -> None:
    now = [10.0]
    worker = FakeWorker()
    broker = BrowserBroker(worker, session_ttl_seconds=30.0, clock=lambda: now[0])
    session_id = open_session(broker)
    now[0] = 41.0

    result = broker.invoke(context(), "inspect", {"session_id": session_id})

    assert result["success"] is False
    assert worker.closed == [session_id]
    assert len(worker.calls) == 1
    assert broker.session_ids() == ()


def test_private_redirect_or_mismatched_worker_session_fails_closed() -> None:
    worker = FakeWorker()
    broker = BrowserBroker(worker)
    worker.next_response = {
        "status": "ok",
        "session_id": "brs_0123456789abcdef",
        "page": {"url": "http://supervisor/", "title": "No", "text": "secret"},
    }

    result = broker.invoke(context(), "open", {"url": "https://example.com"})

    assert result["success"] is False
    assert broker.session_ids() == ()
    assert worker.closed


def test_screenshot_is_published_privately_and_returned_inline_to_codex() -> None:
    worker = FakeWorker()
    publications: list[tuple[str, str, bytes]] = []

    def publish(_owner: BrowserInvocationContext, kind: str, mime_type: str, data: bytes) -> str:
        publications.append((kind, mime_type, data))
        return "art_browser_1"

    broker = BrowserBroker(worker, artifact_sink=publish)
    session_id = open_session(broker)
    image = _png()
    worker.next_response = {
        "status": "ok",
        "session_id": session_id,
        "artifact": {
            "kind": "screenshot",
            "mime_type": "image/png",
            "data_base64": base64.b64encode(image).decode("ascii"),
        },
    }

    result = broker.invoke(context(), "screenshot", {"session_id": session_id})

    assert result["success"] is True
    assert publications == [("screenshot", "image/png", image)]
    assert result["contentItems"][0] == {
        "type": "inputText",
        "text": "Browser screenshot saved as private artifact art_browser_1.",
    }
    assert result["contentItems"][1]["type"] == "inputImage"
    assert result["contentItems"][1]["imageUrl"].startswith("data:image/png;base64,")


def test_pdf_is_published_without_exposing_a_file_path_or_remote_url() -> None:
    worker = FakeWorker()
    broker = BrowserBroker(
        worker,
        artifact_sink=lambda owner, kind, mime, data: "art_browser_pdf",
    )
    session_id = open_session(broker)
    worker.next_response = {
        "status": "ok",
        "session_id": session_id,
        "artifact": {
            "kind": "pdf",
            "mime_type": "application/pdf",
                "data_base64": base64.b64encode(
                    b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog >>\nendobj\n%%EOF\n"
                ).decode("ascii"),
        },
    }

    result = broker.invoke(context(), "pdf", {"session_id": session_id})

    assert result == {
        "success": True,
        "contentItems": [
            {
                "type": "inputText",
                "text": "Browser PDF saved as private artifact art_browser_pdf.",
            }
        ],
    }


def test_close_and_broker_shutdown_destroy_sessions_idempotently() -> None:
    worker = FakeWorker()
    broker = BrowserBroker(worker)
    first = open_session(broker)

    result = broker.invoke(context(), "close", {"session_id": first})
    broker.close()
    broker.close()

    assert result["success"] is True
    assert worker.closed == [first]
    assert broker.session_ids() == ()


def test_close_owner_destroys_only_exact_turn_owned_sessions() -> None:
    worker = FakeWorker()
    broker = BrowserBroker(worker, max_sessions=2)
    first_owner = context()
    second_owner = context(run_id="run_2", turn_id="turn_2")
    assert broker.invoke(first_owner, "open", {"url": "https://example.com"})[
        "success"
    ] is True
    assert broker.invoke(second_owner, "open", {"url": "https://example.com"})[
        "success"
    ] is True
    first = worker.calls[0][1]
    second = worker.calls[1][1]

    broker.close_owner(context(run_id="run_1", turn_id="turn_other"))
    assert set(broker.session_ids()) == {first, second}

    broker.close_owner(first_owner)

    assert broker.session_ids() == (second,)
    assert worker.closed == [first]


def test_artifact_sink_can_only_be_bound_before_browser_use() -> None:
    worker = FakeWorker()
    broker = BrowserBroker(worker)
    published: list[str] = []

    broker.set_artifact_sink(lambda *_args: published.append("saved") or "art_browser")
    open_session(broker)

    with pytest.raises(RuntimeError, match="cannot be changed"):
        broker.set_artifact_sink(lambda *_args: "art_replaced")
    assert published == []


@pytest.mark.parametrize(
    "code",
    ["browser_unavailable", "session_closed", "session_expired", "worker_failed"],
)
def test_terminal_worker_error_destroys_the_session(code: str) -> None:
    worker = FakeWorker()
    broker = BrowserBroker(worker)
    session_id = open_session(broker)
    worker.next_response = {
        "status": "error",
        "session_id": session_id,
        "error": {"code": code, "retryable": False},
    }

    result = broker.invoke(context(), "inspect", {"session_id": session_id})

    assert result["success"] is False
    assert broker.session_ids() == ()
    assert worker.closed == [session_id]


def test_failed_open_does_not_orphan_the_single_session_slot() -> None:
    worker = FakeWorker()
    broker = BrowserBroker(worker)
    worker.next_response = {
        "status": "error",
        "session_id": "brs_0123456789abcdef",
        "error": {"code": "navigation_failed", "retryable": True},
    }

    first = broker.invoke(context(), "open", {"url": "https://example.com"})
    second = broker.invoke(context(), "open", {"url": "https://example.com"})

    assert first["success"] is False
    assert second["success"] is True
    assert len(broker.session_ids()) == 1


def test_unknown_tool_or_injected_action_field_never_reaches_worker() -> None:
    worker = FakeWorker()
    broker = BrowserBroker(worker)

    first = broker.invoke(context(), "evaluate", {"script": "1+1"})
    second = broker.invoke(
        context(),
        "open",
        {"action": "navigate", "url": "https://example.com"},
    )

    assert first["success"] is False
    assert second["success"] is False
    assert worker.calls == []


def test_close_owner_revokes_a_blocked_worker_without_waiting_for_the_global_lock() -> None:
    worker = FakeWorker()
    broker = BrowserBroker(worker)
    session_id = open_session(broker)
    entered = Event()
    release = Event()
    original_execute = worker.execute
    original_close = worker.close_session

    def blocking_execute(action: object, *, session_id: str) -> object:
        if getattr(action, "action", None) == "inspect":
            entered.set()
            assert release.wait(2)
        return original_execute(action, session_id=session_id)

    def cancelling_close(session_id: str) -> None:
        original_close(session_id)
        release.set()

    worker.execute = blocking_execute  # type: ignore[method-assign]
    worker.close_session = cancelling_close  # type: ignore[method-assign]
    result: list[dict[str, object]] = []
    invocation = Thread(
        target=lambda: result.append(
            broker.invoke(context(), "inspect", {"session_id": session_id})
        ),
        daemon=True,
    )
    invocation.start()
    assert entered.wait(1)

    broker.close_owner(context())

    invocation.join(timeout=2)
    assert not invocation.is_alive()
    assert worker.closed == [session_id]
    assert broker.session_ids() == ()
    assert result == [
        {
            "success": False,
            "contentItems": [
                {"type": "inputText", "text": "Browser session unavailable."}
            ],
        }
    ]


def test_close_owner_is_not_blocked_by_artifact_persistence() -> None:
    worker = FakeWorker()
    persistence_started = Event()
    release_persistence = Event()

    def blocking_sink(*_args: object) -> str:
        persistence_started.set()
        assert release_persistence.wait(2)
        return "art_browser_capture"

    broker = BrowserBroker(worker, artifact_sink=blocking_sink)
    session_id = open_session(broker)
    worker.next_response = {
        "status": "ok",
        "session_id": session_id,
        "artifact": {
            "kind": "screenshot",
            "mime_type": "image/png",
            "data_base64": base64.b64encode(_png()).decode("ascii"),
        },
    }
    result: list[dict[str, object]] = []
    invocation = Thread(
        target=lambda: result.append(
            broker.invoke(context(), "screenshot", {"session_id": session_id})
        ),
        daemon=True,
    )
    invocation.start()
    assert persistence_started.wait(1)

    concurrent = broker.invoke(
        context(),
        "inspect",
        {"session_id": session_id},
    )
    assert concurrent == {
        "success": False,
        "contentItems": [
            {"type": "inputText", "text": "Browser session unavailable."}
        ],
    }
    assert len(worker.calls) == 2

    broker.close_owner(context())
    release_persistence.set()

    invocation.join(timeout=2)
    assert not invocation.is_alive()
    assert worker.closed == [session_id]
    assert broker.session_ids() == ()
    assert result[0]["success"] is False


def test_navigation_policy_block_is_terminal_for_the_broker_session() -> None:
    worker = FakeWorker()
    broker = BrowserBroker(worker)
    session_id = open_session(broker)
    worker.next_response = {
        "status": "error",
        "session_id": session_id,
        "error": {"code": "navigation_blocked", "retryable": False},
    }

    result = broker.invoke(
        context(),
        "navigate",
        {"session_id": session_id, "url": "https://example.org"},
    )

    assert result == {
        "success": False,
        "contentItems": [
            {"type": "inputText", "text": "Browser navigation blocked by policy."}
        ],
    }
    assert broker.session_ids() == ()
    assert worker.closed == [session_id]
