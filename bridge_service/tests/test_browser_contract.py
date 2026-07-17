from __future__ import annotations

import base64
import struct
import zlib

import pytest

from codex_bridge_service.browser_contract import (
    BrowserContractError,
    MAX_SCREENSHOT_BYTES,
    browser_dynamic_tool_spec,
    parse_browser_action,
    parse_worker_response,
)


def _png() -> bytes:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        checksum = zlib.crc32(kind + payload) & 0xFFFFFFFF
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", checksum)

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)
    scanline = b"\x00\x00\x00\x00\x00"
    return b"\x89PNG\r\n\x1a\n" + b"".join(
        (chunk(b"IHDR", ihdr), chunk(b"IDAT", zlib.compress(scanline)), chunk(b"IEND", b""))
    )


def test_dynamic_tool_namespace_exposes_only_high_level_actions() -> None:
    spec = browser_dynamic_tool_spec()

    assert spec["type"] == "namespace"
    assert spec["name"] == "ha_browser"
    assert {tool["name"] for tool in spec["tools"]} == {
        "close",
        "click",
        "inspect",
        "navigate",
        "open",
        "pdf",
        "screenshot",
        "select",
        "type",
        "wait",
    }
    property_names = {
        property_name.lower()
        for tool in spec["tools"]
        for property_name in tool["inputSchema"]["properties"]
    }
    assert not property_names.intersection(
        {"javascript", "evaluate", "headers", "cookies", "cdp"}
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8123/",
        "http://[::1]/",
        "http://192.168.50.20/",
        "http://10.0.0.1/",
        "http://169.254.169.254/",
        "http://100.64.0.1/",
        "http://224.0.0.1/",
        "http://homeassistant.local/",
        "http://supervisor/",
        "http://hassio/",
        "file:///config/secrets.yaml",
        "data:text/html,hello",
        "https://user:password@example.com/",
    ],
)
def test_navigation_rejects_local_private_or_credential_targets(url: str) -> None:
    with pytest.raises(BrowserContractError, match="navigation target is not allowed"):
        parse_browser_action({"action": "open", "url": url})


def test_navigation_accepts_public_http_and_normalizes_host() -> None:
    action = parse_browser_action(
        {
            "action": "navigate",
            "session_id": "brs_0123456789abcdef",
            "url": "HTTPS://Example.COM:443/docs?q=1#section",
            "wait_until": "domcontentloaded",
            "timeout_ms": 15000,
        }
    )

    assert action.action == "navigate"
    assert action.url == "https://example.com/docs?q=1#section"


@pytest.mark.parametrize(
    "payload",
    [
        {"action": "evaluate", "script": "fetch('/api')"},
        {"action": "open", "url": "https://example.com", "headers": {"X": "y"}},
        {"action": "click", "session_id": "brs_0123456789abcdef", "selector": "a", "button": "right"},
        {"action": "type", "session_id": "brs_0123456789abcdef", "selector": "#x", "text": "x" * 8193},
        {"action": "inspect", "session_id": "brs_0123456789abcdef", "max_chars": 32769},
        {"action": "wait", "session_id": "brs_0123456789abcdef", "timeout_ms": 10001},
        {"action": "close", "session_id": "../data"},
    ],
)
def test_actions_reject_unknown_fields_unbounded_values_and_unknown_types(payload: dict[str, object]) -> None:
    with pytest.raises(BrowserContractError):
        parse_browser_action(payload)


def test_worker_response_accepts_bounded_safe_page_projection() -> None:
    response = parse_worker_response(
        {
            "status": "ok",
            "session_id": "brs_0123456789abcdef",
            "page": {
                "url": "https://example.com/docs",
                "title": "Documentation",
                "text": "Public page text",
            },
        }
    )

    assert response.status == "ok"
    assert response.page is not None
    assert response.page.title == "Documentation"


def test_worker_response_accepts_only_bounded_png_jpeg_or_pdf_artifacts() -> None:
    response = parse_worker_response(
        {
            "status": "ok",
            "session_id": "brs_0123456789abcdef",
            "artifact": {
                "kind": "screenshot",
                "mime_type": "image/png",
                "data_base64": base64.b64encode(_png()).decode("ascii"),
            },
        }
    )

    assert response.artifact is not None
    assert response.artifact.data.startswith(b"\x89PNG")

    for mime_type, content in (
        ("image/svg+xml", b"<svg/>"),
        ("text/html", b"<html>"),
        ("image/png", b"not-a-png"),
        ("application/pdf", b"not-a-pdf"),
    ):
        with pytest.raises(BrowserContractError, match="worker artifact is invalid"):
            parse_worker_response(
                {
                    "status": "ok",
                    "session_id": "brs_0123456789abcdef",
                    "artifact": {
                        "kind": "screenshot",
                        "mime_type": mime_type,
                        "data_base64": base64.b64encode(content).decode("ascii"),
                    },
                }
            )

    oversized_png = b"\x89PNG\r\n\x1a\n" + b"x" * MAX_SCREENSHOT_BYTES
    with pytest.raises(BrowserContractError, match="worker artifact is invalid"):
        parse_worker_response(
            {
                "status": "ok",
                "session_id": "brs_0123456789abcdef",
                "artifact": {
                    "kind": "screenshot",
                    "mime_type": "image/png",
                    "data_base64": base64.b64encode(oversized_png).decode("ascii"),
                },
            }
        )


def test_worker_response_accepts_inert_complete_pdf_artifact() -> None:
    content = b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog >>\nendobj\n%%EOF\n"

    response = parse_worker_response(
        {
            "status": "ok",
            "session_id": "brs_0123456789abcdef",
            "artifact": {
                "kind": "pdf",
                "mime_type": "application/pdf",
                "data_base64": base64.b64encode(content).decode("ascii"),
            },
        }
    )

    assert response.artifact is not None
    assert response.artifact.kind == "pdf"
    assert response.artifact.data == content


@pytest.mark.parametrize(
    "content",
    [
        b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\n",
        b"%PDF-1.7\n1 0 obj\n<< /OpenAction 2 0 R >>\nendobj\n%%EOF\n",
        b"%PDF-1.7\n%%EOF\nprivate trailing bytes",
    ],
)
def test_worker_response_rejects_incomplete_active_or_trailing_pdf(
    content: bytes,
) -> None:
    with pytest.raises(BrowserContractError, match="worker artifact is invalid"):
        parse_worker_response(
            {
                "status": "ok",
                "session_id": "brs_0123456789abcdef",
                "artifact": {
                    "kind": "pdf",
                    "mime_type": "application/pdf",
                    "data_base64": base64.b64encode(content).decode("ascii"),
                },
            }
        )


def test_worker_failure_is_bounded_and_cannot_echo_private_details() -> None:
    response = parse_worker_response(
        {
            "status": "error",
            "session_id": "brs_0123456789abcdef",
            "error": {"code": "navigation_blocked", "retryable": False},
        }
    )

    assert response.error is not None
    assert response.error.code == "navigation_blocked"

    with pytest.raises(BrowserContractError):
        parse_worker_response(
            {
                "status": "error",
                "session_id": "brs_0123456789abcdef",
                "error": {
                    "code": "navigation_blocked",
                    "retryable": False,
                    "detail": "http://supervisor/core/api",
                },
            }
        )
