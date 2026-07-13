import platform
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from . import __version__
from .build_info import BuildInfo
from .codex_process import codex_subprocess_environment
from .models import BridgeDiagnosticsRecord, DiagnosticToolRecord, RuntimeProfile

if TYPE_CHECKING:
    from .storage import BridgeStorage

DEFAULT_TOOL_NAMES = ("python", "git", "node", "npm", "rg", "uv", "gh", "codex", "fd", "jq", "7z")


class BridgeDiagnosticsProbe:
    def __init__(
        self,
        storage: "BridgeStorage",
        *,
        build_info: BuildInfo | None = None,
        codex_command: str = "codex",
        codex_home: Path | str | None = None,
        tool_names: tuple[str, ...] = DEFAULT_TOOL_NAMES,
        cache_seconds: int = 20,
        runtime_version_provider: Callable[[], str | None] | None = None,
        redact_paths: bool | None = None,
    ) -> None:
        self.storage = storage
        self.build_info = build_info if build_info is not None else BuildInfo()
        self.codex_command = codex_command
        self.codex_home = codex_home
        self.tool_names = tool_names
        self.cache_seconds = cache_seconds
        self.runtime_version_provider = runtime_version_provider
        self.redact_paths = (
            storage.runtime_profile is RuntimeProfile.HOME_ASSISTANT
            if redact_paths is None
            else redact_paths
        )
        self.started_at = datetime.now(UTC)
        self._last_probe_at = 0.0
        self._cached: BridgeDiagnosticsRecord | None = None

    def probe(self) -> BridgeDiagnosticsRecord:
        now = time.monotonic()
        if self._cached is not None and now - self._last_probe_at < self.cache_seconds:
            return self._with_live_fields(self._cached)

        active_codex_version = self._active_codex_version()
        bundled_codex_version = self.build_info.codex_version
        codex_version_match = (
            active_codex_version == bundled_codex_version
            if active_codex_version is not None and bundled_codex_version is not None
            else None
        )
        repo_root = None if self.redact_paths else self._repo_root()
        tools = [self._tool_status(name) for name in self.tool_names]
        record = BridgeDiagnosticsRecord(
            app_version=self.build_info.app_version,
            bridge_version=self._bridge_version(),
            bundled_codex_version=bundled_codex_version,
            active_codex_version=active_codex_version,
            codex_version_match=codex_version_match,
            image_revision=self.build_info.image_revision,
            architecture=self.build_info.architecture,
            release_lock_digest=self.build_info.release_lock_digest,
            git_commit=(
                None
                if repo_root is None
                else self._git_value(repo_root, "rev-parse", "--short", "HEAD")
            ),
            git_branch=(
                None
                if repo_root is None
                else self._git_value(repo_root, "rev-parse", "--abbrev-ref", "HEAD")
            ),
            python_version=platform.python_version(),
            python_executable=None if self.redact_paths else sys.executable,
            platform=platform.platform(),
            codex_cli_version=(
                active_codex_version
                if self.redact_paths
                else self._command_version(self.codex_command)
            ),
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
        active_codex_version = self._active_codex_version()
        updated.active_codex_version = active_codex_version
        updated.codex_version_match = (
            active_codex_version == self.build_info.codex_version
            if active_codex_version is not None
            and self.build_info.codex_version is not None
            else None
        )
        if self.redact_paths:
            updated.codex_cli_version = active_codex_version
        updated.service_uptime_seconds = round((datetime.now(UTC) - self.started_at).total_seconds(), 1)
        updated.last_error = self._last_thread_error()
        return updated

    def _bridge_version(self) -> str:
        return self.build_info.bridge_version or __version__

    def _repo_root(self) -> Path:
        return Path(__file__).resolve().parents[3]

    def _git_value(self, repo_root: Path, *args: str) -> str | None:
        try:
            completed = subprocess.run(
                ["git", "-C", str(repo_root), *args],
                check=False,
                capture_output=True,
                env=codex_subprocess_environment(self.codex_home),
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
        if self.redact_paths:
            return DiagnosticToolRecord(name=name, available=True)
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
                env=codex_subprocess_environment(self.codex_home),
                text=True,
                timeout=4,
            )
        except Exception:
            return None
        output = (completed.stdout or completed.stderr or "").strip().splitlines()
        return output[0][:220] if output else None

    def _last_thread_error(self) -> str | None:
        threads = self.storage.list_threads(include_archived=False)
        if not threads:
            return None
        latest = max(threads, key=lambda thread: thread.updated_at or thread.created_at or "")
        if latest.last_error is None:
            return None
        if self.redact_paths:
            return "A Codex run failed."
        return latest.last_error

    def _active_codex_version(self) -> str | None:
        if self.runtime_version_provider is None:
            return None
        try:
            version = self.runtime_version_provider()
        except Exception:
            return None
        return BuildInfo(codex_version=version).codex_version
