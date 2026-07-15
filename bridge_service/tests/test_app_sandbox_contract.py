"""Task 21 RED contracts for the Home Assistant Codex tool sandbox.

These checks are deliberately readable and deterministic on a Windows checkout:
static App/AppArmor contracts and bounded attestation parsing run everywhere,
while POSIX-only owner/mode/link checks are explicitly gated.  The real
protected-HA execution gate remains separate; this file must fail until the
Task 21 sandbox boundary is installed.
"""

from __future__ import annotations

import ctypes
import errno
import hashlib
import importlib
import importlib.util
import json
import os
from pathlib import Path
import re
import runpy
import struct
import sys
import types

import pytest


ROOT = Path(__file__).resolve().parents[2]
APP_ROOT = ROOT / "codex_bridge_app"
ROOTFS = APP_ROOT / "rootfs"
LIBEXEC = ROOTFS / "usr" / "local" / "libexec" / "codex-bridge"
SANDBOX_CONTRACT = (
    ROOTFS / "usr" / "local" / "share" / "codex-bridge" / "sandbox-contract.json"
)
CODEX_WRAPPER = ROOTFS / "usr" / "local" / "bin" / "codex-ha"
SANDBOX_SELF_TEST = ROOTFS / "usr" / "local" / "bin" / "sandbox-self-test"
SANDBOX_PROBE = LIBEXEC / "sandbox_probe.py"
LOCK = APP_ROOT / "codex-release.json"

_FORBIDDEN_BYPASSES = (
    "--dangerously-bypass-approvals-and-sandbox",
    "--no-sandbox",
    "--full-access",
    "--allow-all",
    "dangerFullAccess",
    "danger-full-access",
)


def _sandbox_module():
    """Import the Task 21 verifier as a test failure, not a collection crash."""

    try:
        return importlib.import_module("codex_bridge_service.sandbox_attestation")
    except ModuleNotFoundError as exc:
        pytest.fail(f"Task 21 sandbox_attestation module is missing: {exc}")


def _sandbox_probe_module():
    """Load the proc-less probe as a module for deterministic unit checks."""

    spec = importlib.util.spec_from_file_location(
        "sandbox_probe_under_test", SANDBOX_PROBE
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sandbox_self_test_namespace(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """Load the Linux self-test helpers without executing its CLI on Windows."""

    monkeypatch.setitem(sys.modules, "pwd", types.SimpleNamespace())
    monkeypatch.setitem(sys.modules, "grp", types.SimpleNamespace())
    monkeypatch.setattr(os, "O_DIRECTORY", 0, raising=False)
    return runpy.run_path(str(SANDBOX_SELF_TEST))


def _canonical_json(path: Path, value: object) -> bytes:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"
    path.write_bytes(payload.encode("utf-8"))
    return path.read_bytes()


def _contract_payload() -> dict[str, object]:
    return {
        "schema_version": 2,
        "architecture": "amd64",
        "codex_version": "0.144.4",
        "release_lock_digest": "a" * 64,
        "executables": {
            "codex": {
                "path": "/usr/local/bin/codex",
                "sha256": "b" * 64,
            },
            "bwrap": {
                "path": "/usr/local/bin/bwrap",
                "sha256": "c" * 64,
            },
            "bwrap_launcher": {
                "path": "/opt/codex/bin/bwrap",
                "sha256": "d" * 64,
            },
        },
        "apparmor": {
            "parent_profile_suffix": "codex_bridge",
            "bwrap_profile_suffix": "//codex_bwrap",
        },
    }


def _valid_attestation(tmp_path: Path) -> tuple[Path, Path]:
    contract_path = tmp_path / "sandbox-contract.json"
    contract_bytes = _canonical_json(contract_path, _contract_payload())
    attestation = {
        "schema_version": 1,
        "contract_sha256": hashlib.sha256(contract_bytes).hexdigest(),
        "attested": True,
    }
    attestation_path = tmp_path / "sandbox-attestation.json"
    _canonical_json(attestation_path, attestation)
    if os.name != "nt":
        contract_path.chmod(0o600)
        attestation_path.chmod(0o600)
    return contract_path, attestation_path


def _verify(contract_path: Path, attestation_path: Path) -> bool:
    module = _sandbox_module()
    kwargs: dict[str, object] = {}
    if os.name != "nt":
        kwargs.update(expected_uid=os.getuid(), expected_gid=os.getgid())
    return bool(
        module.verify_sandbox_attestation(
            contract_path=contract_path,
            attestation_path=attestation_path,
            **kwargs,
        )
    )


def test_codex_wrapper_forces_modern_bwrap_and_rejects_all_bypass_flags() -> None:
    assert CODEX_WRAPPER.is_file(), "Task 21 Codex-only wrapper is missing"
    assert not CODEX_WRAPPER.is_symlink()
    text = CODEX_WRAPPER.read_text(encoding="utf-8")
    assert "/usr/local/bin/codex" in text
    assert "--strict-config" in text
    assert re.search(r"features\.use_legacy_landlock\s*=\s*false", text)
    assert not any(flag in text for flag in _FORBIDDEN_BYPASSES)
    assert "BWRAP" not in text.upper() or "/usr/local/bin/bwrap" in text
    assert not re.search(r"\$\{?BWRAP(?:_BIN)?\b", text, re.I)
    assert "CODEX_COMMAND" not in text


def test_codex_wrapper_allows_only_the_offline_bundled_catalogue_without_strict_config() -> None:
    text = CODEX_WRAPPER.read_text(encoding="utf-8")

    assert '[ "$#" -eq 3 ]' in text
    assert '[ "$1" = "debug" ]' in text
    assert '[ "$2" = "models" ]' in text
    assert '[ "$3" = "--bundled" ]' in text
    bundled_exec = "exec /usr/local/bin/codex debug models --bundled"
    strict_exec = "exec /usr/local/bin/codex \\\n    --strict-config"
    assert text.count(bundled_exec) == 1
    assert text.count("exec /usr/local/bin/codex") == 2
    assert text.index(bundled_exec) < text.index(strict_exec)


def test_bwrap_wrapper_filters_nested_namespaces_and_netlink() -> None:
    wrapper = LIBEXEC / "bwrap-wrapper.py"
    assert wrapper.is_file(), "the Bubblewrap hardening wrapper is missing"
    text = wrapper.read_text(encoding="utf-8")
    assert "/usr/local/bin/bwrap" in text
    assert "--add-seccomp-fd" in text
    for boundary in (
        "CLONE_NEWUSER",
        "unshare",
        "setns",
        "clone3",
        "AF_NETLINK",
    ):
        assert boundary in text
    assert 'arguments in (["--help"], ["--version"])' in text
    assert 'if "--disable-userns" in arguments' in text
    assert 'struct.pack("<HBBI"' in text
    assert "AUDIT_ARCH_X86_64" in text
    assert "AUDIT_ARCH_AARCH64" in text

    dockerfile = (APP_ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "test ! -e /opt/codex/bin/bwrap" in dockerfile
    assert "/opt/codex/bin/bwrap" in dockerfile

    service = (
        ROOTFS / "etc" / "s6-overlay" / "s6-rc.d" / "codex-bridge" / "run"
    ).read_text(encoding="utf-8")
    self_test = (ROOTFS / "usr" / "local" / "bin" / "sandbox-self-test").read_text(
        encoding="utf-8"
    )
    expected_path = (
        "PATH=/opt/codex/bin:/usr/local/bin:/usr/local/sbin:"
        "/usr/sbin:/usr/bin:/sbin:/bin"
    )
    assert expected_path in service
    assert expected_path in self_test


def test_bwrap_seccomp_program_routes_every_locked_branch() -> None:
    namespace = runpy.run_path(str(LIBEXEC / "bwrap-wrapper.py"))

    def evaluate(
        instructions: list[tuple[int, int, int, int]],
        *,
        architecture: int,
        syscall_number: int,
        argument_zero: int = 0,
    ) -> int:
        accumulator = 0
        program_counter = 0
        while 0 <= program_counter < len(instructions):
            code, jump_true, jump_false, value = instructions[program_counter]
            if code == namespace["BPF_LD_W_ABS"]:
                accumulator = {
                    namespace["SECCOMP_DATA_NR_OFFSET"]: syscall_number,
                    namespace["SECCOMP_DATA_ARCH_OFFSET"]: architecture,
                    namespace["SECCOMP_DATA_ARG0_OFFSET"]: argument_zero,
                }[value]
                program_counter += 1
            elif code == namespace["BPF_JMP_JEQ_K"]:
                program_counter += 1 + (
                    jump_true if accumulator == value else jump_false
                )
            elif code == namespace["BPF_JMP_JSET_K"]:
                program_counter += 1 + (
                    jump_true if accumulator & value else jump_false
                )
            elif code == namespace["BPF_RET_K"]:
                return value
            else:
                pytest.fail(f"unexpected BPF opcode: {code}")
        pytest.fail("seccomp program escaped without a return instruction")

    denied = namespace["SECCOMP_RET_ERRNO"] | namespace["LINUX_EPERM"]
    unavailable = namespace["SECCOMP_RET_ERRNO"] | namespace["LINUX_ENOSYS"]
    allowed = namespace["SECCOMP_RET_ALLOW"]
    for table in namespace["SYSCALL_TABLES"].values():
        payload = namespace["_filter"](table)
        assert len(payload) == 20 * 8
        instructions = [
            struct.unpack("<HBBI", payload[offset : offset + 8])
            for offset in range(0, len(payload), 8)
        ]

        def run(number: int, argument: int = 0) -> int:
            return evaluate(
                instructions,
                architecture=table.audit_arch,
                syscall_number=number,
                argument_zero=argument,
            )
        assert run(table.unshare) == denied
        assert run(table.setns) == denied
        assert run(table.clone3) == unavailable
        assert run(table.socket, namespace["AF_NETLINK"]) == denied
        assert run(table.socket, 2) == allowed
        assert run(table.clone, namespace["CLONE_NEWUSER"]) == denied
        assert run(table.clone) == allowed
        assert run(0) == allowed
        assert evaluate(
            instructions,
            architecture=0,
            syscall_number=0,
        ) == namespace["SECCOMP_RET_KILL_PROCESS"]


def test_bridge_s6_service_uses_the_dedicated_workspace_cwd() -> None:
    run = ROOTFS / "etc" / "s6-overlay" / "s6-rc.d" / "codex-bridge" / "run"
    assert run.is_file(), "codex-bridge s6 longrun is missing"
    text = run.read_text(encoding="utf-8")
    assert re.search(r"(?:cd\s+|--cwd\s+|--workdir\s+)/config/workspaces\b", text)


def _apparmor_profile_body(profile: str) -> str:
    text = (APP_ROOT / "apparmor.txt").read_text(encoding="utf-8")
    # Supervisor may qualify the outer profile with its runtime slug; the
    # contract therefore names child profiles by stable suffix.
    leaf = profile.rsplit("//", 1)[-1]
    match = re.search(rf"(?m)^\s*profile\s+{re.escape(leaf)}\b[^{{]*{{", text)
    assert match, f"AppArmor profile {profile!r} is missing"
    start = match.end()
    depth = 1
    for index in range(start, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return text[start:index]
    pytest.fail(f"AppArmor profile {profile!r} has unbalanced braces")


def test_apparmor_has_an_exact_bwrap_child_transition() -> None:
    profile = (APP_ROOT / "apparmor.txt").read_text(encoding="utf-8")
    outer = _apparmor_profile_body("codex_bridge").split("profile codex_bwrap", 1)[0]
    assert re.search(r"(?m)^\s*/init\s+rix\s*,", outer)
    assert "/run/{,**} rwkix," in outer
    assert "/usr/lib/bashio/** rix," in outer
    assert "/usr/local/bin/ r," in outer
    assert "/usr/local/libexec/codex-bridge/ r," in outer
    assert "/config/.sandbox-self-test-* rw," in outer
    assert "/config/**" not in outer
    assert re.search(
        r"(?m)^\s*/opt/codex/bin/bwrap\s+Cx\s+->\s+codex_bwrap\s*,",
        outer,
    ), "the hardened bwrap launcher must enter the exact constrained child profile"
    assert not re.search(
        r"(?m)^\s*/usr/local/bin/bwrap\s+[^\n]*x[^\n]*,",
        outer,
    ), "the bundled bwrap must not be directly executable by the parent profile"
    assert len(re.findall(r"(?m)^\s*profile\s+", profile)) == 2

    bwrap = _apparmor_profile_body("//codex_bwrap")
    assert re.search(r"(?m)^\s*/usr/local/bin/codex\s+ix\s*,", bwrap)
    codex_rules = re.findall(r"(?m)^\s*/usr/local/bin/codex\s+([^\n]+)", bwrap)
    assert codex_rules, "the bwrap profile must explicitly inherit for Codex"
    assert all(
        not re.search(r"(?:\b[PCU]x\b|->|//codex)", rule)
        for rule in codex_rules
    ), "Codex must not use a fallback or nested AppArmor transition"
    assert not re.search(r"(?m)^\s*/data/\*\*", bwrap)
    assert "/config/**" not in bwrap
    assert not re.search(r"(?m)^\s*network\s+(?:inet|inet6|unix)", bwrap)
    assert "/config/workspaces/** rwk," in bwrap
    assert "/usr/local/libexec/codex-bridge/ r," in bwrap
    assert "/usr/local/bin/bwrap ix," in bwrap
    assert "/opt/codex/bin/bwrap r," in bwrap


def test_apparmor_grants_loopback_setup_only_to_bwrap() -> None:
    bwrap = _apparmor_profile_body("//codex_bwrap")
    assert re.search(r"(?m)^\s*capability\s+net_admin\s*,", bwrap)
    assert re.search(r"(?m)^\s*capability\s+mknod\s*,", bwrap)
    assert "/newroot/{,**} rwkl," in bwrap
    for operation in ("mount", "umount", "pivot_root"):
        assert re.search(rf"(?m)^\s*{re.escape(operation)}\s*,", bwrap)
    assert re.search(r"(?m)^\s*network\s+netlink\s+(?:raw|dgram)\s*,", bwrap)


def test_apparmor_is_haos_3_compatible_and_probe_denies_nested_userns() -> None:
    profile = (APP_ROOT / "apparmor.txt").read_text(encoding="utf-8")
    assert not re.search(r"(?m)^\s*(?:deny\s+)?userns\s*,", profile)
    probe = SANDBOX_PROBE.read_text(encoding="utf-8")
    assert "CLONE_NEWUSER" in probe
    assert "nested_user_namespace_denied" in probe
    self_test = SANDBOX_SELF_TEST.read_text(encoding="utf-8")
    assert '"nested_user_namespace_denied"' in self_test


def test_sandbox_probe_uses_behavioral_denial_checks() -> None:
    """The fixed probe must exercise denied operations, not just inspect policy text."""

    probe = SANDBOX_PROBE.read_text(encoding="utf-8")
    self_test = SANDBOX_SELF_TEST.read_text(encoding="utf-8")
    for check in (
        "nested_user_namespace_denied",
        "clone_user_namespace_denied",
        "setns_denied",
        "clone3_unavailable",
        "mount_denied",
        "umount_denied",
        "pivot_root_denied",
        "ipv4_network_denied",
        "ipv6_network_denied",
        "netlink_network_denied",
        "inherited_sockets_absent",
    ):
        assert f'"{check}"' in probe
        assert f'"{check}"' in self_test
    assert "ctypes.CDLL" in probe
    assert "libc.unshare" in probe
    assert "0x10000000" in probe
    assert "errno.ENOSYS" in probe
    assert "CLONE_SYSCALLS" in probe
    assert "SETNS_SYSCALLS" in probe
    assert "CLONE3_SYSCALLS" in probe
    for operation in ("mount", "umount", "pivot_root"):
        assert re.search(rf"def _[a-z_]*{re.escape(operation)}[a-z_]*\(", probe)
        assert operation in probe
    assert re.search(r"_socket_creation_denied\(socket\.AF_INET\)", probe)
    assert re.search(r"_socket_creation_denied\(socket\.AF_INET6\)", probe)
    assert re.search(
        r"_socket_creation_denied\(\s*socket\.AF_NETLINK,\s*socket\.SOCK_RAW\s*\)",
        probe,
    )
    assert re.search(r"def _no_inherited_sockets\(", probe)


def test_sandbox_probe_covers_private_and_outside_read_sentinels() -> None:
    assert SANDBOX_PROBE.is_file(), "sandbox_probe.py is missing"
    text = SANDBOX_PROBE.read_text(encoding="utf-8")
    for path in (
        "/data/codex-home",
        ".sandbox-auth-",
        "/data/bridge-token",
        "/config/workspaces",
    ):
        assert path in text
    for check in (
        "sibling_workspace_read_denied",
        "sibling_workspace_write_denied",
    ):
        assert f'"{check}"' in text
        assert f'"{check}"' in SANDBOX_SELF_TEST.read_text(encoding="utf-8")
    assert "/etc/passwd" in text or "/tmp" in text
    lowered = text.lower()
    for target in ("supervisor", "homeassistant", "127.0.0.1", "192.168.", "openai"):
        assert target in lowered


def test_sandbox_self_test_uses_managed_permission_profile_not_legacy_policy() -> None:
    self_test = SANDBOX_SELF_TEST.read_text(encoding="utf-8")
    assert "permissionProfile/list" in self_test
    assert "ha_bridge" in self_test
    assert "ha_observe" in self_test
    assert "default_permissions" in self_test
    assert "activePermissionProfile" in self_test
    assert "sandboxPolicy" not in self_test


def test_app_bakes_managed_permission_profile_requirements() -> None:
    requirements = ROOTFS / "etc" / "codex" / "requirements.toml"
    assert requirements.is_file(), "managed Codex requirements are missing"
    text = requirements.read_text(encoding="utf-8")
    assert re.search(r"default_permissions\s*=\s*[\"']ha_bridge[\"']", text)
    assert "[allowed_permission_profiles]" in text
    assert re.search(r"(?m)^ha_bridge\s*=\s*true\s*$", text)
    assert re.search(r"(?m)^ha_observe\s*=\s*true\s*$", text)
    for built_in in (":read-only", ":workspace", ":danger-full-access"):
        assert re.search(
            rf"(?m)^[\"']{re.escape(built_in)}[\"']\s*=\s*false\s*$",
            text,
        )


def test_runtime_bootstrap_writes_the_locked_ha_bridge_profile() -> None:
    bootstrap = ROOTFS / "usr" / "local" / "libexec" / "codex-bridge" / "initialize_runtime.py"
    text = bootstrap.read_text(encoding="utf-8")
    assert "default_permissions" in text
    assert "ha_bridge" in text
    assert "permissions.ha_bridge.filesystem" in text
    assert '":minimal" = "read"' in text
    assert '[permissions.ha_bridge.filesystem.":workspace_roots"]' in text
    assert '"." = "write"' in text
    for metadata_dir in (".codex", ".git", ".agents", ".cursor", ".vscode"):
        assert f'"{metadata_dir}" = "write"' in text
    assert "allow_local_binding = false" in text
    assert "allow_upstream_proxy = false" in text
    assert "managed Codex configuration could not be verified" in text


def test_runtime_bootstrap_writes_a_read_only_observe_profile() -> None:
    bootstrap = ROOTFS / "usr" / "local" / "libexec" / "codex-bridge" / "initialize_runtime.py"
    text = bootstrap.read_text(encoding="utf-8")
    observe = text.split("[permissions.ha_observe]", 1)[1].split(
        "[permissions.ha_bridge]", 1
    )[0]

    assert "Home Assistant read-only workspace sandbox" in observe
    assert "permissions.ha_observe.filesystem" in observe
    assert '":minimal" = "read"' in observe
    assert '[permissions.ha_observe.filesystem.":workspace_roots"]' in observe
    assert '"." = "read"' in observe
    assert 'enabled = false' in observe
    assert 'allow_local_binding = false' in observe
    assert 'allow_upstream_proxy = false' in observe
    assert '"write"' not in observe


def test_sandbox_probe_attests_final_state_without_exposing_procfs() -> None:
    text = SANDBOX_PROBE.read_text(encoding="utf-8")
    assert "/proc/self" not in text
    assert "LSM_GET_SELF_ATTR_SYSCALLS" in text
    assert "LSM_ATTR_CURRENT" in text
    assert "LSM_ID_APPARMOR" in text
    assert "_apparmor_profile_matches" in text
    assert "CAPGET_SYSCALLS" in text
    assert "LINUX_CAPABILITY_VERSION_3" in text
    assert "PR_GET_NO_NEW_PRIVS" in text
    assert "PR_GET_SECCOMP" in text
    assert "statvfs" in text
    assert '"root_filesystem_write_denied"' in text
    assert "_create_denied" in text
    assert "os.fstat" in text
    assert "stat.S_ISSOCK" in text
    assert "os.fsencode(workspace)" in text
    assert re.search(r'"zero_[a-z_]*capabilities"', text)
    assert "/proc/1/exe" not in text
    assert "/proc/1/cmdline" not in text

    self_test = SANDBOX_SELF_TEST.read_text(encoding="utf-8")
    assert '"supervisor_environment_absent"' in self_test
    assert '"sandbox_mounts_present"' not in self_test
    assert '"namespaces"' not in self_test
    capability_checks = set(re.findall(r'"(zero_[a-z_]*capabilities)"', text))
    assert capability_checks
    assert capability_checks <= set(re.findall(r'"(zero_[a-z_]*capabilities)"', self_test))


def test_sandbox_probe_prctl_attestation_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _sandbox_probe_module()

    class FakeLibc:
        def __init__(self, result: int) -> None:
            self.result = result

        def prctl(self, *_args: object) -> int:
            return self.result

    monkeypatch.setattr(module, "_libc", lambda: FakeLibc(0))
    assert module._prctl_value(module.PR_GET_NO_NEW_PRIVS) == 0
    monkeypatch.setattr(module, "_libc", lambda: FakeLibc(1))
    assert module._prctl_value(module.PR_GET_NO_NEW_PRIVS) == 1
    monkeypatch.setattr(module, "_libc", lambda: FakeLibc(-1))
    assert module._prctl_value(module.PR_GET_NO_NEW_PRIVS) is None


def test_sandbox_probe_capability_range_requires_zero_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _sandbox_probe_module()

    class FakeLibc:
        def __init__(self, mode: str) -> None:
            self.mode = mode

        def prctl(
            self, _option: object, capability_or_op: object, *_args: object
        ) -> int:
            capability = int(capability_or_op.value)  # type: ignore[attr-defined]
            if self.mode == "nonzero" and capability == 3:
                return 1
            if self.mode == "error":
                ctypes.set_errno(errno.EIO)
                return -1
            return 0

    monkeypatch.setattr(module, "_libc", lambda: FakeLibc("zero"))
    assert module._capability_range_zero(ambient=False)
    assert module._capability_range_zero(ambient=True)
    monkeypatch.setattr(module, "_libc", lambda: FakeLibc("nonzero"))
    assert not module._capability_range_zero(ambient=False)
    monkeypatch.setattr(module, "_libc", lambda: FakeLibc("error"))
    assert not module._capability_range_zero(ambient=True)


def test_sandbox_probe_capget_handles_zero_nonzero_and_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _sandbox_probe_module()
    monkeypatch.setattr(
        module.os,
        "uname",
        lambda: type("Uname", (), {"machine": "amd64"})(),
        raising=False,
    )

    class FakeLibc:
        def __init__(self, result: int, values: tuple[int, int, int]) -> None:
            self.result = result
            self.values = values

        def syscall(self, *_args: object) -> int:
            if self.result != 0:
                return self.result
            data = _args[2]._obj  # type: ignore[attr-defined]
            data[0].effective, data[0].permitted, data[0].inheritable = self.values
            return 0

    monkeypatch.setattr(module, "_libc", lambda: FakeLibc(0, (0, 0, 0)))
    assert module._capability_sets() == {
        "effective": 0,
        "permitted": 0,
        "inheritable": 0,
    }
    monkeypatch.setattr(module, "_libc", lambda: FakeLibc(0, (1, 2, 4)))
    assert module._capability_sets() == {
        "effective": 1,
        "permitted": 2,
        "inheritable": 4,
    }
    monkeypatch.setattr(module, "_libc", lambda: FakeLibc(-1, (0, 0, 0)))
    assert module._capability_sets() is None


def test_sandbox_probe_lsm_get_self_attr_parses_exact_apparmor_label(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _sandbox_probe_module()
    monkeypatch.setattr(
        module.os,
        "uname",
        lambda: type("Uname", (), {"machine": "amd64"})(),
        raising=False,
    )
    label = b"codex_bridge//codex_bwrap (enforce)\0"
    alignment = ctypes.alignment(module._LsmContext)
    unaligned_length = ctypes.sizeof(module._LsmContext) + len(label)
    record_length = (unaligned_length + alignment - 1) // alignment * alignment
    header = module._LsmContext(
        id=module.LSM_ID_APPARMOR,
        flags=0,
        length=record_length,
        context_length=len(label),
    )
    payload = bytes(header) + label + b"\0" * (record_length - unaligned_length)

    class FakeLibc:
        def __init__(self, response: bytes = payload, record_count: int = 1) -> None:
            self.calls = 0
            self.response = response
            self.record_count = record_count

        def syscall(self, *_args: object) -> int:
            self.calls += 1
            size = _args[3]._obj  # type: ignore[attr-defined]
            if self.calls == 1:
                size.value = len(self.response)
                ctypes.set_errno(errno.E2BIG)
                return -1
            ctypes.memmove(_args[2], self.response, len(self.response))
            size.value = len(self.response)
            return self.record_count

    fake = FakeLibc()
    monkeypatch.setattr(module, "_libc", lambda: fake)
    assert module._apparmor_profile_matches("codex_bridge//codex_bwrap")

    fake = FakeLibc()
    monkeypatch.setattr(module, "_libc", lambda: fake)
    assert not module._apparmor_profile_matches("codex_bridge//other")

    fake = FakeLibc(payload + payload, record_count=1)
    monkeypatch.setattr(module, "_libc", lambda: fake)
    assert not module._apparmor_profile_matches("codex_bridge//codex_bwrap")

    fake = FakeLibc(payload, record_count=2)
    monkeypatch.setattr(module, "_libc", lambda: fake)
    assert not module._apparmor_profile_matches("codex_bridge//codex_bwrap")

    unaligned_header = module._LsmContext(
        id=module.LSM_ID_APPARMOR,
        flags=0,
        length=unaligned_length,
        context_length=len(label),
    )
    fake = FakeLibc(bytes(unaligned_header) + label)
    monkeypatch.setattr(module, "_libc", lambda: fake)
    assert not module._apparmor_profile_matches("codex_bridge//codex_bwrap")

    # Live HAOS AppArmor reports ctx_len without the NUL while the aligned
    # record padding is zero-filled. This is a bounded terminator, not an
    # unterminated context.
    unpadded = label.removesuffix(b"\0")
    live_length = ctypes.sizeof(module._LsmContext) + len(unpadded)
    padded_length = (live_length + alignment - 1) // alignment * alignment
    padding_length = padded_length - live_length
    padded_header = module._LsmContext(
        id=module.LSM_ID_APPARMOR,
        flags=0,
        length=padded_length,
        context_length=len(unpadded),
    )
    fake = FakeLibc(bytes(padded_header) + unpadded + b"\0" * padding_length)
    monkeypatch.setattr(module, "_libc", lambda: fake)
    assert module._apparmor_profile_matches("codex_bridge//codex_bwrap")

    bad_padding = bytearray(b"\0" * padding_length)
    bad_padding[-1] = ord("X")
    fake = FakeLibc(bytes(padded_header) + unpadded + bytes(bad_padding))
    monkeypatch.setattr(module, "_libc", lambda: fake)
    assert not module._apparmor_profile_matches("codex_bridge//codex_bwrap")

    unterminated_profile = "codex"
    while (
        ctypes.sizeof(module._LsmContext)
        + len(f"{unterminated_profile} (enforce)".encode("ascii"))
    ) % alignment:
        unterminated_profile += "x"
    unterminated = f"{unterminated_profile} (enforce)".encode("ascii")
    unterminated_header = module._LsmContext(
        id=module.LSM_ID_APPARMOR,
        flags=0,
        length=ctypes.sizeof(module._LsmContext) + len(unterminated),
        context_length=len(unterminated),
    )
    fake = FakeLibc(bytes(unterminated_header) + unterminated)
    monkeypatch.setattr(module, "_libc", lambda: fake)
    assert not module._apparmor_profile_matches(unterminated_profile)

    class ErrorLibc(FakeLibc):
        def syscall(self, *_args: object) -> int:
            ctypes.set_errno(errno.EPERM)
            return -1

    monkeypatch.setattr(module, "_libc", lambda: ErrorLibc())
    assert not module._apparmor_profile_matches("codex_bridge//codex_bwrap")


def test_bridge_profile_accepts_only_canonical_roots_inside_workspace(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    namespace = _sandbox_self_test_namespace(monkeypatch)
    assert_profile_thread = namespace["_assert_profile_thread"]
    workspace = tmp_path / ".sandbox-self-test-0123456789abcdef0123456789abcdef"
    workspace.mkdir()
    roots = [
        *(str(workspace / name) for name in (".agents", ".codex", ".cursor", ".git", ".vscode")),
    ]

    class FakeClient:
        def request(self, *_args: object, **_kwargs: object) -> dict[str, object]:
            return {
                "approvalPolicy": "on-request",
                "approvalsReviewer": "user",
                "thread": {"ephemeral": True},
                "activePermissionProfile": {"id": "ha_bridge", "extends": None},
                "sandbox": {
                    "type": "workspaceWrite",
                    "writableRoots": roots,
                    "networkAccess": False,
                    "excludeSlashTmp": True,
                    "excludeTmpdirEnvVar": True,
                },
            }

    assert_profile_thread(
        FakeClient(), workspace=workspace, permission_profile="ha_bridge"
    )
    assert namespace["_writable_roots_within_workspace"]([], workspace=workspace)


@pytest.mark.parametrize(
    "case",
    [
        "outside",
        "sibling",
        "traversal",
        "contained-parent",
        "duplicate",
        "relative",
        "nul",
    ],
)
def test_bridge_profile_rejects_roots_outside_workspace_or_noncanonical(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    case: str,
) -> None:
    namespace = _sandbox_self_test_namespace(monkeypatch)
    assert_profile_thread = namespace["_assert_profile_thread"]
    self_test_error = namespace["SelfTestError"]
    workspace = tmp_path / ".sandbox-self-test-0123456789abcdef0123456789abcdef"
    workspace.mkdir()
    sibling = tmp_path / "sibling"
    sibling.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    roots_by_case = {
        "outside": [str(outside)],
        "sibling": [str(workspace), str(sibling)],
        "traversal": [str(workspace), str(workspace / ".." / "escape")],
        "contained-parent": [str(workspace / "sub" / ".." / ".codex")],
        "duplicate": [str(workspace), str(workspace)],
        "relative": [str(workspace), "relative/path"],
        "nul": [str(workspace / "invalid\0root")],
    }
    roots = roots_by_case[case]

    class FakeClient:
        def request(self, *_args: object, **_kwargs: object) -> dict[str, object]:
            return {
                "approvalPolicy": "on-request",
                "approvalsReviewer": "user",
                "thread": {"ephemeral": True},
                "activePermissionProfile": {"id": "ha_bridge", "extends": None},
                "sandbox": {
                    "type": "workspaceWrite",
                    "writableRoots": roots,
                    "networkAccess": False,
                    "excludeSlashTmp": True,
                    "excludeTmpdirEnvVar": True,
                },
            }

    with pytest.raises(self_test_error):
        assert_profile_thread(
            FakeClient(), workspace=workspace, permission_profile="ha_bridge"
        )


def test_bridge_profile_accepts_workspace_symlink_and_resolves_roots(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    namespace = _sandbox_self_test_namespace(monkeypatch)
    real_workspace = tmp_path / "real-workspace"
    real_workspace.mkdir()
    workspace = tmp_path / "workspace-link"
    try:
        workspace.symlink_to(real_workspace, target_is_directory=True)
    except OSError:
        pytest.skip("host does not permit directory symlink creation")

    assert namespace["_writable_roots_within_workspace"](
        [str(workspace / ".codex")], workspace=workspace
    )


def test_bridge_profile_rejects_child_symlink_resolving_outside_workspace(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    namespace = _sandbox_self_test_namespace(monkeypatch)
    real_workspace = tmp_path / "workspace"
    real_workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    child_link = real_workspace / "escape"
    try:
        child_link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("host does not permit directory symlink creation")

    assert not namespace["_writable_roots_within_workspace"](
        [str(child_link)], workspace=real_workspace
    )


def test_init_invokes_self_test_and_preserves_fatal_readiness_on_failure() -> None:
    init = LIBEXEC / "initialize.sh"
    assert init.is_file(), "App initialization script is missing"
    assert SANDBOX_SELF_TEST.is_file(), "sandbox-self-test executable is missing"
    assert not SANDBOX_SELF_TEST.is_symlink()
    self_test_text = SANDBOX_SELF_TEST.read_text(encoding="utf-8")
    assert not re.search(r"\|\|\s*true\b", self_test_text)
    assert not any(flag in self_test_text for flag in _FORBIDDEN_BYPASSES)
    text = init.read_text(encoding="utf-8")
    assert "sandbox-self-test" in text
    assert re.search(r"sandbox-self-test[^\n]*(?:\|\||;|if\b)", text)
    all_text = "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in ROOTFS.rglob("*")
        if path.is_file() and path.suffix not in {".pyc"}
    )
    assert "sandbox_unavailable" in all_text
    assert "fatal" in all_text


def test_generated_sandbox_contract_is_immutable_and_tied_to_task19_lock() -> None:
    stage = (ROOT / "scripts" / "stage_app_context.py").read_text(encoding="utf-8")
    assert "_sandbox_contract" in stage
    assert "sandbox-contract.json" in stage
    lock_digest = hashlib.sha256(LOCK.read_bytes()).hexdigest()
    spec = importlib.util.spec_from_file_location(
        "task21_stage_app_context", ROOT / "scripts" / "stage_app_context.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    payload = module._sandbox_contract(lock, "amd64", lock_digest)
    assert isinstance(payload, dict)
    assert payload["schema_version"] == 2
    assert payload["architecture"] == "amd64"
    assert payload["release_lock_digest"] == lock_digest
    assert set(payload) == {
        "schema_version",
        "architecture",
        "codex_version",
        "release_lock_digest",
        "executables",
        "apparmor",
    }
    assert payload["apparmor"] == {
        "parent_profile_suffix": "codex_bridge",
        "bwrap_profile_suffix": "//codex_bwrap",
    }
    executables = payload["executables"]
    assert isinstance(executables, dict)
    assert set(executables) == {"codex", "bwrap", "bwrap_launcher"}
    assert executables["bwrap"]["path"] == "/usr/local/bin/bwrap"
    assert executables["bwrap_launcher"]["path"] == "/opt/codex/bin/bwrap"
    assert executables["bwrap"]["sha256"] != (
        executables["bwrap_launcher"]["sha256"]
    )
    assert "os.replace" in stage or "write_bytes" in stage


def test_attestation_reader_accepts_only_an_exact_bounded_contract(tmp_path: Path) -> None:
    contract_path, attestation_path = _valid_attestation(tmp_path)
    assert _verify(contract_path, attestation_path)


def test_attestation_reader_rejects_symlink(tmp_path: Path) -> None:
    if os.name == "nt":
        pytest.skip("Windows symlink creation requires a developer-mode privilege")
    contract_path, attestation_path = _valid_attestation(tmp_path)
    link = tmp_path / "attestation-link.json"
    link.symlink_to(attestation_path)
    assert not _verify(contract_path, link)


@pytest.mark.skipif(os.name == "nt", reason="POSIX owner/mode/link metadata")
def test_attestation_reader_rejects_wrong_owner_and_mode(tmp_path: Path) -> None:
    contract_path, attestation_path = _valid_attestation(tmp_path)
    module = _sandbox_module()
    assert not module.verify_sandbox_attestation(
        contract_path=contract_path,
        attestation_path=attestation_path,
        expected_uid=os.getuid() + 1,
        expected_gid=os.getgid(),
    )
    attestation_path.chmod(0o644)
    assert not _verify(contract_path, attestation_path)


@pytest.mark.skipif(os.name == "nt", reason="POSIX nlink metadata")
def test_attestation_reader_rejects_hardlinked_nlink_two(tmp_path: Path) -> None:
    contract_path, attestation_path = _valid_attestation(tmp_path)
    hardlink = tmp_path / "attestation-hardlink.json"
    os.link(attestation_path, hardlink)
    assert not _verify(contract_path, attestation_path)


def test_attestation_reader_rejects_oversize_duplicate_and_mismatched_json(
    tmp_path: Path,
) -> None:
    contract_path, attestation_path = _valid_attestation(tmp_path)
    module = _sandbox_module()
    limit = int(getattr(module, "MAX_ATTESTATION_BYTES", 64 * 1024))
    attestation_path.write_bytes(
        json.dumps(
            {
                "schema_version": 1,
                "contract_sha256": "a" * 64,
                "attested": True,
            },
            separators=(",", ":"),
        ).encode()
        + b" " * (limit + 1)
    )
    assert not _verify(contract_path, attestation_path)

    digest = hashlib.sha256(contract_path.read_bytes()).hexdigest()
    attestation_path.write_text(
        '{"schema_version":1,"contract_sha256":"%s","attested":true,"attested":true}'
        % digest,
        encoding="utf-8",
    )
    assert not _verify(contract_path, attestation_path)

    _canonical_json(
        attestation_path,
        {
            "schema_version": 1,
            "contract_sha256": "d" * 64,
            "attested": True,
        },
    )
    assert not _verify(contract_path, attestation_path)


def test_build_info_and_readiness_expose_only_safe_sandbox_fields(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from codex_bridge_service.app import create_app
    from codex_bridge_service.build_info import BuildInfo

    build_info = BuildInfo(sandbox_contract_version=2)
    app = create_app(
        root_path=tmp_path,
        auth_token="secret",
        build_info=build_info,
        sandbox_ready=False,
    )
    payload = TestClient(app).get(
        "/ready", headers={"Authorization": "Bearer secret"}
    ).json()
    assert payload["sandbox"] == {"contract_version": 2, "attested": False}
    assert set(payload["sandbox"]) == {"contract_version", "attested"}
    assert all(secret not in json.dumps(payload) for secret in ("/data", "token"))


def test_production_readiness_accepts_only_the_current_contract_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _sandbox_module()
    observed: dict[str, object] = {}

    def verified(**kwargs: object) -> bool:
        observed.update(kwargs)
        return True

    monkeypatch.setattr(module, "verify_sandbox_attestation", verified)
    build_info = {
        "sandbox_contract_version": 2,
        "architecture": "amd64",
        "codex_version": "0.144.4",
        "release_lock_digest": "a" * 64,
    }
    assert module.sandbox_attestation_ready(build_info)
    assert observed["expected_contract_version"] == 2
    assert observed["expected_architecture"] == "amd64"
    assert observed["expected_codex_version"] == "0.144.4"
    assert observed["expected_release_lock_digest"] == "a" * 64

    observed.clear()
    assert not module.sandbox_attestation_ready(
        {**build_info, "sandbox_contract_version": 1}
    )
    assert not observed


def test_build_info_sandbox_contract_version_is_bounded_and_environment_validated() -> None:
    from codex_bridge_service.build_info import BuildInfo

    assert BuildInfo.from_environment(
        {"CODEX_BRIDGE_SANDBOX_CONTRACT_VERSION": "7"}
    ).sandbox_contract_version == 7
    assert BuildInfo.from_environment(
        {"CODEX_BRIDGE_SANDBOX_CONTRACT_VERSION": "0;secret"}
    ).sandbox_contract_version is None
