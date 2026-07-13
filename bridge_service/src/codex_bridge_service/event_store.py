from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
from contextlib import ExitStack, closing, contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from threading import BoundedSemaphore, Condition, RLock
from time import monotonic
from typing import Any, Callable, Iterator, Literal, Mapping, Sequence
from uuid import uuid4


EventScopeName = Literal["auth", "runtime", "thread"]
_VALID_SCOPES = frozenset({"auth", "runtime", "thread"})
_METADATA_RETENTION_BYTES_PER_ROW = 16 * 1024
_TOMBSTONE_RETENTION_BYTES_PER_ROW = 1024
_SQLITE_VALUE_BATCH_SIZE = 500


class EventStoreError(RuntimeError):
    pass


class EventStoreClosedError(EventStoreError):
    pass


class EventPayloadTooLargeError(EventStoreError):
    pass


class EventStoreCapacityError(EventStoreError):
    pass


class EventStoreAdmissionError(EventStoreCapacityError):
    """Capacity failed before a durable operation could publish state."""


class EventWaitCapacityError(EventStoreError):
    pass


class OperationKeyConflictError(EventStoreError):
    pass


class OperationKeyExpiredError(EventStoreError):
    pass


class EventCursorExpiredError(EventStoreError):
    def __init__(
        self,
        *,
        requested_cursor: int,
        minimum_cursor: int,
        snapshot_cursor: int,
        scope: str | None = None,
        thread_id: str | None = None,
    ) -> None:
        self.requested_cursor = requested_cursor
        self.minimum_cursor = minimum_cursor
        self.snapshot_cursor = snapshot_cursor
        self.scope = scope
        self.thread_id = thread_id
        super().__init__("The requested event cursor is no longer retained.")


class ThreadEventSequenceExpiredError(EventStoreError):
    def __init__(
        self,
        *,
        requested_sequence: int,
        minimum_sequence: int,
        snapshot_cursor: int,
        thread_id: str,
    ) -> None:
        self.requested_sequence = requested_sequence
        self.minimum_sequence = minimum_sequence
        self.snapshot_cursor = snapshot_cursor
        self.thread_id = thread_id
        super().__init__("The requested thread event sequence is no longer retained.")


@dataclass(frozen=True, slots=True)
class StoredEventRecord:
    cursor: int
    event_id: str
    scope: EventScopeName
    thread_id: str | None
    event_type: str
    payload: dict[str, Any]
    timestamp: str
    operation_id: str | None = None
    scope_sequence: int = 0


@dataclass(frozen=True, slots=True)
class EventBatch:
    events: list[StoredEventRecord]
    next_cursor: int
    minimum_cursor: int
    has_more: bool
    heartbeat: bool = False


@dataclass(frozen=True, slots=True)
class CompactionResult:
    deleted_count: int
    minimum_cursor: int
    snapshot_cursor: int


@dataclass(frozen=True, slots=True)
class LegacyImportResult:
    scanned_count: int
    imported_count: int
    duplicate_count: int


@dataclass(frozen=True, slots=True)
class EventDraft:
    scope: EventScopeName
    event_type: str
    payload: Mapping[str, Any]
    thread_id: str | None = None
    timestamp: str | None = None


@dataclass(frozen=True, slots=True)
class OutboxWrite:
    relative_path: str
    state_revision: int
    state_payload: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class _OpenedStateTarget:
    target: Path
    name: str
    parent_fd: int | None


class InjectedOutboxCrash(RuntimeError):
    pass


class OutboxStateConflictError(EventStoreError):
    pass


class DurableOperationTooLargeError(EventStoreError):
    pass


class BridgeEventStore:
    """SQLite WAL journal with global cursors and durable dedupe ledgers."""

    def __init__(
        self,
        database_path: Path | str,
        *,
        max_event_payload_bytes: int = 1024 * 1024,
        max_batch_events: int = 256,
        max_batch_payload_bytes: int = 4 * 1024 * 1024,
        max_events_per_thread: int = 25_000,
        max_thread_event_bytes: int = 50 * 1024 * 1024,
        max_events_per_non_thread_scope: int = 25_000,
        max_non_thread_event_bytes: int = 50 * 1024 * 1024,
        max_journal_bytes: int = 512 * 1024 * 1024,
        max_orphaned_metadata_rows: int | None = None,
        max_operation_tombstones: int | None = None,
        max_concurrent_waiters: int = 32,
    ) -> None:
        limits = (
            max_event_payload_bytes,
            max_batch_events,
            max_batch_payload_bytes,
            max_events_per_thread,
            max_thread_event_bytes,
            max_events_per_non_thread_scope,
            max_non_thread_event_bytes,
            max_journal_bytes,
            max_concurrent_waiters,
        )
        if any(type(value) is not int or value <= 0 for value in limits):
            raise ValueError("event store limits must be positive")
        if max_journal_bytes < 512 * 1024:
            raise ValueError("event journal capacity is too small")
        maximum_metadata_rows = max(
            1,
            max_journal_bytes // _METADATA_RETENTION_BYTES_PER_ROW,
        )
        if max_orphaned_metadata_rows is None:
            max_orphaned_metadata_rows = maximum_metadata_rows
        elif (
            type(max_orphaned_metadata_rows) is not int
            or not 0 < max_orphaned_metadata_rows <= maximum_metadata_rows
        ):
            raise ValueError("metadata retention limit is invalid")
        maximum_tombstones = max(
            1,
            max_journal_bytes // _TOMBSTONE_RETENTION_BYTES_PER_ROW,
        )
        if max_operation_tombstones is None:
            max_operation_tombstones = maximum_tombstones
        elif (
            type(max_operation_tombstones) is not int
            or not 0 < max_operation_tombstones <= maximum_tombstones
        ):
            raise ValueError("operation tombstone limit is invalid")
        self.path = Path(database_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.max_event_payload_bytes = max_event_payload_bytes
        self.max_batch_events = max_batch_events
        self.max_batch_payload_bytes = max_batch_payload_bytes
        self.max_events_per_thread = max_events_per_thread
        self.max_thread_event_bytes = max_thread_event_bytes
        self.max_events_per_non_thread_scope = max_events_per_non_thread_scope
        self.max_non_thread_event_bytes = max_non_thread_event_bytes
        self.max_journal_bytes = max_journal_bytes
        self.max_orphaned_metadata_rows = max_orphaned_metadata_rows
        self.max_operation_tombstones = max_operation_tombstones
        self.max_concurrent_waiters = max_concurrent_waiters
        self._wait_capacity = BoundedSemaphore(max_concurrent_waiters)
        self._condition = Condition(RLock())
        self._signal_revision = 0
        self._closed = False
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path,
            timeout=30.0,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.execute("PRAGMA synchronous = FULL")
        page_size_row = connection.execute("PRAGMA page_size").fetchone()
        page_size = int(page_size_row[0]) if page_size_row is not None else 4096
        wal_reserve = min(4 * 1024 * 1024, self.max_journal_bytes // 8)
        main_budget = max(
            page_size * 64,
            self.max_journal_bytes - wal_reserve - 64 * 1024,
        )
        max_pages = max(64, main_budget // page_size)
        connection.execute(f"PRAGMA max_page_count = {max_pages}")
        connection.execute("PRAGMA wal_autocheckpoint = 128")
        connection.execute(f"PRAGMA journal_size_limit = {wal_reserve}")
        return connection

    def _initialize(self) -> None:
        with closing(self._connect()) as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS events (
                    cursor INTEGER PRIMARY KEY AUTOINCREMENT,
                    operation_key TEXT NOT NULL UNIQUE,
                    operation_id TEXT,
                    event_id TEXT NOT NULL UNIQUE,
                    scope TEXT NOT NULL CHECK(scope IN ('auth','runtime','thread')),
                    scope_id TEXT NOT NULL,
                    thread_id TEXT,
                    scope_sequence INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    payload_bytes INTEGER NOT NULL,
                    timestamp TEXT NOT NULL,
                    UNIQUE(scope, scope_id, scope_sequence)
                );

                CREATE TABLE IF NOT EXISTS operation_ledger (
                    operation_key TEXT PRIMARY KEY,
                    fingerprint TEXT NOT NULL,
                    cursor INTEGER NOT NULL,
                    event_id TEXT NOT NULL,
                    operation_id TEXT,
                    scope TEXT NOT NULL,
                    scope_id TEXT NOT NULL,
                    thread_id TEXT,
                    scope_sequence INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    timestamp TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS scope_state (
                    scope TEXT NOT NULL,
                    scope_id TEXT NOT NULL,
                    next_sequence INTEGER NOT NULL DEFAULT 1,
                    minimum_cursor INTEGER NOT NULL DEFAULT 0,
                    snapshot_cursor INTEGER NOT NULL DEFAULT 0,
                    retained_count INTEGER NOT NULL DEFAULT 0,
                    retained_bytes INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(scope, scope_id)
                );

                CREATE TABLE IF NOT EXISTS legacy_import_ledger (
                    legacy_key TEXT PRIMARY KEY,
                    fingerprint TEXT NOT NULL,
                    operation_key TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS outbox_operations (
                    operation_id TEXT PRIMARY KEY,
                    fingerprint TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    applied_at TEXT,
                    has_events INTEGER NOT NULL DEFAULT 1
                );

                CREATE TABLE IF NOT EXISTS operation_tombstones (
                    tombstone_key TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_events_scope_cursor
                    ON events(scope, scope_id, cursor);
                CREATE INDEX IF NOT EXISTS idx_events_operation_id
                    ON events(operation_id);
                CREATE INDEX IF NOT EXISTS idx_operation_ledger_operation_id
                    ON operation_ledger(operation_id);
                CREATE INDEX IF NOT EXISTS idx_outbox_pending
                    ON outbox_operations(applied_at, created_at);
                """
            )
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(scope_state)")
            }
            if "retained_count" not in columns:
                connection.execute(
                    "ALTER TABLE scope_state ADD COLUMN "
                    "retained_count INTEGER NOT NULL DEFAULT 0"
                )
            if "retained_bytes" not in columns:
                connection.execute(
                    "ALTER TABLE scope_state ADD COLUMN "
                    "retained_bytes INTEGER NOT NULL DEFAULT 0"
                )
            outbox_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(outbox_operations)")
            }
            if "has_events" not in outbox_columns:
                connection.execute(
                    "ALTER TABLE outbox_operations ADD COLUMN "
                    "has_events INTEGER NOT NULL DEFAULT 1"
                )
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    "UPDATE scope_state SET "
                    "retained_count = (SELECT COUNT(*) FROM events "
                    "WHERE events.scope = scope_state.scope "
                    "AND events.scope_id = scope_state.scope_id), "
                    "retained_bytes = COALESCE((SELECT SUM(payload_bytes) FROM events "
                    "WHERE events.scope = scope_state.scope "
                    "AND events.scope_id = scope_state.scope_id), 0)"
                )
                connection.execute(
                    "UPDATE outbox_operations SET payload_json = '{}' "
                    "WHERE applied_at IS NOT NULL"
                )
                self._prune_orphaned_metadata(connection)
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def close(self) -> None:
        with self._condition:
            if self._closed:
                return
            self._closed = True
            self._signal_revision += 1
            self._condition.notify_all()

    def append(
        self,
        *,
        operation_key: str,
        scope: str,
        event_type: str,
        payload: Mapping[str, Any],
        thread_id: str | None = None,
        timestamp: str | None = None,
        operation_id: str | None = None,
        event_id: str | None = None,
        scope_sequence: int | None = None,
    ) -> StoredEventRecord:
        record, _inserted = self._append(
            operation_key=operation_key,
            scope=scope,
            event_type=event_type,
            payload=payload,
            thread_id=thread_id,
            timestamp=timestamp,
            operation_id=operation_id,
            event_id=event_id,
            scope_sequence=scope_sequence,
        )
        return record

    def _append(
        self,
        *,
        operation_key: str,
        scope: str,
        event_type: str,
        payload: Mapping[str, Any],
        thread_id: str | None,
        timestamp: str | None,
        operation_id: str | None,
        event_id: str | None,
        scope_sequence: int | None,
        connection: sqlite3.Connection | None = None,
        notify: bool = True,
    ) -> tuple[StoredEventRecord, bool]:
        self._require_open()
        normalized_scope, normalized_thread_id, scope_id = _normalize_scope(
            scope, thread_id
        )
        operation_key = _bounded_identifier(
            operation_key, field="operation key", maximum=512
        )
        event_type = _bounded_identifier(event_type, field="event type", maximum=128)
        if operation_id is not None:
            operation_id = _bounded_identifier(
                operation_id, field="operation id", maximum=256
            )
        if scope_sequence is not None and (
            type(scope_sequence) is not int or scope_sequence < 1
        ):
            raise ValueError("scope sequence is invalid")
        normalized_timestamp = (
            _bounded_identifier(timestamp, field="event timestamp", maximum=64)
            if timestamp is not None
            else None
        )
        # Event payloads are the public projection as well as the durable
        # journal record.  Apply the projection before hashing, sizing, and
        # writing so direct appends, outbox retries, and legacy imports cannot
        # retain private state or device-login material.
        payload_json, normalized_payload = _canonical_payload(
            _public_event_payload(event_type, payload)
        )
        payload_bytes = len(payload_json.encode("utf-8"))
        if payload_bytes > self.max_event_payload_bytes:
            raise EventPayloadTooLargeError("The event payload exceeds its limit.")
        fingerprint = _event_fingerprint(
            scope=normalized_scope,
            thread_id=normalized_thread_id,
            event_type=event_type,
            payload_json=payload_json,
            timestamp=normalized_timestamp,
            scope_sequence=scope_sequence,
        )
        actual_timestamp = normalized_timestamp or _now()
        actual_event_id = event_id or f"evt_{uuid4().hex}"
        actual_event_id = _bounded_identifier(
            actual_event_id, field="event id", maximum=128
        )

        owns_connection = connection is None
        database = connection or self._connect()
        try:
            if owns_connection:
                database.execute("BEGIN IMMEDIATE")
            existing = database.execute(
                "SELECT * FROM operation_ledger WHERE operation_key = ?",
                (operation_key,),
            ).fetchone()
            if existing is not None:
                if existing["fingerprint"] != fingerprint:
                    raise OperationKeyConflictError(
                        "The event operation key was reused with different content."
                    )
                record = _ledger_record(existing, normalized_payload)
                if owns_connection:
                    database.commit()
                return record, False
            self._require_event_operation_key_available(database, operation_key)
            # Capacity is only relevant for a new journal write.  Keep
            # idempotent retries readable even when the physical journal is
            # already full.
            self._require_journal_capacity(database)

            state = database.execute(
                "SELECT next_sequence FROM scope_state "
                "WHERE scope = ? AND scope_id = ?",
                (normalized_scope, scope_id),
            ).fetchone()
            next_sequence = int(state["next_sequence"]) if state is not None else 1
            actual_sequence = scope_sequence or next_sequence
            new_next_sequence = max(next_sequence, actual_sequence + 1)
            database.execute(
                "INSERT INTO scope_state(scope, scope_id, next_sequence) "
                "VALUES(?, ?, ?) "
                "ON CONFLICT(scope, scope_id) DO UPDATE SET "
                "next_sequence = max(scope_state.next_sequence, excluded.next_sequence)",
                (normalized_scope, scope_id, new_next_sequence),
            )
            try:
                cursor = database.execute(
                    "INSERT INTO events("
                    "operation_key, operation_id, event_id, scope, scope_id, "
                    "thread_id, scope_sequence, event_type, payload_json, "
                    "payload_bytes, timestamp"
                    ") VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        operation_key,
                        operation_id,
                        actual_event_id,
                        normalized_scope,
                        scope_id,
                        normalized_thread_id,
                        actual_sequence,
                        event_type,
                        payload_json,
                        payload_bytes,
                        actual_timestamp,
                    ),
                ).lastrowid
            except sqlite3.IntegrityError as error:
                raise OperationKeyConflictError(
                    "The event identifier or scope sequence conflicts."
                ) from error
            assert cursor is not None
            database.execute(
                "INSERT INTO operation_ledger("
                "operation_key, fingerprint, cursor, event_id, operation_id, "
                "scope, scope_id, thread_id, scope_sequence, event_type, timestamp"
                ") VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    operation_key,
                    fingerprint,
                    cursor,
                    actual_event_id,
                    operation_id,
                    normalized_scope,
                    scope_id,
                    normalized_thread_id,
                    actual_sequence,
                    event_type,
                    actual_timestamp,
                ),
            )
            database.execute(
                "UPDATE scope_state SET retained_count = retained_count + 1, "
                "retained_bytes = retained_bytes + ? "
                "WHERE scope = ? AND scope_id = ?",
                (payload_bytes, normalized_scope, scope_id),
            )
            if normalized_scope == "thread":
                maximum_count = self.max_events_per_thread
                maximum_bytes = self.max_thread_event_bytes
            else:
                maximum_count = self.max_events_per_non_thread_scope
                maximum_bytes = self.max_non_thread_event_bytes
            if self._enforce_scope_retention(
                database,
                scope=normalized_scope,
                scope_id=scope_id,
                snapshot_cursor=int(cursor),
                maximum_count=maximum_count,
                maximum_bytes=maximum_bytes,
            ):
                self._prune_orphaned_metadata(database)
            if owns_connection:
                database.commit()
            record = StoredEventRecord(
                cursor=int(cursor),
                event_id=actual_event_id,
                scope=normalized_scope,
                thread_id=normalized_thread_id,
                event_type=event_type,
                payload=normalized_payload,
                timestamp=actual_timestamp,
                operation_id=operation_id,
                scope_sequence=actual_sequence,
            )
        except sqlite3.Error as error:
            if owns_connection:
                database.rollback()
            if _sqlite_capacity_error(error):
                raise EventStoreCapacityError("The event journal is full.") from None
            raise
        except BaseException:
            if owns_connection:
                database.rollback()
            raise
        finally:
            if owns_connection:
                database.close()
        if notify:
            self._notify_waiters()
        return record, True

    def _enforce_scope_retention(
        self,
        connection: sqlite3.Connection,
        *,
        scope: EventScopeName,
        scope_id: str,
        snapshot_cursor: int,
        maximum_count: int,
        maximum_bytes: int,
    ) -> bool:
        state = connection.execute(
            "SELECT retained_count, retained_bytes FROM scope_state "
            "WHERE scope = ? AND scope_id = ?",
            (scope, scope_id),
        ).fetchone()
        assert state is not None
        retained_count = int(state["retained_count"])
        retained_bytes = int(state["retained_bytes"])
        excess_count = max(0, retained_count - maximum_count)
        excess_bytes = max(0, retained_bytes - maximum_bytes)
        if excess_count == 0 and excess_bytes == 0:
            return False
        rows = connection.execute(
            "SELECT cursor, payload_bytes FROM events "
            "WHERE scope = ? AND scope_id = ? ORDER BY cursor ASC",
            (scope, scope_id),
        )
        deleted_count = 0
        deleted_bytes = 0
        through_cursor = 0
        while (
            deleted_count < excess_count or deleted_bytes < excess_bytes
        ) and retained_count - deleted_count > 1:
            row = rows.fetchone()
            if row is None:
                break
            deleted_count += 1
            deleted_bytes += int(row["payload_bytes"])
            through_cursor = int(row["cursor"])
        if through_cursor == 0:
            return False
        connection.execute(
            "DELETE FROM events WHERE scope = ? AND scope_id = ? "
            "AND cursor <= ?",
            (scope, scope_id, through_cursor),
        )
        connection.execute(
            "UPDATE scope_state SET "
            "minimum_cursor = max(minimum_cursor, ?), "
            "snapshot_cursor = max(snapshot_cursor, ?), "
            "retained_count = max(0, retained_count - ?), "
            "retained_bytes = max(0, retained_bytes - ?) "
            "WHERE scope = ? AND scope_id = ?",
            (
                through_cursor,
                snapshot_cursor,
                deleted_count,
                deleted_bytes,
                scope,
                scope_id,
            ),
        )
        return True

    def _prune_orphaned_metadata(self, connection: sqlite3.Connection) -> None:
        """Bound inactive dedupe metadata without permitting operation-key reuse.

        Callers hold a write transaction.  An event operation is only retired as
        a complete group, so durable-outbox retries cannot observe a partially
        retained operation.  Retired operation keys are kept as fixed-size,
        non-evicting digests; exhausting that safe tombstone budget fails the
        enclosing write rather than forgetting an old key.
        """

        orphan_rows = connection.execute(
            "SELECT ledger.operation_key, ledger.operation_id, ledger.cursor "
            "FROM operation_ledger AS ledger "
            "WHERE NOT EXISTS("
            "SELECT 1 FROM events WHERE events.operation_key = ledger.operation_key"
            ") "
            "AND (ledger.operation_id IS NULL OR NOT EXISTS("
            "SELECT 1 FROM events AS operation_events "
            "WHERE operation_events.operation_id = ledger.operation_id"
            ")) "
            "AND NOT EXISTS("
            "SELECT 1 FROM outbox_operations AS pending "
            "WHERE pending.operation_id = ledger.operation_id "
            "AND pending.applied_at IS NULL"
            ") "
            "ORDER BY ledger.cursor DESC, ledger.operation_key DESC"
        ).fetchall()
        groups: dict[tuple[str, str], list[str]] = {}
        for row in orphan_rows:
            operation_id = row["operation_id"]
            group = (
                ("operation", str(operation_id))
                if operation_id is not None
                else ("key", str(row["operation_key"]))
            )
            groups.setdefault(group, []).append(str(row["operation_key"]))

        remaining_rows = self.max_orphaned_metadata_rows
        operation_keys_to_expire: list[str] = []
        keep_groups = True
        for operation_keys in groups.values():
            if keep_groups and len(operation_keys) <= remaining_rows:
                remaining_rows -= len(operation_keys)
                continue
            keep_groups = False
            operation_keys_to_expire.extend(operation_keys)

        self._add_tombstones(
            connection,
            (_event_tombstone_key(operation_key) for operation_key in operation_keys_to_expire),
        )
        self._delete_values(
            connection,
            table="operation_ledger",
            column="operation_key",
            values=operation_keys_to_expire,
        )
        connection.execute(
            "DELETE FROM legacy_import_ledger "
            "WHERE NOT EXISTS("
            "SELECT 1 FROM operation_ledger "
            "WHERE operation_ledger.operation_key = legacy_import_ledger.operation_key"
            ") "
            "AND NOT EXISTS("
            "SELECT 1 FROM events "
            "WHERE events.operation_key = legacy_import_ledger.operation_key"
            ")"
        )

        applied_rows = connection.execute(
            "SELECT operation_id, has_events FROM outbox_operations AS operation "
            "WHERE operation.applied_at IS NOT NULL "
            "AND NOT EXISTS("
            "SELECT 1 FROM events "
            "WHERE events.operation_id = operation.operation_id"
            ") "
            "AND NOT EXISTS("
            "SELECT 1 FROM operation_ledger "
            "WHERE operation_ledger.operation_id = operation.operation_id"
            ") "
            "ORDER BY operation.rowid DESC"
        ).fetchall()
        event_operation_ids_to_expire = [
            str(row["operation_id"])
            for row in applied_rows
            if int(row["has_events"]) != 0
        ]
        state_operation_ids = [
            str(row["operation_id"])
            for row in applied_rows
            if int(row["has_events"]) == 0
        ]
        operation_ids_to_expire = (
            event_operation_ids_to_expire
            + state_operation_ids[self.max_orphaned_metadata_rows :]
        )
        self._add_tombstones(
            connection,
            (_outbox_tombstone_key(operation_id) for operation_id in operation_ids_to_expire),
        )
        self._delete_values(
            connection,
            table="outbox_operations",
            column="operation_id",
            values=operation_ids_to_expire,
        )

    def _add_tombstones(
        self,
        connection: sqlite3.Connection,
        tombstone_keys: Iterator[str],
    ) -> None:
        keys = list(tombstone_keys)
        if not keys:
            return
        existing: set[str] = set()
        for batch in _value_batches(keys):
            placeholders = ",".join("?" for _ in batch)
            rows = connection.execute(
                "SELECT tombstone_key FROM operation_tombstones "
                f"WHERE tombstone_key IN ({placeholders})",
                batch,
            ).fetchall()
            existing.update(str(row["tombstone_key"]) for row in rows)
        new_keys = [key for key in keys if key not in existing]
        if not new_keys:
            return
        count_row = connection.execute(
            "SELECT COUNT(*) AS count FROM operation_tombstones"
        ).fetchone()
        assert count_row is not None
        self._require_tombstone_capacity(
            count=int(count_row["count"]),
            additional=len(new_keys),
        )
        created_at = _now()
        connection.executemany(
            "INSERT INTO operation_tombstones(tombstone_key, created_at) "
            "VALUES(?, ?)",
            ((key, created_at) for key in new_keys),
        )

    def _require_outbox_completion_capacity(
        self,
        connection: sqlite3.Connection,
        *,
        has_events: bool,
    ) -> None:
        if has_events:
            return
        row = connection.execute(
            "SELECT COUNT(*) AS count FROM outbox_operations AS operation "
            "WHERE operation.applied_at IS NOT NULL "
            "AND NOT EXISTS("
            "SELECT 1 FROM events "
            "WHERE events.operation_id = operation.operation_id"
            ") "
            "AND NOT EXISTS("
            "SELECT 1 FROM operation_ledger "
            "WHERE operation_ledger.operation_id = operation.operation_id"
            ")"
        ).fetchone()
        assert row is not None
        if int(row["count"]) >= self.max_orphaned_metadata_rows:
            tombstone_row = connection.execute(
                "SELECT COUNT(*) AS count FROM operation_tombstones"
            ).fetchone()
            assert tombstone_row is not None
            self._require_tombstone_capacity(
                count=int(tombstone_row["count"]),
                additional=1,
            )

    def _require_tombstone_capacity(
        self,
        *,
        count: int,
        additional: int,
    ) -> None:
        if count + additional > self.max_operation_tombstones:
            raise EventStoreCapacityError(
                "The event journal idempotency tombstone capacity is exhausted."
            )

    def _delete_values(
        self,
        connection: sqlite3.Connection,
        *,
        table: Literal["operation_ledger", "outbox_operations"],
        column: Literal["operation_key", "operation_id"],
        values: Sequence[str],
    ) -> None:
        for batch in _value_batches(values):
            placeholders = ",".join("?" for _ in batch)
            connection.execute(
                f"DELETE FROM {table} WHERE {column} IN ({placeholders})",
                batch,
            )

    def _require_event_operation_key_available(
        self,
        connection: sqlite3.Connection,
        operation_key: str,
    ) -> None:
        self._require_tombstone_absent(
            connection,
            tombstone_key=_event_tombstone_key(operation_key),
            message="The event operation key has expired from dedupe retention.",
        )

    def _require_outbox_operation_id_available(
        self,
        connection: sqlite3.Connection,
        operation_id: str,
    ) -> None:
        self._require_tombstone_absent(
            connection,
            tombstone_key=_outbox_tombstone_key(operation_id),
            message="The durable operation id has expired from dedupe retention.",
        )

    def _require_tombstone_absent(
        self,
        connection: sqlite3.Connection,
        *,
        tombstone_key: str,
        message: str,
    ) -> None:
        row = connection.execute(
            "SELECT 1 FROM operation_tombstones WHERE tombstone_key = ?",
            (tombstone_key,),
        ).fetchone()
        if row is not None:
            raise OperationKeyExpiredError(message)

    def replay(
        self,
        *,
        after_cursor: int | None = None,
        after: int | None = None,
        scopes: Sequence[str] | None = None,
        thread_ids: Sequence[str] | None = None,
        limit: int | None = None,
    ) -> EventBatch:
        self._require_open()
        cursor = _normalize_after(after_cursor=after_cursor, after=after)
        normalized_scopes, normalized_threads = _normalize_filters(scopes, thread_ids)
        requested_limit = self.max_batch_events if limit is None else limit
        if type(requested_limit) is not int or requested_limit < 1:
            raise ValueError("event batch limit is invalid")
        batch_limit = min(requested_limit, self.max_batch_events)
        with closing(self._connect()) as connection:
            minimum_cursor, snapshot_cursor = self._cursor_floor(
                connection,
                scopes=normalized_scopes,
                thread_ids=normalized_threads,
            )
            if cursor < minimum_cursor:
                raise EventCursorExpiredError(
                    requested_cursor=cursor,
                    minimum_cursor=minimum_cursor,
                    snapshot_cursor=snapshot_cursor,
                    scope=(
                        normalized_scopes[0]
                        if normalized_scopes is not None and len(normalized_scopes) == 1
                        else None
                    ),
                    thread_id=(
                        normalized_threads[0]
                        if normalized_threads is not None
                        and len(normalized_threads) == 1
                        else None
                    ),
                )
            where, parameters = _event_filter_sql(
                after_cursor=cursor,
                scopes=normalized_scopes,
                thread_ids=normalized_threads,
            )
            rows = connection.execute(
                f"SELECT * FROM events WHERE {where} ORDER BY cursor LIMIT ?",
                (*parameters, batch_limit + 1),
            ).fetchall()
            global_cursor_row = connection.execute(
                "SELECT COALESCE(MAX(cursor), 0) AS cursor FROM events"
            ).fetchone()

        events: list[StoredEventRecord] = []
        aggregate_bytes = 0
        has_more = False
        for row in rows:
            row_bytes = int(row["payload_bytes"])
            if events and aggregate_bytes + row_bytes > self.max_batch_payload_bytes:
                has_more = True
                break
            if len(events) >= batch_limit:
                has_more = True
                break
            events.append(_event_row(row))
            aggregate_bytes += row_bytes
        if len(rows) > len(events):
            has_more = True
        if events:
            next_cursor = events[-1].cursor
        elif global_cursor_row is not None:
            next_cursor = max(cursor, int(global_cursor_row["cursor"]))
        else:
            next_cursor = cursor
        return EventBatch(
            events=events,
            next_cursor=next_cursor,
            minimum_cursor=minimum_cursor,
            has_more=has_more,
        )

    def wait(
        self,
        *,
        after_cursor: int | None = None,
        after: int | None = None,
        scopes: Sequence[str] | None = None,
        thread_ids: Sequence[str] | None = None,
        limit: int | None = None,
        timeout_seconds: float = 20.0,
    ) -> EventBatch:
        if isinstance(timeout_seconds, bool) or not isinstance(
            timeout_seconds, (int, float)
        ):
            raise ValueError("event wait timeout is invalid")
        if timeout_seconds < 0:
            raise ValueError("event wait timeout is invalid")
        if not self._wait_capacity.acquire(blocking=False):
            raise EventWaitCapacityError("The event wait capacity is exhausted.")
        try:
            deadline = monotonic() + float(timeout_seconds)
            with self._condition:
                while True:
                    batch = self.replay(
                        after_cursor=after_cursor,
                        after=after,
                        scopes=scopes,
                        thread_ids=thread_ids,
                        limit=limit,
                    )
                    if batch.events or batch.has_more:
                        return batch
                    remaining = deadline - monotonic()
                    if remaining <= 0:
                        return replace(batch, heartbeat=True)
                    observed = self._signal_revision
                    self._condition.wait(timeout=min(remaining, 1.0))
                    if self._closed:
                        raise EventStoreClosedError("The event store is closed.")
                    if self._signal_revision == observed and monotonic() < deadline:
                        continue
        finally:
            self._wait_capacity.release()

    def replay_thread(
        self,
        thread_id: str,
        *,
        after_sequence: int | None = None,
    ) -> list[StoredEventRecord]:
        """Return the bounded v0 per-thread sequence projection."""

        self._require_open()
        _scope, normalized_thread, scope_id = _normalize_scope("thread", thread_id)
        if after_sequence is None:
            after_sequence = 0
        if type(after_sequence) is not int or after_sequence < 0:
            raise ValueError("thread event sequence is invalid")
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM events WHERE scope = 'thread' AND scope_id = ? "
                "AND scope_sequence > ? ORDER BY scope_sequence",
                (scope_id, after_sequence),
            ).fetchall()
            state = connection.execute(
                "SELECT next_sequence, minimum_cursor, snapshot_cursor "
                "FROM scope_state WHERE scope = 'thread' AND scope_id = ?",
                (scope_id,),
            ).fetchone()
        if state is not None and int(state["minimum_cursor"]) > 0:
            minimum_sequence = (
                int(rows[0]["scope_sequence"])
                if rows
                else int(state["next_sequence"])
            )
            if after_sequence < max(0, minimum_sequence - 1):
                assert normalized_thread is not None
                raise ThreadEventSequenceExpiredError(
                    requested_sequence=after_sequence,
                    minimum_sequence=minimum_sequence,
                    snapshot_cursor=int(state["snapshot_cursor"]),
                    thread_id=normalized_thread,
                )
        assert normalized_thread is not None
        return [_event_row(row) for row in rows]

    def purge_thread(self, thread_id: str) -> CompactionResult:
        """Remove one deleted chat's replayable payloads, retaining cursor guidance."""

        self._require_open()
        _scope, _thread_id, scope_id = _normalize_scope("thread", thread_id)
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            state = connection.execute(
                "SELECT next_sequence, minimum_cursor, snapshot_cursor "
                "FROM scope_state WHERE scope = 'thread' AND scope_id = ?",
                (scope_id,),
            ).fetchone()
            removed = connection.execute(
                "SELECT COUNT(*) AS count, COALESCE(MAX(cursor), 0) AS cursor "
                "FROM events WHERE scope = 'thread' AND scope_id = ?",
                (scope_id,),
            ).fetchone()
            assert removed is not None
            current_minimum = int(state["minimum_cursor"]) if state is not None else 0
            current_snapshot = (
                int(state["snapshot_cursor"]) if state is not None else 0
            )
            through_cursor = max(current_minimum, int(removed["cursor"]))
            snapshot_cursor = max(current_snapshot, through_cursor)
            deleted = connection.execute(
                "DELETE FROM events WHERE scope = 'thread' AND scope_id = ?",
                (scope_id,),
            ).rowcount
            connection.execute(
                "INSERT INTO scope_state("
                "scope, scope_id, next_sequence, minimum_cursor, snapshot_cursor, "
                "retained_count, retained_bytes"
                ") VALUES('thread', ?, 1, ?, ?, 0, 0) "
                "ON CONFLICT(scope, scope_id) DO UPDATE SET "
                "minimum_cursor = max(scope_state.minimum_cursor, "
                "excluded.minimum_cursor), "
                "snapshot_cursor = max(scope_state.snapshot_cursor, "
                "excluded.snapshot_cursor), "
                "retained_count = 0, retained_bytes = 0",
                (scope_id, through_cursor, snapshot_cursor),
            )
            self._prune_orphaned_metadata(connection)
            connection.commit()
        self._notify_waiters()
        return CompactionResult(
            deleted_count=max(0, int(deleted)),
            minimum_cursor=through_cursor,
            snapshot_cursor=snapshot_cursor,
        )

    def compact(
        self,
        *,
        scope: str,
        through_cursor: int,
        snapshot_cursor: int,
        thread_id: str | None = None,
    ) -> CompactionResult:
        self._require_open()
        normalized_scope, _thread_id, scope_id = _normalize_scope(scope, thread_id)
        if type(through_cursor) is not int or through_cursor < 0:
            raise ValueError("compaction cursor is invalid")
        if type(snapshot_cursor) is not int or snapshot_cursor < through_cursor:
            raise ValueError("snapshot cursor is invalid")
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            removed = connection.execute(
                "SELECT COUNT(*) AS count, "
                "COALESCE(SUM(payload_bytes), 0) AS bytes FROM events "
                "WHERE scope = ? AND scope_id = ? AND cursor <= ?",
                (normalized_scope, scope_id, through_cursor),
            ).fetchone()
            assert removed is not None
            deleted = connection.execute(
                "DELETE FROM events WHERE scope = ? AND scope_id = ? AND cursor <= ?",
                (normalized_scope, scope_id, through_cursor),
            ).rowcount
            connection.execute(
                "INSERT INTO scope_state("
                "scope, scope_id, next_sequence, minimum_cursor, snapshot_cursor"
                ") VALUES(?, ?, 1, ?, ?) "
                "ON CONFLICT(scope, scope_id) DO UPDATE SET "
                "minimum_cursor = max(scope_state.minimum_cursor, excluded.minimum_cursor), "
                "snapshot_cursor = max(scope_state.snapshot_cursor, excluded.snapshot_cursor), "
                "retained_count = max(0, scope_state.retained_count - ?), "
                "retained_bytes = max(0, scope_state.retained_bytes - ?)",
                (
                    normalized_scope,
                    scope_id,
                    through_cursor,
                    snapshot_cursor,
                    int(removed["count"]),
                    int(removed["bytes"]),
                ),
            )
            self._prune_orphaned_metadata(connection)
            floor = connection.execute(
                "SELECT minimum_cursor, snapshot_cursor FROM scope_state "
                "WHERE scope = ? AND scope_id = ?",
                (normalized_scope, scope_id),
            ).fetchone()
            connection.commit()
        self._notify_waiters()
        assert floor is not None
        return CompactionResult(
            deleted_count=max(0, int(deleted)),
            minimum_cursor=int(floor["minimum_cursor"]),
            snapshot_cursor=int(floor["snapshot_cursor"]),
        )

    def import_legacy_jsonl(
        self,
        path: Path | str,
        *,
        thread_id: str,
    ) -> LegacyImportResult:
        self._require_open()
        normalized_thread = _bounded_identifier(
            thread_id, field="thread id", maximum=128
        )
        scanned = imported = duplicates = 0
        with Path(path).open("r", encoding="utf-8") as stream:
            for line in stream:
                if not line.strip():
                    continue
                scanned += 1
                try:
                    raw = json.loads(line)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    raise EventStoreError("A legacy event record is invalid.") from None
                if (
                    not isinstance(raw, dict)
                    or raw.get("thread_id") != normalized_thread
                ):
                    raise EventStoreError("A legacy event record is invalid.")
                try:
                    sequence = raw["sequence"]
                    event_id = raw["event_id"]
                    event_type = raw["event_type"]
                    payload = raw.get("payload", {})
                    timestamp = raw["timestamp"]
                except KeyError:
                    raise EventStoreError("A legacy event record is invalid.") from None
                if type(sequence) is not int or sequence < 1:
                    raise EventStoreError("A legacy event record is invalid.")
                try:
                    event_type = _bounded_identifier(
                        event_type,
                        field="legacy event type",
                        maximum=128,
                    )
                except ValueError:
                    raise EventStoreError(
                        "A legacy event record is invalid."
                    ) from None
                legacy_fields = _LEGACY_EVENT_FIELDS.get(event_type)
                if legacy_fields is None:
                    payload = {"legacy_event_type": event_type}
                    event_type = "legacy.event"
                elif not isinstance(payload, Mapping):
                    raise EventStoreError("A legacy event record is invalid.")
                else:
                    payload = {
                        key: payload[key] for key in legacy_fields if key in payload
                    }
                try:
                    timestamp = _bounded_identifier(
                        timestamp,
                        field="legacy event timestamp",
                        maximum=64,
                    )
                except ValueError:
                    raise EventStoreError(
                        "A legacy event record is invalid."
                    ) from None
                legacy_key = f"legacy:{normalized_thread}:{event_id}"
                legacy_fingerprint = hashlib.sha256(
                    json.dumps(
                        raw,
                        sort_keys=True,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest()
                with closing(self._connect()) as connection:
                    existing = connection.execute(
                        "SELECT fingerprint FROM legacy_import_ledger "
                        "WHERE legacy_key = ?",
                        (legacy_key,),
                    ).fetchone()
                if existing is not None:
                    if existing["fingerprint"] != legacy_fingerprint:
                        raise OperationKeyConflictError(
                            "A legacy event identifier has conflicting content."
                        )
                    duplicates += 1
                    continue
                try:
                    record, inserted = self._append(
                        operation_key=legacy_key,
                        scope="thread",
                        thread_id=normalized_thread,
                        event_type=event_type,
                        payload=payload,
                        timestamp=timestamp,
                        event_id=event_id,
                        operation_id=None,
                        scope_sequence=sequence,
                    )
                except OperationKeyExpiredError:
                    duplicates += 1
                    continue
                del record
                with closing(self._connect()) as connection:
                    connection.execute("BEGIN IMMEDIATE")
                    try:
                        connection.execute(
                            "INSERT INTO legacy_import_ledger("
                            "legacy_key, fingerprint, operation_key"
                            ") VALUES(?, ?, ?)",
                            (legacy_key, legacy_fingerprint, legacy_key),
                        )
                        connection.commit()
                    except sqlite3.IntegrityError:
                        connection.rollback()
                        duplicates += 1
                        continue
                if inserted:
                    imported += 1
                else:
                    duplicates += 1
        return LegacyImportResult(
            scanned_count=scanned,
            imported_count=imported,
            duplicate_count=duplicates,
        )

    def _cursor_floor(
        self,
        connection: sqlite3.Connection,
        *,
        scopes: tuple[str, ...] | None,
        thread_ids: tuple[str, ...] | None,
    ) -> tuple[int, int]:
        clauses: list[str] = []
        parameters: list[object] = []
        if scopes is not None:
            clauses.append(f"scope IN ({','.join('?' for _ in scopes)})")
            parameters.extend(scopes)
        if thread_ids is not None:
            thread_clause = f"scope_id IN ({','.join('?' for _ in thread_ids)})"
            if scopes is None or any(scope != "thread" for scope in scopes):
                clauses.append(f"(scope != 'thread' OR {thread_clause})")
            else:
                clauses.append(thread_clause)
            parameters.extend(thread_ids)
        where = " AND ".join(clauses) if clauses else "1 = 1"
        row = connection.execute(
            "SELECT COALESCE(MAX(minimum_cursor), 0) AS minimum_cursor, "
            "COALESCE(MAX(snapshot_cursor), 0) AS snapshot_cursor "
            f"FROM scope_state WHERE {where}",
            parameters,
        ).fetchone()
        assert row is not None
        return int(row["minimum_cursor"]), int(row["snapshot_cursor"])

    def _notify_waiters(self) -> None:
        with self._condition:
            self._signal_revision += 1
            self._condition.notify_all()

    def _journal_size_bytes(self) -> int:
        return sum(
            path.stat().st_size
            for path in (
                self.path,
                self.path.with_name(f"{self.path.name}-wal"),
                self.path.with_name(f"{self.path.name}-shm"),
            )
            if path.exists()
        )

    def _require_journal_capacity(
        self,
        connection: sqlite3.Connection | None = None,
    ) -> None:
        if self._journal_size_bytes() < self.max_journal_bytes:
            return
        if connection is not None:
            free_row = connection.execute("PRAGMA freelist_count").fetchone()
            if free_row is not None and int(free_row[0]) > 0:
                return
        raise EventStoreCapacityError("The event journal is full.")

    def _require_open(self) -> None:
        if self._closed:
            raise EventStoreClosedError("The event store is closed.")


def _normalize_scope(
    scope: str,
    thread_id: str | None,
) -> tuple[EventScopeName, str | None, str]:
    if scope not in _VALID_SCOPES:
        raise ValueError("event scope is invalid")
    if scope == "thread":
        normalized_thread = _bounded_identifier(
            thread_id, field="thread id", maximum=128
        )
        return "thread", normalized_thread, normalized_thread
    if thread_id is not None:
        raise ValueError("only thread events may include a thread id")
    return scope, None, ""


def _normalize_filters(
    scopes: Sequence[str] | None,
    thread_ids: Sequence[str] | None,
) -> tuple[tuple[str, ...] | None, tuple[str, ...] | None]:
    normalized_scopes: tuple[str, ...] | None = None
    if scopes is not None:
        values = tuple(dict.fromkeys(scopes))
        if not values or any(scope not in _VALID_SCOPES for scope in values):
            raise ValueError("event scopes are invalid")
        normalized_scopes = values
    normalized_threads: tuple[str, ...] | None = None
    if thread_ids is not None:
        values = tuple(
            dict.fromkeys(
                _bounded_identifier(value, field="thread id", maximum=128)
                for value in thread_ids
            )
        )
        if not values:
            raise ValueError("thread filters are invalid")
        normalized_threads = values
    return normalized_scopes, normalized_threads


def _event_filter_sql(
    *,
    after_cursor: int,
    scopes: tuple[str, ...] | None,
    thread_ids: tuple[str, ...] | None,
) -> tuple[str, tuple[object, ...]]:
    clauses = ["cursor > ?"]
    parameters: list[object] = [after_cursor]
    if scopes is not None:
        clauses.append(f"scope IN ({','.join('?' for _ in scopes)})")
        parameters.extend(scopes)
    if thread_ids is not None:
        thread_clause = f"thread_id IN ({','.join('?' for _ in thread_ids)})"
        if scopes is None or any(scope != "thread" for scope in scopes):
            clauses.append(f"(scope != 'thread' OR {thread_clause})")
        else:
            clauses.append(thread_clause)
        parameters.extend(thread_ids)
    return " AND ".join(clauses), tuple(parameters)


def _normalize_after(*, after_cursor: int | None, after: int | None) -> int:
    if after_cursor is not None and after is not None:
        raise ValueError("only one event cursor may be supplied")
    value = after if after is not None else after_cursor
    if value is None:
        value = 0
    if type(value) is not int or value < 0:
        raise ValueError("event cursor is invalid")
    return value


def _canonical_payload(payload: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    if not isinstance(payload, Mapping):
        raise ValueError("event payload must be an object")
    try:
        payload_json = json.dumps(
            dict(payload),
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
        normalized = json.loads(payload_json)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("event payload is not valid JSON") from None
    if not isinstance(normalized, dict):
        raise ValueError("event payload must be an object")
    return payload_json, normalized


_PUBLIC_EVENT_FIELDS: dict[str, frozenset[str]] = {
    "auth.status_changed": frozenset(
        {
            "revision",
            "state",
            "busy",
            "auth_required",
            "auth_mode",
            "plan_type",
            "updated_at",
        }
    ),
    "thread.created": frozenset(
        {
            "title",
            "project_id",
            "project_name",
            "workspace_id",
            "mode",
            "model_override",
            "thinking_override",
            "created_at",
        }
    ),
    "attachment.added": frozenset(
        {
            "attachment_id",
            "filename",
            "mime_type",
            "relative_path",
            "size_bytes",
        }
    ),
    "artifact.added": frozenset(
        {
            "artifact_id",
            "filename",
            "mime_type",
            "relative_path",
            "size_bytes",
            "source",
        }
    ),
    # The retired exec adapter used to forward the complete provider JSON.
    # Keep only routing metadata so private cwd/auth/prompt fields cannot enter
    # either the canonical v1 replay or its list-shaped v0 adapter.
    "codex.event": frozenset({"run_id", "provider_event_type"}),
    "legacy.event": frozenset({"legacy_event_type"}),
}

_LEGACY_EVENT_FIELDS: dict[str, frozenset[str]] = {
    "artifact.added": _PUBLIC_EVENT_FIELDS["artifact.added"],
    "attachment.added": _PUBLIC_EVENT_FIELDS["attachment.added"],
    "codex.event": _PUBLIC_EVENT_FIELDS["codex.event"],
    "message.completed": frozenset({"run_id", "role", "text"}),
    "message.created": frozenset(
        {"run_id", "role", "text", "client_request_id", "queued"}
    ),
    "run.cancelled": frozenset({"run_id"}),
    "run.completed": frozenset({"run_id"}),
    "run.dequeued": frozenset({"run_id"}),
    "run.failed": frozenset(
        {"run_id", "failure_type", "blocked", "auth_required"}
    ),
    "run.queue_cleared": frozenset({"queued_count"}),
    "run.queued": frozenset({"run_id"}),
    "run.started": frozenset({"run_id"}),
    "session.bound": frozenset({"run_id"}),
    "thread.archived": frozenset({"archived_at"}),
    "thread.created": _PUBLIC_EVENT_FIELDS["thread.created"],
    "thread.restored": frozenset({"updated_at"}),
    "thread.updated": frozenset(
        {
            "title",
            "mode",
            "model_override",
            "thinking_override",
            "updated_at",
        }
    ),
}


def _public_event_payload(
    event_type: str,
    payload: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Return the minimum safe projection for known public event types.

    The canonical state files and provider responses may contain private
    locators or device-login material.  Those values must never cross into the
    replayable event journal, including a pending durable-outbox envelope.
    Unknown event types remain extensible and retain their validated payload.
    """

    fields = _PUBLIC_EVENT_FIELDS.get(event_type)
    if fields is None or not isinstance(payload, Mapping):
        return payload
    return {key: payload[key] for key in fields if key in payload}


def _event_fingerprint(
    *,
    scope: str,
    thread_id: str | None,
    event_type: str,
    payload_json: str,
    timestamp: str | None,
    scope_sequence: int | None,
) -> str:
    material = json.dumps(
        [
            scope,
            thread_id,
            event_type,
            json.loads(payload_json),
            timestamp,
            scope_sequence,
        ],
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def _event_row(row: sqlite3.Row) -> StoredEventRecord:
    return StoredEventRecord(
        cursor=int(row["cursor"]),
        event_id=row["event_id"],
        scope=row["scope"],
        thread_id=row["thread_id"],
        event_type=row["event_type"],
        payload=json.loads(row["payload_json"]),
        timestamp=row["timestamp"],
        operation_id=row["operation_id"],
        scope_sequence=int(row["scope_sequence"]),
    )


def _ledger_record(row: sqlite3.Row, payload: dict[str, Any]) -> StoredEventRecord:
    return StoredEventRecord(
        cursor=int(row["cursor"]),
        event_id=row["event_id"],
        scope=row["scope"],
        thread_id=row["thread_id"],
        event_type=row["event_type"],
        payload=payload,
        timestamp=row["timestamp"],
        operation_id=row["operation_id"],
        scope_sequence=int(row["scope_sequence"]),
    )


def _bounded_identifier(
    value: object,
    *,
    field: str,
    maximum: int,
) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > maximum
        or value != value.strip()
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)
    ):
        raise ValueError(f"{field} is invalid")
    return value


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _event_tombstone_key(operation_key: str) -> str:
    return _tombstone_digest("event", operation_key)


def _outbox_tombstone_key(operation_id: str) -> str:
    return _tombstone_digest("outbox", operation_id)


def _tombstone_digest(kind: str, value: str) -> str:
    return hashlib.sha256(f"{kind}\0{value}".encode("utf-8")).hexdigest()


def _value_batches(values: Sequence[str]) -> Iterator[Sequence[str]]:
    for index in range(0, len(values), _SQLITE_VALUE_BATCH_SIZE):
        yield values[index : index + _SQLITE_VALUE_BATCH_SIZE]


def _sqlite_capacity_error(error: sqlite3.Error) -> bool:
    return (
        getattr(error, "sqlite_errorcode", None) == sqlite3.SQLITE_FULL
        or "database or disk is full" in str(error).lower()
    )


FailureInjector = Callable[[str], None]


class DurableOutbox:
    """Crash-reconcilable canonical JSON writes paired with journal events."""

    def __init__(
        self,
        event_store: BridgeEventStore,
        *,
        state_root: Path | str,
        failure_injector: FailureInjector | None = None,
        max_operation_events: int = 20_000,
        max_operation_writes: int = 16,
        max_operation_bytes: int = 80 * 1024 * 1024,
    ) -> None:
        limits = (
            max_operation_events,
            max_operation_writes,
            max_operation_bytes,
        )
        if any(type(value) is not int or value <= 0 for value in limits):
            raise ValueError("durable operation limits must be positive")
        self.event_store = event_store
        self.state_root = Path(state_root)
        self.state_root.mkdir(parents=True, exist_ok=True)
        self._resolved_state_root = self.state_root.resolve(strict=True)
        self.failure_injector = failure_injector
        self.max_operation_events = max_operation_events
        self.max_operation_writes = max_operation_writes
        self.max_operation_bytes = max_operation_bytes
        self._operation_lock = RLock()
        # One Uvicorn worker owns this outbox. A successful reconciliation or
        # finalize proves there are no older pending rows; a prepared operation
        # marks the instance dirty until finalize/reconciliation succeeds.
        self._reconciled = False

    def commit_json(
        self,
        *,
        operation_id: str,
        relative_path: str,
        state_revision: int,
        state_payload: Mapping[str, Any],
        event: EventDraft,
    ) -> tuple[StoredEventRecord, ...]:
        return self.commit_operation(
            operation_id=operation_id,
            writes=(
                OutboxWrite(
                    relative_path=relative_path,
                    state_revision=state_revision,
                    state_payload=state_payload,
                ),
            ),
            events=(event,),
        )

    def commit_operation(
        self,
        *,
        operation_id: str,
        writes: Sequence[OutboxWrite],
        events: Sequence[EventDraft],
    ) -> tuple[StoredEventRecord, ...]:
        with self._operation_lock:
            # A prior process/thread may have replaced canonical JSON and
            # failed before its events committed. Finish every older operation
            # before allowing a newer revision to supersede that marker.
            self._ensure_reconciled_locked()
            return self._commit_operation_locked(
                operation_id=operation_id,
                writes=writes,
                events=events,
            )

    def _commit_operation_locked(
        self,
        *,
        operation_id: str,
        writes: Sequence[OutboxWrite],
        events: Sequence[EventDraft],
    ) -> tuple[StoredEventRecord, ...]:
        operation_id = _bounded_identifier(
            operation_id,
            field="operation id",
            maximum=256,
        )
        self._validate_operation_bounds(writes=writes, events=events)
        envelope = self._operation_envelope(
            operation_id=operation_id,
            writes=writes,
            events=events,
        )
        payload_json = json.dumps(
            envelope,
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
        if len(payload_json.encode("utf-8")) > self.max_operation_bytes:
            raise DurableOperationTooLargeError(
                "The durable operation exceeds its limit."
            )
        fingerprint_envelope = json.loads(payload_json)
        for serialized, draft in zip(
            fingerprint_envelope["events"],
            events,
            strict=True,
        ):
            if draft.timestamp is None:
                serialized["timestamp"] = None
        fingerprint = hashlib.sha256(
            json.dumps(
                fingerprint_envelope,
                sort_keys=True,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        applied, prepared_payload = self._prepare(
            operation_id=operation_id,
            fingerprint=fingerprint,
            payload_json=payload_json,
            event_operation_keys=tuple(
                str(event["operation_key"]) for event in envelope["events"]
            ),
        )
        if applied:
            return self._applied_records(envelope)
        # `_prepare` has durably created or rediscovered a pending row. Leave
        # the dirty marker set across every injected/real failure after here.
        self._reconciled = False
        if prepared_payload is not None:
            try:
                prepared_envelope = json.loads(prepared_payload)
            except (json.JSONDecodeError, TypeError):
                raise EventStoreError("A durable operation is invalid.") from None
            if not isinstance(prepared_envelope, dict):
                raise EventStoreError("A durable operation is invalid.")
            envelope = prepared_envelope
        self._inject("after_outbox_commit")
        self._apply_writes(envelope)
        self._inject("before_event_append")
        records = self._finalize(envelope)
        self._reconciled = True
        return records

    def _validate_operation_bounds(
        self,
        *,
        writes: Sequence[OutboxWrite],
        events: Sequence[EventDraft],
    ) -> None:
        if len(writes) > self.max_operation_writes:
            raise DurableOperationTooLargeError(
                "The durable operation has too many state writes."
            )
        if len(events) > self.max_operation_events:
            raise DurableOperationTooLargeError(
                "The durable operation has too many events."
            )
        estimated_bytes = 512
        for write in writes:
            self._relative_state_path(write.relative_path)
            if type(write.state_revision) is not int or write.state_revision < 1:
                raise ValueError("state revision is invalid")
            if not isinstance(write.state_payload, Mapping):
                raise ValueError("state payload must be an object")
            payload = dict(write.state_payload)
            if "_bridge_operation" in payload:
                raise ValueError("state payload uses a reserved field")
            estimated_bytes += len(_canonical_document_bytes(payload)) + 512
            if estimated_bytes > self.max_operation_bytes:
                raise DurableOperationTooLargeError(
                    "The durable operation exceeds its limit."
                )
        for draft in events:
            _normalize_scope(draft.scope, draft.thread_id)
            event_type = _bounded_identifier(
                draft.event_type,
                field="event type",
                maximum=128,
            )
            if draft.timestamp is not None:
                _bounded_identifier(
                    draft.timestamp,
                    field="event timestamp",
                    maximum=64,
                )
            payload_json, _payload = _canonical_payload(
                _public_event_payload(event_type, draft.payload)
            )
            payload_bytes = len(payload_json.encode("utf-8"))
            if payload_bytes > self.event_store.max_event_payload_bytes:
                raise EventPayloadTooLargeError(
                    "The event payload exceeds its limit."
                )
            estimated_bytes += payload_bytes + 512
            if estimated_bytes > self.max_operation_bytes:
                raise DurableOperationTooLargeError(
                    "The durable operation exceeds its limit."
                )

    def reconcile(self) -> int:
        with self._operation_lock:
            return self._reconcile_locked()

    def _ensure_reconciled_locked(self) -> None:
        if not self._reconciled:
            self._reconcile_locked()

    def _reconcile_locked(self) -> int:
        self._reconciled = False
        applied = 0
        while True:
            with closing(self.event_store._connect()) as connection:
                row = connection.execute(
                    "SELECT rowid, payload_json FROM outbox_operations "
                    "WHERE applied_at IS NULL ORDER BY rowid LIMIT 1"
                ).fetchone()
            if row is None:
                self._reconciled = True
                return applied
            try:
                envelope = json.loads(row["payload_json"])
            except (json.JSONDecodeError, TypeError):
                raise EventStoreError("A durable operation is invalid.") from None
            if not isinstance(envelope, dict):
                raise EventStoreError("A durable operation is invalid.")
            self._apply_writes(envelope)
            self._inject("before_event_append")
            self._finalize(envelope)
            applied += 1

    def pending_count(self) -> int:
        with closing(self.event_store._connect()) as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS count FROM outbox_operations "
                "WHERE applied_at IS NULL"
            ).fetchone()
        assert row is not None
        return int(row["count"])

    def next_state_revision(self, relative_path: str) -> int:
        with self._operation_lock:
            self._ensure_reconciled_locked()
            return self._next_state_revision_locked(relative_path)

    def _next_state_revision_locked(self, relative_path: str) -> int:
        try:
            content = self._read_state(relative_path)
            if content is None:
                return 1
            raw = json.loads(content)
        except FileNotFoundError:
            return 1
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            raise OutboxStateConflictError(
                "The canonical state cannot be inspected."
            ) from None
        marker = _state_marker(raw)
        return 1 if marker is None else marker[1] + 1

    def _operation_envelope(
        self,
        *,
        operation_id: str,
        writes: Sequence[OutboxWrite],
        events: Sequence[EventDraft],
    ) -> dict[str, Any]:
        if not writes and not events:
            raise ValueError("a durable operation must contain state or events")
        serialized_writes: list[dict[str, Any]] = []
        seen_paths: set[str] = set()
        for write in writes:
            relative_path = self._relative_state_path(write.relative_path)
            if relative_path in seen_paths:
                raise ValueError("a durable operation cannot write state twice")
            seen_paths.add(relative_path)
            if type(write.state_revision) is not int or write.state_revision < 1:
                raise ValueError("state revision is invalid")
            if not isinstance(write.state_payload, Mapping):
                raise ValueError("state payload must be an object")
            state_payload = dict(write.state_payload)
            if "_bridge_operation" in state_payload:
                raise ValueError("state payload uses a reserved field")
            try:
                normalized_state = json.loads(
                    json.dumps(
                        state_payload,
                        sort_keys=True,
                        ensure_ascii=False,
                        allow_nan=False,
                        separators=(",", ":"),
                    )
                )
            except (TypeError, ValueError, OverflowError):
                raise ValueError("state payload is not valid JSON") from None
            if not isinstance(normalized_state, dict):
                raise ValueError("state payload must be an object")
            document = {
                "_bridge_operation": {
                    "operation_id": operation_id,
                    "revision": write.state_revision,
                },
                **normalized_state,
            }
            serialized_writes.append(
                {
                    "relative_path": relative_path,
                    "state_revision": write.state_revision,
                    "document": document,
                    "sha256": hashlib.sha256(
                        _canonical_document_bytes(document)
                    ).hexdigest(),
                }
            )

        serialized_events: list[dict[str, Any]] = []
        for index, draft in enumerate(events):
            scope, thread_id, _scope_id = _normalize_scope(
                draft.scope,
                draft.thread_id,
            )
            event_type = _bounded_identifier(
                draft.event_type,
                field="event type",
                maximum=128,
            )
            _payload_json, payload = _canonical_payload(
                _public_event_payload(event_type, draft.payload)
            )
            timestamp = draft.timestamp or _now()
            identity = hashlib.sha256(
                f"{operation_id}:{index}".encode("utf-8")
            ).hexdigest()
            serialized_events.append(
                {
                    "operation_key": f"outbox:{operation_id}:{index}",
                    "event_id": f"evt_{identity[:32]}",
                    "scope": scope,
                    "thread_id": thread_id,
                    "event_type": event_type,
                    "payload": payload,
                    "timestamp": timestamp,
                }
            )
        return {
            "schema_version": 1,
            "operation_id": operation_id,
            "writes": serialized_writes,
            "events": serialized_events,
        }

    def _prepare(
        self,
        *,
        operation_id: str,
        fingerprint: str,
        payload_json: str,
        event_operation_keys: Sequence[str],
    ) -> tuple[bool, str | None]:
        with closing(self.event_store._connect()) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                existing = connection.execute(
                    "SELECT fingerprint, payload_json, applied_at "
                    "FROM outbox_operations WHERE operation_id = ?",
                    (operation_id,),
                ).fetchone()
                if existing is not None:
                    if existing["fingerprint"] != fingerprint:
                        raise OperationKeyConflictError(
                            "The durable operation id was reused with different content."
                        )
                    connection.commit()
                    return existing["applied_at"] is not None, (
                        None
                        if existing["applied_at"] is not None
                        else existing["payload_json"]
                    )
                self.event_store._require_outbox_operation_id_available(
                    connection,
                    operation_id,
                )
                for event_operation_key in event_operation_keys:
                    self.event_store._require_event_operation_key_available(
                        connection,
                        event_operation_key,
                    )
                self.event_store._require_outbox_completion_capacity(
                    connection,
                    has_events=bool(event_operation_keys),
                )
                # Preparing a new operation consumes journal space; looking
                # up an existing operation does not.  This keeps retries
                # idempotent when the journal is at capacity.
                self.event_store._require_journal_capacity(connection)
                connection.execute(
                    "INSERT INTO outbox_operations("
                    "operation_id, fingerprint, payload_json, created_at, applied_at, "
                    "has_events"
                    ") VALUES(?, ?, ?, ?, NULL, ?)",
                    (
                        operation_id,
                        fingerprint,
                        payload_json,
                        _now(),
                        int(bool(event_operation_keys)),
                    ),
                )
                connection.commit()
            except EventStoreCapacityError as error:
                connection.rollback()
                raise EventStoreAdmissionError(
                    "The event journal cannot admit this durable operation."
                ) from error
            except sqlite3.Error as error:
                connection.rollback()
                if _sqlite_capacity_error(error):
                    raise EventStoreAdmissionError(
                        "The event journal cannot admit this durable operation."
                    ) from None
                raise
            except BaseException:
                connection.rollback()
                raise
        return False, None

    def _applied_records(
        self,
        envelope: Mapping[str, Any],
    ) -> tuple[StoredEventRecord, ...]:
        operation_id, _writes, events = _validate_outbox_envelope(envelope)
        records: list[StoredEventRecord] = []
        with closing(self.event_store._connect()) as connection:
            for index, event in enumerate(events):
                row = connection.execute(
                    "SELECT * FROM operation_ledger WHERE operation_key = ?",
                    (f"outbox:{operation_id}:{index}",),
                ).fetchone()
                if row is None:
                    raise EventStoreError(
                        "An applied durable operation is incomplete."
                    )
                records.append(_ledger_record(row, dict(event["payload"])))
        return tuple(records)

    def _apply_writes(self, envelope: Mapping[str, Any]) -> None:
        operation_id, writes, _events = _validate_outbox_envelope(envelope)
        pending: list[tuple[_OpenedStateTarget, bytes]] = []
        intended_by_path: list[tuple[str, bytes]] = []
        with ExitStack() as stack:
            for write in writes:
                relative_path = self._relative_state_path(write["relative_path"])
                opened = stack.enter_context(
                    self._open_state_target(relative_path)
                )
                document = write["document"]
                intended = _canonical_document_bytes(document)
                intended_by_path.append((relative_path, intended))
                if hashlib.sha256(intended).hexdigest() != write["sha256"]:
                    raise EventStoreError("A durable state payload is invalid.")
                intended_revision = write["state_revision"]
                current_bytes = self._read_opened_state(opened)
                if current_bytes is None:
                    pending.append((opened, intended))
                    continue
                try:
                    current = json.loads(current_bytes)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    raise OutboxStateConflictError(
                        "The canonical state is invalid."
                    ) from None
                marker = _state_marker(current)
                if marker is None:
                    pending.append((opened, intended))
                    continue
                current_operation, current_revision = marker
                if (
                    current_operation == operation_id
                    and current_revision == intended_revision
                ):
                    if _canonical_document_bytes(current) != intended:
                        raise OutboxStateConflictError(
                            "The canonical state conflicts with its operation marker."
                        )
                    continue
                if current_revision < intended_revision:
                    pending.append((opened, intended))
                    continue
                raise OutboxStateConflictError(
                    "The canonical state has a divergent revision."
                )

            self._inject("before_state_replace")
            for opened, payload in pending:
                self._replace_opened_state(opened, payload)
                self._inject("after_state_replace")

        # Re-open from the retained root after replacement. If a private path
        # was renamed or swapped while descriptors were held, do not publish
        # events for state that is no longer canonical.
        for relative_path, intended in intended_by_path:
            if self._read_state(relative_path) != intended:
                raise OutboxStateConflictError(
                    "The canonical state changed during durable publication."
                )

    def _finalize(
        self,
        envelope: Mapping[str, Any],
    ) -> tuple[StoredEventRecord, ...]:
        operation_id, _writes, events = _validate_outbox_envelope(envelope)
        records: list[StoredEventRecord] = []
        with closing(self.event_store._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT applied_at FROM outbox_operations WHERE operation_id = ?",
                    (operation_id,),
                ).fetchone()
                if row is None:
                    raise EventStoreError("A durable operation is unavailable.")
                for event in events:
                    record, _inserted = self.event_store._append(
                        operation_key=event["operation_key"],
                        operation_id=operation_id,
                        event_id=event["event_id"],
                        scope=event["scope"],
                        thread_id=event["thread_id"],
                        event_type=event["event_type"],
                        payload=event["payload"],
                        timestamp=event["timestamp"],
                        scope_sequence=None,
                        connection=connection,
                        notify=False,
                    )
                    records.append(record)
                connection.execute(
                    "UPDATE outbox_operations SET "
                    "applied_at = COALESCE(applied_at, ?), payload_json = '{}' "
                    "WHERE operation_id = ?",
                    (_now(), operation_id),
                )
                self.event_store._prune_orphaned_metadata(connection)
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        self.event_store._notify_waiters()
        return tuple(records)

    def _relative_state_path(self, value: object) -> str:
        if (
            not isinstance(value, str)
            or not value
            or "\\" in value
            or any(ord(character) < 0x20 for character in value)
        ):
            raise ValueError("state path is invalid")
        path = PurePosixPath(value)
        if (
            path.is_absolute()
            or path.as_posix() != value
            or any(part in {"", ".", ".."} for part in path.parts)
            or ":" in path.parts[0]
        ):
            raise ValueError("state path is invalid")
        return path.as_posix()

    def _state_target(self, relative_path: str) -> Path:
        normalized = self._relative_state_path(relative_path)
        target = self.state_root.joinpath(*PurePosixPath(normalized).parts)
        try:
            resolved = target.resolve(strict=False)
        except OSError:
            raise ValueError("state path is invalid") from None
        if not resolved.is_relative_to(self._resolved_state_root):
            raise ValueError("state path is invalid")
        return target

    @contextmanager
    def _open_state_target(
        self,
        relative_path: str,
    ) -> Iterator[_OpenedStateTarget]:
        normalized = self._relative_state_path(relative_path)
        try:
            target = self._state_target(normalized)
        except ValueError:
            raise OutboxStateConflictError(
                "The canonical state path is unavailable."
            ) from None
        parts = PurePosixPath(normalized).parts
        if os.name == "nt":
            yield _OpenedStateTarget(
                target=target,
                name=parts[-1],
                parent_fd=None,
            )
            return

        directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        descriptor: int | None = None
        try:
            descriptor = os.open(self._resolved_state_root, directory_flags)
            for part in parts[:-1]:
                try:
                    child = os.open(part, directory_flags, dir_fd=descriptor)
                except FileNotFoundError:
                    # Create each missing component through the already-open
                    # parent descriptor.  A concurrent creator is fine, but
                    # opening the resulting directory with O_NOFOLLOW keeps a
                    # raced-in symlink from ever becoming a traversal target.
                    created = False
                    try:
                        os.mkdir(part, 0o700, dir_fd=descriptor)
                        created = True
                    except FileExistsError:
                        pass
                    if created:
                        os.fsync(descriptor)
                    child = os.open(part, directory_flags, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = child
            yield _OpenedStateTarget(
                target=target,
                name=parts[-1],
                parent_fd=descriptor,
            )
        except OSError:
            raise OutboxStateConflictError(
                "The canonical state path is unavailable."
            ) from None
        finally:
            if descriptor is not None:
                os.close(descriptor)

    def _read_state(self, relative_path: str) -> bytes | None:
        with self._open_state_target(relative_path) as opened:
            return self._read_opened_state(opened)

    @staticmethod
    def _read_opened_state(opened: _OpenedStateTarget) -> bytes | None:
        if opened.parent_fd is None:
            try:
                return opened.target.read_bytes()
            except FileNotFoundError:
                return None
            except OSError:
                raise OutboxStateConflictError(
                    "The canonical state cannot be inspected."
                ) from None
        try:
            descriptor = os.open(
                opened.name,
                os.O_RDONLY | os.O_NOFOLLOW,
                dir_fd=opened.parent_fd,
            )
        except FileNotFoundError:
            return None
        except OSError:
            raise OutboxStateConflictError(
                "The canonical state cannot be inspected."
            ) from None
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                raise OutboxStateConflictError(
                    "The canonical state is not a regular file."
                )
            with os.fdopen(descriptor, "rb") as stream:
                descriptor = -1
                return stream.read()
        finally:
            if descriptor >= 0:
                os.close(descriptor)

    @staticmethod
    def _replace_opened_state(opened: _OpenedStateTarget, payload: bytes) -> None:
        if opened.parent_fd is None:
            _atomic_replace_bytes(opened.target, payload)
            return
        temporary_name = f".{opened.name}.{uuid4().hex}.tmp"
        try:
            descriptor = os.open(
                temporary_name,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,
                0o600,
                dir_fd=opened.parent_fd,
            )
            with os.fdopen(descriptor, "wb") as stream:
                view = memoryview(payload)
                while view:
                    written = stream.write(view)
                    if written is None or written <= 0:
                        raise OSError("state write failed")
                    view = view[written:]
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(
                temporary_name,
                opened.name,
                src_dir_fd=opened.parent_fd,
                dst_dir_fd=opened.parent_fd,
            )
            os.fsync(opened.parent_fd)
        except OSError:
            try:
                os.unlink(temporary_name, dir_fd=opened.parent_fd)
            except OSError:
                pass
            raise EventStoreError("The canonical state could not be saved.") from None

    def _inject(self, point: str) -> None:
        if self.failure_injector is not None:
            self.failure_injector(point)


def _validate_outbox_envelope(
    envelope: Mapping[str, Any],
) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    try:
        schema_version = envelope["schema_version"]
        operation_id = envelope["operation_id"]
        writes = envelope["writes"]
        events = envelope["events"]
    except KeyError:
        raise EventStoreError("A durable operation is invalid.") from None
    if (
        schema_version != 1
        or not isinstance(operation_id, str)
        or not isinstance(writes, list)
        or not isinstance(events, list)
        or any(not isinstance(write, dict) for write in writes)
        or any(not isinstance(event, dict) for event in events)
    ):
        raise EventStoreError("A durable operation is invalid.")
    return operation_id, writes, events


def _state_marker(value: object) -> tuple[str, int] | None:
    if not isinstance(value, dict):
        raise OutboxStateConflictError("The canonical state is not an object.")
    marker = value.get("_bridge_operation")
    if marker is None:
        return None
    if not isinstance(marker, dict) or set(marker) != {"operation_id", "revision"}:
        raise OutboxStateConflictError("The canonical state marker is invalid.")
    operation_id = marker["operation_id"]
    revision = marker["revision"]
    if not isinstance(operation_id, str) or type(revision) is not int or revision < 1:
        raise OutboxStateConflictError("The canonical state marker is invalid.")
    return operation_id, revision


def _canonical_document_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError):
        raise EventStoreError("A durable state payload is invalid.") from None


def _atomic_replace_bytes(target: Path, payload: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
    try:
        descriptor = os.open(
            temporary,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            0o600,
        )
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        if os.name != "nt":
            directory = os.open(target.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    except OSError:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise EventStoreError("The canonical state could not be saved.") from None
