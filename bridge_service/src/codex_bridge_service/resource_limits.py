from __future__ import annotations

import os
import sqlite3
import stat
import struct
import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass
from math import isfinite
from pathlib import Path, PurePosixPath
from threading import RLock
from typing import BinaryIO, Callable, Iterator
from uuid import uuid4
from zipfile import BadZipFile, ZIP_DEFLATED, ZIP_STORED, ZipFile, ZipInfo

from .workspace import WorkspaceInputError, normalize_portable_relative_path

MIB = 1024 * 1024
GIB = 1024 * MIB
_PROCESS_INSTANCE_ID = f"process-{os.getpid()}-{uuid4().hex}"
_MAX_SQLITE_INTEGER = (1 << 63) - 1
_LEDGER_LOCK_GUARD = RLock()
_HELD_LEDGER_LOCKS: dict[str, "_HeldLedgerLock"] = {}


@dataclass(frozen=True, slots=True)
class ResourceLimits:
    """Immutable single-user limits for the Home Assistant runtime profile."""

    max_active_turns: int = 1
    max_queued_prompts: int = 8
    run_total_timeout_seconds: float = 4 * 60 * 60
    run_idle_timeout_seconds: float = 10 * 60
    cancel_grace_seconds: float = 15
    max_upload_file_bytes: int = 100 * MIB
    max_upload_request_overhead_bytes: int = MIB
    max_workspace_bytes: int = 10 * GIB
    max_private_bytes: int = 2 * GIB
    max_archive_entries: int = 20_000
    max_archive_expanded_bytes: int = 2 * GIB
    max_archive_expansion_ratio: float = 100
    max_archive_metadata_bytes: int = 16 * MIB
    max_events_per_thread: int = 25_000
    max_event_log_bytes: int = 50 * MIB
    max_event_payload_bytes: int = 1 * MIB
    service_log_file_bytes: int = 10 * MIB
    service_log_backups: int = 10
    minimum_free_bytes: int = 1 * GIB
    minimum_free_fraction: float = 0.05
    max_transient_snapshot_bytes: int = 256 * MIB

    def __post_init__(self) -> None:
        positive_integers = (
            "max_active_turns",
            "max_upload_file_bytes",
            "max_upload_request_overhead_bytes",
            "max_workspace_bytes",
            "max_private_bytes",
            "max_archive_entries",
            "max_archive_expanded_bytes",
            "max_archive_metadata_bytes",
            "max_events_per_thread",
            "max_event_log_bytes",
            "max_event_payload_bytes",
            "service_log_file_bytes",
            "max_transient_snapshot_bytes",
        )
        for field_name in positive_integers:
            value = getattr(self, field_name)
            if type(value) is not int or not 0 < value <= _MAX_SQLITE_INTEGER:
                raise ValueError(f"{field_name} must be positive")
        nonnegative_integers = (
            "max_queued_prompts",
            "service_log_backups",
            "minimum_free_bytes",
        )
        for field_name in nonnegative_integers:
            value = getattr(self, field_name)
            if type(value) is not int or not 0 <= value <= _MAX_SQLITE_INTEGER:
                raise ValueError(f"{field_name} must not be negative")
        positive_numbers = (
            "run_total_timeout_seconds",
            "run_idle_timeout_seconds",
            "cancel_grace_seconds",
            "max_archive_expansion_ratio",
        )
        for field_name in positive_numbers:
            value = getattr(self, field_name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not isfinite(value)
                or value <= 0
            ):
                raise ValueError(f"{field_name} must be a finite positive number")
        if type(self.max_queued_prompts) is not int or self.max_queued_prompts < 0:
            raise ValueError("max_queued_prompts must not be negative")
        if (
            isinstance(self.minimum_free_fraction, bool)
            or not isinstance(self.minimum_free_fraction, (int, float))
            or not isfinite(self.minimum_free_fraction)
            or not 0 <= self.minimum_free_fraction < 1
        ):
            raise ValueError("minimum_free_fraction must be in [0, 1)")


class ResourceLimitError(RuntimeError):
    code = "resource_limit"

    def __init__(self, resource: str) -> None:
        self.resource = resource
        super().__init__("The requested operation exceeds a configured resource limit.")

    def __repr__(self) -> str:
        return f"{type(self).__name__}(code={self.code!r}, resource={self.resource!r})"


class QuotaExceededError(ResourceLimitError):
    code = "quota_exceeded"


class ReservationConflictError(ResourceLimitError):
    code = "reservation_conflict"


@dataclass(frozen=True, slots=True)
class QuotaPool:
    limit_bytes: int
    usage_bytes: Callable[[], int]
    free_bytes: Callable[[], int]
    total_bytes: Callable[[], int] | None = None
    filesystem_id: Callable[[], str] | None = None

    def __post_init__(self) -> None:
        if (
            type(self.limit_bytes) is not int
            or not 0 < self.limit_bytes <= _MAX_SQLITE_INTEGER
        ):
            raise ValueError("quota pool limit must be positive")


@dataclass(slots=True)
class _HeldLedgerLock:
    owner_id: str
    handle: BinaryIO
    references: int = 1


class _LedgerLockLease:
    def __init__(self, key: str, owner_id: str, *, fresh: bool) -> None:
        self.key = key
        self.owner_id = owner_id
        self.fresh = fresh
        self._active = True

    def close(self) -> None:
        if not self._active:
            return
        self._active = False
        with _LEDGER_LOCK_GUARD:
            held = _HELD_LEDGER_LOCKS.get(self.key)
            if held is None or held.owner_id != self.owner_id:
                return
            held.references -= 1
            if held.references:
                return
            _unlock_file(held.handle)
            held.handle.close()
            _HELD_LEDGER_LOCKS.pop(self.key, None)


def _acquire_ledger_lock(path: Path, owner_id: str) -> _LedgerLockLease:
    key = str(path.resolve(strict=False))
    with _LEDGER_LOCK_GUARD:
        held = _HELD_LEDGER_LOCKS.get(key)
        if held is not None:
            if held.owner_id != owner_id:
                raise ReservationConflictError("quota_ledger")
            held.references += 1
            return _LedgerLockLease(key, owner_id, fresh=False)

        lock_path = path.with_name(f"{path.name}.lock")
        handle = lock_path.open("a+b")
        try:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            _lock_file(handle)
        except BaseException:
            handle.close()
            raise
        _HELD_LEDGER_LOCKS[key] = _HeldLedgerLock(owner_id, handle)
        return _LedgerLockLease(key, owner_id, fresh=True)


def _lock_file(handle: BinaryIO) -> None:
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            flock = getattr(fcntl, "flock")
            lock_ex = int(getattr(fcntl, "LOCK_EX"))
            lock_nb = int(getattr(fcntl, "LOCK_NB"))
            flock(handle.fileno(), lock_ex | lock_nb)
    except (ImportError, OSError):
        raise ReservationConflictError("quota_ledger") from None


def _unlock_file(handle: BinaryIO) -> None:
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            flock = getattr(fcntl, "flock")
            lock_un = int(getattr(fcntl, "LOCK_UN"))
            flock(handle.fileno(), lock_un)
    except (ImportError, OSError):
        pass


class QuotaReservation:
    """One atomic capacity claim.

    Callers consume before writing. Failed mutations must remove any partial
    file before calling ``release`` so a later fresh scan accounts accurately.
    """

    def __init__(
        self,
        manager: "QuotaManager",
        reservation_id: str,
        pool: str,
        *,
        reserved_bytes: int,
        item_limit_bytes: int | None,
        conflict_key: str | None,
    ) -> None:
        self._manager = manager
        self.reservation_id = reservation_id
        self.pool = pool
        self.reserved_bytes = reserved_bytes
        self.consumed_bytes = 0
        self.item_limit_bytes = item_limit_bytes
        self.conflict_key = conflict_key
        self._active = True

    @property
    def active(self) -> bool:
        return self._active

    def consume(self, byte_count: int) -> int:
        return self._manager._consume(self, byte_count)

    def commit(self, *, persisted_bytes: int | None = None) -> None:
        self._manager._commit(self, persisted_bytes=persisted_bytes)

    def release(self) -> None:
        self._manager._release(self)

    def __enter__(self) -> "QuotaReservation":
        if not self.active:
            raise ReservationConflictError(self.pool)
        return self

    def __exit__(self, exc_type, _exc, _traceback) -> None:
        if not self.active:
            return
        if exc_type is None:
            self.commit()
        else:
            self.release()


class QuotaManager:
    """Atomic logical reservations over freshly measured disk usage.

    An optional SQLite ledger coordinates independent storage/manager objects
    and survives a process crash. A new process owner discards abandoned
    capacity claims; the next reservation then recounts any partial files that
    actually remain on disk.
    """

    def __init__(
        self,
        *,
        pools: dict[str, QuotaPool],
        minimum_free_bytes: int,
        minimum_free_fraction: float = 0,
        ledger_path: Path | str | None = None,
        owner_id: str | None = None,
    ) -> None:
        if not pools:
            raise ValueError("at least one quota pool is required")
        if (
            type(minimum_free_bytes) is not int
            or not 0 <= minimum_free_bytes <= _MAX_SQLITE_INTEGER
        ):
            raise ValueError("minimum free bytes must not be negative")
        if (
            isinstance(minimum_free_fraction, bool)
            or not isinstance(minimum_free_fraction, (int, float))
            or not isfinite(minimum_free_fraction)
            or not 0 <= minimum_free_fraction < 1
        ):
            raise ValueError("minimum free fraction must be in [0, 1)")
        self._pools = dict(pools)
        self._minimum_free_bytes = minimum_free_bytes
        self._minimum_free_fraction = minimum_free_fraction
        self._lock = RLock()
        self._closed = False
        self._owner_id = owner_id or _PROCESS_INSTANCE_ID
        if not self._owner_id.strip() or len(self._owner_id) > 200:
            raise ValueError("quota owner id is invalid")
        self._ledger_lock: _LedgerLockLease | None = None
        self._connection: sqlite3.Connection
        database = ":memory:"
        if ledger_path is not None:
            resolved_ledger = Path(ledger_path)
            if not resolved_ledger.is_absolute():
                resolved_ledger = resolved_ledger.resolve()
            self._ledger_lock = _acquire_ledger_lock(resolved_ledger, self._owner_id)
            database = os.fspath(resolved_ledger)
        try:
            self._connection = sqlite3.connect(
                database,
                isolation_level=None,
                check_same_thread=False,
                timeout=30,
            )
            self._connection.execute("PRAGMA busy_timeout = 30000")
            if ledger_path is not None:
                self._connection.execute("PRAGMA journal_mode = WAL")
                self._connection.execute("PRAGMA synchronous = FULL")
            self._initialize_ledger(
                recover_abandoned=(
                    self._ledger_lock is None or self._ledger_lock.fresh
                )
            )
        except BaseException:
            if self._ledger_lock is not None:
                self._ledger_lock.close()
            raise

    @property
    def active_reservations(self) -> int:
        with self._lock:
            self._require_open()
            row = self._connection.execute(
                "SELECT COUNT(*) FROM quota_reservations"
            ).fetchone()
            assert row is not None
            return int(row[0])

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._connection.close()
            if self._ledger_lock is not None:
                self._ledger_lock.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    def reserve(
        self,
        pool: str,
        *,
        amount_bytes: int = 0,
        item_limit_bytes: int | None = None,
        conflict_key: str | None = None,
    ) -> QuotaReservation:
        if (
            type(amount_bytes) is not int
            or not 0 <= amount_bytes <= _MAX_SQLITE_INTEGER
        ):
            raise ValueError("reservation amount must not be negative")
        if item_limit_bytes is not None and (
            type(item_limit_bytes) is not int
            or not 0 < item_limit_bytes <= _MAX_SQLITE_INTEGER
        ):
            raise ValueError("item limit must be positive")
        if item_limit_bytes is not None and amount_bytes > item_limit_bytes:
            raise QuotaExceededError(pool)
        with self._lock:
            self._pool(pool)
            filesystem_id = self._filesystem_id(pool)
            reservation_id = f"qres_{uuid4().hex}"
            with self._transaction():
                if conflict_key is not None:
                    conflict = self._connection.execute(
                        """
                        SELECT 1 FROM quota_reservations
                        WHERE pool = ? AND conflict_key = ?
                        """,
                        (pool, conflict_key),
                    ).fetchone()
                    if conflict is not None:
                        raise ReservationConflictError(pool)
                self._refresh_if_idle(pool, filesystem_id)
                self._ensure_growth(pool, filesystem_id, amount_bytes)
                baseline_row = self._connection.execute(
                    """
                    SELECT baseline_usage_bytes FROM quota_pool_state
                    WHERE pool = ?
                    """,
                    (pool,),
                ).fetchone()
                if baseline_row is None:
                    raise ReservationConflictError(pool)
                try:
                    self._connection.execute(
                        """
                        INSERT INTO quota_reservations (
                            reservation_id, pool, reserved_bytes, consumed_bytes,
                            item_limit_bytes, conflict_key, owner_id, filesystem_id,
                            observed_baseline_bytes
                        ) VALUES (?, ?, ?, 0, ?, ?, ?, ?, ?)
                        """,
                        (
                            reservation_id,
                            pool,
                            amount_bytes,
                            item_limit_bytes,
                            conflict_key,
                            self._owner_id,
                            filesystem_id,
                            int(baseline_row[0]),
                        ),
                    )
                except sqlite3.IntegrityError:
                    raise ReservationConflictError(pool) from None
            return QuotaReservation(
                self,
                reservation_id,
                pool,
                reserved_bytes=amount_bytes,
                item_limit_bytes=item_limit_bytes,
                conflict_key=conflict_key,
            )

    def check(self, pool: str, *, additional_bytes: int = 0) -> None:
        if (
            type(additional_bytes) is not int
            or not 0 <= additional_bytes <= _MAX_SQLITE_INTEGER
        ):
            raise ValueError("additional bytes must not be negative")
        with self._lock:
            self._pool(pool)
            filesystem_id = self._filesystem_id(pool)
            with self._transaction():
                self._refresh_if_idle(pool, filesystem_id)
                self._ensure_growth(pool, filesystem_id, additional_bytes)

    def observe_growth(self, reservation: QuotaReservation) -> int:
        """Account for writes performed outside Bridge-owned output streams."""

        with self._lock:
            with self._transaction():
                row = self._require_active(reservation)
                reserved_bytes, consumed_bytes, item_limit, filesystem_id = row
                baseline_row = self._connection.execute(
                    """
                    SELECT observed_baseline_bytes FROM quota_reservations
                    WHERE reservation_id = ?
                    """,
                    (reservation.reservation_id,),
                ).fetchone()
                if baseline_row is None:
                    raise ReservationConflictError(reservation.pool)
                current_usage = self._pool(reservation.pool).usage_bytes()
                if (
                    type(current_usage) is not int
                    or not 0 <= current_usage <= _MAX_SQLITE_INTEGER
                ):
                    raise ReservationConflictError(reservation.pool)
                observed = max(0, current_usage - int(baseline_row[0]))
                consumed = max(consumed_bytes, observed)
                if item_limit is not None and consumed > item_limit:
                    raise QuotaExceededError(reservation.pool)
                growth = max(0, consumed - reserved_bytes)
                self._ensure_growth(
                    reservation.pool,
                    filesystem_id,
                    growth,
                    observed_consumed_growth=growth,
                )
                reserved_bytes += growth
                self._connection.execute(
                    """
                    UPDATE quota_reservations
                    SET reserved_bytes = ?, consumed_bytes = ?
                    WHERE reservation_id = ?
                    """,
                    (reserved_bytes, consumed, reservation.reservation_id),
                )
            reservation.reserved_bytes = reserved_bytes
            reservation.consumed_bytes = consumed
            return consumed

    def _pool(self, pool: str) -> QuotaPool:
        try:
            return self._pools[pool]
        except KeyError:
            raise ValueError("quota pool is invalid") from None

    def _filesystem_id(self, pool: str) -> str:
        provider = self._pool(pool).filesystem_id
        filesystem_id = f"pool:{pool}" if provider is None else provider()
        if (
            not isinstance(filesystem_id, str)
            or not filesystem_id.strip()
            or filesystem_id != filesystem_id.strip()
            or len(filesystem_id) > 200
        ):
            raise ReservationConflictError(pool)
        return filesystem_id

    def _refresh_if_idle(self, pool: str, filesystem_id: str) -> None:
        row = self._connection.execute(
            "SELECT COUNT(*) FROM quota_reservations WHERE pool = ?",
            (pool,),
        ).fetchone()
        assert row is not None
        if int(row[0]):
            state = self._connection.execute(
                "SELECT 1 FROM quota_pool_state WHERE pool = ?",
                (pool,),
            ).fetchone()
            if state is None:
                raise ReservationConflictError(pool)
            return
        definition = self._pool(pool)
        usage = definition.usage_bytes()
        if type(usage) is not int or not 0 <= usage <= _MAX_SQLITE_INTEGER:
            raise ReservationConflictError(pool)
        self._connection.execute(
            """
            INSERT INTO quota_pool_state (
                pool, baseline_usage_bytes, baseline_free_bytes
            ) VALUES (?, ?, ?)
            ON CONFLICT(pool) DO UPDATE SET
                baseline_usage_bytes = excluded.baseline_usage_bytes,
                baseline_free_bytes = excluded.baseline_free_bytes
            """,
            (pool, usage, 0),
        )

        filesystem_rows = self._connection.execute(
            """
            SELECT COUNT(*) FROM quota_reservations
            WHERE filesystem_id = ?
            """,
            (filesystem_id,),
        ).fetchone()
        assert filesystem_rows is not None
        if int(filesystem_rows[0]):
            state = self._connection.execute(
                """
                SELECT 1 FROM quota_filesystem_state
                WHERE filesystem_id = ?
                """,
                (filesystem_id,),
            ).fetchone()
            if state is None:
                raise ReservationConflictError(pool)
            return

        free_bytes, total_bytes = self._filesystem_space(pool)
        self._connection.execute(
            """
            INSERT INTO quota_filesystem_state (
                filesystem_id, baseline_free_bytes, total_bytes
            ) VALUES (?, ?, ?)
            ON CONFLICT(filesystem_id) DO UPDATE SET
                baseline_free_bytes = excluded.baseline_free_bytes,
                total_bytes = excluded.total_bytes
            """,
            (filesystem_id, free_bytes, total_bytes),
        )

    def _filesystem_space(self, pool: str) -> tuple[int, int]:
        definition = self._pool(pool)
        free_bytes = definition.free_bytes()
        if type(free_bytes) is not int or not 0 <= free_bytes <= _MAX_SQLITE_INTEGER:
            raise ReservationConflictError(pool)
        if definition.total_bytes is None and self._minimum_free_fraction > 0:
            raise ReservationConflictError(pool)
        total_bytes = (
            free_bytes + definition.usage_bytes()
            if definition.total_bytes is None
            else definition.total_bytes()
        )
        if (
            type(total_bytes) is not int
            or not free_bytes <= total_bytes <= _MAX_SQLITE_INTEGER
        ):
            raise ReservationConflictError(pool)
        return free_bytes, total_bytes

    def _free_floor(self, total_bytes: int) -> int:
        fraction_floor = int(total_bytes * self._minimum_free_fraction)
        return max(self._minimum_free_bytes, fraction_floor)

    def _ensure_growth(
        self,
        pool: str,
        filesystem_id: str,
        growth_bytes: int,
        *,
        observed_consumed_growth: int = 0,
    ) -> None:
        state = self._connection.execute(
            """
            SELECT baseline_usage_bytes, baseline_free_bytes
            FROM quota_pool_state WHERE pool = ?
            """,
            (pool,),
        ).fetchone()
        if state is None:
            raise ReservationConflictError(pool)
        baseline_usage_bytes = int(state[0])
        reserved_row = self._connection.execute(
            """
            SELECT COALESCE(SUM(reserved_bytes), 0)
            FROM quota_reservations WHERE pool = ?
            """,
            (pool,),
        ).fetchone()
        assert reserved_row is not None
        reserved = int(reserved_row[0])
        definition = self._pool(pool)
        if baseline_usage_bytes + reserved + growth_bytes > definition.limit_bytes:
            raise QuotaExceededError(pool)

        filesystem_state = self._connection.execute(
            """
            SELECT baseline_free_bytes, total_bytes
            FROM quota_filesystem_state WHERE filesystem_id = ?
            """,
            (filesystem_id,),
        ).fetchone()
        if filesystem_state is None:
            raise ReservationConflictError(pool)
        baseline_free_bytes = int(filesystem_state[0])
        total_bytes = int(filesystem_state[1])
        filesystem_reserved_row = self._connection.execute(
            """
            SELECT
                COALESCE(SUM(reserved_bytes), 0),
                COALESCE(SUM(consumed_bytes), 0)
            FROM quota_reservations WHERE filesystem_id = ?
            """,
            (filesystem_id,),
        ).fetchone()
        assert filesystem_reserved_row is not None
        filesystem_reserved = int(filesystem_reserved_row[0])
        filesystem_consumed = int(filesystem_reserved_row[1])
        current_free, current_total = self._filesystem_space(pool)
        if current_total != total_bytes:
            raise ReservationConflictError(pool)
        effective_free = min(
            baseline_free_bytes,
            min(
                _MAX_SQLITE_INTEGER,
                current_free + filesystem_consumed + observed_consumed_growth,
            ),
        )
        if effective_free != baseline_free_bytes:
            self._connection.execute(
                """
                UPDATE quota_filesystem_state SET baseline_free_bytes = ?
                WHERE filesystem_id = ?
                """,
                (effective_free, filesystem_id),
            )
        if (
            effective_free - filesystem_reserved - growth_bytes
            < self._free_floor(total_bytes)
        ):
            raise QuotaExceededError(pool)

    def _consume(self, reservation: QuotaReservation, byte_count: int) -> int:
        if (
            type(byte_count) is not int
            or not 0 <= byte_count <= _MAX_SQLITE_INTEGER
        ):
            raise ValueError("consumed bytes must not be negative")
        with self._lock:
            with self._transaction():
                row = self._require_active(reservation)
                reserved_bytes = int(row[0])
                consumed = int(row[1]) + byte_count
                item_limit = row[2]
                filesystem_id = str(row[3])
                if consumed > _MAX_SQLITE_INTEGER:
                    raise QuotaExceededError(reservation.pool)
                if item_limit is not None and consumed > int(item_limit):
                    raise QuotaExceededError(reservation.pool)
                growth = max(0, consumed - reserved_bytes)
                self._ensure_growth(reservation.pool, filesystem_id, growth)
                reserved_bytes += growth
                self._connection.execute(
                    """
                    UPDATE quota_reservations
                    SET reserved_bytes = ?, consumed_bytes = ?
                    WHERE reservation_id = ?
                    """,
                    (reserved_bytes, consumed, reservation.reservation_id),
                )
            reservation.reserved_bytes = reserved_bytes
            reservation.consumed_bytes = consumed
            return consumed

    def _commit(
        self,
        reservation: QuotaReservation,
        *,
        persisted_bytes: int | None,
    ) -> None:
        with self._lock:
            with self._transaction():
                row = self._require_active(reservation)
                reserved_bytes = int(row[0])
                consumed_bytes = int(row[1])
                item_limit = row[2]
                filesystem_id = str(row[3])
                persisted = consumed_bytes if persisted_bytes is None else persisted_bytes
                if (
                    type(persisted) is not int
                    or not 0 <= persisted <= _MAX_SQLITE_INTEGER
                ):
                    raise ValueError("persisted bytes must not be negative")
                if item_limit is not None and persisted > int(item_limit):
                    raise QuotaExceededError(reservation.pool)
                growth = max(0, persisted - reserved_bytes)
                self._ensure_growth(reservation.pool, filesystem_id, growth)
                self._connection.execute(
                    """
                    UPDATE quota_pool_state SET
                        baseline_usage_bytes = baseline_usage_bytes + ?,
                        baseline_free_bytes = MAX(0, baseline_free_bytes - ?)
                    WHERE pool = ?
                    """,
                    (persisted, persisted, reservation.pool),
                )
                self._connection.execute(
                    """
                    UPDATE quota_filesystem_state SET
                        baseline_free_bytes = MAX(0, baseline_free_bytes - ?)
                    WHERE filesystem_id = ?
                    """,
                    (persisted, filesystem_id),
                )
                self._finish(reservation)
            reservation._active = False

    def _release(self, reservation: QuotaReservation) -> None:
        with self._lock:
            if not reservation.active:
                return
            with self._transaction():
                self._require_active(reservation)
                self._finish(reservation)
            reservation._active = False

    def _require_active(
        self,
        reservation: QuotaReservation,
    ) -> tuple[int, int, int | None, str]:
        if not reservation.active:
            raise ReservationConflictError(reservation.pool)
        row = self._connection.execute(
            """
            SELECT reserved_bytes, consumed_bytes, item_limit_bytes, filesystem_id
            FROM quota_reservations
            WHERE reservation_id = ? AND pool = ? AND owner_id = ?
            """,
            (reservation.reservation_id, reservation.pool, self._owner_id),
        ).fetchone()
        if row is None:
            raise ReservationConflictError(reservation.pool)
        item_limit = None if row[2] is None else int(row[2])
        return int(row[0]), int(row[1]), item_limit, str(row[3])

    def _finish(self, reservation: QuotaReservation) -> None:
        deleted = self._connection.execute(
            "DELETE FROM quota_reservations WHERE reservation_id = ?",
            (reservation.reservation_id,),
        ).rowcount
        if deleted != 1:
            raise ReservationConflictError(reservation.pool)

    def _initialize_ledger(self, *, recover_abandoned: bool) -> None:
        with self._lock:
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS quota_pool_state (
                    pool TEXT PRIMARY KEY,
                    baseline_usage_bytes INTEGER NOT NULL,
                    baseline_free_bytes INTEGER NOT NULL
                )
                """
            )
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS quota_filesystem_state (
                    filesystem_id TEXT PRIMARY KEY,
                    baseline_free_bytes INTEGER NOT NULL,
                    total_bytes INTEGER NOT NULL
                )
                """
            )
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS quota_reservations (
                    reservation_id TEXT PRIMARY KEY,
                    pool TEXT NOT NULL,
                    reserved_bytes INTEGER NOT NULL,
                    consumed_bytes INTEGER NOT NULL,
                    item_limit_bytes INTEGER,
                    conflict_key TEXT,
                    owner_id TEXT NOT NULL,
                    filesystem_id TEXT NOT NULL,
                    observed_baseline_bytes INTEGER NOT NULL
                )
                """
            )
            columns = {
                str(row[1])
                for row in self._connection.execute(
                    "PRAGMA table_info(quota_reservations)"
                )
            }
            if "filesystem_id" not in columns:
                self._connection.execute(
                    "ALTER TABLE quota_reservations ADD COLUMN filesystem_id TEXT"
                )
            if "observed_baseline_bytes" not in columns:
                self._connection.execute(
                    """
                    ALTER TABLE quota_reservations
                    ADD COLUMN observed_baseline_bytes INTEGER
                    """
                )
                self._connection.execute(
                    """
                    UPDATE quota_reservations SET observed_baseline_bytes = 0
                    WHERE observed_baseline_bytes IS NULL
                    """
                )
                self._connection.execute(
                    """
                    UPDATE quota_reservations
                    SET filesystem_id = 'pool:' || pool
                    WHERE filesystem_id IS NULL
                    """
                )
            self._connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS
                    quota_reservation_conflict
                ON quota_reservations(pool, conflict_key)
                WHERE conflict_key IS NOT NULL
                """
            )
            self._connection.execute(
                """
                CREATE INDEX IF NOT EXISTS quota_reservation_filesystem
                ON quota_reservations(filesystem_id)
                """
            )
            with self._transaction():
                if recover_abandoned:
                    self._connection.execute("DELETE FROM quota_reservations")
                else:
                    foreign_owner = self._connection.execute(
                        """
                        SELECT 1 FROM quota_reservations
                        WHERE owner_id <> ? LIMIT 1
                        """,
                        (self._owner_id,),
                    ).fetchone()
                    if foreign_owner is not None:
                        raise ReservationConflictError("quota_ledger")
                self._connection.execute(
                    """
                    DELETE FROM quota_pool_state
                    WHERE NOT EXISTS (
                        SELECT 1 FROM quota_reservations
                        WHERE quota_reservations.pool = quota_pool_state.pool
                    )
                    """
                )
                self._connection.execute(
                    """
                    DELETE FROM quota_filesystem_state
                    WHERE NOT EXISTS (
                        SELECT 1 FROM quota_reservations
                        WHERE quota_reservations.filesystem_id =
                            quota_filesystem_state.filesystem_id
                    )
                    """
                )

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        self._require_open()
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            yield
            self._connection.commit()
        except BaseException:
            try:
                self._connection.rollback()
            except (OSError, sqlite3.Error):
                pass
            raise

    def _require_open(self) -> None:
        if self._closed:
            raise ReservationConflictError("quota_ledger")


class StreamingByteCounter:
    def __init__(self, *, limit_bytes: int, resource: str) -> None:
        if (
            type(limit_bytes) is not int
            or not 0 < limit_bytes <= _MAX_SQLITE_INTEGER
        ):
            raise ValueError("streaming limit must be positive")
        self.limit_bytes = limit_bytes
        self.resource = resource
        self.consumed_bytes = 0

    @property
    def remaining_bytes(self) -> int:
        return self.limit_bytes - self.consumed_bytes

    def checkpoint(self) -> int:
        return self.consumed_bytes

    def _rollback_to(self, checkpoint: int) -> None:
        if (
            type(checkpoint) is not int
            or not 0 <= checkpoint <= self.consumed_bytes
        ):
            raise ReservationConflictError(self.resource)
        self.consumed_bytes = checkpoint

    def consume(self, byte_count: int) -> int:
        if (
            type(byte_count) is not int
            or not 0 <= byte_count <= _MAX_SQLITE_INTEGER
        ):
            raise ValueError("consumed bytes must not be negative")
        updated = self.consumed_bytes + byte_count
        if updated > self.limit_bytes:
            raise QuotaExceededError(self.resource)
        self.consumed_bytes = updated
        return updated


@dataclass(frozen=True, slots=True)
class SafeArchiveEntry:
    name: str
    expanded_bytes: int
    compressed_bytes: int
    is_directory: bool


def inspect_archive(
    archive: ZipFile,
    limits: ResourceLimits,
) -> tuple[SafeArchiveEntry, ...]:
    """Validate a complete ZIP manifest before a caller mutates storage."""

    infos = archive.infolist()
    if len(infos) > limits.max_archive_entries:
        raise QuotaExceededError("archive_entries")

    expanded = StreamingByteCounter(
        limit_bytes=limits.max_archive_expanded_bytes,
        resource="archive_expanded",
    )
    total_compressed = 0
    seen: set[tuple[str, ...]] = set()
    file_paths: set[tuple[str, ...]] = set()
    parent_prefixes: set[tuple[str, ...]] = set()
    result: list[SafeArchiveEntry] = []
    for info in infos:
        name = _validate_archive_member(info)
        canonical = tuple(
            unicodedata.normalize("NFC", part).casefold()
            for part in name.rstrip("/").split("/")
        )
        is_directory = info.is_dir()
        if canonical in seen:
            raise QuotaExceededError("archive_entry")
        for index in range(1, len(canonical)):
            if canonical[:index] in file_paths:
                raise QuotaExceededError("archive_entry")
        if not is_directory and canonical in parent_prefixes:
            raise QuotaExceededError("archive_entry")
        seen.add(canonical)
        if not is_directory:
            file_paths.add(canonical)
        for index in range(1, len(canonical)):
            parent_prefixes.add(canonical[:index])
        expanded.consume(info.file_size)
        total_compressed += info.compress_size
        if _expansion_ratio(info.file_size, info.compress_size) > limits.max_archive_expansion_ratio:
            raise QuotaExceededError("archive_ratio")
        result.append(
            SafeArchiveEntry(
                name=name,
                expanded_bytes=info.file_size,
                compressed_bytes=info.compress_size,
                is_directory=is_directory,
            )
        )

    if _expansion_ratio(expanded.consumed_bytes, total_compressed) > limits.max_archive_expansion_ratio:
        raise QuotaExceededError("archive_ratio")
    return tuple(result)


@contextmanager
def open_inspected_archive(
    source: BinaryIO,
    limits: ResourceLimits,
    *,
    max_container_bytes: int | None = None,
) -> Iterator[tuple[ZipFile, tuple[SafeArchiveEntry, ...]]]:
    """Bound ZIP metadata before constructing ``ZipFile`` and validate it."""

    _preflight_archive_container(
        source,
        limits,
        max_container_bytes=(
            limits.max_upload_file_bytes
            if max_container_bytes is None
            else max_container_bytes
        ),
    )
    try:
        source.seek(0)
        archive = ZipFile(source, mode="r")
    except (BadZipFile, OSError, RuntimeError, ValueError):
        raise QuotaExceededError("archive_entry") from None
    try:
        try:
            entries = inspect_archive(archive, limits)
        except (BadZipFile, NotImplementedError):
            raise QuotaExceededError("archive_entry") from None
        yield archive, entries
    finally:
        archive.close()


def copy_archive_entry(
    archive: ZipFile,
    entry: SafeArchiveEntry,
    destination: BinaryIO,
    *,
    limits: ResourceLimits,
    aggregate_counter: StreamingByteCounter | None = None,
) -> int:
    """Copy one inspected member with observed-byte and rollback guarantees."""

    if entry.is_directory:
        return 0
    try:
        rollback_position = destination.tell()
        destination.seek(rollback_position)
    except (AttributeError, OSError, ValueError):
        raise ReservationConflictError("archive_destination") from None
    observed = 0
    total_counter = aggregate_counter or StreamingByteCounter(
        limit_bytes=limits.max_archive_expanded_bytes,
        resource="archive_expanded",
    )
    counter_checkpoint = total_counter.checkpoint()
    try:
        info = archive.getinfo(entry.name)
        with archive.open(info, mode="r") as source:
            while True:
                chunk = source.read(MIB)
                if not chunk:
                    break
                updated = observed + len(chunk)
                if updated > entry.expanded_bytes:
                    raise QuotaExceededError("archive_expanded")
                total_counter.consume(len(chunk))
                _write_all(destination, chunk)
                observed = updated
        if observed != entry.expanded_bytes:
            raise QuotaExceededError("archive_expanded")
        return observed
    except QuotaExceededError:
        total_counter._rollback_to(counter_checkpoint)
        if not _rollback_stream(destination, rollback_position):
            raise ReservationConflictError("archive_destination") from None
        raise
    except (BadZipFile, KeyError, NotImplementedError, RuntimeError):
        total_counter._rollback_to(counter_checkpoint)
        if not _rollback_stream(destination, rollback_position):
            raise ReservationConflictError("archive_destination") from None
        raise QuotaExceededError("archive_entry") from None
    except BaseException:
        total_counter._rollback_to(counter_checkpoint)
        if not _rollback_stream(destination, rollback_position):
            raise ReservationConflictError("archive_destination") from None
        raise


def archive_container_detected(source: BinaryIO) -> bool:
    """Detect a bounded EOCD record without trusting a filename or prefix."""

    try:
        original_position = source.tell()
        source.seek(0, os.SEEK_END)
        container_bytes = source.tell()
        if not isinstance(container_bytes, int) or container_bytes < 22:
            return False
        tail_size = min(container_bytes, 22 + 65_535)
        source.seek(container_bytes - tail_size)
        tail = source.read(tail_size)
        return isinstance(tail, bytes) and _find_eocd_index(tail) >= 0
    except (AttributeError, OSError, ValueError):
        return False
    finally:
        try:
            source.seek(original_position)
        except (AttributeError, OSError, UnboundLocalError, ValueError):
            pass


def _preflight_archive_container(
    source: BinaryIO,
    limits: ResourceLimits,
    *,
    max_container_bytes: int,
) -> None:
    if (
        type(max_container_bytes) is not int
        or not 0 < max_container_bytes <= _MAX_SQLITE_INTEGER
    ):
        raise ValueError("archive container limit must be positive")
    try:
        source.seek(0, os.SEEK_END)
        container_bytes = source.tell()
        if (
            type(container_bytes) is not int
            or container_bytes < 22
            or container_bytes > max_container_bytes
        ):
            raise QuotaExceededError("archive_container")
        tail_size = min(container_bytes, 22 + 65_535)
        source.seek(container_bytes - tail_size)
        tail = source.read(tail_size)
        if not isinstance(tail, bytes) or len(tail) != tail_size:
            raise QuotaExceededError("archive_entry")
    except QuotaExceededError:
        raise
    except (AttributeError, OSError, ValueError):
        raise QuotaExceededError("archive_entry") from None

    eocd_index = _find_eocd_index(tail)
    if eocd_index < 0:
        raise QuotaExceededError("archive_entry")

    (
        _signature,
        disk_number,
        directory_disk,
        entries_on_disk,
        entries_total,
        directory_bytes,
        directory_offset,
        _comment_bytes,
    ) = struct.unpack_from("<4s4H2LH", tail, eocd_index)
    if entries_total > limits.max_archive_entries:
        raise QuotaExceededError("archive_entries")
    if (
        disk_number != 0
        or directory_disk != 0
        or entries_on_disk != entries_total
        or entries_total == 0xFFFF
        or directory_bytes == 0xFFFFFFFF
        or directory_offset == 0xFFFFFFFF
    ):
        raise QuotaExceededError("archive_entry")
    if directory_bytes > limits.max_archive_metadata_bytes:
        raise QuotaExceededError("archive_metadata")
    eocd_absolute = container_bytes - tail_size + eocd_index
    prefix_bytes = eocd_absolute - (directory_offset + directory_bytes)
    if prefix_bytes < 0:
        raise QuotaExceededError("archive_entry")
    try:
        source.seek(prefix_bytes + directory_offset)
        directory = source.read(directory_bytes)
    except (AttributeError, OSError, ValueError):
        raise QuotaExceededError("archive_entry") from None
    if not isinstance(directory, bytes) or len(directory) != directory_bytes:
        raise QuotaExceededError("archive_entry")
    actual_entries = _count_central_directory_records(directory, limits)
    if actual_entries != entries_total:
        raise QuotaExceededError("archive_entry")


def _find_eocd_index(tail: bytes) -> int:
    """Find the last complete EOCD record, allowing bounded trailing data.

    ``zipfile.ZipFile`` accepts archives with bytes after the EOCD record.  The
    upload classifier must recognize the same shape or a disguised archive can
    avoid the archive safety preflight merely by appending a suffix.
    """

    signature = b"PK\x05\x06"
    cursor = len(tail)
    while cursor:
        candidate = tail.rfind(signature, 0, cursor)
        if candidate < 0:
            return -1
        if candidate + 22 <= len(tail):
            comment_bytes = struct.unpack_from("<H", tail, candidate + 20)[0]
            if candidate + 22 + comment_bytes <= len(tail):
                return candidate
        cursor = candidate
    return -1


def _count_central_directory_records(
    directory: bytes,
    limits: ResourceLimits,
) -> int:
    offset = 0
    entries = 0
    while offset < len(directory):
        if offset + 46 > len(directory) or directory[offset : offset + 4] != b"PK\x01\x02":
            raise QuotaExceededError("archive_entry")
        filename_bytes, extra_bytes, comment_bytes = struct.unpack_from(
            "<3H",
            directory,
            offset + 28,
        )
        record_bytes = 46 + filename_bytes + extra_bytes + comment_bytes
        if record_bytes <= 46 or offset + record_bytes > len(directory):
            raise QuotaExceededError("archive_entry")
        entries += 1
        if entries > limits.max_archive_entries:
            raise QuotaExceededError("archive_entries")
        offset += record_bytes
    return entries


def _write_all(destination: BinaryIO, content: bytes) -> None:
    view = memoryview(content)
    while view:
        written = destination.write(view)
        if not isinstance(written, int) or written <= 0:
            raise OSError("archive destination write failed")
        view = view[written:]


def _rollback_stream(destination: BinaryIO, position: int) -> bool:
    try:
        destination.seek(position)
        destination.truncate(position)
        return True
    except (AttributeError, OSError, ValueError):
        return False


def _validate_archive_member(info: ZipInfo) -> str:
    name = info.filename
    if (
        not name
        or "\\" in name
        or name.startswith("/")
        or "\x00" in name
        or any(unicodedata.category(character).startswith("C") for character in name)
    ):
        raise QuotaExceededError("archive_entry")
    portable = name[:-1] if name.endswith("/") else name
    if not portable or ":" in portable:
        raise QuotaExceededError("archive_entry")
    raw_parts = portable.split("/")
    if any(part in {"", ".", ".."} for part in raw_parts):
        raise QuotaExceededError("archive_entry")
    try:
        portable = normalize_portable_relative_path(portable)
    except WorkspaceInputError:
        raise QuotaExceededError("archive_entry") from None
    path = PurePosixPath(portable)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise QuotaExceededError("archive_entry")
    if info.flag_bits & 0x1:
        raise QuotaExceededError("archive_entry")
    if info.compress_type not in {ZIP_STORED, ZIP_DEFLATED}:
        raise QuotaExceededError("archive_entry")

    mode = (info.external_attr >> 16) & 0xFFFF
    file_type = stat.S_IFMT(mode)
    allowed_types = {0, stat.S_IFREG}
    if info.is_dir():
        allowed_types.add(stat.S_IFDIR)
    if file_type not in allowed_types:
        raise QuotaExceededError("archive_entry")
    return name


def _expansion_ratio(expanded_bytes: int, compressed_bytes: int) -> float:
    if expanded_bytes <= 0:
        return 0
    return expanded_bytes / max(compressed_bytes, 1)
