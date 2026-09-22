#!/usr/local/bin/python
"""Bind runtime diagnostics to the immutable contract and actual machine ABI."""

import os

from codex_bridge_service.sandbox_attestation import read_sandbox_contract


def runtime_architecture() -> str:
    architecture = {"x86_64": "amd64", "aarch64": "aarch64"}.get(os.uname().machine)
    contract = read_sandbox_contract(require_root=True)
    if architecture is None or contract is None or contract[0]["architecture"] != architecture:
        raise RuntimeError("App architecture does not match its sandbox contract")
    return architecture


if __name__ == "__main__":
    print(runtime_architecture())
