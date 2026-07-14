"""Static/unit startup contracts for the non-root Home Assistant App.

The tests inspect the s6 bootstrap files and are intentionally runnable on
Windows.  They do not claim that a Linux image or Supervisor is available;
those runtime checks are a separate acceptance gate.
"""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import re
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
APP_ROOT = ROOT / "codex_bridge_app"
ROOTFS = APP_ROOT / "rootfs"
LIBEXEC = ROOTFS / "usr" / "local" / "libexec" / "codex-bridge"


def _load_helper(name: str):
    helper = LIBEXEC / f"{name}.py"
    assert helper.is_file(), f"{name} helper is missing"
    spec = importlib.util.spec_from_file_location(f"task20_{name}", helper)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _startup_files() -> list[Path]:
    assert ROOTFS.is_dir(), "Task 20 rootfs is missing"
    files = [path for path in ROOTFS.rglob("*") if path.is_file()]
    assert files, "Task 20 rootfs contains no startup services"
    return files


def _text() -> str:
    return "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in _startup_files()
        if path.suffix in {"", ".py", ".sh", ".toml", ".yaml", ".json"}
    )


def _run_scripts() -> list[Path]:
    return [path for path in _startup_files() if path.name == "run"]


def _oneshot_script(service: str) -> Path:
    up = ROOTFS / "etc" / "s6-overlay" / "s6-rc.d" / service / "up"
    command = up.read_text(encoding="utf-8").strip()
    assert command.startswith("/usr/local/libexec/codex-bridge/")
    assert "\n" not in command
    script = ROOTFS / command.removeprefix("/")
    assert script.is_file()
    return script


def _discovery_script() -> Path:
    script = LIBEXEC / "publish-discovery.sh"
    assert script.is_file()
    return script


def test_bootstrap_generates_token_cryptographically_and_replaces_atomically() -> None:
    text = _text()
    assert re.search(r"openssl\s+rand|/dev/urandom|secrets\.token_urlsafe", text)
    assert re.search(r"mktemp|\.tmp|\.new", text), (
        "token must be written to a temporary file"
    )
    assert re.search(r"\b(mv|rename|replace)\b", text), (
        "token replacement must be atomic"
    )
    assert re.search(r"(?:chmod\s+0?600|fchmod\([^\n]*0o600)", text)
    assert not re.search(
        r"(?:echo|printf)[^\n]*>\s*/data/bridge-token\s*$", text, re.MULTILINE
    )


@pytest.mark.parametrize(
    "path", ["/data/codex-home", "/data/bridge", "/config/workspaces"]
)
def test_private_directories_are_created_with_restrictive_mode(path: str) -> None:
    text = _text()
    assert path in text
    assert re.search(r"install\s+[^\n]*-m\s+0700", text) or re.search(
        r"(?:mode\s*=\s*0o700|fchmod\([^\n]*0o700)", text
    )
    assert re.search(r"\b(?:chown|fchown)\b", text)


def test_bootstrap_uses_descriptor_relative_nofollow_operations() -> None:
    bootstrap = LIBEXEC / "initialize_runtime.py"
    assert bootstrap.is_file(), "secure runtime bootstrap is missing"
    text = bootstrap.read_text(encoding="utf-8")
    assert "O_NOFOLLOW" in text
    assert "dir_fd=" in text
    assert "os.fchown" in text
    assert "os.fchmod" in text
    assert "os.replace" in text
    assert "shutil.rmtree" not in text


def test_codex_uses_file_credentials_and_bridge_token_file() -> None:
    text = _text()
    assert (
        "CODEX_HOME=/data/codex-home" in text or "CODEX_HOME /data/codex-home" in text
    )
    assert re.search(r"cli_auth_credentials_store\s*=\s*[\"']file[\"']", text)
    assert re.search(r"/data/bridge-token", text)
    assert not re.search(r"(?:CODEX_BRIDGE_)?AUTH_TOKEN=|BRIDGE_TOKEN=\$", text)


def test_long_lived_bridge_environment_is_sanitized_and_has_no_supervisor_token() -> (
    None
):
    scripts = _run_scripts()
    assert scripts, "s6 longrun must provide a run script"
    longrun = "\n".join(
        path.read_text(encoding="utf-8", errors="replace") for path in scripts
    )
    assert re.search(
        r"env\s+-i|env\s+--ignore-environment|env\s+(?:-u|--unset[= ])\s*SUPERVISOR_TOKEN|unset\s+SUPERVISOR_TOKEN",
        longrun,
    )
    assert "SUPERVISOR_TOKEN" not in re.sub(
        r"(?m)^\s*(?:unset|export|env\s+(?:-u|--unset[= ])\s*)SUPERVISOR_TOKEN\b.*$",
        "",
        longrun,
    )


def test_bridge_runs_as_one_non_root_uvicorn_worker() -> None:
    dockerfile = APP_ROOT / "Dockerfile"
    assert dockerfile.is_file(), "Task 20 Dockerfile is missing"
    docker = dockerfile.read_text(encoding="utf-8")
    assert re.search(r"(?i)\b(?:adduser|useradd|addgroup)\b", docker)
    assert not re.search(r"(?im)^\s*USER\s+root\b", docker)
    text = _text()
    assert re.search(r"python\s+-m\s+uvicorn\b[^\n]*\B--workers\s+1\b", text)
    assert re.search(r"s6-setuidgid\s+(?!root\b)\S+", text) or re.search(
        r"runuser\s+.*--\s*(?:uvicorn|python)", text
    )


def test_bridge_listens_on_the_app_network_while_readiness_stays_local() -> None:
    bridge_run = ROOTFS / "etc" / "s6-overlay" / "s6-rc.d" / "codex-bridge" / "run"
    bridge = bridge_run.read_text(encoding="utf-8")
    discovery = _discovery_script().read_text(encoding="utf-8")

    assert "CODEX_BRIDGE_HOST=0.0.0.0" in bridge
    assert re.search(r"uvicorn\b[^\n]*--host\s+0\.0\.0\.0\b", bridge)
    assert "127.0.0.1:8766" in discovery


@pytest.mark.parametrize("service", ["codex-bridge-init"])
def test_s6_oneshots_use_single_command_launchers(service: str) -> None:
    script = _oneshot_script(service)
    text = script.read_bytes()
    assert text.startswith(b"#!/usr/bin/with-contenv bashio\n")
    assert b"\r\n" not in text


def test_discovery_is_supervised_and_interruptible_during_readiness_wait() -> None:
    service = ROOTFS / "etc" / "s6-overlay" / "s6-rc.d" / "codex-bridge-discovery"
    assert (service / "type").read_text(encoding="utf-8").strip() == "longrun"
    run = (service / "run").read_text(encoding="utf-8")
    assert re.search(r"(?m)^exec\s+/usr/local/libexec/codex-bridge/", run)
    script = _discovery_script().read_text(encoding="utf-8")
    assert "trap terminate TERM INT" in script
    assert re.search(r"kill\s+-TERM\s+", script)
    assert re.search(r"wait\s+", script)
    assert re.search(r"unset\s+SUPERVISOR_TOKEN", script)
    assert re.search(r"exec\s+env\s+-i", script)
    assert not re.search(r"SUPERVISOR_TOKEN=.*publish_discovery", script)


def test_discovery_waits_for_authenticated_readiness_and_uses_exact_payload() -> None:
    text = _text()
    assert "/ready" in text
    assert re.search(r"\b(?:curl|wget|urlopen|urllib)\b", text)
    assert "Authorization" in text and "Bearer {token}" in text
    assert "publish_discovery.py" in text
    assert '"service": "codex_bridge"' in text
    assert "bashio::app.hostname" in text
    assert re.search(r"(?:port|PORT)[^\n]*8766", text)
    assert re.search(r"(?:api|API)[^\n]*1", text)
    assert re.search(r"(?:host|HOST)[^\n]*bashio::app\.hostname", text)

    module = _load_helper("publish_discovery")
    token = "a" * 64
    assert module.discovery_payload(host="local-codex-bridge", token=token) == {
        "service": "codex_bridge",
        "config": {
            "host": "local-codex-bridge",
            "port": 8766,
            "token": token,
            "api": {"minimum": 1, "maximum": 1},
        },
    }


def test_discovery_rejects_fatal_readiness_without_putting_token_in_argv() -> None:
    module = _load_helper("wait_for_bridge")

    assert module.acceptable_readiness(
        json.dumps({"readiness": {"state": "ready"}}).encode()
    )
    assert module.acceptable_readiness(
        json.dumps({"readiness": {"state": "auth_required"}}).encode()
    )
    assert module.acceptable_readiness(
        json.dumps({"readiness": {"state": "degraded_catalogue"}}).encode()
    )
    assert not module.acceptable_readiness(
        json.dumps({"readiness": {"state": "fatal"}}).encode()
    )
    assert not module.acceptable_readiness(b"not-json")

    discovery = _discovery_script().read_text(encoding="utf-8")
    assert not re.search(r"--header[^\n]*\$\{?token", discovery, re.IGNORECASE)
    assert "bashio::discovery" not in _text()
    publisher = (LIBEXEC / "publish_discovery.py").read_text(encoding="utf-8")
    assert "--token" not in publisher
    assert "SUPERVISOR_TOKEN" in publisher
    assert "print(" not in publisher


def test_discovery_posts_only_to_the_fixed_supervisor_endpoint() -> None:
    module = _load_helper("publish_discovery")
    bridge_token = "b" * 64
    supervisor_token = "supervisor.test.token"
    identity = "35b86ec12bbc4be083098650f746d420"

    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, maximum: int) -> bytes:
            assert maximum == module.MAX_RESPONSE_BYTES + 1
            return json.dumps({"result": "ok", "data": {"uuid": identity}}).encode()

    class FakeOpener:
        request = None
        timeout = None

        def open(self, request, *, timeout: int):
            self.request = request
            self.timeout = timeout
            return FakeResponse()

    opener = FakeOpener()
    assert (
        module._post_discovery(
            host="local-codex-bridge",
            token=bridge_token,
            supervisor_token=supervisor_token,
            opener=opener,
        )
        == identity
    )
    assert opener.timeout == 10
    assert opener.request.full_url == "http://supervisor/discovery"
    assert opener.request.method == "POST"
    assert supervisor_token not in opener.request.full_url
    assert bridge_token not in opener.request.full_url
    assert opener.request.get_header("Authorization") == f"Bearer {supervisor_token}"
    assert opener.request.get_header("Content-type") == "application/json"
    assert json.loads(opener.request.data) == module.discovery_payload(
        host="local-codex-bridge", token=bridge_token
    )


@pytest.mark.parametrize(
    "host",
    ["", "bad host", "http://elsewhere", "supervisor:80", "../bridge", "a" * 254],
)
def test_discovery_rejects_unsafe_hostnames(host: str) -> None:
    module = _load_helper("publish_discovery")
    with pytest.raises(module.DiscoveryError):
        module.discovery_payload(host=host, token="a" * 64)


@pytest.mark.parametrize(
    "payload",
    [
        b'{"uuid":"35b86ec12bbc4be083098650f746d420"}',
        b'{"result":"ok","data":{"uuid":"not-a-uuid"}}',
        b'{"result":"error","data":{"uuid":"35b86ec12bbc4be083098650f746d420"}}',
        b"not-json",
    ],
)
def test_discovery_rejects_invalid_supervisor_responses(payload: bytes) -> None:
    module = _load_helper("publish_discovery")
    with pytest.raises(module.DiscoveryError):
        module._parse_supervisor_response(payload)


def test_discovery_rejects_oversized_responses_and_redirects() -> None:
    module = _load_helper("publish_discovery")
    with pytest.raises(module.DiscoveryError):
        module._parse_supervisor_response(b"x" * (module.MAX_RESPONSE_BYTES + 1))
    request = module.Request("http://supervisor/discovery")
    assert (
        module._RejectRedirects().redirect_request(
            request,
            None,
            302,
            "Found",
            {},
            "http://untrusted.example/",
        )
        is None
    )


def test_discovery_publisher_refuses_non_root_execution(monkeypatch) -> None:
    module = _load_helper("publish_discovery")
    monkeypatch.setattr(module.os, "geteuid", lambda: 1000, raising=False)
    with pytest.raises(module.DiscoveryError):
        module.publish_discovery(
            host="local-codex-bridge", supervisor_token="supervisor.test.token"
        )


@pytest.mark.skipif(os.name == "nt", reason="descriptor-relative POSIX file contract")
def test_discovery_identity_replaces_a_symlink_without_following_it(
    tmp_path: Path,
) -> None:
    module = _load_helper("publish_discovery")
    identity = "35b86ec12bbc4be083098650f746d420"
    victim = tmp_path / "victim"
    victim.write_text("do-not-replace", encoding="utf-8")
    destination = tmp_path / "bridge-discovery-uuid"
    destination.symlink_to(victim)

    module._atomic_write_identity(
        destination, identity, uid=os.getuid(), gid=os.getgid()
    )

    assert victim.read_text(encoding="utf-8") == "do-not-replace"
    assert not destination.is_symlink()
    assert destination.read_text(encoding="ascii") == f"{identity}\n"
    assert destination.stat().st_mode & 0o777 == 0o600


def test_discovery_identity_is_persisted_and_logs_are_redacted() -> None:
    text = _text()
    assert re.search(r"(?:uuid|UUID|instance)[^\n]*/data/bridge", text)
    assert re.search(r"(?:install|touch|umask|chmod)[^\n]*(?:0600|0o600)", text)
    assert not re.search(
        r"(?:echo|printf|log|logger)[^\n]*(?:TOKEN|auth\.json|bridge-token)",
        text,
        re.IGNORECASE,
    )
    assert "set -x" not in text


def test_startup_uses_exec_and_handles_term_signal_for_clean_shutdown() -> None:
    scripts = _run_scripts()
    assert scripts, "s6 longrun must provide a run script"
    longruns = [
        script
        for script in scripts
        if (script.parent / "type").is_file()
        and (script.parent / "type").read_text(encoding="utf-8").strip() == "longrun"
    ]
    assert longruns, "Bridge must be an s6 longrun service"
    for script in longruns:
        text = script.read_text(encoding="utf-8", errors="replace")
        assert re.search(r"(?m)^\s*exec\s+", text)
        assert not re.search(r"(?:^|\s)&\s*$", text, re.MULTILINE), (
            "longrun must keep the Bridge in the foreground for signal-safe shutdown"
        )
