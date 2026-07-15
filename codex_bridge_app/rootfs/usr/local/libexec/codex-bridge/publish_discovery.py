#!/usr/bin/env python3
"""Publish the private Bridge endpoint without exposing its token to Bashio."""

from __future__ import annotations

import argparse
from ipaddress import ip_address, ip_network
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
from uuid import UUID, uuid4


SUPERVISOR_DISCOVERY_URL = "http://supervisor/discovery"
TOKEN_PATH = Path("/data/bridge-token")
DISCOVERY_UUID_PATH = Path("/data/bridge-discovery-uuid")
MAX_RESPONSE_BYTES = 64 * 1024
UUID_PATTERN = re.compile(r"[0-9a-f]{32}")
PUBLICATION_ID_PATTERN = re.compile(r"[0-9a-f]{32}")
PRIVATE_APP_NETWORKS = (
    ip_network("10.0.0.0/8"),
    ip_network("172.16.0.0/12"),
    ip_network("192.168.0.0/16"),
    ip_network("fc00::/7"),
)
DIRECTORY_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_NOFOLLOW", 0)
)
FILE_FLAGS = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)


class DiscoveryError(RuntimeError):
    """Discovery could not be published safely."""

    def __init__(self, message: str, *, category: str = "configuration") -> None:
        super().__init__(message)
        self.category = category


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


def _validate_private_app_ip(host: str) -> str:
    try:
        address = ip_address(host)
    except ValueError as error:
        raise DiscoveryError("invalid App IP address") from error
    if not any(address in network for network in PRIVATE_APP_NETWORKS):
        raise DiscoveryError("App IP address is not private")
    return str(address)


def _validate_secret(value: str, *, minimum: int, maximum: int) -> str:
    if not minimum <= len(value) <= maximum:
        raise DiscoveryError("invalid credential", category="credential")
    try:
        payload = value.encode("ascii")
    except UnicodeEncodeError as error:
        raise DiscoveryError("invalid credential", category="credential") from error
    if any(not 0x21 <= byte <= 0x7E for byte in payload):
        raise DiscoveryError("invalid credential", category="credential")
    return value


def _validate_publication_id(publication_id: str) -> str:
    if PUBLICATION_ID_PATTERN.fullmatch(publication_id) is None:
        raise DiscoveryError("invalid publication marker")
    return publication_id


def discovery_payload(
    *, host: str, token: str, publication_id: str
) -> dict[str, object]:
    return {
        "service": "codex_bridge",
        "config": {
            "host": _validate_private_app_ip(host),
            "port": 8766,
            "token": _validate_secret(token, minimum=32, maximum=512),
            "api": {"minimum": 1, "maximum": 1},
            "publication_id": _validate_publication_id(publication_id),
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
            raise DiscoveryError("unsafe credential file", category="credential")
        payload = os.read(descriptor, 513)
        if len(payload) != metadata.st_size or os.read(descriptor, 1):
            raise DiscoveryError("credential file changed while reading", category="credential")
    finally:
        os.close(descriptor)
    try:
        token = payload.decode("ascii")
    except UnicodeDecodeError as error:
        raise DiscoveryError("invalid credential", category="credential") from error
    return _validate_secret(token, minimum=32, maximum=512)


def _parse_supervisor_response(payload: bytes) -> str:
    if len(payload) > MAX_RESPONSE_BYTES:
        raise DiscoveryError("Supervisor response is too large", category="Supervisor")
    try:
        document = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise DiscoveryError("Supervisor response is invalid", category="Supervisor") from error
    if (
        not isinstance(document, dict)
        or document.get("result") != "ok"
        or not isinstance(document.get("data"), dict)
        or not isinstance(document["data"].get("uuid"), str)
    ):
        raise DiscoveryError("Supervisor rejected discovery", category="Supervisor")
    identity = document["data"]["uuid"]
    if UUID_PATTERN.fullmatch(identity) is None:
        raise DiscoveryError("Supervisor returned an invalid identity", category="Supervisor")
    if UUID(hex=identity).int == 0:
        raise DiscoveryError("Supervisor returned an invalid identity", category="Supervisor")
    return identity


def _post_discovery(
    *,
    host: str,
    token: str,
    publication_id: str,
    supervisor_token: str,
    opener: Any | None = None,
) -> str:
    body = json.dumps(
        discovery_payload(host=host, token=token, publication_id=publication_id),
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
                raise DiscoveryError("Supervisor rejected discovery", category="Supervisor")
            payload = response.read(MAX_RESPONSE_BYTES + 1)
    except HTTPError as error:
        raise DiscoveryError("Supervisor rejected discovery", category="Supervisor") from error
    except (URLError, OSError, TimeoutError) as error:
        raise DiscoveryError("Supervisor discovery request failed", category="transport") from error
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
                raise DiscoveryError("discovery identity write failed", category="storage")
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
    publication_id = uuid4().hex
    identity = _post_discovery(
        host=host,
        token=token,
        publication_id=publication_id,
        supervisor_token=supervisor_token,
    )
    try:
        _atomic_write_identity(DISCOVERY_UUID_PATH, identity)
    except OSError as error:
        raise DiscoveryError("discovery identity write failed", category="storage") from error


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", required=True)
    arguments = parser.parse_args()
    supervisor_token = os.environ.pop("SUPERVISOR_TOKEN", "")
    try:
        publish_discovery(host=arguments.host, supervisor_token=supervisor_token)
    except DiscoveryError as error:
        return {
            "configuration": 2,
            "credential": 3,
            "Supervisor": 4,
            "transport": 5,
            "storage": 6,
        }.get(error.category, 1)
    except Exception:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
