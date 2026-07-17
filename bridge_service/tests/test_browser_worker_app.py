"""Static App-image contracts for the unadvertised Chromium worker slice."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
APP_ROOT = ROOT / "codex_bridge_app"
LIBEXEC = APP_ROOT / "rootfs" / "usr" / "local" / "libexec" / "codex-bridge"
WORKER = LIBEXEC / "browser_worker.py"
POLICY = LIBEXEC / "browser_policy.py"
INITIALIZE = LIBEXEC / "initialize.sh"
SANDBOX_SELF_TEST = APP_ROOT / "rootfs" / "usr" / "local" / "bin" / "sandbox-self-test"


def test_worker_is_fixed_private_pipe_process_not_a_browser_facing_service() -> None:
    source = WORKER.read_text(encoding="utf-8")

    assert "--remote-debugging-pipe" in source
    assert "--remote-debugging-port" not in source
    assert "--proxy-server=http://127.0.0.1:" in source
    assert "--proxy-bypass-list=<-loopback>" in source
    assert "--disable-quic" in source
    assert "disable_non_proxied_udp" in source
    assert "--disable-extensions" in source
    assert "--download-restrictions=3" in source
    assert "--no-sandbox" not in source
    assert "asyncio.start_server" not in source
    assert "socket.socket" not in source
    assert "parse_browser_action" in source
    assert '"evaluate"' not in source
    assert '"cdp"' not in source.lower()
    assert "mkdtemp(prefix=\"codex-bridge-browser-\", dir=\"/tmp\")" in source
    assert "MAX_ACTIONS" in source
    assert "MAX_SESSION_SECONDS" in source
    assert "MAX_BROWSER_MEMORY_BYTES" in source


def test_worker_uses_the_signed_connection_time_policy_proxy_but_does_not_self_attest() -> None:
    worker = WORKER.read_text(encoding="utf-8")
    policy = POLICY.read_text(encoding="utf-8")

    assert "LoopbackPolicyProxy" in worker
    assert "BrowserPolicyProxy" in policy
    assert "codex_bridge_service.browser_egress" in policy
    assert "browser_worker_attestation_ready" in worker
    assert "browser-worker-attestation" not in worker
    assert "chromium_sandbox" not in worker
    assert "egress_boundary" not in worker


def test_worker_revalidates_the_actual_main_frame_before_actions_and_captures() -> None:
    worker = WORKER.read_text(encoding="utf-8")

    # The runtime must not trust only the requested URL: these calls bind the
    # final top-level CDP navigation entry through normalize_public_url and
    # the NavigationBlocked path closes the profile before anything can be
    # captured or reused.
    assert '"Page.getNavigationHistory"' in worker
    assert "def public_main_frame_url" in worker
    assert "return normalize_public_url(url)" in worker
    assert "except NavigationBlocked:" in worker
    assert "session.public_main_frame_url()\n            options" in worker
    assert "session.public_main_frame_url()\n            result" in worker


def test_app_startup_keeps_the_browser_worker_inert_without_a_separate_proof() -> None:
    startup = INITIALIZE.read_text(encoding="utf-8")
    self_test = SANDBOX_SELF_TEST.read_text(encoding="utf-8")

    assert "browser_worker.py" not in startup
    assert "browser-worker-attestation" not in startup
    assert "browser-worker-attestation" not in self_test


def test_dockerfile_pins_the_alpine_chromium_package_and_verifies_its_version() -> None:
    dockerfile = (APP_ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "apk add --no-cache chromium=150.0.7871.124-r0" in dockerfile
    assert 'test "$(/usr/bin/chromium-browser --product-version)" = "150.0.7871.124"' in dockerfile
