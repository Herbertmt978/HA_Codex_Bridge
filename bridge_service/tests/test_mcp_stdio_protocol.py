"""A stdio worker must not bypass framing, deadlines or request ownership."""

from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest
from codex_bridge_service.mcp_stdio_protocol import StdioPipe, StdioProtocolError

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX pipe deadlines")


class _Worker:
    def __init__(self, source: str) -> None:
        self.process = subprocess.Popen(
            [sys.executable, "-u", "-c", source],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            close_fds=True,
        )

    def close(self) -> None:
        if self.process.poll() is None:
            self.process.kill()
        self.process.communicate(timeout=2)


def _pipe(source: str) -> StdioPipe:
    return StdioPipe(_Worker(source))


def test_matching_response_and_server_request_is_declined() -> None:
    pipe = _pipe("""
import json, sys
request = json.loads(sys.stdin.readline())
print(json.dumps({'jsonrpc':'2.0','id':77,'method':'sampling/createMessage','params':{}}), flush=True)
denial = json.loads(sys.stdin.readline())
assert denial['id'] == 77 and denial['error']['code'] == -32601
print(json.dumps({'jsonrpc':'2.0','id':request['id'],'result':{'tools':[]}}), flush=True)
""")
    try:
        result = pipe.transact({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, timeout_seconds=2)
        assert result == {"jsonrpc": "2.0", "id": 1, "result": {"tools": []}}
    finally:
        pipe.close()


def test_malformed_stdout_revokes_worker() -> None:
    pipe = _pipe("import sys; sys.stdin.readline(); print('not-json', flush=True)")
    with pytest.raises(StdioProtocolError):
        pipe.transact({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, timeout_seconds=2)
    assert pipe.worker.process.poll() is not None


def test_deadline_revokes_worker() -> None:
    pipe = _pipe("import sys,time; sys.stdin.readline(); time.sleep(5)")
    began = time.monotonic()
    with pytest.raises(StdioProtocolError):
        pipe.transact({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, timeout_seconds=0.2)
    assert time.monotonic() - began < 2
    assert pipe.worker.process.poll() is not None


def test_oversized_stderr_revokes_worker() -> None:
    pipe = _pipe("import sys,time; sys.stdin.readline(); sys.stderr.write('x'*20000); sys.stderr.flush(); time.sleep(5)")
    with pytest.raises(StdioProtocolError):
        pipe.transact({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, timeout_seconds=2)
    assert pipe.worker.process.poll() is not None
