import importlib.metadata
import platform
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from .models import BridgeDiagnosticsRecord, DiagnosticToolRecord

if TYPE_CHECKING:
    from .storage import BridgeStorage

FALLBACK_BRIDGE_VERSION = "0.4.12"
DEFAULT_TOOL_NAMES = ("python", "git", "node", "npm", "rg", "uv", "gh", "codex", "fd", "jq", "7z")


class BridgeDiagnosticsProbe:
    def __init__(
        self,
        storage: "BridgeStorage",
        *,
        codex_command: str = "codex",
        tool_names: tuple[str, ...] = DEFAULT_TOOL_NAMES,
        cache_seconds: int = 20,
    ) -> None:
        self.storage = storage
        self.codex_command = codex_command
        self.tool_names = tool_names
        self.cache_seconds = cache_seconds
        self.started_at = datetime.now(UTC)
        self._last_probe_at = 0.0
        self._cached: BridgeDiagnosticsRecord | None = None

    def probe(self) -> BridgeDiagnosticsRecord:
        now = time.monotonic()
        if self._cached is not None and now - self._last_probe_at < self.cache_seconds:
            return self._with_live_fields(self._cached)

        repo_root = self._repo_root()
        tools = [self._tool_status(name) for name in self.tool_names]
        record = BridgeDiagnosticsRecord(
            bridge_version=self._bridge_version(),
            git_commit=self._git_value(repo_root, "rev-parse", "--short", "HEAD"),
            git_branch=self._git_value(repo_root, "rev-parse", "--abbrev-ref", "HEAD"),
            python_version=platform.python_version(),
            python_executable=sys.executable,
            platform=platform.platform(),
            codex_cli_version=self._command_version(self.codex_command),
            service_started_at=self.started_at.isoformat().replace("+00:00", "Z"),
            service_uptime_seconds=round((datetime.now(UTC) - self.started_at).total_seconds(), 1),
            last_error=self._last_thread_error(),
            tools=tools,
        )
        self._last_probe_at = now
        self._cached = record
        return record

    def _with_live_fields(self, record: BridgeDiagnosticsRecord) -> BridgeDiagnosticsRecord:
        updated = record.model_copy(deep=True)
        updated.service_uptime_seconds = round((datetime.now(UTC) - self.started_at).total_seconds(), 1)
        updated.last_error = self._last_thread_error()
        return updated

    def _bridge_version(self) -> str:
        try:
            return importlib.metadata.version("codex-bridge-service")
        except importlib.metadata.PackageNotFoundError:
            return FALLBACK_BRIDGE_VERSION

    def _repo_root(self) -> Path:
        return Path(__file__).resolve().parents[3]

    def _git_value(self, repo_root: Path, *args: str) -> str | None:
        try:
            completed = subprocess.run(
                ["git", "-C", str(repo_root), *args],
                check=False,
                capture_output=True,
                text=True,
                timeout=3,
            )
        except Exception:
            return None
        value = (completed.stdout or "").strip()
        return value or None

    def _tool_status(self, name: str) -> DiagnosticToolRecord:
        path = shutil.which(name)
        if not path:
            return DiagnosticToolRecord(name=name, available=False)
        return DiagnosticToolRecord(
            name=name,
            available=True,
            path=path,
            version=self._command_version(path),
        )

    def _command_version(self, command: str) -> str | None:
        try:
            completed = subprocess.run(
                [command, "--version"],
                check=False,
                capture_output=True,
                text=True,
                timeout=4,
            )
        except Exception:
            return None
        output = (completed.stdout or completed.stderr or "").strip().splitlines()
        return output[0][:220] if output else None

    def _last_thread_error(self) -> str | None:
        errored_threads = [
            thread
            for thread in self.storage.list_threads(include_archived=True)
            if thread.last_error
        ]
        if not errored_threads:
            return None
        latest = max(errored_threads, key=lambda thread: thread.updated_at or thread.created_at or "")
        return latest.last_error
