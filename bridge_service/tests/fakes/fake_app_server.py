#!/usr/bin/env python3
"""Scriptable JSONL peer used by the Codex app-server transport tests.

The peer intentionally receives its scenario through files below ``CODEX_HOME``.
That keeps the subprocess contract realistic and lets tests verify that unrelated
parent environment variables are not inherited by the supervised process.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
from threading import Lock, Thread
import time
from typing import Any


class ScriptedPeer:
    def __init__(self) -> None:
        codex_home_value = os.environ.get("CODEX_HOME")
        if not codex_home_value:
            raise SystemExit("CODEX_HOME is required")
        self.codex_home = Path(codex_home_value)
        self.sidecars = self.codex_home / ".fake-app-server"
        self.sidecars.mkdir(parents=True, exist_ok=True)
        self.generation = self._claim_generation()
        self.scenario = self._read_json(
            self.sidecars / f"scenario-{self.generation}.json"
        )
        self.control_path = self.sidecars / f"control-{self.generation}.json"
        self.transcript_path = self.sidecars / f"transcript-{self.generation}.jsonl"
        self.process_path = self.sidecars / f"process-{self.generation}.json"
        self._write_lock = Lock()
        self._transcript_lock = Lock()
        self._reverse_requests: dict[str, list[dict[str, Any]]] = {}
        self._child: subprocess.Popen[bytes] | None = None

    def _claim_generation(self) -> int:
        generation = 1
        while True:
            claim = self.sidecars / f"generation-{generation}.claim"
            try:
                descriptor = os.open(claim, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                generation += 1
                continue
            os.close(descriptor)
            return generation

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise SystemExit(f"scenario must be an object: {path}")
        return value

    def _record(self, direction: str, message: object) -> None:
        entry = json.dumps(
            {"direction": direction, "message": message}, separators=(",", ":")
        )
        with self._transcript_lock:
            with self.transcript_path.open("a", encoding="utf-8") as transcript:
                transcript.write(f"{entry}\n")
                transcript.flush()

    def _send(self, message: object) -> None:
        self._record("server", message)
        payload = json.dumps(message, separators=(",", ":"))
        with self._write_lock:
            sys.stdout.write(f"{payload}\n")
            sys.stdout.flush()

    def _send_raw(self, payload: str) -> None:
        self._record("server-raw", {"length": len(payload), "prefix": payload[:80]})
        with self._write_lock:
            sys.stdout.write(payload)
            if not payload.endswith("\n"):
                sys.stdout.write("\n")
            sys.stdout.flush()

    def _write_process_sidecar(self) -> None:
        process: dict[str, object] = {
            "pid": os.getpid(),
            "argv": sys.argv[1:],
            "environmentKeys": sorted(os.environ),
        }
        if self.scenario.get("spawn_child"):
            child_code = (
                "import os, signal, time; "
                "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                "print(os.getpid(), flush=True); "
                "time.sleep(120)"
            )
            self._child = subprocess.Popen(
                [sys.executable, "-c", child_code],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            assert self._child.stdout is not None
            process["childPid"] = int(
                self._child.stdout.readline().decode("ascii").strip()
            )
        self.process_path.write_text(json.dumps(process), encoding="utf-8")

    def _install_signals(self) -> None:
        if self.scenario.get("ignore_sigterm") and hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, signal.SIG_IGN)

    def _on_initialized(self) -> None:
        for item in self.scenario.get("on_initialized", []):
            if not isinstance(item, dict):
                continue
            kind = item.get("kind")
            if kind == "notification":
                self._send({"method": item["method"], "params": item.get("params")})
            elif kind == "request":
                self._send(
                    {
                        "id": item["id"],
                        "method": item["method"],
                        "params": item.get("params"),
                    }
                )
            elif kind == "stderr":
                self._write_stderr(item)

    @staticmethod
    def _write_stderr(action: dict[str, Any]) -> None:
        lines = action.get("lines", [])
        repeat = int(action.get("repeat", 1))
        for _ in range(repeat):
            for line in lines:
                sys.stderr.write(f"{line}\n")
        sys.stderr.flush()

    def _delayed_response(
        self, request: dict[str, Any], action: dict[str, Any]
    ) -> None:
        time.sleep(float(action.get("delay_seconds", 0.1)))
        self._send_result(request, action)

    def _held_response(self, request: dict[str, Any], action: dict[str, Any]) -> None:
        control_key = str(action.get("control_key", request.get("method", "release")))
        while True:
            control = self._read_json(self.control_path)
            released = control.get("release", [])
            if control_key in released:
                self._send_result(request, action)
                return
            time.sleep(0.01)

    def _send_result(self, request: dict[str, Any], action: dict[str, Any]) -> None:
        if "result" in action:
            result = action["result"]
        else:
            result = {"echo": request.get("params")}
        self._send({"id": request["id"], "result": result})

    def _handle_request(self, request: dict[str, Any]) -> None:
        method = str(request["method"])
        configured = self.scenario.get("responses", {}).get(method, {})
        action = configured if isinstance(configured, dict) else {}
        mode = action.get("mode", "echo")
        if mode == "echo":
            self._send_result(request, action)
        elif mode == "reverse_pair":
            pending = self._reverse_requests.setdefault(method, [])
            pending.append(request)
            if len(pending) == 2:
                second, first = pending[1], pending[0]
                self._send_result(second, action)
                self._send_result(first, action)
                pending.clear()
        elif mode == "delay":
            Thread(
                target=self._delayed_response, args=(request, action), daemon=True
            ).start()
        elif mode == "hold":
            Thread(
                target=self._held_response, args=(request, action), daemon=True
            ).start()
        elif mode == "error":
            self._send(
                {"id": request["id"], "error": action.get("error", {"code": -32000})}
            )
        elif mode == "malformed":
            self._send_raw(str(action.get("payload", "{malformed-json")))
        elif mode == "oversize":
            self._send_raw("x" * int(action.get("size", 65536)))
        elif mode == "stderr_then_echo":
            self._write_stderr(action)
            self._send_result(request, action)
        elif mode == "notifications_then_echo":
            for notification in action.get("notifications", []):
                self._send(
                    {
                        "method": notification["method"],
                        "params": notification.get("params"),
                    }
                )
            self._send_result(request, action)
        elif mode == "crash":
            os._exit(int(action.get("exit_code", 23)))
        else:
            raise SystemExit(f"unknown fake response mode: {mode}")

    def run(self) -> int:
        self._install_signals()
        self._write_process_sidecar()
        startup = self.scenario.get("startup", "normal")
        if startup == "crash":
            return int(self.scenario.get("exit_code", 17))
        for raw_line in sys.stdin:
            try:
                message = json.loads(raw_line)
            except json.JSONDecodeError:
                self._record("client-raw", raw_line.rstrip("\n"))
                continue
            self._record("client", message)
            if not isinstance(message, dict):
                continue
            method = message.get("method")
            if method == "initialize" and "id" in message:
                if startup == "stall_initialize":
                    continue
                self._send(
                    {
                        "id": message["id"],
                        "result": self.scenario.get(
                            "initialize_result",
                            {
                                "codexHome": str(self.codex_home.resolve()),
                                "platformFamily": (
                                    "windows" if os.name == "nt" else "unix"
                                ),
                                "platformOs": (
                                    "windows" if os.name == "nt" else "linux"
                                ),
                                "userAgent": (
                                    "Codex Desktop/0.144.4 (test; x86_64) "
                                    "fake (ha_codex_bridge; 0.6.0)"
                                ),
                            },
                        ),
                    }
                )
            elif method == "initialized" and "id" not in message:
                self._on_initialized()
            elif method is not None and "id" in message:
                self._handle_request(message)
        self._record("server-control", {"event": "stdin-eof"})
        return 0


def main() -> int:
    if sys.argv[1:] not in (
        ["app-server", "--stdio"],
        ["-c", "mcp_servers={}", "app-server", "--stdio"],
    ):
        raise SystemExit(f"unexpected argv: {sys.argv[1:]!r}")
    return ScriptedPeer().run()


if __name__ == "__main__":
    raise SystemExit(main())
