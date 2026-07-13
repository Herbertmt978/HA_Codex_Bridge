from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from codex_bridge_service import runtime_state
from codex_bridge_service.runtime_state import (
    RuntimeStateCorruptError,
    RuntimeStateError,
    RuntimeStateRecord,
    RuntimeStateStore,
    RuntimeStateVersionError,
)


@pytest.mark.parametrize(
    "payload",
    [
        b'{"schema_version":1,"revision":',
        b'{"schema_version":1,"revision":"invalid"}',
    ],
    ids=["truncated-json", "invalid-v1-shape"],
)
def test_load_rejects_malformed_or_truncated_v1_state(
    tmp_path: Path,
    payload: bytes,
) -> None:
    store = RuntimeStateStore(tmp_path)
    store.path.write_bytes(payload)

    with pytest.raises(RuntimeStateCorruptError):
        store.load()


def test_load_rejects_future_schema_without_replacing_checkpoint(
    tmp_path: Path,
) -> None:
    store = RuntimeStateStore(tmp_path)
    payload = b'{"schema_version":2,"revision":7}'
    store.path.write_bytes(payload)

    with pytest.raises(RuntimeStateVersionError):
        store.load()

    assert store.path.read_bytes() == payload


def test_quarantine_moves_corrupt_checkpoint_without_changing_contents(
    tmp_path: Path,
) -> None:
    store = RuntimeStateStore(tmp_path)
    payload = b'{"schema_version":1,"revision":'
    store.path.write_bytes(payload)

    with pytest.raises(RuntimeStateCorruptError):
        store.load()

    quarantine_path = store.quarantine_corrupt()

    assert quarantine_path is not None
    assert quarantine_path.parent == tmp_path
    assert quarantine_path.name.startswith("runtime-state.corrupt.")
    assert quarantine_path.suffix == ".json"
    assert not store.path.exists()
    assert quarantine_path.read_bytes() == payload
    assert store.quarantine_corrupt() is None


def test_save_revalidates_mutated_model_before_replacing_checkpoint(
    tmp_path: Path,
) -> None:
    store = RuntimeStateStore(tmp_path)
    store.save(RuntimeStateRecord(revision=3))
    checkpoint = store.path.read_bytes()
    invalid_state = RuntimeStateRecord(revision=4)
    invalid_state.revision = -1

    with pytest.raises(RuntimeStateError, match="could not be validated"):
        store.save(invalid_state)

    assert store.path.read_bytes() == checkpoint
    assert store.load().revision == 3


def test_save_rejects_oversized_state_without_replacing_checkpoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = RuntimeStateStore(tmp_path)
    store.save(RuntimeStateRecord(revision=5))
    checkpoint = store.path.read_bytes()
    monkeypatch.setattr(runtime_state, "MAX_RUNTIME_STATE_BYTES", len(checkpoint) + 8)
    oversized = RuntimeStateRecord(revision=int("9" * 100))

    with pytest.raises(RuntimeStateError, match="exceeds its limit"):
        store.save(oversized)

    assert store.path.read_bytes() == checkpoint


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits are unavailable")
def test_save_creates_private_checkpoint_with_owner_only_permissions(
    tmp_path: Path,
) -> None:
    store = RuntimeStateStore(tmp_path)

    store.save(RuntimeStateRecord())

    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
