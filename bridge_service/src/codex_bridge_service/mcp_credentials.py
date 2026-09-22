"""Bounded static MCP authentication and streaming reflection protection."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import re

from .mcp_local_policy import LocalMcpError

_NAME = re.compile(r"[A-Za-z][A-Za-z0-9-]{0,63}\Z", re.ASCII)
_RESERVED = {
    "authorization", "proxy-authorization", "host", "cookie", "set-cookie",
    "connection", "keep-alive", "transfer-encoding", "content-length",
    "content-type", "content-encoding", "accept", "accept-encoding", "te",
    "trailer", "upgrade", "expect", "origin", "referer", "user-agent",
    "forwarded", "via", "range", "date", "dnt", "last-event-id",
    "x-real-ip", "x-original-url", "x-rewrite-url", "x-http-method-override",
    "x-method-override", "x-http-method", "destination", "max-forwards",
}


@dataclass(frozen=True)
class McpCredential:
    mode: str = "none"
    headers: tuple[tuple[str, str], ...] = field(default=(), repr=False)

    @property
    def configured(self) -> bool:
        return bool(self.headers)

    def stored(self) -> dict[str, object]:
        return {"mode": self.mode, "headers": [list(pair) for pair in self.headers]}


def _secret(value: object) -> str:
    # Short values would redact common protocol text as well as reflected secrets.
    if (not isinstance(value, str) or not 8 <= len(value) <= 4096
            or value != value.strip() or any(not 32 <= ord(c) <= 126 for c in value)):
        raise LocalMcpError()
    return value


def parse_credential(value: object, *, stored: bool = False) -> McpCredential:
    if value is None and not stored:
        return McpCredential()
    if not isinstance(value, dict):
        raise LocalMcpError()
    mode = value.get("mode")
    if not isinstance(mode, str) or mode not in {"none", "bearer", "headers"}:
        raise LocalMcpError()
    if stored:
        if set(value) != {"mode", "headers"} or not isinstance(value["headers"], list):
            raise LocalMcpError()
        pairs = value["headers"]
        if not pairs:
            return McpCredential(mode)
        if any(not isinstance(pair, list) or len(pair) != 2 for pair in pairs):
            raise LocalMcpError()
        if mode == "bearer":
            if len(pairs) != 1 or pairs[0][0] != "Authorization" or not isinstance(pairs[0][1], str) or not pairs[0][1].startswith("Bearer "):
                raise LocalMcpError()
            return parse_credential({"mode": mode, "token": pairs[0][1][7:]})
        return parse_credential({"mode": mode, "headers": [dict(zip(("name", "value"), pair, strict=True)) for pair in pairs]})
    if mode == "none":
        if set(value) != {"mode"}:
            raise LocalMcpError()
        return McpCredential()
    if mode == "bearer":
        if set(value) != {"mode", "token"}:
            raise LocalMcpError()
        token = _secret(value["token"])
        if not re.fullmatch(r"[A-Za-z0-9._~+/-]+=*", token, re.ASCII):
            raise LocalMcpError()
        return McpCredential(mode, (("Authorization", "Bearer " + token),))
    if set(value) != {"mode", "headers"} or not isinstance(value["headers"], list) or not 1 <= len(value["headers"]) <= 8:
        raise LocalMcpError()
    result = []
    seen = set()
    for pair in value["headers"]:
        if not isinstance(pair, dict) or set(pair) != {"name", "value"}:
            raise LocalMcpError()
        name = pair["name"]
        if (not isinstance(name, str) or not _NAME.fullmatch(name)
                or name.lower() in _RESERVED or name.lower() in seen
                or name.lower().startswith(("proxy-", "sec-", "mcp-", "x-forwarded-", "x-codex-", "content-", "if-"))):
            raise LocalMcpError()
        seen.add(name.lower())
        result.append((name, _secret(pair["value"])))
    if sum(len(k) + len(v) for k, v in result) > 8192:
        raise LocalMcpError()
    return McpCredential(mode, tuple(result))


class CredentialRedactor:
    """Hold only a possible secret prefix between chunks; never buffer an SSE event."""

    def __init__(self, credential: McpCredential) -> None:
        values = [value for _, value in credential.headers]
        if credential.mode == "bearer" and values:
            values.append(values[0][7:])
        patterns = {item.encode() for value in values for item in (value, json.dumps(value)[1:-1])}
        self._patterns = sorted(patterns, key=len, reverse=True)
        self._matcher = re.compile(b"|".join(re.escape(p) for p in self._patterns)) if patterns else None
        self._pending = b""

    def feed(self, chunk: bytes, *, final: bool = False) -> bytes:
        if not self._patterns:
            return chunk
        data = self._pending + chunk
        result = []
        end = 0
        for match in self._matcher.finditer(data):
            result.extend((data[end:match.start()], b"[redacted]"))
            end = match.end()
        tail = data[end:]
        keep = 0
        if not final:
            for pattern in self._patterns:
                for length in range(min(len(tail), len(pattern) - 1), keep, -1):
                    if pattern.startswith(tail[-length:]):
                        keep = length
                        break
        self._pending = tail[-keep:] if keep else b""
        result.append(tail[:-keep] if keep else tail)
        return b"".join(result)
