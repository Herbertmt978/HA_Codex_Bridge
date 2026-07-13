from pathlib import Path
from threading import Lock

from codex_bridge_service.runner import BridgeRunner
from codex_bridge_service.storage import BridgeStorage


def legacy_ha_runner(
    storage: BridgeStorage,
    codex_command: str = "codex",
    *,
    codex_home: Path | str | None = None,
    bypass_sandbox: bool = False,
    ignore_user_config: bool = False,
    idle_timeout_seconds: float | None = 1800.0,
    recover_stale_runs: bool = True,
) -> BridgeRunner:
    """Construct the retired HA exec adapter only for its legacy regression tests."""
    runner = object.__new__(BridgeRunner)
    runner.storage = storage
    runner.codex_command = codex_command
    runner.codex_home = codex_home
    runner.bypass_sandbox = bypass_sandbox
    runner.ignore_user_config = ignore_user_config
    runner.idle_timeout_seconds = idle_timeout_seconds
    runner._lock = Lock()
    runner._home_assistant_run_lock = Lock()
    runner._processes = {}
    runner._cancelled_runs = set()
    if recover_stale_runs:
        runner._recover_stale_runs()
    return runner
