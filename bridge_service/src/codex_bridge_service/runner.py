import json
import subprocess
import sys
from pathlib import Path
from threading import Lock, Thread
from uuid import uuid4

from .models import RunMode, RunRecord, ThreadRecord, ThreadViewRecord
from .storage import BridgeStorage


class ThreadBusyError(RuntimeError):
    pass


class NoActiveRunError(RuntimeError):
    pass


class BridgeRunner:
    def __init__(
        self,
        storage: BridgeStorage,
        codex_command: str = "codex",
        *,
        bypass_sandbox: bool = False,
    ) -> None:
        self.storage = storage
        self.codex_command = codex_command
        self.bypass_sandbox = bypass_sandbox
        self._lock = Lock()
        self._processes: dict[str, subprocess.Popen[str]] = {}
        self._cancelled_runs: set[str] = set()

    def submit_prompt(self, thread_id: str, prompt: str) -> RunRecord:
        with self._lock:
            record = self.storage.load_thread(thread_id)
            if record.status == "running":
                raise ThreadBusyError(thread_id)

            run = RunRecord(
                run_id=f"run_{uuid4().hex[:12]}",
                thread_id=thread_id,
                status="running",
            )
            record.status = "running"
            record.active_run_id = run.run_id
            record.last_error = None
            self.storage.save_thread(record)
            self.storage.clear_limits_blocked()
            self.storage.append_thread_event(
                thread_id=thread_id,
                event_type="message.created",
                payload={
                    "run_id": run.run_id,
                    "role": "user",
                    "text": prompt,
                },
            )
            worker = Thread(
                target=self._run_prompt,
                args=(self.storage.get_thread(thread_id), run, prompt),
                daemon=True,
            )
            worker.start()
            return run

    def cancel_run(self, thread_id: str) -> RunRecord:
        with self._lock:
            record = self.storage.load_thread(thread_id)
            run_id = record.active_run_id
            if record.status != "running" or not run_id:
                raise NoActiveRunError(thread_id)

            self._cancelled_runs.add(run_id)
            process = self._processes.get(thread_id)
            if process is not None and process.poll() is None:
                process.terminate()

            record.status = "idle"
            record.active_run_id = None
            record.last_error = "Run cancelled"
            self.storage.save_thread(record)
            self.storage.append_thread_event(
                thread_id=thread_id,
                event_type="run.cancelled",
                payload={
                    "run_id": run_id,
                    "reason": "cancelled by user",
                },
            )
            return RunRecord(run_id=run_id, thread_id=thread_id, status="cancelled")

    def _run_prompt(self, record: ThreadViewRecord, run: RunRecord, prompt: str) -> None:
        stderr_lines: list[str] = []
        saw_run_completion = False
        try:
            process = subprocess.Popen(
                self._build_command(record, prompt),
                cwd=record.workspace_path,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
            )
            assert process.stdout is not None
            assert process.stderr is not None
            with self._lock:
                self._processes[record.thread_id] = process

            for raw_line in process.stdout:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    stderr_lines.append(line)
                    continue

                if self._handle_codex_event(record.thread_id, run.run_id, event):
                    saw_run_completion = True

            stderr_lines.extend(line.strip() for line in process.stderr if line.strip())
            return_code = process.wait()
            if run.run_id in self._cancelled_runs:
                self.storage.sync_thread_artifacts(record.thread_id)
                return
            if return_code != 0:
                raise RuntimeError(
                    stderr_lines[-1] if stderr_lines else f"codex exited with code {return_code}"
                )
            if not saw_run_completion:
                self.storage.append_thread_event(
                    thread_id=record.thread_id,
                    event_type="run.completed",
                    payload={
                        "run_id": run.run_id,
                        "usage": {},
                    },
                )
            self.storage.sync_thread_artifacts(record.thread_id)
            self._complete_run(record.thread_id)
        except Exception as exc:
            if run.run_id in self._cancelled_runs:
                return
            failure_payload = self._failure_payload(str(exc))
            self.storage.append_thread_event(
                thread_id=record.thread_id,
                event_type="run.failed",
                payload={
                    "run_id": run.run_id,
                    **failure_payload,
                },
            )
            self._complete_run(record.thread_id, error=str(exc))
        finally:
            with self._lock:
                self._processes.pop(record.thread_id, None)
                self._cancelled_runs.discard(run.run_id)

    def _build_command(self, record: ThreadViewRecord, prompt: str) -> list[str]:
        prompt_with_context = self._compose_prompt(record, prompt)
        command = self._command_prefix()
        command.append("exec")
        if record.codex_session_id:
            command.extend(["resume", record.codex_session_id])

        if record.effective_model:
            command.extend(["-m", record.effective_model])
        if record.effective_thinking_level:
            command.extend(["-c", f"model_reasoning_effort={record.effective_thinking_level}"])

        command.extend(
            [
                prompt_with_context,
                "--json",
                "--skip-git-repo-check",
            ]
        )
        if not record.codex_session_id:
            command.extend(["-C", record.workspace_path])
            uploads_dir = self.storage.uploads_dir / record.thread_id
            if uploads_dir.exists():
                command.extend(["--add-dir", str(uploads_dir)])

        if self.bypass_sandbox:
            command.append("--dangerously-bypass-approvals-and-sandbox")
        elif record.mode is RunMode.OBSERVE:
            command.extend(["--sandbox", "read-only"])
        elif record.mode is RunMode.FULL_AUTO:
            command.append("--full-auto")
        else:
            command.extend(["--sandbox", "workspace-write"])

        return command

    def _command_prefix(self) -> list[str]:
        target = Path(self.codex_command)
        suffix = target.suffix.lower()
        if suffix == ".ps1":
            return ["powershell", "-File", str(target)]
        if suffix == ".py":
            return [sys.executable, str(target)]
        return [str(target)]

    def _compose_prompt(self, record: ThreadRecord, prompt: str) -> str:
        if not record.attachments:
            return prompt

        attachment_lines = "\n".join(
            f"- {attachment.filename} ({attachment.mime_type}): {attachment.stored_path}"
            for attachment in record.attachments
        )
        return (
            "Bridge workspace context:\n"
            f"- Working directory: {record.workspace_path}\n"
            "- Uploaded files are available at these local paths:\n"
            f"{attachment_lines}\n\n"
            "User request:\n"
            f"{prompt}"
        )

    def _handle_codex_event(self, thread_id: str, run_id: str, event: dict[str, object]) -> bool:
        event_type = str(event.get("type", "codex.event"))
        if event_type == "thread.started":
            session_id = str(event.get("thread_id", ""))
            if session_id:
                record = self.storage.load_thread(thread_id)
                record.codex_session_id = session_id
                self.storage.save_thread(record)
                self.storage.append_thread_event(
                    thread_id=thread_id,
                    event_type="session.bound",
                    payload={
                        "run_id": run_id,
                        "codex_session_id": session_id,
                    },
                )
            return False

        if event_type == "turn.started":
            self.storage.append_thread_event(
                thread_id=thread_id,
                event_type="run.started",
                payload={"run_id": run_id},
            )
            return False

        if event_type == "token_count":
            rate_limits = event.get("rate_limits")
            if isinstance(rate_limits, dict):
                self.storage.update_limits_from_rate_data(rate_limits)
            return False

        if event_type == "item.completed":
            item = event.get("item", {})
            if isinstance(item, dict) and item.get("type") == "agent_message":
                self.storage.append_thread_event(
                    thread_id=thread_id,
                    event_type="message.completed",
                    payload={
                        "run_id": run_id,
                        "role": "assistant",
                        "text": item.get("text", ""),
                    },
                )
                return False

        if event_type == "turn.completed":
            self.storage.append_thread_event(
                thread_id=thread_id,
                event_type="run.completed",
                payload={
                    "run_id": run_id,
                    "usage": event.get("usage", {}),
                },
            )
            return True

        self.storage.append_thread_event(
            thread_id=thread_id,
            event_type="codex.event",
            payload={
                "run_id": run_id,
                "event": event,
            },
        )
        return False

    def _failure_payload(self, message: str) -> dict[str, object]:
        lowered = message.lower()
        blocked = any(marker in lowered for marker in ("limit", "credit", "quota"))
        if blocked:
            self.storage.mark_limits_blocked(message)
            return {
                "error": message,
                "blocked": True,
                "failure_type": "limits.exhausted",
            }

        return {
            "error": message,
            "blocked": False,
            "failure_type": "run.failed",
        }

    def _complete_run(self, thread_id: str, *, error: str | None = None) -> None:
        record = self.storage.load_thread(thread_id)
        record.status = "error" if error else "idle"
        record.active_run_id = None
        record.last_error = error
        self.storage.save_thread(record)
