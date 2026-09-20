"""Private pairing, explicit consent and per-run authority for HAOS access."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from ipaddress import ip_address, ip_network
import json
import os
from pathlib import Path
import stat
from threading import RLock
import time
from uuid import uuid4

import httpx
from pydantic import Field, SecretStr, field_validator

from .host_access_contract import (
    HOST_ACCESS_WARNINGS, HOST_INSTALLATION_URL,
    HostCommand, HostContract, HostIdentity, HostInvocation, HostResult,
)


class HostAccessError(RuntimeError):
    """Fixed public explanation; never retain a private transport exception."""


class HostPairing(HostContract):
    host: str
    port: int = Field(default=8767, ge=8767, le=8767)
    token: SecretStr = Field(min_length=32, max_length=512)
    companion_id: str = Field(pattern=r"^[a-f0-9]{32}$")

    @field_validator("host")
    @classmethod
    def private_address(cls, value: str) -> str:
        address = ip_address(value)
        if not any(address in ip_network(network) for network in (
            "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7",
        )):
            raise ValueError("Host Access must use a private Supervisor address")
        return str(address)

    @field_validator("token")
    @classmethod
    def valid_token(cls, value: SecretStr) -> SecretStr:
        if any(not 0x21 <= ord(char) <= 0x7e for char in value.get_secret_value()):
            raise ValueError("Invalid Host Access credential")
        return value

    @property
    def url(self) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"http://{host}:{self.port}"


@dataclass(frozen=True)
class HostLease:
    run_id: str
    grant_id: str
    scope_revision: str
    worker_session: str
    pairing: HostPairing


class HostAccessManager:
    def __init__(self, root: Path, *, transport=None) -> None:
        self._path = root / "host-access.json"
        self._lock = RLock()
        self._pairing: HostPairing | None = None
        self._grant: dict[str, str] | None = None
        self._leases: dict[str, HostLease] = {}
        self._transport = transport
        self._cancellations = ThreadPoolExecutor(max_workers=2, thread_name_prefix="host-stop")
        self._closed = False
        try:
            descriptor = os.open(self._path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        except OSError:
            # An unreadable optional grant must not prevent normal chats from
            # starting, and must never restore host authority.
            return
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or metadata.st_size > 8192:
                raise ValueError("Invalid private state")
            if os.name == "posix" and (
                metadata.st_uid != os.geteuid() or stat.S_IMODE(metadata.st_mode) != 0o600
            ):
                raise ValueError("Invalid private state permissions")
            payload = json.loads(os.read(descriptor, 8193))
            if payload.get("pairing") is not None:
                self._pairing = HostPairing.model_validate(payload["pairing"])
            grant = payload.get("grant")
            if grant is not None:
                if set(grant) != {"grant_id", "scope_revision"} or not all(
                    isinstance(grant[key], str) and len(grant[key]) == length
                    and all(char in "0123456789abcdef" for char in grant[key])
                    for key, length in (("grant_id", 32), ("scope_revision", 64))
                ):
                    raise ValueError("Invalid grant")
                self._grant = grant
        except Exception:
            # Corrupt or unexpectedly permissive consent never restores access.
            self._pairing = None
            self._grant = None
        finally:
            os.close(descriptor)

    def _save_locked(self) -> None:
        pairing = None
        if self._pairing is not None:
            pairing = self._pairing.model_dump(mode="json")
            pairing["token"] = self._pairing.token.get_secret_value()
        payload = json.dumps({"pairing": pairing, "grant": self._grant}).encode()
        temporary = self._path.with_name(f".host-access-{uuid4().hex}.tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self._path)
            if os.name == "posix":
                parent = os.open(self._path.parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(parent)
                finally:
                    os.close(parent)
        finally:
            temporary.unlink(missing_ok=True)

    def _request(self, pairing: HostPairing, method: str, path: str, *, timeout=10, **kwargs):
        try:
            with httpx.Client(
                transport=self._transport, trust_env=False, follow_redirects=False,
                timeout=timeout, headers={
                    "Authorization": f"Bearer {pairing.token.get_secret_value()}",
                    "X-Codex-Host-Api": "1",
                },
            ) as client:
                with client.stream(method, pairing.url + path, **kwargs) as response:
                    if response.status_code != 200:
                        raise ValueError("Host Access rejected the request")
                    body = bytearray()
                    for chunk in response.iter_bytes():
                        body.extend(chunk)
                        if len(body) > 2 * 1024 * 1024:
                            raise ValueError("Host Access response is too large")
                return json.loads(body)
        except Exception:
            raise HostAccessError("Host Access is unavailable or rejected the request.") from None

    def _worker_status(self, pairing: HostPairing) -> tuple[HostIdentity, str]:
        payload = self._request(pairing, "GET", "/status")
        try:
            identity = HostIdentity.model_validate(payload["identity"])
            session = payload["session_id"]
            if (
                payload["ready"] is not True or identity.companion_id != pairing.companion_id
                or not isinstance(session, str) or len(session) != 32
                or any(char not in "0123456789abcdef" for char in session)
            ):
                raise ValueError("Invalid worker")
            return identity, session
        except Exception:
            raise HostAccessError("The Host Access environment could not be verified.") from None

    def pair(self, pairing: HostPairing) -> None:
        self._worker_status(pairing)
        with self._lock:
            if pairing != self._pairing:
                self._revoke_locked()
                self._pairing = pairing
                self._save_locked()

    def status(self) -> dict[str, object]:
        setup = {
            "installation_url": HOST_INSTALLATION_URL,
            "warnings": [
                {"title": title, "description": description}
                for title, description in HOST_ACCESS_WARNINGS
            ],
        }
        with self._lock:
            pairing = self._pairing
        if pairing is None or self._closed:
            return {**setup, "state": "not_paired", "enabled": False}
        try:
            identity, _ = self._worker_status(pairing)
        except HostAccessError:
            with self._lock:
                if pairing == self._pairing:
                    if self._grant is not None or self._leases:
                        self._revoke_locked()
            return {**setup, "state": "unavailable", "enabled": False}
        with self._lock:
            if pairing != self._pairing:
                return {**setup, "state": "unavailable", "enabled": False}
            if self._grant and self._grant["scope_revision"] != identity.scope_revision:
                self._revoke_locked()
            return {
                **setup, "state": "ready", "enabled": self._grant is not None,
                "grant_id": self._grant["grant_id"] if self._grant else None,
                "disclosure": identity.disclosure(),
            }

    def enable(self, scope_revision: str, acknowledged: bool) -> dict[str, object]:
        if acknowledged is not True:
            raise HostAccessError("Read and acknowledge the host access warning first.")
        with self._lock:
            pairing = self._pairing
        if pairing is None:
            raise HostAccessError("Install and start the optional Host Access App first.")
        identity, _ = self._worker_status(pairing)
        with self._lock:
            if self._closed or pairing != self._pairing or identity.scope_revision != scope_revision:
                raise HostAccessError("The host access warning changed. Read it again.")
            self._revoke_locked()
            self._grant = {"grant_id": uuid4().hex, "scope_revision": scope_revision}
            try:
                self._save_locked()
            except Exception:
                self._grant = None
                raise HostAccessError("Host access consent could not be saved.") from None
        return self.status()

    def validate_selection(self, grant_id: str | None) -> None:
        status = self.status()
        if not status.get("enabled") or status.get("grant_id") != grant_id:
            raise HostAccessError("Enable host access in Settings, then select it again for this task.")

    def authorise(self, run_id: str, grant_id: str | None) -> HostLease:
        self.validate_selection(grant_id)
        with self._lock:
            pairing = self._pairing
        if pairing is None:
            raise HostAccessError("Host Access is not paired.")
        identity, session = self._worker_status(pairing)
        with self._lock:
            if (
                self._closed or pairing != self._pairing or not self._grant
                or self._grant["grant_id"] != grant_id
                or self._grant["scope_revision"] != identity.scope_revision
            ):
                raise HostAccessError("Host access has been revoked or changed.")
            lease = HostLease(run_id, grant_id, identity.scope_revision, session, pairing)
            self._leases[run_id] = lease
            return lease

    def active(self, lease: HostLease) -> bool:
        with self._lock:
            return not self._closed and self._leases.get(lease.run_id) is lease and (
                self._grant is not None and self._grant["grant_id"] == lease.grant_id
            )

    def invoke(self, lease: HostLease, arguments: dict) -> dict[str, object]:
        operation = HostCommand.model_validate(arguments)
        with self._lock:
            if not self.active(lease):
                raise HostAccessError("Host access has been revoked.")
        payload = HostInvocation(
            run_id=lease.run_id, request_id=uuid4().hex,
            scope_revision=lease.scope_revision, worker_session=lease.worker_session,
            expires_at=time.time() + 15, operation=operation,
        )
        result = self._request(
            lease.pairing, "POST", "/execute", json=payload.model_dump(mode="json"),
            timeout=operation.timeout_seconds + 15,
        )
        return HostResult.model_validate(result).model_dump()

    def _cancel(self, lease: HostLease) -> None:
        try:
            self._request(
                lease.pairing, "DELETE", f"/runs/{lease.run_id}",
                headers={
                    "Authorization": f"Bearer {lease.pairing.token.get_secret_value()}",
                    "X-Codex-Host-Api": "1", "X-Codex-Host-Session": lease.worker_session,
                },
            )
        except HostAccessError:
            pass

    def close_run(self, run_id: str) -> None:
        with self._lock:
            lease = self._leases.pop(run_id, None)
            if lease is not None:
                self._cancellations.submit(self._cancel, lease)

    def _revoke_locked(self) -> None:
        self._grant = None
        for run_id in tuple(self._leases):
            self.close_run(run_id)
        try:
            self._save_locked()
        except OSError:
            # A full disk can prevent atomic replacement. Remove the old grant
            # so a subsequent Bridge restart cannot silently restore authority.
            # Pairing can be recovered by Supervisor discovery.
            try:
                self._path.unlink(missing_ok=True)
                if os.name == "posix":
                    parent = os.open(self._path.parent, os.O_RDONLY | os.O_DIRECTORY)
                    try:
                        os.fsync(parent)
                    finally:
                        os.close(parent)
            except OSError:
                raise HostAccessError(
                    "Host access is blocked in this Bridge, but revocation could "
                    "not be saved. Stop the Host Access App before restarting Bridge."
                ) from None

    def revoke(self) -> dict[str, object]:
        with self._lock:
            self._revoke_locked()
        return {"enabled": False}

    def close(self) -> None:
        with self._lock:
            self._closed = True
            for run_id in tuple(self._leases):
                self.close_run(run_id)
        self._cancellations.shutdown(wait=True)
