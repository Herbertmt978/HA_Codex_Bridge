import os
from pathlib import PurePosixPath

import pytest

from codex_bridge_service import codex_process
from codex_bridge_service.codex_process import (
    codex_subprocess_environment,
    resolve_codex_home,
)


_SYNTHETIC_CREDENTIAL_CARRIERS = (
    "gho_SYNTHETIC_CARRIER_1234567890",
    "GhU_SYNTHETIC_CARRIER_1234567890",
    "ghs_SYNTHETIC_CARRIER_1234567890",
    "ghr_SYNTHETIC_CARRIER_1234567890",
    "eyJhbGciOiJub25lIn0.eyJzdWIiOiJzeW50aGV0aWMifQ.synthetic_signature",
    "BeArEr:SYNTHETIC_CARRIER_1234567890",
    "PAT_SYNTHETIC_CARRIER_1234567890",
    "CI-JOB-TOKEN=SYNTHETIC_CARRIER_1234567890",
    "CoDeX-Access-Token:SYNTHETIC_CARRIER_1234567890",
    "SUPERVISOR_REFRESH_TOKEN=SYNTHETIC_CARRIER_1234567890",
    "GitHub-Token:SYNTHETIC_CARRIER_1234567890",
    "GitLab-Secret=SYNTHETIC_CARRIER_1234567890",
    "OPENAI_TOKEN=SYNTHETIC_CARRIER_1234567890",
    "BRIDGE_SECRET:SYNTHETIC_CARRIER_1234567890",
    "HASSIO_KEY=SYNTHETIC_CARRIER_1234567890",
    "HOMEASSISTANT_SECRET:SYNTHETIC_CARRIER_1234567890",
    "CI_SECRET=SYNTHETIC_CARRIER_1234567890",
    "CODEX_KEY:SYNTHETIC_CARRIER_1234567890",
    "SUPERVISOR_PAT=SYNTHETIC_CARRIER_1234567890",
    "CODEX_BRIDGE_AUTH_TOKEN=SYNTHETIC_CARRIER_1234567890",
    "GH_TOKEN=SYNTHETIC_CARRIER_1234567890",
    "HA_TOKEN=SYNTHETIC_CARRIER_1234567890",
    "Cookie:SYNTHETIC_SESSION_1234567890",
    "sessionid=SYNTHETIC_CARRIER_1234567890",
    "PASSWORD:SYNTHETIC_CARRIER_1234567890",
    "client-secret=SYNTHETIC_CARRIER_1234567890",
    "-----BEGIN PRIVATE KEY-----SYNTHETIC",
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


@pytest.mark.parametrize("carrier", _SYNTHETIC_CREDENTIAL_CARRIERS)
def test_codex_subprocess_environment_removes_credential_carriers_from_path_entries(
    tmp_path,
    carrier: str,
) -> None:
    safe_bin = tmp_path / "safe-bin"
    safe_tools = tmp_path / "safe-tools"
    path_carrier = carrier.replace(":", "=") if os.pathsep == ":" else carrier
    source_path = os.pathsep.join(
        (str(safe_bin), str(tmp_path / path_carrier), str(safe_tools))
    )

    environment = codex_subprocess_environment(
        tmp_path / "codex-home",
        {"PATH": source_path},
    )

    assert environment["PATH"] == os.pathsep.join((str(safe_bin), str(safe_tools)))
    assert path_carrier not in environment["PATH"]


def test_codex_subprocess_environment_filters_unsafe_path_entries(tmp_path) -> None:
    safe_bin = tmp_path / "safe-bin"
    safe_tools = tmp_path / "safe-tools"
    source_path = os.pathsep.join(
        (
            str(safe_bin),
            "",
            ".",
            "relative-bin",
            "https://executables.invalid/bin",
            f"{tmp_path / 'control'}\nsmuggled",
            str(safe_tools),
        )
    )

    environment = codex_subprocess_environment(
        tmp_path / "codex-home",
        {"PATH": source_path},
    )

    assert environment["PATH"] == os.pathsep.join((str(safe_bin), str(safe_tools)))


def test_codex_subprocess_environment_preserves_absolute_path_after_relative_posix_entry(
    monkeypatch,
) -> None:
    monkeypatch.setattr(codex_process.os, "pathsep", ":")
    monkeypatch.setattr(codex_process, "Path", PurePosixPath)

    environment = codex_subprocess_environment(
        "/codex-home",
        {"PATH": "relative:/usr/bin:https://executables.invalid/bin:/opt/bin"},
    )

    assert environment["PATH"] == "/usr/bin:/opt/bin"


@pytest.mark.parametrize(
    "locale",
    ("C", "POSIX", "C.UTF-8", "en_GB.UTF-8", "de_DE@euro"),
)
def test_codex_subprocess_environment_accepts_structured_locales(
    tmp_path,
    locale: str,
) -> None:
    environment = codex_subprocess_environment(
        tmp_path / "codex-home",
        {"PATH": str(tmp_path / "bin"), "LANG": locale},
    )

    assert environment["LANG"] == locale


@pytest.mark.parametrize(
    "locale",
    (
        "SYNTHETIC_CARRIER",
        "english_UK.UTF-8",
        "en_GBR.UTF-8",
        "en-GB",
        "C@synthetic",
    ),
)
def test_codex_subprocess_environment_rejects_unstructured_locales(
    tmp_path,
    locale: str,
) -> None:
    environment = codex_subprocess_environment(
        tmp_path / "codex-home",
        {"PATH": str(tmp_path / "bin"), "LANG": locale},
    )

    assert "LANG" not in environment


@pytest.mark.parametrize(
    ("name", "carrier"),
    [
        pytest.param("SYSTEMROOT", "gho_SYNTHETIC_CARRIER_1234567890", id="platform"),
        pytest.param("TMPDIR", "ghs_SYNTHETIC_CARRIER_1234567890", id="temporary"),
        pytest.param("LANG", "ghr_SYNTHETIC_CARRIER_1234567890", id="locale"),
    ],
)
def test_codex_subprocess_environment_rejects_carriers_in_allowed_value_classes(
    tmp_path,
    name: str,
    carrier: str,
) -> None:
    value = carrier if name == "LANG" else str(tmp_path / carrier)
    environment = codex_subprocess_environment(
        tmp_path / "codex-home",
        {"PATH": str(tmp_path / "bin"), name: value},
    )

    assert name not in environment


def test_codex_subprocess_environment_rejects_carrier_in_dedicated_home(tmp_path) -> None:
    carrier_home = tmp_path / "ghu_SYNTHETIC_CARRIER_1234567890"

    environment = codex_subprocess_environment(
        carrier_home,
        {"PATH": str(tmp_path / "bin")},
    )

    assert "HOME" not in environment
    assert "CODEX_HOME" not in environment


def test_codex_subprocess_environment_rejects_carriers_in_existing_certificate_paths(
    tmp_path,
) -> None:
    certificate_file = tmp_path / (
        "eyJhbGciOiJub25lIn0.eyJzdWIiOiJzeW50aGV0aWMifQ.synthetic_signature.pem"
    )
    certificate_directory = tmp_path / "SUPERVISOR-ACCESS-TOKEN=SYNTHETIC_CARRIER_1234567890"
    certificate_file.write_text("synthetic certificate", encoding="utf-8")
    certificate_directory.mkdir()

    environment = codex_subprocess_environment(
        tmp_path / "codex-home",
        {
            "PATH": str(tmp_path / "bin"),
            "SSL_CERT_FILE": str(certificate_file),
            "SSL_CERT_DIR": str(certificate_directory),
        },
    )

    assert "SSL_CERT_FILE" not in environment
    assert "SSL_CERT_DIR" not in environment


@pytest.mark.parametrize(
    ("carrier_class", "carrier"),
    [
        pytest.param(
            "platform",
            "CODEX_BRIDGE_AUTH_TOKEN=SYNTHETIC_CARRIER_1234567890",
            id="platform-codex-bridge",
        ),
        pytest.param(
            "temporary",
            "GH_TOKEN=SYNTHETIC_CARRIER_1234567890",
            id="temporary-gh",
        ),
        pytest.param(
            "home",
            "HA_TOKEN=SYNTHETIC_CARRIER_1234567890",
            id="home-ha",
        ),
        pytest.param(
            "certificate",
            "HA_TOKEN=SYNTHETIC_CARRIER_1234567890",
            id="certificate-ha",
        ),
    ],
)
def test_codex_subprocess_environment_rejects_bridge_and_ha_alias_carriers_in_paths(
    tmp_path,
    carrier_class: str,
    carrier: str,
) -> None:
    carrier_path = tmp_path / carrier
    codex_home = tmp_path / "codex-home"
    source = {"PATH": str(tmp_path / "bin")}
    rejected_names: tuple[str, ...]

    if carrier_class == "platform":
        source["SYSTEMROOT"] = str(carrier_path)
        rejected_names = ("SYSTEMROOT",)
    elif carrier_class == "temporary":
        source["TMPDIR"] = str(carrier_path)
        rejected_names = ("TMPDIR",)
    elif carrier_class == "home":
        codex_home = carrier_path
        rejected_names = ("HOME", "CODEX_HOME")
    else:
        carrier_path.write_text("synthetic certificate", encoding="utf-8")
        source["SSL_CERT_FILE"] = str(carrier_path)
        rejected_names = ("SSL_CERT_FILE",)

    environment = codex_subprocess_environment(codex_home, source)

    assert all(name not in environment for name in rejected_names)
    assert all(carrier not in value for value in environment.values())


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
