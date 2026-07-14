#!/usr/bin/env python3
"""Publish the private Bridge endpoint without exposing its token to Bashio."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import secrets
import stat
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import (
    HTTPRedirectHandler,
    ProxyHandler,
    Request,
    build_opener,
)
from uuid import UUID


SUPERVISOR_DISCOVERY_URL = "http://supervisor/discovery"
TOKEN_PATH = Path("/data/bridge-token")
DISCOVERY_UUID_PATH = Path("/data/bridge-discovery-uuid")
MAX_RESPONSE_BYTES = 64 * 1024
HOST_PATTERN = re.compile(r"[a-z0-9](?:[a-z0-9_-]{0,61}[a-z0-9])?")
UUID_PATTERN = re.compile(r"[0-9a-f]{32}")
DIRECTORY_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_NOFOLLOW", 0)
)
FILE_FLAGS = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)


class DiscoveryError(RuntimeError):
    """Discovery could not be published safely."""


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(
        self,
        request: Request,
        file_pointer: Any,
        code: int,
        message: str,
        headers: Any,
        new_url: str,
    ) -> None:
        return None


def _validate_host(host: str) -> str:
    if HOST_PATTERN.fullmatch(host) is None:
        raise DiscoveryError("invalid App hostname")
    return host


def _validate_secret(value: str, *, minimum: int, maximum: int) -> str:
    if not minimum <= len(value) <= maximum:
        raise DiscoveryError("invalid credential")
    try:
        payload = value.encode("ascii")
    except UnicodeEncodeError as error:
        raise DiscoveryError("invalid credential") from error
    if any(not 0x21 <= byte <= 0x7E for byte in payload):
        raise DiscoveryError("invalid credential")
    return value


def discovery_payload(*, host: str, token: str) -> dict[str, object]:
    return {
        "service": "codex_bridge",
        "config": {
            "host": _validate_host(host),
            "port": 8766,
            "token": _validate_secret(token, minimum=32, maximum=512),
            "api": {"minimum": 1, "maximum": 1},
        },
    }


def _runtime_owner() -> tuple[int, int]:
    import grp
    import pwd

    return pwd.getpwnam("codexbridge").pw_uid, grp.getgrnam("codexbridge").gr_gid


def _read_token(path: Path, *, uid: int, gid: int) -> str:
    descriptor = os.open(path, FILE_FLAGS)
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_uid != uid
            or metadata.st_gid != gid
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or not 32 <= metadata.st_size <= 512
        ):
            raise DiscoveryError("unsafe credential file")
        payload = os.read(descriptor, 513)
        if len(payload) != metadata.st_size or os.read(descriptor, 1):
            raise DiscoveryError("credential file changed while reading")
    finally:
        os.close(descriptor)
    try:
        token = payload.decode("ascii")
    except UnicodeDecodeError as error:
        raise DiscoveryError("invalid credential") from error
    return _validate_secret(token, minimum=32, maximum=512)


def _parse_supervisor_response(payload: bytes) -> str:
    if len(payload) > MAX_RESPONSE_BYTES:
        raise DiscoveryError("Supervisor response is too large")
    try:
        document = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise DiscoveryError("Supervisor response is invalid") from error
    if (
        not isinstance(document, dict)
        or document.get("result") != "ok"
        or not isinstance(document.get("data"), dict)
        or not isinstance(document["data"].get("uuid"), str)
    ):
        raise DiscoveryError("Supervisor rejected discovery")
    identity = document["data"]["uuid"]
    if UUID_PATTERN.fullmatch(identity) is None:
        raise DiscoveryError("Supervisor returned an invalid identity")
    if UUID(hex=identity).int == 0:
        raise DiscoveryError("Supervisor returned an invalid identity")
    return identity


def _post_discovery(
    *, host: str, token: str, supervisor_token: str, opener: Any | None = None
) -> str:
    body = json.dumps(
        discovery_payload(host=host, token=token),
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("ascii")
    request = Request(
        SUPERVISOR_DISCOVERY_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {_validate_secret(supervisor_token, minimum=16, maximum=8192)}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    resolved_opener = opener or build_opener(ProxyHandler({}), _RejectRedirects())
    try:
        with resolved_opener.open(request, timeout=10) as response:
            if response.status != 200:
                raise DiscoveryError("Supervisor rejected discovery")
            payload = response.read(MAX_RESPONSE_BYTES + 1)
    except (HTTPError, URLError, OSError, TimeoutError) as error:
        raise DiscoveryError("Supervisor discovery request failed") from error
    return _parse_supervisor_response(payload)


def _atomic_write_identity(
    path: Path, identity: str, *, uid: int = 0, gid: int = 0
) -> None:
    parent_descriptor = os.open(path.parent, DIRECTORY_FLAGS)
    temporary_name = f".{path.name}.tmp-{secrets.token_hex(8)}"
    descriptor = -1
    try:
        descriptor = os.open(
            temporary_name,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=parent_descriptor,
        )
        payload = f"{identity}\n".encode("ascii")
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise DiscoveryError("discovery identity write failed")
            view = view[written:]
        os.fchown(descriptor, uid, gid)
        os.fchmod(descriptor, 0o600)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(
            temporary_name,
            path.name,
            src_dir_fd=parent_descriptor,
            dst_dir_fd=parent_descriptor,
        )
        os.fsync(parent_descriptor)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.unlink(temporary_name, dir_fd=parent_descriptor)
        except FileNotFoundError:
            pass
        os.close(parent_descriptor)


def publish_discovery(*, host: str, supervisor_token: str) -> None:
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        raise DiscoveryError("discovery publisher must run as root")
    uid, gid = _runtime_owner()
    token = _read_token(TOKEN_PATH, uid=uid, gid=gid)
    identity = _post_discovery(
        host=host,
        token=token,
        supervisor_token=supervisor_token,
    )
    _atomic_write_identity(DISCOVERY_UUID_PATH, identity)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", required=True)
    arguments = parser.parse_args()
    supervisor_token = os.environ.pop("SUPERVISOR_TOKEN", "")
    try:
        publish_discovery(host=arguments.host, supervisor_token=supervisor_token)
    except Exception:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
