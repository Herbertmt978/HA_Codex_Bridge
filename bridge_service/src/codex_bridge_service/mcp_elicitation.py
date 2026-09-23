"""Bounded, text-only projection of MCP questions for attended chats."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from math import isfinite
import re
from typing import Callable
from urllib.parse import urlsplit

from .mcp_manager import McpProtocolError, _valid_name, _validate_oauth_authorization_url
from .models import InteractionDisplayRecord, McpFormFieldRecord

_SENSITIVE_FIELD = re.compile(
    r"(?:pass(?:word|phrase)?|secret|token|api[_ -]?key|credential|private[_ -]?key|"
    r"authorization|bearer|client[_ -]?secret|"
    r"(?<![A-Za-z0-9])(?:pin(?:[_ -]?code)?|otp|totp|mfa|2fa|"
    r"one[_ -]?time[_ -]?code|verification[_ -]?code|access[_ -]?code|"
    r"auth[_ -]?code|security[_ -]?code)(?![A-Za-z0-9]))",
    re.IGNORECASE,
)
_FIELD_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,127}\Z")


@dataclass(frozen=True, slots=True)
class McpElicitationSpec:
    kind: str
    server_name: str
    display: InteractionDisplayRecord
    authorization_url: str | None = None


def parse_elicitation(
    params: object,
    *,
    url_validator: Callable[[object], str] = _validate_oauth_authorization_url,
) -> McpElicitationSpec | None:
    """Decline unsupported or ambiguous schemas instead of guessing an answer."""

    if not isinstance(params, dict) or not _valid_name(params.get("serverName")):
        return None
    server_name = params["serverName"]
    message = _text(params.get("message"), 512)
    if not message:
        return None
    if params.get("mode") == "url":
        if not _text(params.get("elicitationId"), 256):
            return None
        try:
            url = url_validator(params.get("url"))
        except (McpProtocolError, ValueError):
            return None
        host = urlsplit(url).hostname
        if host is None:
            return None
        return McpElicitationSpec(
            "mcp_url", server_name,
            InteractionDisplayRecord(
                title="MCP authorisation request",
                summary=re.sub(
                    r"https?://\S+", "[link shown below]", message,
                    flags=re.IGNORECASE,
                ),
                mcp_server=server_name, mcp_url_host=host,
            ),
            url,
        )
    if params.get("mode") != "form":
        return None
    schema = params.get("requestedSchema")
    if (
        not isinstance(schema, dict) or schema.get("type") != "object"
        or not set(schema).issubset({"type", "properties", "required", "$schema"})
    ):
        return None
    properties = schema.get("properties")
    required = schema.get("required") or []
    if (
        not isinstance(properties, dict) or not 1 <= len(properties) <= 16
        or not isinstance(required, list) or len(required) > 16
        or any(not isinstance(name, str) for name in required)
        or len(set(required)) != len(required)
        or not set(required).issubset(properties)
    ):
        return None
    fields: list[McpFormFieldRecord] = []
    for name, raw in properties.items():
        if not isinstance(name, str) or not _FIELD_NAME.fullmatch(name):
            return None
        if not isinstance(raw, dict):
            return None
        label = _text(raw.get("title"), 160) or name
        description = _text(raw.get("description"), 512)
        if _SENSITIVE_FIELD.search(f"{name} {label} {description or ''}"):
            return None
        kind = raw.get("type")
        options: list[str] = []
        option_labels: list[str] = []
        if kind == "string":
            if not set(raw).issubset({
                "type", "title", "description", "default", "format",
                "minLength", "maxLength", "enum", "enumNames", "oneOf",
            }):
                return None
            if "enum" in raw or "oneOf" in raw:
                if any(raw.get(key) is not None for key in ("format", "minLength", "maxLength")):
                    return None
                parsed_options = _options(raw)
                if parsed_options is None:
                    return None
                options, option_labels = parsed_options
                kind = "select"
        elif kind == "array":
            if not set(raw).issubset({
                "type", "title", "description", "default", "items", "minItems", "maxItems",
            }):
                return None
            items = raw.get("items")
            if not isinstance(items, dict) or not set(items).issubset({"type", "enum", "anyOf"}):
                return None
            if "enum" in items and items.get("type") != "string":
                return None
            parsed_options = _options(items)
            if parsed_options is None:
                return None
            options, option_labels = parsed_options
            kind = "multi_select"
        elif kind in {"number", "integer"}:
            if not set(raw).issubset({
                "type", "title", "description", "default", "minimum", "maximum",
            }):
                return None
        elif kind == "boolean":
            if not set(raw).issubset({"type", "title", "description", "default"}):
                return None
        else:
            return None
        if options and (
            any(not isinstance(option, str) or not option or len(option) > 160 for option in options)
            or len(set(options)) != len(options)
        ):
            return None
        if kind in {"select", "multi_select"} and not options:
            return None
        format_value = raw.get("format") if kind == "string" else None
        if format_value not in {None, "email", "uri", "date", "date-time"}:
            return None
        limits: dict[str, int | float | None] = {}
        for source, target, ceiling in (
            ("minLength", "min_length", 4096), ("maxLength", "max_length", 4096),
            ("minItems", "min_items", 32), ("maxItems", "max_items", 32),
        ):
            value = raw.get(source)
            if value is not None:
                if type(value) is not int or value < 0 or value > ceiling:
                    return None
                limits[target] = value
        for source in ("minimum", "maximum"):
            value = raw.get(source)
            if value is not None:
                if type(value) not in (int, float) or not isfinite(value):
                    return None
                limits[source] = value
        if any(
            limits.get(low) is not None and limits.get(high) is not None
            and limits[low] > limits[high]
            for low, high in (("min_length", "max_length"), ("min_items", "max_items"), ("minimum", "maximum"))
        ):
            return None
        try:
            fields.append(McpFormFieldRecord(
                name=name, label=label, description=description, kind=kind,
                required=name in required, options=options, option_labels=option_labels,
                format=format_value,
                **limits,
            ))
        except ValueError:
            return None
    return McpElicitationSpec(
        "mcp_form", server_name,
        InteractionDisplayRecord(
            title="MCP server question", summary=message,
            mcp_server=server_name, mcp_fields=fields,
        ),
    )


def _options(raw: dict[str, object]) -> tuple[list[str], list[str]] | None:
    if "enum" in raw and ("oneOf" in raw or "anyOf" in raw):
        return None
    if "enumNames" in raw and "enum" not in raw:
        return None
    offered = raw.get("enum", raw.get("oneOf", raw.get("anyOf")))
    if not isinstance(offered, list) or not 1 <= len(offered) <= 32:
        return None
    if "enum" in raw:
        names = raw.get("enumNames")
        if names is not None and (
            not isinstance(names, list) or len(names) != len(offered)
        ):
            return None
        values = offered
        labels = offered if names is None else names
    else:
        if any(
            not isinstance(item, dict) or set(item) != {"const", "title"}
            for item in offered
        ):
            return None
        values = [item["const"] for item in offered]
        labels = [item["title"] for item in offered]
    if (
        any(not isinstance(value, str) or not value or len(value) > 160 for value in values)
        or any(not isinstance(label, str) or not label or len(label) > 160 for label in labels)
        or len(set(values)) != len(values)
    ):
        return None
    return values, labels


def validate_form_content(
    content: object,
    fields: list[McpFormFieldRecord],
) -> dict[str, object]:
    if not isinstance(content, dict) or len(content) > 16:
        raise ValueError("MCP form content is invalid")
    by_name = {field.name: field for field in fields}
    if not set(content).issubset(by_name) or any(
        field.required and field.name not in content for field in fields
    ):
        raise ValueError("MCP form fields do not match the request")
    for name, value in content.items():
        field = by_name[name]
        if field.kind in {"string", "select"}:
            if not isinstance(value, str) or len(value) > 4096:
                raise ValueError("MCP form value is invalid")
            if field.min_length is not None and len(value) < field.min_length:
                raise ValueError("MCP form value is too short")
            if field.max_length is not None and len(value) > field.max_length:
                raise ValueError("MCP form value is too long")
            if field.kind == "select" and value not in field.options:
                raise ValueError("MCP form option is invalid")
            if field.kind == "string" and not _valid_format(value, field.format):
                raise ValueError("MCP form format is invalid")
        elif field.kind == "multi_select":
            if (
                not isinstance(value, list) or len(value) > 32
                or any(not isinstance(item, str) or item not in field.options for item in value)
                or len(set(value)) != len(value)
                or field.min_items is not None and len(value) < field.min_items
                or field.max_items is not None and len(value) > field.max_items
            ):
                raise ValueError("MCP form selection is invalid")
        elif field.kind == "boolean":
            if type(value) is not bool:
                raise ValueError("MCP form boolean is invalid")
        elif field.kind in {"number", "integer"}:
            if type(value) not in ((int,) if field.kind == "integer" else (int, float)):
                raise ValueError("MCP form number is invalid")
            if not isfinite(value):
                raise ValueError("MCP form number is invalid")
            if field.minimum is not None and value < field.minimum:
                raise ValueError("MCP form number is too small")
            if field.maximum is not None and value > field.maximum:
                raise ValueError("MCP form number is too large")
    return content


def _valid_format(value: str, format_name: str | None) -> bool:
    if format_name == "email":
        return len(value) <= 320 and re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value) is not None
    if format_name == "uri":
        parsed = urlsplit(value)
        return parsed.scheme in {"https", "http"} and bool(parsed.netloc)
    if format_name in {"date", "date-time"}:
        pattern = (
            r"\d{4}-\d{2}-\d{2}"
            if format_name == "date" else
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-](?:0\d|1\d|2[0-3]):[0-5]\d)"
        )
        if re.fullmatch(pattern, value) is None:
            return False
        try:
            (date.fromisoformat if format_name == "date" else datetime.fromisoformat)(value)
        except ValueError:
            return False
    return True


def _text(value: object, limit: int) -> str | None:
    if not isinstance(value, str) or not value or len(value) > limit:
        return None
    if any(ord(char) < 32 and char not in "\n\t" for char in value):
        return None
    return value.strip() or None
