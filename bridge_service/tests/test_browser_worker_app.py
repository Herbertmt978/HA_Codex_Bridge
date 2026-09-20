"""Static App-image contracts for the optional Chromium worker."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
APP_ROOT = ROOT / "codex_bridge_app"
LIBEXEC = APP_ROOT / "rootfs" / "usr" / "local" / "libexec" / "codex-bridge"
WORKER = LIBEXEC / "browser_worker.py"
POLICY = LIBEXEC / "browser_policy.py"
SANDBOX = LIBEXEC / "browser_sandbox.py"
RESOURCES = LIBEXEC / "browser_resources.py"
INITIALIZE = LIBEXEC / "initialize.sh"
SANDBOX_SELF_TEST = APP_ROOT / "rootfs" / "usr" / "local" / "bin" / "sandbox-self-test"


def test_worker_is_fixed_private_pipe_process_not_a_browser_facing_service() -> None:
    source = WORKER.read_text(encoding="utf-8") + SANDBOX.read_text(encoding="utf-8")

    assert "--remote-debugging-pipe" in source
    assert "--remote-debugging-port" not in source
    assert "--proxy-server=http://127.0.0.1:" in source
    assert "--proxy-bypass-list=<-loopback>" in source
    assert "--disable-quic" in source
    assert "disable_non_proxied_udp" in source
    assert "--disable-extensions" in source
    assert "--download-restrictions=3" in source
    assert "--no-sandbox" not in source
    assert "asyncio.start_server" not in WORKER.read_text(encoding="utf-8")
    assert "socket.socket" not in source
    assert "parse_browser_action" in source
    assert '"evaluate"' not in source
    assert '"cdp"' not in source.lower()
    assert "mkdtemp(prefix=\"codex-bridge-browser-\", dir=\"/tmp\")" in source
    assert "MAX_ACTIONS" in source
    assert "MAX_SESSION_SECONDS" in source
    assert "MAX_BROWSER_MEMORY_BYTES" in RESOURCES.read_text(encoding="utf-8")


def test_worker_uses_the_signed_connection_time_policy_proxy_but_does_not_self_attest() -> None:
    worker = WORKER.read_text(encoding="utf-8")
    policy = POLICY.read_text(encoding="utf-8")

    assert "UnixPolicyProxy" in worker
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


def test_app_startup_requires_separate_browser_proof_after_explicit_opt_in() -> None:
    startup = INITIALIZE.read_text(encoding="utf-8")
    self_test = SANDBOX_SELF_TEST.read_text(encoding="utf-8")

    assert "browser_worker.py" not in startup
    assert "bashio::config 'enable_browser'" in startup
    assert 'browser_attest.py --disabled' in startup
    assert 'browser_attest.py; then' in startup
    assert "browser-worker-attestation" not in self_test


def test_dockerfile_pins_the_alpine_chromium_package_and_verifies_its_version() -> None:
    dockerfile = (APP_ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "https://dl-cdn.alpinelinux.org/alpine/v3.24/community/x86_64/chromium-152.0.7977.82-r0.apk" in dockerfile
    assert "01af27a66507b9762ecb86ad0fb74ec08b2613817cb0e9a2e6848221dcde53b0" in dockerfile
    assert "96cab47f77a18f037f435c20f4a665a495395a57d42909bdefe9a561c5df5d8b" in dockerfile
    assert "/tmp/chromium-152.0.7977.82-r0.apk\" | sha256sum -c -" in dockerfile
    assert "apk add --no-cache /tmp/chromium-152.0.7977.82-r0.apk" in dockerfile
    assert 'test "$(/usr/bin/chromium-browser --product-version)" = "152.0.7977.82"' in dockerfile


def test_browser_has_a_distinct_child_profile_without_workspace_or_private_state() -> None:
    profile = (APP_ROOT / "apparmor.txt").read_text(encoding="utf-8")
    parent, child = profile.split("  profile browser_bwrap ", 1)
    assert "/opt/codex-browser/bin/bwrap Cx -> browser_bwrap," in parent
    assert "deny /config/{,**} rwklmx," in child
    assert "deny /data/{,**} rwklmx," in child
    assert "deny /run/codex-bridge/{,**} rwklmx," in child
    assert "deny /proc/**/{fd,fdinfo}/* rwklmx," in child
    assert "owner /proc/*/{uid_map,gid_map,setgroups} rw," in child
    assert "/proc/** rw," not in child
    assert "/config/workspaces" not in child
    assert "ptrace (" not in child
