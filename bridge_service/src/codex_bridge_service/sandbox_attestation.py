from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from pathlib import Path
import re
import stat
from typing import Any


CONTRACT_PATH = Path("/usr/local/share/codex-bridge/sandbox-contract.json")
ATTESTATION_PATH = Path("/run/codex-bridge/sandbox-attestation.json")
MAX_CONTRACT_BYTES = 8 * 1024
MAX_ATTESTATION_BYTES = 1024

_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_SEMVER_PATTERN = re.compile(
    r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\Z",
    re.ASCII,
)
_ARCHITECTURES = frozenset({"amd64", "aarch64"})
_CONTRACT_KEYS = frozenset(
    {
        "schema_version",
        "architecture",
        "codex_version",
        "release_lock_digest",
        "executables",
        "apparmor",
    }
)
_ATTESTATION_KEYS = frozenset(
    {"schema_version", "contract_sha256", "attested"}
)


class _DuplicateKeyError(ValueError):
    pass


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise _DuplicateKeyError
        value[key] = item
    return value


def _read_regular_file(
    path: Path,
    *,
    maximum: int,
    allowed_modes: frozenset[int],
    expected_uid: int | None = None,
    expected_gid: int | None = None,
) -> bytes:
    descriptor = -1
    try:
        flags = (
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
        )
        if os.name == "nt" and path.is_symlink():
            raise OSError
        descriptor = os.open(path, flags)
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_size <= 0
            or metadata.st_size > maximum
        ):
            raise OSError
        if os.name != "nt":
            if stat.S_IMODE(metadata.st_mode) not in allowed_modes:
                raise OSError
            if expected_uid is not None and metadata.st_uid != expected_uid:
                raise OSError
            if expected_gid is not None and metadata.st_gid != expected_gid:
                raise OSError
        remaining = metadata.st_size
        chunks: list[bytes] = []
        while remaining:
            chunk = os.read(descriptor, min(remaining, 4096))
            if not chunk:
                raise OSError
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise OSError
        return b"".join(chunks)
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _load_json(payload: bytes) -> object:
    try:
        return json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, _DuplicateKeyError) as exc:
        raise ValueError from exc


def _valid_contract(value: object) -> bool:
    if not isinstance(value, dict) or set(value) != _CONTRACT_KEYS:
        return False
    if (
        type(value.get("schema_version")) is not int
        or value["schema_version"] != 2
        or value.get("architecture") not in _ARCHITECTURES
        or not isinstance(value.get("codex_version"), str)
        or _SEMVER_PATTERN.fullmatch(value["codex_version"]) is None
        or not isinstance(value.get("release_lock_digest"), str)
        or _SHA256_PATTERN.fullmatch(value["release_lock_digest"]) is None
    ):
        return False
    executables = value.get("executables")
    executable_paths = {
        "codex": "/usr/local/bin/codex",
        "bwrap": "/usr/local/bin/bwrap",
        "bwrap_launcher": "/opt/codex/bin/bwrap",
    }
    if not isinstance(executables, dict) or set(executables) != set(
        executable_paths
    ):
        return False
    for name, expected_path in executable_paths.items():
        executable = executables.get(name)
        if (
            not isinstance(executable, dict)
            or set(executable) != {"path", "sha256"}
            or executable.get("path") != expected_path
            or not isinstance(executable.get("sha256"), str)
            or _SHA256_PATTERN.fullmatch(executable["sha256"]) is None
        ):
            return False
    return value.get("apparmor") == {
        "parent_profile_suffix": "codex_bridge",
        "bwrap_profile_suffix": "//codex_bwrap",
    }


def _valid_attestation(value: object, *, contract_digest: str) -> bool:
    return (
        isinstance(value, dict)
        and set(value) == _ATTESTATION_KEYS
        and type(value.get("schema_version")) is int
        and value.get("schema_version") == 1
        and value.get("contract_sha256") == contract_digest
        and value.get("attested") is True
    )


def read_sandbox_contract(
    path: Path = CONTRACT_PATH,
    *,
    require_root: bool = False,
) -> tuple[dict[str, object], bytes] | None:
    """Read and validate the immutable, non-secret sandbox build contract."""

    try:
        payload = _read_regular_file(
            path,
            maximum=MAX_CONTRACT_BYTES,
            allowed_modes=frozenset({0o400, 0o440, 0o444, 0o600}),
            expected_uid=0 if require_root else None,
        )
        value = _load_json(payload)
        if not _valid_contract(value):
            return None
        assert isinstance(value, dict)
        return value, payload
    except (OSError, ValueError, TypeError):
        return None


def verify_sandbox_attestation(
    *,
    contract_path: Path = CONTRACT_PATH,
    attestation_path: Path = ATTESTATION_PATH,
    expected_uid: int | None = None,
    expected_gid: int | None = None,
    require_root_contract: bool = False,
    expected_contract_version: int | None = None,
    expected_architecture: str | None = None,
    expected_codex_version: str | None = None,
    expected_release_lock_digest: str | None = None,
) -> bool:
    """Verify one root-created, boot-local sandbox attestation without leaking it."""

    try:
        loaded_contract = read_sandbox_contract(
            contract_path,
            require_root=require_root_contract,
        )
        if loaded_contract is None:
            return False
        contract, contract_bytes = loaded_contract
        expected_values = (
            ("schema_version", expected_contract_version),
            ("architecture", expected_architecture),
            ("codex_version", expected_codex_version),
            ("release_lock_digest", expected_release_lock_digest),
        )
        if any(
            expected is not None and contract.get(name) != expected
            for name, expected in expected_values
        ):
            return False
        attestation_bytes = _read_regular_file(
            attestation_path,
            maximum=MAX_ATTESTATION_BYTES,
            allowed_modes=frozenset({0o400, 0o440, 0o600}),
            expected_uid=expected_uid,
            expected_gid=expected_gid,
        )
        attestation = _load_json(attestation_bytes)
        return _valid_attestation(
            attestation,
            contract_digest=hashlib.sha256(contract_bytes).hexdigest(),
        )
    except (OSError, ValueError, TypeError):
        return False


def sandbox_attestation_ready(
    build_info: Mapping[str, object],
    *,
    contract_path: Path = CONTRACT_PATH,
    attestation_path: Path = ATTESTATION_PATH,
) -> bool:
    """Verify the production App attestation against immutable build metadata."""

    version = build_info.get("sandbox_contract_version")
    architecture = build_info.get("architecture")
    codex_version = build_info.get("codex_version")
    release_digest = build_info.get("release_lock_digest")
    if (
        type(version) is not int
        or version != 2
        or architecture not in _ARCHITECTURES
        or not isinstance(codex_version, str)
        or not isinstance(release_digest, str)
    ):
        return False
    return verify_sandbox_attestation(
        contract_path=contract_path,
        attestation_path=attestation_path,
        expected_uid=0,
        expected_gid=os.getgid() if hasattr(os, "getgid") else None,
        require_root_contract=True,
        expected_contract_version=version,
        expected_architecture=architecture,
        expected_codex_version=codex_version,
        expected_release_lock_digest=release_digest,
    )
