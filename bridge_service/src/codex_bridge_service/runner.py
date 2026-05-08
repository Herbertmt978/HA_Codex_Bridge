import json
import subprocess
import sys
from pathlib import Path
from threading import Lock, Thread
from uuid import uuid4

from .models import RunMode, RunRecord, ThreadRecord
from .storage import BridgeStorage, ThreadNotFoundError


class ThreadBusyError(RuntimeError):
    pass


class BridgeRunner:
    def __init__(self, storage: BridgeStorage, codex_command: str = "codex") -> None:
        self.storage = storage
        self.codex_command = codex_command
        self._lock = Lock()

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
                args=(record, run, prompt),
                daemon=True,
            )
            worker.start()
            return run

    def _run_prompt(self, record: ThreadRecord, run: RunRecord, prompt: str) -> None:
        stderr_lines: list[str] = []
        saw_run_completion = False
        try:
            process = subprocess.Popen(
                self._build_command(record, prompt),
                cwd=record.workspace_path,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
            )
            assert process.stdout is not None
            assert process.stderr is not None

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
            self.storage.append_thread_event(
                thread_id=record.thread_id,
                event_type="run.failed",
                payload={
                    "run_id": run.run_id,
                    "error": str(exc),
                },
            )
            self._complete_run(record.thread_id, error=str(exc))

    def _build_command(self, record: ThreadRecord, prompt: str) -> list[str]:
        prompt_with_context = self._compose_prompt(record, prompt)
        command = self._command_prefix()
        if record.codex_session_id:
            command.extend(
                [
                    "exec",
                    "resume",
                    record.codex_session_id,
                    prompt_with_context,
                    "--json",
                    "--skip-git-repo-check",
                ]
            )
        else:
            command.extend(
                [
                    "exec",
                    prompt_with_context,
                    "--json",
                    "--skip-git-repo-check",
                    "-C",
                    record.workspace_path,
                ]
            )
            uploads_dir = self.storage.uploads_dir / record.thread_id
            if uploads_dir.exists():
                command.extend(["--add-dir", str(uploads_dir)])

        if record.mode is RunMode.OBSERVE:
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

    def _complete_run(self, thread_id: str, *, error: str | None = None) -> None:
        record = self.storage.load_thread(thread_id)
        record.status = "error" if error else "idle"
        record.active_run_id = None
        record.last_error = error
        self.storage.save_thread(record)
