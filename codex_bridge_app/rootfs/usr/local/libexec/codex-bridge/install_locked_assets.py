#!/usr/bin/env python3
"""Install only raw Task 19-verified Codex/Bubblewrap ELF binaries."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
from typing import Any


TARGETS = {"amd64": ("x86_64", 62), "aarch64": ("aarch64", 183)}
COMPONENTS = ("codex", "bwrap")
MAX_BINARY_BYTES = 512 * 1024 * 1024


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _locked_string(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"invalid locked {name}")
    return value


def _locked_size(value: Any) -> int:
    if not isinstance(value, int) or not 0 < value <= MAX_BINARY_BYTES:
        raise ValueError("invalid locked binary size")
    return value


def _install(
    *,
    source: Path,
    asset: dict[str, object],
    component: str,
    arch: str,
    destination: Path,
) -> None:
    target, machine = TARGETS[arch]
    expected_archive = f"{component}-{target}-unknown-linux-musl.tar.gz"
    if _locked_string(asset.get("name"), "archive name") != expected_archive:
        raise ValueError("locked archive name does not match the target")
    expected_size = _locked_size(asset.get("decompressed_size"))
    expected_digest = _locked_string(asset.get("decompressed_sha256"), "binary digest")
    if len(expected_digest) != 64 or any(
        character not in "0123456789abcdef" for character in expected_digest
    ):
        raise ValueError("invalid locked binary digest")

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(source, flags)
    temporary = destination / f".{component}.{os.getpid()}.partial"
    output_descriptor = -1
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_size != expected_size
        ):
            raise ValueError("staged binary is not the locked regular file")
        output_descriptor = os.open(
            temporary,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        digest = hashlib.sha256()
        header = bytearray()
        copied = 0
        while chunk := os.read(descriptor, 1024 * 1024):
            copied += len(chunk)
            if copied > expected_size:
                raise ValueError("staged binary exceeded its locked size")
            if len(header) < 20:
                header.extend(chunk[: 20 - len(header)])
            digest.update(chunk)
            view = memoryview(chunk)
            while view:
                written = os.write(output_descriptor, view)
                view = view[written:]
        if copied != expected_size or digest.hexdigest() != expected_digest:
            raise ValueError("staged binary digest or size mismatch")
        if (
            len(header) < 20
            or header[:4] != b"\x7fELF"
            or header[4:7] != b"\x02\x01\x01"
            or int.from_bytes(header[18:20], "little") != machine
        ):
            raise ValueError("staged binary is not the expected ELF64 target")
        os.fchmod(output_descriptor, 0o755)
        os.fsync(output_descriptor)
        os.close(output_descriptor)
        output_descriptor = -1
        os.replace(temporary, destination / component)
    finally:
        os.close(descriptor)
        if output_descriptor >= 0:
            os.close(output_descriptor)
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--arch", choices=sorted(TARGETS), required=True)
    parser.add_argument("--lock", type=Path, required=True)
    parser.add_argument("--lock-digest", required=True)
    parser.add_argument("--assets-dir", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    arguments = parser.parse_args()
    if _sha256(arguments.lock) != arguments.lock_digest:
        raise ValueError("release lock digest mismatch")
    lock = json.loads(arguments.lock.read_text(encoding="utf-8"))
    assets = lock["assets"][arguments.arch]
    arguments.destination.mkdir(parents=True, exist_ok=True)
    for component in COMPONENTS:
        asset = assets[component]
        if not isinstance(asset, dict):
            raise ValueError("invalid release-lock component")
        _install(
            source=arguments.assets_dir / component,
            asset=asset,
            component=component,
            arch=arguments.arch,
            destination=arguments.destination,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
