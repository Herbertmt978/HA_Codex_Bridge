from __future__ import annotations

import io
import stat
import struct
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

import pytest

from codex_bridge_service.resource_limits import (
    GIB,
    MIB,
    QuotaManager,
    QuotaPool,
    QuotaExceededError,
    ReservationConflictError,
    ResourceLimits,
    StreamingByteCounter,
    copy_archive_entry,
    inspect_archive,
    open_inspected_archive,
)


def test_resource_limit_defaults_match_the_home_assistant_host_budget() -> None:
    limits = ResourceLimits()

    assert limits.max_active_turns == 1
    assert limits.max_queued_prompts == 8
    assert limits.run_total_timeout_seconds == 4 * 60 * 60
    assert limits.run_idle_timeout_seconds == 10 * 60
    assert limits.cancel_grace_seconds == 15
    assert limits.max_upload_file_bytes == 100 * MIB
    assert limits.max_workspace_bytes == 10 * GIB
    assert limits.max_private_bytes == 2 * GIB
    assert limits.max_archive_entries == 20_000
    assert limits.max_archive_expanded_bytes == 2 * GIB
    assert limits.max_archive_expansion_ratio == 100
    assert limits.max_archive_metadata_bytes == 16 * MIB
    assert limits.max_events_per_thread == 25_000
    assert limits.max_event_log_bytes == 50 * MIB
    assert limits.service_log_file_bytes == 10 * MIB
    assert limits.service_log_backups == 10

    with pytest.raises(FrozenInstanceError):
        limits.max_queued_prompts = 99  # type: ignore[misc]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("max_active_turns", 0),
        ("max_queued_prompts", -1),
        ("max_upload_file_bytes", 0),
        ("max_archive_expansion_ratio", 0),
        ("minimum_free_bytes", -1),
    ],
)
def test_resource_limits_reject_invalid_values(field: str, value: int) -> None:
    with pytest.raises(ValueError):
        ResourceLimits(**{field: value})


def test_streaming_counter_rejects_before_crossing_its_limit() -> None:
    counter = StreamingByteCounter(limit_bytes=5, resource="upload_file")

    assert counter.consume(3) == 3
    with pytest.raises(QuotaExceededError) as error:
        counter.consume(3)

    assert error.value.code == "quota_exceeded"
    assert error.value.resource == "upload_file"
    assert counter.consumed_bytes == 3


def test_atomic_reservations_allow_only_one_racing_writer() -> None:
    manager = QuotaManager(
        pools={
            "private": QuotaPool(
                limit_bytes=100,
                usage_bytes=lambda: 0,
                free_bytes=lambda: 1_000,
            )
        },
        minimum_free_bytes=0,
    )

    def reserve() -> object:
        try:
            return manager.reserve("private", amount_bytes=70)
        except QuotaExceededError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _index: reserve(), range(2)))

    reservations = [result for result in results if not isinstance(result, Exception)]
    failures = [result for result in results if isinstance(result, QuotaExceededError)]
    assert len(reservations) == 1
    assert len(failures) == 1
    reservations[0].release()  # type: ignore[union-attr]
    assert manager.active_reservations == 0


def test_reservation_enforces_item_pool_and_free_space_limits() -> None:
    manager = QuotaManager(
        pools={
            "private": QuotaPool(
                limit_bytes=100,
                usage_bytes=lambda: 20,
                free_bytes=lambda: 40,
            )
        },
        minimum_free_bytes=10,
    )

    reservation = manager.reserve("private", item_limit_bytes=25)
    reservation.consume(25)
    with pytest.raises(QuotaExceededError):
        reservation.consume(1)
    reservation.release()

    with pytest.raises(QuotaExceededError):
        manager.reserve("private", amount_bytes=31)


def test_commit_cannot_bypass_the_item_limit() -> None:
    manager = QuotaManager(
        pools={
            "private": QuotaPool(
                limit_bytes=1_000,
                usage_bytes=lambda: 0,
                free_bytes=lambda: 10_000,
            )
        },
        minimum_free_bytes=0,
    )
    reservation = manager.reserve("private", item_limit_bytes=25)

    with pytest.raises(QuotaExceededError):
        reservation.commit(persisted_bytes=26)

    assert reservation.active is True
    reservation.release()


def test_failed_mutation_releases_quota_for_a_retry() -> None:
    manager = QuotaManager(
        pools={
            "private": QuotaPool(
                limit_bytes=10,
                usage_bytes=lambda: 0,
                free_bytes=lambda: 100,
            )
        },
        minimum_free_bytes=0,
    )

    reservation = manager.reserve("private")
    reservation.consume(10)
    reservation.release()

    retry = manager.reserve("private", amount_bytes=10)
    retry.commit(persisted_bytes=0)
    assert manager.active_reservations == 0


def test_new_manager_recovers_from_stale_reservations_and_counts_partial_files(
    tmp_path,
) -> None:
    persisted = {"bytes": 0}
    pool = QuotaPool(
        limit_bytes=100,
        usage_bytes=lambda: persisted["bytes"],
        free_bytes=lambda: 1_000 - persisted["bytes"],
    )
    abandoned_manager = QuotaManager(
        pools={"private": pool},
        minimum_free_bytes=0,
        ledger_path=tmp_path / "quota.sqlite3",
        owner_id="abandoned-process",
    )
    abandoned_manager.reserve("private", amount_bytes=80)
    abandoned_manager.close()

    recovered = QuotaManager(
        pools={"private": pool},
        minimum_free_bytes=0,
        ledger_path=tmp_path / "quota.sqlite3",
        owner_id="restarted-process",
    )
    recovered.reserve("private", amount_bytes=100).release()
    recovered.close()

    persisted["bytes"] = 80
    restarted_with_partial_file = QuotaManager(
        pools={"private": pool},
        minimum_free_bytes=0,
        ledger_path=tmp_path / "quota.sqlite3",
        owner_id="second-restart",
    )
    with pytest.raises(QuotaExceededError):
        restarted_with_partial_file.reserve("private", amount_bytes=21)


def test_two_managers_share_one_atomic_persistent_ledger(tmp_path) -> None:
    ledger = tmp_path / "quota.sqlite3"
    pool = QuotaPool(
        limit_bytes=100,
        usage_bytes=lambda: 0,
        free_bytes=lambda: 1_000,
    )
    first_manager = QuotaManager(
        pools={"private": pool},
        minimum_free_bytes=0,
        ledger_path=ledger,
    )
    second_manager = QuotaManager(
        pools={"private": pool},
        minimum_free_bytes=0,
        ledger_path=ledger,
    )
    first = first_manager.reserve("private", amount_bytes=70)

    with pytest.raises(QuotaExceededError):
        second_manager.reserve("private", amount_bytes=31)

    first.release()
    second_manager.reserve("private", amount_bytes=100).release()


def test_live_distinct_ledger_owner_cannot_reclaim_reservations(tmp_path) -> None:
    ledger = tmp_path / "quota.sqlite3"
    pool = QuotaPool(
        limit_bytes=100,
        usage_bytes=lambda: 0,
        free_bytes=lambda: 1_000,
    )
    first_manager = QuotaManager(
        pools={"private": pool},
        minimum_free_bytes=0,
        ledger_path=ledger,
        owner_id="live-owner-1",
    )
    first = first_manager.reserve("private", amount_bytes=70)

    with pytest.raises(ReservationConflictError):
        QuotaManager(
            pools={"private": pool},
            minimum_free_bytes=0,
            ledger_path=ledger,
            owner_id="live-owner-2",
        )

    first.release()
    first_manager.close()


def test_reopen_after_clean_close_reconciles_same_process_claims(tmp_path) -> None:
    ledger = tmp_path / "quota.sqlite3"
    pool = QuotaPool(
        limit_bytes=100,
        usage_bytes=lambda: 0,
        free_bytes=lambda: 1_000,
    )
    first = QuotaManager(
        pools={"private": pool},
        minimum_free_bytes=0,
        ledger_path=ledger,
    )
    first.reserve("private", amount_bytes=100)
    first.close()

    reopened = QuotaManager(
        pools={"private": pool},
        minimum_free_bytes=0,
        ledger_path=ledger,
    )
    reopened.reserve("private", amount_bytes=100).release()


def test_pools_on_one_filesystem_cannot_double_spend_free_space() -> None:
    def shared_filesystem() -> str:
        return "device-1"

    workspace = QuotaPool(
        limit_bytes=1_000,
        usage_bytes=lambda: 0,
        free_bytes=lambda: 100,
        total_bytes=lambda: 1_000,
        filesystem_id=shared_filesystem,
    )
    private = QuotaPool(
        limit_bytes=1_000,
        usage_bytes=lambda: 0,
        free_bytes=lambda: 100,
        total_bytes=lambda: 1_000,
        filesystem_id=shared_filesystem,
    )
    manager = QuotaManager(
        pools={"workspace": workspace, "private": private},
        minimum_free_bytes=10,
    )

    first = manager.reserve("workspace", amount_bytes=60)
    with pytest.raises(QuotaExceededError):
        manager.reserve("private", amount_bytes=31)
    first.release()


def test_free_space_fraction_uses_filesystem_capacity() -> None:
    pool = QuotaPool(
        limit_bytes=1_000,
        usage_bytes=lambda: 0,
        free_bytes=lambda: 101,
        total_bytes=lambda: 1_000,
        filesystem_id=lambda: "device-1",
    )
    manager = QuotaManager(
        pools={"private": pool},
        minimum_free_bytes=0,
        minimum_free_fraction=0.1,
    )

    manager.reserve("private", amount_bytes=1).release()
    with pytest.raises(QuotaExceededError):
        manager.reserve("private", amount_bytes=2)


def test_fractional_free_space_requires_real_filesystem_capacity() -> None:
    manager = QuotaManager(
        pools={
            "private": QuotaPool(
                limit_bytes=1_000,
                usage_bytes=lambda: 0,
                free_bytes=lambda: 100,
            )
        },
        minimum_free_bytes=0,
        minimum_free_fraction=0.05,
    )

    with pytest.raises(ReservationConflictError):
        manager.reserve("private", amount_bytes=1)


def test_active_reservation_rechecks_external_free_space_loss() -> None:
    available = {"bytes": 100}
    pool = QuotaPool(
        limit_bytes=1_000,
        usage_bytes=lambda: 0,
        free_bytes=lambda: available["bytes"],
        total_bytes=lambda: 1_000,
        filesystem_id=lambda: "device-1",
    )
    manager = QuotaManager(
        pools={"private": pool},
        minimum_free_bytes=10,
    )
    reservation = manager.reserve("private", amount_bytes=50)
    available["bytes"] = 30

    with pytest.raises(QuotaExceededError):
        reservation.consume(1)

    reservation.release()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("run_total_timeout_seconds", float("nan")),
        ("max_archive_expansion_ratio", float("inf")),
        ("max_upload_file_bytes", True),
    ],
)
def test_resource_limits_reject_nonfinite_and_nonintegral_policy_values(
    field: str,
    value: object,
) -> None:
    with pytest.raises((TypeError, ValueError)):
        ResourceLimits(**{field: value})


def test_exclusive_reservation_conflicts_are_typed_and_release_cleanly() -> None:
    manager = QuotaManager(
        pools={
            "private": QuotaPool(
                limit_bytes=100,
                usage_bytes=lambda: 0,
                free_bytes=lambda: 1_000,
            )
        },
        minimum_free_bytes=0,
    )
    first = manager.reserve("private", conflict_key="thread-1")

    with pytest.raises(ReservationConflictError) as error:
        manager.reserve("private", conflict_key="thread-1")

    assert error.value.code == "reservation_conflict"
    first.release()
    manager.reserve("private", conflict_key="thread-1").release()


def test_ledger_commit_failure_rolls_back_and_allows_retry() -> None:
    manager = QuotaManager(
        pools={
            "private": QuotaPool(
                limit_bytes=100,
                usage_bytes=lambda: 0,
                free_bytes=lambda: 1_000,
            )
        },
        minimum_free_bytes=0,
    )
    connection = manager._connection

    class FailOneCommit:
        def __init__(self) -> None:
            self.failed = False

        def __getattr__(self, name):
            return getattr(connection, name)

        def commit(self):
            if not self.failed:
                self.failed = True
                raise OSError("commit failed")
            return connection.commit()

    manager._connection = FailOneCommit()  # type: ignore[assignment]
    with pytest.raises(OSError):
        manager.reserve("private", amount_bytes=50)

    manager.reserve("private", amount_bytes=100).release()


def _zip_bytes(entries: list[tuple[ZipInfo | str, bytes]]) -> bytes:
    output = io.BytesIO()
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        for name, content in entries:
            archive.writestr(name, content)
    return output.getvalue()


def test_safe_archive_inspection_returns_portable_regular_entries() -> None:
    payload = _zip_bytes([("folder/readme.txt", b"hello"), ("empty/", b"")])

    with ZipFile(io.BytesIO(payload)) as archive:
        entries = inspect_archive(archive, ResourceLimits())

    assert [entry.name for entry in entries] == ["folder/readme.txt", "empty/"]
    assert entries[0].expanded_bytes == 5


@pytest.mark.parametrize(
    "name",
    [
        "../escape.txt",
        "/absolute.txt",
        "C:/drive.txt",
        "CON.txt",
        "trailing-dot./file.txt",
        "question?.txt",
        " leading-space.txt",
        f"{'x' * 256}.txt",
    ],
)
def test_safe_archive_inspection_rejects_escaping_or_nonportable_names(name: str) -> None:
    payload = _zip_bytes([(name, b"payload")])

    with ZipFile(io.BytesIO(payload)) as archive:
        with pytest.raises(QuotaExceededError) as error:
            inspect_archive(archive, ResourceLimits())

    assert error.value.resource == "archive_entry"


def test_safe_archive_inspection_rejects_duplicate_casefolded_names() -> None:
    payload = _zip_bytes([("Folder/File.txt", b"a"), ("folder/file.TXT", b"b")])

    with ZipFile(io.BytesIO(payload)) as archive:
        with pytest.raises(QuotaExceededError):
            inspect_archive(archive, ResourceLimits())


@pytest.mark.parametrize(
    "entries",
    [
        [("a/./b.txt", b"a"), ("a/b.txt", b"b")],
        [("a//b.txt", b"a")],
        [("a", b"file"), ("a/b.txt", b"child")],
        [("a/b.txt", b"child"), ("a", b"file")],
    ],
)
def test_safe_archive_inspection_rejects_normalized_and_prefix_conflicts(
    entries: list[tuple[str, bytes]],
) -> None:
    payload = _zip_bytes(entries)

    with ZipFile(io.BytesIO(payload)) as archive:
        with pytest.raises(QuotaExceededError):
            inspect_archive(archive, ResourceLimits())


def test_safe_archive_inspection_rejects_symlinks_and_special_entries() -> None:
    symlink = ZipInfo("link")
    symlink.create_system = 3
    symlink.external_attr = (stat.S_IFLNK | 0o777) << 16
    payload = _zip_bytes([(symlink, b"target")])

    with ZipFile(io.BytesIO(payload)) as archive:
        with pytest.raises(QuotaExceededError) as error:
            inspect_archive(archive, ResourceLimits())

    assert error.value.resource == "archive_entry"


def test_safe_archive_inspection_rejects_unsupported_compression() -> None:
    unsupported = ZipInfo("payload.bin")
    unsupported.compress_type = 99
    unsupported.file_size = 1
    unsupported.compress_size = 1

    class UnsupportedArchive:
        def infolist(self):
            return [unsupported]

    with pytest.raises(QuotaExceededError) as error:
        inspect_archive(UnsupportedArchive(), ResourceLimits())  # type: ignore[arg-type]

    assert error.value.resource == "archive_entry"


def test_safe_archive_inspection_rejects_entry_expanded_and_ratio_bombs() -> None:
    entry_payload = _zip_bytes([("one", b"1"), ("two", b"2")])
    with ZipFile(io.BytesIO(entry_payload)) as archive:
        with pytest.raises(QuotaExceededError) as error:
            inspect_archive(archive, ResourceLimits(max_archive_entries=1))
    assert error.value.resource == "archive_entries"

    expanded_payload = _zip_bytes([("large", b"x" * 32)])
    with ZipFile(io.BytesIO(expanded_payload)) as archive:
        with pytest.raises(QuotaExceededError) as error:
            inspect_archive(
                archive,
                ResourceLimits(max_archive_expanded_bytes=16),
            )
    assert error.value.resource == "archive_expanded"

    ratio_payload = _zip_bytes([("zeros", b"0" * 10_000)])
    with ZipFile(io.BytesIO(ratio_payload)) as archive:
        with pytest.raises(QuotaExceededError) as error:
            inspect_archive(
                archive,
                ResourceLimits(max_archive_expansion_ratio=2),
            )
    assert error.value.resource == "archive_ratio"


def test_safe_archive_open_rejects_entry_count_before_zipfile_materialization() -> None:
    payload = bytearray(_zip_bytes([("one.txt", b"one")]))
    eocd = payload.rfind(b"PK\x05\x06")
    assert eocd >= 0
    struct.pack_into("<H", payload, eocd + 8, 20_001)
    struct.pack_into("<H", payload, eocd + 10, 20_001)

    with pytest.raises(QuotaExceededError) as error:
        with open_inspected_archive(io.BytesIO(payload), ResourceLimits()):
            pass

    assert error.value.resource == "archive_entries"


def test_safe_archive_open_counts_central_records_instead_of_trusting_eocd() -> None:
    payload = bytearray(_zip_bytes([("one.txt", b"one"), ("two.txt", b"two")]))
    eocd = payload.rfind(b"PK\x05\x06")
    assert eocd >= 0
    struct.pack_into("<H", payload, eocd + 8, 1)
    struct.pack_into("<H", payload, eocd + 10, 1)

    with pytest.raises(QuotaExceededError):
        with open_inspected_archive(io.BytesIO(payload), ResourceLimits()):
            pass


def test_safe_archive_context_does_not_reclassify_caller_exceptions() -> None:
    class CallerFailure(Exception):
        pass

    payload = _zip_bytes([("one.txt", b"one")])
    with pytest.raises(CallerFailure):
        with open_inspected_archive(io.BytesIO(payload), ResourceLimits()):
            raise CallerFailure


def test_streaming_archive_copy_rolls_back_destination_after_crc_failure() -> None:
    output = io.BytesIO()
    with ZipFile(output, "w") as archive:
        archive.writestr("payload.txt", b"known-payload")
    payload = bytearray(output.getvalue())
    content_offset = payload.find(b"known-payload")
    assert content_offset >= 0
    payload[content_offset] ^= 0x01
    destination = io.BytesIO()
    aggregate = StreamingByteCounter(
        limit_bytes=ResourceLimits().max_archive_expanded_bytes,
        resource="archive_expanded",
    )

    with open_inspected_archive(io.BytesIO(payload), ResourceLimits()) as inspected:
        archive, entries = inspected
        with pytest.raises(QuotaExceededError):
            copy_archive_entry(
                archive,
                entries[0],
                destination,
                limits=ResourceLimits(),
                aggregate_counter=aggregate,
            )

    assert destination.getvalue() == b""
    assert aggregate.consumed_bytes == 0


def test_archive_copy_reports_destination_rollback_failure() -> None:
    class BrokenRollback(io.BytesIO):
        def truncate(self, size=None):
            raise OSError("rollback failed")

    output = io.BytesIO()
    with ZipFile(output, "w") as archive:
        archive.writestr("payload.txt", b"known-payload")
    payload = bytearray(output.getvalue())
    payload[payload.find(b"known-payload")] ^= 0x01
    aggregate = StreamingByteCounter(limit_bytes=100, resource="archive_expanded")

    with open_inspected_archive(io.BytesIO(payload), ResourceLimits()) as inspected:
        archive, entries = inspected
        with pytest.raises(ReservationConflictError) as error:
            copy_archive_entry(
                archive,
                entries[0],
                BrokenRollback(),
                limits=ResourceLimits(),
                aggregate_counter=aggregate,
            )

    assert error.value.resource == "archive_destination"
    assert aggregate.consumed_bytes == 0
