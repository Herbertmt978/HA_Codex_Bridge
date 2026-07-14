#!/usr/bin/env python3
"""Wait for authenticated, non-fatal Bridge readiness without argv secrets."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import stat
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


MAX_RESPONSE_BYTES = 64 * 1024
ACCEPTABLE_STATES = {"ready", "auth_required", "degraded_catalogue"}


def acceptable_readiness(payload: bytes) -> bool:
    if len(payload) > MAX_RESPONSE_BYTES:
        return False
    try:
        document = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False
    return (
        isinstance(document, dict)
        and isinstance(document.get("readiness"), dict)
        and document["readiness"].get("state") in ACCEPTABLE_STATES
    )


def _read_token(path: Path) -> str:
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or not 32 <= metadata.st_size <= 512
        ):
            raise ValueError("unsafe credential file")
        payload = os.read(descriptor, 513)
        if len(payload) != metadata.st_size or os.read(descriptor, 1):
            raise ValueError("credential file changed while reading")
        token = payload.decode("ascii")
        if token != token.strip() or any(
            not 0x21 <= ord(value) <= 0x7E for value in token
        ):
            raise ValueError("invalid credential")
        return token
    finally:
        os.close(descriptor)


def wait_for_bridge(*, url: str, token_file: Path, timeout_seconds: float) -> bool:
    token = _read_token(token_file)
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            request = Request(
                url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-Codex-Bridge-Api": "1",
                },
            )
            with urlopen(request, timeout=5) as response:
                payload = response.read(MAX_RESPONSE_BYTES + 1)
            if acceptable_readiness(payload):
                return True
        except (HTTPError, URLError, OSError, TimeoutError, ValueError):
            pass
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(1, remaining))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8766/ready")
    parser.add_argument("--token-file", type=Path, default=Path("/data/bridge-token"))
    parser.add_argument("--timeout-seconds", type=float, default=60)
    arguments = parser.parse_args()
    return (
        0
        if wait_for_bridge(
            url=arguments.url,
            token_file=arguments.token_file,
            timeout_seconds=arguments.timeout_seconds,
        )
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
