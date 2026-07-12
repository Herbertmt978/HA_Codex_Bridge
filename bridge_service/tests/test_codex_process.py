import os

import pytest

from codex_bridge_service.codex_process import (
    codex_subprocess_environment,
    resolve_codex_home,
)


def test_resolve_codex_home_prefers_bridge_override_then_standard_environment(
    tmp_path,
    monkeypatch,
) -> None:
    standard_home = tmp_path / "standard-codex-home"
    bridge_home = tmp_path / "bridge-codex-home"
    monkeypatch.setenv("CODEX_HOME", str(standard_home))

    assert resolve_codex_home(None, "codex") == standard_home
    assert resolve_codex_home(str(bridge_home), "codex") == bridge_home


def test_resolve_codex_home_can_infer_home_from_sandbox_wrapper(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("CODEX_HOME", raising=False)
    wrapper = tmp_path / ".codex" / ".sandbox-bin" / "codex.exe"

    assert resolve_codex_home(None, str(wrapper)) == tmp_path / ".codex"


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("SUPERVISOR_TOKEN", "supervisor-realistic-secret"),
        ("HASSIO_TOKEN", "hassio-realistic-secret"),
        ("HOMEASSISTANT_TOKEN", "home-assistant-realistic-secret"),
        ("CODEX_BRIDGE_AUTH_TOKEN", "bridge-realistic-secret"),
        ("OPENAI_API_KEY", "sk-proj-realistic-secret-carrier"),
        ("OPENAI_ACCESS_TOKEN", "openai-realistic-secret"),
        ("CODEX_TOKEN", "codex-realistic-secret"),
        ("GH_TOKEN", "ghp_realisticSecretCarrier123456789"),
        ("GITHUB_TOKEN", "github_pat_realistic_secret_carrier"),
        ("GITHUB_PAT", "github-pat-realistic-secret"),
        ("CI_JOB_TOKEN", "ci-realistic-secret"),
        ("COOKIE", "session=realistic-secret"),
        ("AUTHORIZATION", "Bearer realistic-secret"),
        ("HTTP_PROXY", "http://user:secret@proxy.invalid:8080"),
        ("HTTPS_PROXY", "https://user:secret@proxy.invalid:8443"),
        ("ALL_PROXY", "socks5://user:secret@proxy.invalid:1080"),
        ("NO_PROXY", "supervisor,homeassistant,metadata"),
        ("no_proxy", "supervisor,homeassistant,metadata"),
        ("UNRELATED_PRIVATE_VALUE", "must-not-cross-the-boundary"),
    ],
)
def test_codex_subprocess_environment_uses_literal_allowlist(
    tmp_path,
    name: str,
    value: str,
) -> None:
    source = {
        "PATH": os.pathsep.join((str(tmp_path / "bin"), str(tmp_path / "tools"))),
        name: value,
    }

    environment = codex_subprocess_environment(tmp_path / "codex-home", source)

    assert environment["PATH"] == source["PATH"]
    assert name not in environment
    assert value not in environment.values()


def test_codex_subprocess_environment_retains_only_valid_runtime_values(tmp_path) -> None:
    codex_home = tmp_path / "codex-home"
    temporary_directory = tmp_path / "tmp"
    certificate_file = tmp_path / "certificates" / "ca.pem"
    certificate_directory = tmp_path / "certificates" / "ca-dir"
    certificate_file.parent.mkdir()
    certificate_file.write_text("test certificate bundle", encoding="utf-8")
    certificate_directory.mkdir()
    source = {
        "PATH": os.pathsep.join((str(tmp_path / "bin"), str(tmp_path / "tools"))),
        "SYSTEMROOT": str(tmp_path / "Windows"),
        "WINDIR": str(tmp_path / "Windows"),
        "COMSPEC": str(tmp_path / "Windows" / "System32" / "cmd.exe"),
        "PATHEXT": ".COM;.EXE;.BAT;.CMD",
        "HOME": str(tmp_path / "parent-home"),
        "CODEX_HOME": str(tmp_path / "parent-codex-home"),
        "TMPDIR": str(temporary_directory),
        "LANG": "en_GB.UTF-8",
        "LC_ALL": "C.UTF-8",
        "SSL_CERT_FILE": str(certificate_file),
        "SSL_CERT_DIR": str(certificate_directory),
    }

    environment = codex_subprocess_environment(codex_home, source)

    assert environment == {
        "PATH": source["PATH"],
        "SYSTEMROOT": source["SYSTEMROOT"],
        "WINDIR": source["WINDIR"],
        "COMSPEC": source["COMSPEC"],
        "PATHEXT": source["PATHEXT"],
        "HOME": str(codex_home),
        "CODEX_HOME": str(codex_home),
        "TMPDIR": str(temporary_directory),
        "LANG": "en_GB.UTF-8",
        "LC_ALL": "C.UTF-8",
        "SSL_CERT_FILE": str(certificate_file),
        "SSL_CERT_DIR": str(certificate_directory),
    }


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("LANG", "en_GB.UTF-8\nOPENAI_API_KEY=sk-proj-secret"),
        ("LANG", "x" * 65),
        ("LC_ALL", "https://locale.invalid/C.UTF-8"),
        ("SSL_CERT_FILE", "https://certificates.invalid/ca.pem"),
        ("SSL_CERT_FILE", "-----BEGIN CERTIFICATE-----\ninline"),
        ("SSL_CERT_DIR", "Bearer realistic-secret"),
        ("SSL_CERT_DIR", "github_pat_realistic_secret_carrier"),
    ],
)
def test_codex_subprocess_environment_rejects_invalid_locale_and_certificate_carriers(
    tmp_path,
    name: str,
    value: str,
) -> None:
    environment = codex_subprocess_environment(
        tmp_path / "codex-home",
        {"PATH": str(tmp_path / "bin"), name: value},
    )

    assert name not in environment
