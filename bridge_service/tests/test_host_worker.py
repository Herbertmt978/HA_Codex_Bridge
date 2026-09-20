import os
import subprocess
import sys
from threading import Thread
import time

from fastapi.testclient import TestClient
import pytest

from codex_bridge_service.host_access_contract import HostCommand, HostIdentity, HostInvocation, MAX_OUTPUT_BYTES
from codex_bridge_service.host_worker import HostWorker, HostWorkerError, create_host_worker_app


@pytest.fixture
def worker():
    def no_spawn(*args, **kwargs):
        raise AssertionError("Rejected requests must not execute")
    worker = HostWorker(
        HostIdentity(machine_fingerprint="d" * 64, companion_id="a" * 32, hostname="ha-test", os_version="18.3"),
        spawn=no_spawn,
    )
    yield worker
    worker.close()


def invocation(worker, **changes):
    values = {
        "run_id": "run-1", "request_id": "request-1",
        "scope_revision": worker.identity.scope_revision,
        "worker_session": worker.session_id, "expires_at": time.time() + 10,
        "operation": HostCommand(command="hostname"),
    }
    values.update(changes)
    return HostInvocation(**values)


@pytest.mark.parametrize("change", ["expiry", "session", "revision", "cancel", "close"])
def test_invalid_authority_never_spawns(worker, change):
    request = invocation(worker)
    if change == "expiry":
        request = request.model_copy(update={"expires_at": time.time() - 1})
    elif change == "session":
        request = request.model_copy(update={"worker_session": "c" * 32})
    elif change == "revision":
        request = request.model_copy(update={"scope_revision": "c" * 64})
    elif change == "cancel":
        worker.cancel(request.run_id)
    else:
        worker.close()
    with pytest.raises(HostWorkerError):
        worker.execute(request)


def test_failed_spawn_is_not_retried(worker):
    request = invocation(worker)
    with pytest.raises(HostWorkerError, match="could not start"):
        worker.execute(request)
    with pytest.raises(HostWorkerError, match="already been submitted"):
        worker.execute(request)


def test_api_authenticates_before_parsing_and_does_not_echo_invalid_command(worker):
    with TestClient(create_host_worker_app(worker, "s" * 64)) as client:
        assert client.get("/status").status_code == 401
        assert client.post("/execute", json={"secret": "private-value"}).status_code == 401
        headers = {"Authorization": "Bearer " + "s" * 64, "X-Codex-Host-Api": "1"}
        assert client.get("/status", headers=headers).status_code == 200
        bad = client.post("/execute", headers=headers, json={"command": "private-value"})
        assert bad.status_code == 422
        assert "private-value" not in bad.text
        oversized = client.post("/execute", headers=headers, content=b"x" * 200000)
        assert oversized.status_code == 413
        assert client.delete("/runs/run-1", headers=headers).status_code == 409


@pytest.mark.skipif(sys.platform != "linux", reason="Real process-group and pipe tests require Linux")
@pytest.mark.parametrize("kind", ["complete", "failure", "timeout", "output", "closed_output", "cancel"])
def test_real_process_bounds_and_cancel(worker, kind):
    scripts = {
        "complete": "import sys; sys.stdin.read(); print('done')",
        "failure": "import sys; sys.stdin.read(); sys.exit(7)",
        "timeout": "import time,sys; sys.stdin.read(); time.sleep(20)",
        "closed_output": "import time,sys,os; sys.stdin.read(); os.close(1); os.close(2); time.sleep(20)",
        "cancel": "import time,sys; sys.stdin.read(); time.sleep(20)",
        "output": f"import sys; sys.stdin.read(); print('x' * {MAX_OUTPUT_BYTES + 20000})",
    }
    processes = []

    def spawn(args, **kwargs):
        process = subprocess.Popen([sys.executable, "-c", scripts[kind]], **kwargs)
        processes.append(process)
        return process

    worker._spawn = spawn
    request = invocation(worker, operation=HostCommand(command="probe", timeout_seconds=1))
    result = []
    thread = Thread(target=lambda: result.append(worker.execute(request)))
    thread.start()
    if kind == "cancel":
        for _ in range(200):
            if processes:
                break
            time.sleep(0.005)
        worker.cancel(request.run_id)
    thread.join(5)
    assert not thread.is_alive()
    expected = {"complete": "completed", "failure": "failed", "timeout": "timed_out", "closed_output": "timed_out", "output": "output_limit", "cancel": "cancelled"}
    assert result[0]["status"] == expected[kind]
    assert len(result[0]["output"].encode()) <= MAX_OUTPUT_BYTES
    assert processes[0].returncode is not None
    with pytest.raises(ProcessLookupError):
        os.kill(processes[0].pid, 0)
