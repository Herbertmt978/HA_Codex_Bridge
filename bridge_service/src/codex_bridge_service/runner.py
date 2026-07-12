import json
import subprocess
from pathlib import Path
from queue import Empty, Queue
from threading import Lock, Thread
from time import monotonic
from uuid import uuid4

from .codex_auth import AUTH_EXPIRED_MESSAGE, is_codex_auth_failure
from .codex_process import codex_command_prefix, codex_subprocess_environment
from .models import PendingPromptRecord, RunMode, RunRecord, ThreadRecord, ThreadViewRecord
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
        codex_home: Path | str | None = None,
        bypass_sandbox: bool = False,
        ignore_user_config: bool = False,
        idle_timeout_seconds: float | None = 1800.0,
        recover_stale_runs: bool = True,
    ) -> None:
        self.storage = storage
        self.codex_command = codex_command
        self.codex_home = codex_home
        self.bypass_sandbox = bypass_sandbox
        self.ignore_user_config = ignore_user_config
        self.idle_timeout_seconds = idle_timeout_seconds
        self._lock = Lock()
        self._processes: dict[str, subprocess.Popen[str]] = {}
        self._cancelled_runs: set[str] = set()
        if recover_stale_runs:
            self._recover_stale_runs()

    def submit_prompt(self, thread_id: str, prompt: str) -> RunRecord:
        with self._lock:
            record = self.storage.load_thread(thread_id)
            if record.status == "running":
                return self._queue_prompt(record, prompt)

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
            self._start_worker(self.storage.get_thread(thread_id), run, prompt)
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
            queued_count = len(record.pending_prompts)
            record.pending_prompts.clear()
            self.storage.save_thread(record)
            self.storage.append_thread_event(
                thread_id=thread_id,
                event_type="run.cancelled",
                payload={
                    "run_id": run_id,
                    "reason": "cancelled by user",
                },
            )
            if queued_count:
                self.storage.append_thread_event(
                    thread_id=thread_id,
                    event_type="run.queue_cleared",
                    payload={
                        "reason": "active run cancelled",
                        "queued_count": queued_count,
                    },
                )
            return RunRecord(run_id=run_id, thread_id=thread_id, status="cancelled")

    def _queue_prompt(self, record: ThreadRecord, prompt: str) -> RunRecord:
        pending = PendingPromptRecord(
            run_id=f"run_{uuid4().hex[:12]}",
            prompt=prompt,
            created_at=self.storage._now(),
        )
        record.pending_prompts.append(pending)
        self.storage._touch_thread(record)
        self.storage.save_thread(record)
        self.storage.append_thread_event(
            thread_id=record.thread_id,
            event_type="message.created",
            payload={
                "run_id": pending.run_id,
                "role": "user",
                "text": prompt,
                "queued": True,
            },
        )
        self.storage.append_thread_event(
            thread_id=record.thread_id,
            event_type="run.queued",
            payload={
                "run_id": pending.run_id,
                "pending_count": len(record.pending_prompts),
            },
        )
        return RunRecord(run_id=pending.run_id, thread_id=record.thread_id, status="queued")

    def _start_worker(self, record: ThreadViewRecord, run: RunRecord, prompt: str) -> None:
        worker = Thread(
            target=self._run_prompt,
            args=(record, run, prompt),
            daemon=True,
        )
        worker.start()

    def _run_prompt(self, record: ThreadViewRecord, run: RunRecord, prompt: str) -> None:
        stderr_lines: list[str] = []
        codex_error: str | None = None
        saw_run_completion = False
        error: str | None = None
        try:
            process = subprocess.Popen(
                self._build_command(record, prompt),
                cwd=record.workspace_path,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=codex_subprocess_environment(self.codex_home),
                text=True,
                encoding="utf-8",
            )
            assert process.stdout is not None
            assert process.stderr is not None
            with self._lock:
                self._processes[record.thread_id] = process

            stream_queue: Queue[tuple[str, str | None]] = Queue()
            Thread(
                target=self._read_stream,
                args=(process.stdout, "stdout", stream_queue),
                daemon=True,
            ).start()
            Thread(
                target=self._read_stream,
                args=(process.stderr, "stderr", stream_queue),
                daemon=True,
            ).start()

            stdout_done = False
            stderr_done = False
            last_output_at = monotonic()
            while not stdout_done:
                if (
                    self.idle_timeout_seconds is not None
                    and self._process_is_running(process)
                    and monotonic() - last_output_at > self.idle_timeout_seconds
                ):
                    process.terminate()
                    raise TimeoutError(
                        f"codex produced no output for {self.idle_timeout_seconds:g} seconds"
                    )

                try:
                    stream_name, raw_line = stream_queue.get(timeout=0.2)
                except Empty:
                    continue

                if raw_line is None:
                    if stream_name == "stdout":
                        stdout_done = True
                    elif stream_name == "stderr":
                        stderr_done = True
                    continue

                line = raw_line.strip()
                if not line:
                    continue
                last_output_at = monotonic()

                if stream_name == "stderr":
                    stderr_lines.append(line)
                    continue

                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    stderr_lines.append(line)
                    continue

                codex_error = self._extract_codex_error(event) or codex_error
                if self._handle_codex_event(record.thread_id, run.run_id, event):
                    saw_run_completion = True

            return_code = process.wait()
            while not stderr_done:
                try:
                    stream_name, raw_line = stream_queue.get(timeout=0.2)
                except Empty:
                    break
                if stream_name == "stderr" and raw_line is None:
                    stderr_done = True
                    continue
                if stream_name == "stderr" and raw_line and raw_line.strip():
                    stderr_lines.append(raw_line.strip())
            if run.run_id in self._cancelled_runs:
                self.storage.sync_thread_artifacts(record.thread_id)
                return
            if return_code != 0:
                raise RuntimeError(
                    codex_error
                    or (stderr_lines[-1] if stderr_lines else f"codex exited with code {return_code}")
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
        except Exception as exc:
            if run.run_id in self._cancelled_runs:
                return
            failure_payload = self._failure_payload(str(exc))
            error = str(failure_payload["error"])
            self.storage.append_thread_event(
                thread_id=record.thread_id,
                event_type="run.failed",
                payload={
                    "run_id": run.run_id,
                    **failure_payload,
                },
            )
        finally:
            self._finish_run(record.thread_id, run.run_id, error=error)

    def _read_stream(
        self,
        stream,
        stream_name: str,
        stream_queue: Queue[tuple[str, str | None]],
    ) -> None:
        try:
            for raw_line in stream:
                stream_queue.put((stream_name, raw_line))
        finally:
            stream_queue.put((stream_name, None))

    def _process_is_running(self, process) -> bool:
        poll = getattr(process, "poll", None)
        if poll is None:
            return True
        return poll() is None

    def _recover_stale_runs(self) -> None:
        for thread in self.storage.list_threads(include_archived=True):
            record = self.storage.load_thread(thread.thread_id)
            if record.status != "running" or not record.active_run_id:
                continue

            run_id = record.active_run_id
            queued_count = len(record.pending_prompts)
            message = "Bridge restarted while this run was active; the previous Codex process can no longer be tracked."
            record.status = "error"
            record.active_run_id = None
            record.last_error = message
            record.pending_prompts.clear()
            self.storage.save_thread(record)
            self.storage.append_thread_event(
                thread_id=record.thread_id,
                event_type="run.failed",
                payload={
                    "run_id": run_id,
                    "error": message,
                    "blocked": False,
                    "failure_type": "run.orphaned",
                },
            )
            if queued_count:
                self.storage.append_thread_event(
                    thread_id=record.thread_id,
                    event_type="run.queue_cleared",
                    payload={
                        "reason": "bridge restarted",
                        "queued_count": queued_count,
                    },
                )

    def _build_command(self, record: ThreadViewRecord, prompt: str) -> list[str]:
        prompt_with_context = self._compose_prompt(record, prompt)
        command = self._command_prefix()
        command.append("exec")
        if self.ignore_user_config:
            command.append("--ignore-user-config")
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
        return codex_command_prefix(self.codex_command)

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

    def _extract_codex_error(self, event: dict[str, object]) -> str | None:
        event_type = str(event.get("type", ""))
        if event_type == "error":
            return self._error_message(event.get("message"))
        if event_type == "turn.failed":
            return self._error_message(event.get("error"))
        return None

    def _error_message(self, value: object) -> str | None:
        if value is None:
            return None
        if isinstance(value, dict):
            nested_error = value.get("error")
            if nested_error is not None:
                nested = self._error_message(nested_error)
                if nested:
                    return nested
            nested_message = value.get("message")
            if nested_message is not None:
                nested = self._error_message(nested_message)
                if nested:
                    return nested
            return None
        if isinstance(value, str):
            message = value.strip()
            if not message:
                return None
            if message.startswith("{"):
                try:
                    return self._error_message(json.loads(message)) or message
                except json.JSONDecodeError:
                    return message
            return message
        return str(value)

    def _failure_payload(self, message: str) -> dict[str, object]:
        if is_codex_auth_failure(message):
            return {
                "error": AUTH_EXPIRED_MESSAGE,
                "raw_error": message,
                "blocked": False,
                "auth_required": True,
                "failure_type": "auth.expired",
            }

        lowered = message.lower()
        if "model is not supported" in lowered:
            return {
                "error": message,
                "blocked": False,
                "failure_type": "model.unsupported",
            }

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

    def _finish_run(self, thread_id: str, run_id: str, *, error: str | None = None) -> None:
        with self._lock:
            was_cancelled = run_id in self._cancelled_runs
            self._processes.pop(thread_id, None)
            self._cancelled_runs.discard(run_id)
            if was_cancelled:
                return

            record = self.storage.load_thread(thread_id)
            if error:
                queued_count = len(record.pending_prompts)
                record.status = "error"
                record.active_run_id = None
                record.last_error = error
                record.pending_prompts.clear()
                self.storage.save_thread(record)
                if queued_count:
                    self.storage.append_thread_event(
                        thread_id=thread_id,
                        event_type="run.queue_cleared",
                        payload={
                            "reason": "active run failed",
                            "queued_count": queued_count,
                        },
                    )
                return

            if record.pending_prompts:
                pending = record.pending_prompts.pop(0)
                next_run = RunRecord(
                    run_id=pending.run_id,
                    thread_id=thread_id,
                    status="running",
                )
                record.status = "running"
                record.active_run_id = pending.run_id
                record.last_error = None
                self.storage.save_thread(record)
                self.storage.append_thread_event(
                    thread_id=thread_id,
                    event_type="run.dequeued",
                    payload={
                        "run_id": pending.run_id,
                        "pending_count": len(record.pending_prompts),
                    },
                )
                self._start_worker(self.storage.get_thread(thread_id), next_run, pending.prompt)
                return

            record.status = "idle"
            record.active_run_id = None
            record.last_error = None
            self.storage.save_thread(record)
