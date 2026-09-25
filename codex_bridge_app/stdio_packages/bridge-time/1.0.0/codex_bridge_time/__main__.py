"""MCP stdio server for the current time and IANA timezone conversion.

Only JSON-RPC messages go to stdout. The process has no network or file tools.
Its timezone database is supplied by the read-only Python runtime image.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import sys
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import __version__


PROTOCOL_VERSION = "2025-06-18"
MAX_MESSAGE_BYTES = 64 * 1024
TOOLS = (
    {
        "name": "get_current_time",
        "description": "Get the current wall-clock time in an IANA timezone.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "timezone": {"type": "string", "description": "IANA timezone, such as Europe/London."}
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "convert_time",
        "description": "Convert a time with a UTC offset to an IANA timezone.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "time": {
                    "type": "string",
                    "description": "ISO 8601 date and time with a UTC offset.",
                },
                "timezone": {"type": "string", "description": "Target IANA timezone."},
            },
            "required": ["time", "timezone"],
            "additionalProperties": False,
        },
    },
)


def _zone(value: Any) -> ZoneInfo | timezone:
    if not isinstance(value, str) or not value or len(value) > 128:
        raise ValueError("timezone must be an IANA timezone name")
    # ZoneInfo itself confines lookup to the timezone database. Restrict the
    # syntax here as well so errors cannot echo arbitrary caller input.
    if value.startswith("/") or ".." in value or any(
        not (part.isalnum() or part in "_+-") for part in value.replace("/", "")
    ):
        raise ValueError("timezone must be an IANA timezone name")
    if value == "UTC":
        return timezone.utc
    try:
        return ZoneInfo(value)
    except ZoneInfoNotFoundError as exc:
        raise ValueError("timezone is unavailable in this App image") from exc


def _tool_result(name: str, arguments: Any) -> dict[str, Any]:
    if not isinstance(arguments, dict):
        raise ValueError("arguments must be an object")
    if name == "get_current_time":
        if set(arguments) - {"timezone"}:
            raise ValueError("unsupported argument")
        zone_name = arguments.get("timezone", "UTC")
        zone = _zone(zone_name)
        result = datetime.now(zone)
    elif name == "convert_time":
        if set(arguments) != {"time", "timezone"}:
            raise ValueError("time and timezone are required")
        value = arguments["time"]
        if not isinstance(value, str) or len(value) > 64:
            raise ValueError("time must be a bounded ISO 8601 string")
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("time must be ISO 8601") from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("time must include a UTC offset")
        zone_name = arguments["timezone"]
        zone = _zone(zone_name)
        result = parsed.astimezone(zone)
    else:
        raise ValueError("unknown tool")
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(
                    {"time": result.isoformat(), "timezone": zone_name},
                    separators=(",", ":"),
                ),
            }
        ],
        "isError": False,
    }


def _reply(request: Any) -> dict[str, Any] | None:
    if not isinstance(request, dict) or request.get("jsonrpc") != "2.0":
        return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid request"}}
    identifier = request.get("id")
    if "id" not in request:
        # Client notifications must not receive a reply.
        return None
    if isinstance(identifier, (dict, list, bool)) or identifier is None:
        return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid request"}}
    method = request.get("method")
    params = request.get("params", {})
    if not isinstance(method, str) or not isinstance(params, dict):
        return {"jsonrpc": "2.0", "id": identifier, "error": {"code": -32600, "message": "Invalid request"}}
    if method == "initialize":
        result: dict[str, Any] = {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "codex-bridge-time", "version": __version__},
        }
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        name = params.get("name")
        if not isinstance(name, str):
            return {"jsonrpc": "2.0", "id": identifier, "error": {"code": -32602, "message": "Invalid parameters"}}
        try:
            result = _tool_result(name, params.get("arguments", {}))
        except ValueError as exc:
            result = {"content": [{"type": "text", "text": str(exc)}], "isError": True}
    else:
        return {"jsonrpc": "2.0", "id": identifier, "error": {"code": -32601, "message": "Method not found"}}
    return {"jsonrpc": "2.0", "id": identifier, "result": result}


def main() -> int:
    source = sys.stdin.buffer
    sink = sys.stdout.buffer
    while line := source.readline(MAX_MESSAGE_BYTES + 1):
        if len(line) > MAX_MESSAGE_BYTES or not line.endswith(b"\n"):
            # Framing is no longer reliable; the parent reports the failure.
            return 2
        try:
            response = _reply(json.loads(line))
        except (UnicodeDecodeError, json.JSONDecodeError):
            response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
        if response is not None:
            sink.write(json.dumps(response, separators=(",", ":"), ensure_ascii=True).encode() + b"\n")
            sink.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
