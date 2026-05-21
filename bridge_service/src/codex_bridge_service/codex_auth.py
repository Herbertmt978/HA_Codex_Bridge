import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock, Thread

from .models import CodexAuthStatusRecord

AUTH_EXPIRED_MESSAGE = "Codex login expired on the VM. Start a new VM sign-in from Home Assistant."
_AUTH_FAILURE_MARKERS = (
    "401 unauthorized",
    "access token could not be refreshed",
    "refresh token was already used",
    "please log out and sign in again",
    "not authenticated",
    "authentication failed",
)


def is_codex_auth_failure(message: str | None) -> bool:
    if not message:
        return False
    lowered = message.lower()
    return any(marker in lowered for marker in _AUTH_FAILURE_MARKERS)


class CodexAuthManager:
    def __init__(self, codex_command: str = "codex") -> None:
        self.codex_command = codex_command
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

    def start_device_login(self, *, force_logout: bool = True) -> CodexAuthStatusRecord:
        with self._lock:
            login_is_starting = self._status.state in {"login_starting", "login_running"}
            if login_is_starting or (self._process is not None and self._process.poll() is None):
                return self._status.model_copy(deep=True)
            self._status = CodexAuthStatusRecord(
                state="login_starting",
                auth_required=True,
                message="Starting Codex sign-in on the VM.",
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
        )
        output_tail = self._tail_output(completed.stdout, completed.stderr)
        state = "logged_out" if completed.returncode == 0 else "logout_failed"
        message = "Codex credentials were removed from the VM." if completed.returncode == 0 else "Codex logout failed."
        with self._lock:
            self._process = None
            self._status = CodexAuthStatusRecord(
                state=state,
                auth_required=True,
                message=message,
                output_tail=output_tail,
                updated_at=self._now(),
            )
            return self._status.model_copy(deep=True)

    def _run_device_login(self, force_logout: bool) -> None:
        output_lines: list[str] = []
        try:
            if force_logout:
                subprocess.run(
                    [*self._command_prefix(), "logout"],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    timeout=30,
                )

            process = subprocess.Popen(
                [*self._command_prefix(), "login", "--device-auth"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
            )
            with self._lock:
                self._process = process
                self._status = self._status.model_copy(
                    update={
                        "state": "login_running",
                        "message": "Codex sign-in is running on the VM.",
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
                            "message": "Codex sign-in completed on the VM.",
                            "output_tail": output_lines[-20:],
                            "updated_at": self._now(),
                        }
                    )
                else:
                    self._status = self._status.model_copy(
                        update={
                            "state": "login_failed",
                            "auth_required": True,
                            "message": "Codex sign-in did not complete on the VM.",
                            "output_tail": output_lines[-20:],
                            "updated_at": self._now(),
                        }
                    )
        except Exception as exc:
            with self._lock:
                self._process = None
                self._status = CodexAuthStatusRecord(
                    state="login_failed",
                    auth_required=True,
                    message=str(exc),
                    output_tail=output_lines[-20:],
                    updated_at=self._now(),
                )

    def _update_login_output(self, output_lines: list[str]) -> None:
        text = "\n".join(output_lines)
        urls = re.findall(r"https?://[^\s)>\"]+", text)
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
                    "output_tail": output_lines[-20:],
                    "updated_at": self._now(),
                }
            )

    def _extract_user_code(self, text: str) -> str | None:
        labelled = re.search(r"(?:code|user code)[:\s]+([A-Z0-9-]{6,})", text, flags=re.IGNORECASE)
        if labelled:
            return labelled.group(1)
        standalone = re.search(r"\b[A-Z0-9]{4}-[A-Z0-9]{4}\b", text)
        return standalone.group(0) if standalone else None

    def _command_prefix(self) -> list[str]:
        target = Path(self.codex_command)
        suffix = target.suffix.lower()
        if suffix == ".ps1":
            return ["powershell", "-File", str(target)]
        if suffix == ".py":
            return [sys.executable, str(target)]
        return [str(target)]

    def _tail_output(self, stdout: str | None, stderr: str | None) -> list[str]:
        lines = []
        for value in (stdout, stderr):
            if value:
                lines.extend(line.strip() for line in value.splitlines() if line.strip())
        return lines[-20:]

    def _now(self) -> str:
        return datetime.now(UTC).isoformat().replace("+00:00", "Z")
