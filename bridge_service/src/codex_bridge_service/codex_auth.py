import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock, Thread
from urllib.parse import urlsplit

from .codex_process import codex_command_prefix, codex_subprocess_environment
from .models import CodexAuthStatusRecord

AUTH_EXPIRED_MESSAGE = "Codex sign-in expired. Start a new sign-in from Home Assistant."
_AUTH_FAILURE_MARKERS = (
    "401 unauthorized",
    "access token could not be refreshed",
    "refresh token was already used",
    "please log out and sign in again",
    "not authenticated",
    "authentication failed",
)
_ANSI_PATTERN = re.compile(r"\x1b\[[0-9;]*m")
_DEVICE_CODE_PATTERN = re.compile(r"\b(?=[A-Z0-9-]*\d)[A-Z0-9]{4,6}-[A-Z0-9]{4,6}\b")
_ALLOWED_LOGIN_HOSTS = frozenset(
    {"auth.openai.com", "chatgpt.com", "platform.openai.com"}
)


def is_codex_auth_failure(message: str | None) -> bool:
    if not message:
        return False
    lowered = message.lower()
    return any(marker in lowered for marker in _AUTH_FAILURE_MARKERS)


class CodexAuthManager:
    def __init__(
        self,
        codex_command: str = "codex",
        *,
        codex_home: Path | str | None = None,
    ) -> None:
        self.codex_command = codex_command
        self.codex_home = codex_home
        self._lock = Lock()
        self._process: subprocess.Popen[str] | None = None
        self._last_auth_error: str | None = None
        self._resolved_auth_error: str | None = None
        self._status = CodexAuthStatusRecord(
            state="unknown",
            message="Codex auth has not been checked by the bridge yet.",
            updated_at=self._now(),
        )

    def status(self, *, last_error: str | None = None) -> CodexAuthStatusRecord:
        with self._lock:
            current = self._status.model_copy(deep=True)
            login_is_running = self._process is not None and self._process.poll() is None
            stale_auth_error = (
                last_error is not None
                and self._resolved_auth_error is not None
                and last_error == self._resolved_auth_error
            )
        if login_is_running:
            return current
        if is_codex_auth_failure(last_error) and not stale_auth_error:
            with self._lock:
                self._last_auth_error = last_error
            return CodexAuthStatusRecord(
                state="expired",
                auth_required=True,
                message=AUTH_EXPIRED_MESSAGE,
                updated_at=self._now(),
            )
        return current

    def start_device_login(self, *, force_logout: bool = False) -> CodexAuthStatusRecord:
        with self._lock:
            login_is_starting = self._status.state in {"login_starting", "login_running"}
            if login_is_starting or (self._process is not None and self._process.poll() is None):
                return self._status.model_copy(deep=True)
            self._status = CodexAuthStatusRecord(
                state="login_starting",
                auth_required=True,
                message="Starting Codex sign-in.",
                updated_at=self._now(),
            )
            worker = Thread(target=self._run_device_login, args=(force_logout,), daemon=True)
            worker.start()
            return self._status.model_copy(deep=True)

    def logout(self) -> CodexAuthStatusRecord:
        with self._lock:
            process = self._process
        if process is not None and process.poll() is None:
            process.terminate()
        completed = subprocess.run(
            [*self._command_prefix(), "logout"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
            env=codex_subprocess_environment(self.codex_home),
        )
        state = "logged_out" if completed.returncode == 0 else "logout_failed"
        message = "Codex credentials were removed." if completed.returncode == 0 else "Codex sign-out failed."
        with self._lock:
            self._process = None
            self._status = CodexAuthStatusRecord(
                state=state,
                auth_required=True,
                message=message,
                output_tail=[],
                updated_at=self._now(),
            )
            return self._status.model_copy(deep=True)

    def _run_device_login(self, force_logout: bool = False) -> None:
        output_lines: list[str] = []
        try:
            if force_logout:
                subprocess.run(
                    [*self._command_prefix(), "logout"],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    timeout=30,
                    env=codex_subprocess_environment(self.codex_home),
                )

            process = subprocess.Popen(
                [*self._command_prefix(), "login", "--device-auth"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=codex_subprocess_environment(self.codex_home),
                text=True,
                encoding="utf-8",
            )
            with self._lock:
                self._process = process
                self._status = self._status.model_copy(
                    update={
                        "state": "login_running",
                        "message": "Codex sign-in is waiting for a device code.",
                        "updated_at": self._now(),
                    }
                )

            assert process.stdout is not None
            for raw_line in process.stdout:
                line = raw_line.strip()
                if not line:
                    continue
                output_lines.append(line)
                self._update_login_output(output_lines)

            return_code = process.wait()
            with self._lock:
                self._process = None
                if return_code == 0:
                    self._resolved_auth_error = self._last_auth_error
                    self._status = self._status.model_copy(
                        update={
                            "state": "ok",
                            "auth_required": False,
                            "message": "Codex sign-in completed.",
                            "verification_uri": None,
                            "login_url": None,
                            "user_code": None,
                            "output_tail": [],
                            "updated_at": self._now(),
                        }
                    )
                else:
                    self._status = self._status.model_copy(
                        update={
                            "state": "login_failed",
                            "auth_required": True,
                            "message": "Codex sign-in did not complete.",
                            "verification_uri": None,
                            "login_url": None,
                            "user_code": None,
                            "output_tail": [],
                            "updated_at": self._now(),
                        }
                    )
        except Exception:
            with self._lock:
                self._process = None
                self._status = CodexAuthStatusRecord(
                    state="login_failed",
                    auth_required=True,
                    message="Codex sign-in did not complete.",
                    output_tail=[],
                    updated_at=self._now(),
                )

    def _update_login_output(self, output_lines: list[str]) -> None:
        clean_lines = [self._strip_ansi(line) for line in output_lines]
        text = "\n".join(clean_lines)
        urls = [
            url
            for url in re.findall(r"https?://[^\s)>\"]+", text)
            if self._safe_login_url(url)
        ]
        code = self._extract_user_code(text)
        with self._lock:
            self._status = self._status.model_copy(
                update={
                    "state": "login_running",
                    "auth_required": True,
                    "message": "Complete the displayed Codex device sign-in step.",
                    "verification_uri": urls[0] if urls else self._status.verification_uri,
                    "login_url": urls[-1] if urls else self._status.login_url,
                    "user_code": code or self._status.user_code,
                    "output_tail": [],
                    "updated_at": self._now(),
                }
            )

    def _extract_user_code(self, text: str) -> str | None:
        clean_text = self._strip_ansi(text)
        labelled = re.search(
            r"(?:one-time code|user code)[:\s\(\)A-Za-z0-9-]*?\n\s*([A-Z0-9]{4,6}-[A-Z0-9]{4,6})",
            clean_text,
            flags=re.IGNORECASE,
        )
        if labelled:
            return labelled.group(1)
        standalone = _DEVICE_CODE_PATTERN.search(clean_text)
        return standalone.group(0) if standalone else None

    def _safe_login_url(self, value: str) -> bool:
        if not isinstance(value, str) or value != value.strip():
            return False
        try:
            parsed = urlsplit(value)
            hostname = parsed.hostname
            port = parsed.port
        except (TypeError, ValueError):
            return False
        return (
            parsed.scheme == "https"
            and hostname in _ALLOWED_LOGIN_HOSTS
            and port in {None, 443}
            and parsed.path not in {"", "/"}
            and parsed.username is None
            and parsed.password is None
            and not parsed.query
            and not parsed.fragment
        )

    def _strip_ansi(self, value: str) -> str:
        return _ANSI_PATTERN.sub("", value)

    def _command_prefix(self) -> list[str]:
        return codex_command_prefix(self.codex_command)

    def _now(self) -> str:
        return datetime.now(UTC).isoformat().replace("+00:00", "Z")
