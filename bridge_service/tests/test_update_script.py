import ctypes
import os
import subprocess
import sys
from pathlib import Path

import pytest


windows_only = pytest.mark.skipif(
    sys.platform != "win32",
    reason="PowerShell updater tests require Windows",
)


def _run_update_script(
    update_script: Path,
    fake_codex: Path,
    log_path: Path,
    *extra_args: str,
    environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(update_script),
            "-CodexPath",
            str(fake_codex),
            "-LogPath",
            str(log_path),
            *extra_args,
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
        env=environment,
    )


def _write_fake_codex(path: Path) -> None:
    path.write_text(
        """@echo off
if "%~1"=="--version" (
  echo codex-cli 9.9.9
  exit /b 0
)
if "%~1"=="update" (
  echo SECRET_SENTINEL_FROM_UPDATE
  >"%~dp0update-install-dir.txt" echo %CODEX_INSTALL_DIR%
  where.exe tar >"%~dp0update-tar-path.txt"
  >"%~dp0codex-real.exe" echo changed-by-update
  if errorlevel 1 goto update_failure
  if /I "%FAKE_CODEX_FAILURE%"=="update" goto update_failure
  exit /b 0
)
if "%~1"=="debug" if "%~2"=="models" if "%~3"=="--bundled" (
  if /I "%FAKE_CODEX_FAILURE%"=="smoke" goto smoke_failure
  echo {"models":[{"slug":"gpt-test"}]}
  exit /b 0
)
exit /b 99
:update_failure
exit /b 17
:smoke_failure
echo not-json
exit /b 0
""",
        encoding="utf-8",
    )


@windows_only
def test_codex_update_script_check_only_records_installed_version(tmp_path) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    update_script = repo_root / "scripts" / "Update-Codex.ps1"
    fake_codex = tmp_path / "codex.cmd"
    log_path = tmp_path / "codex-update.log"
    _write_fake_codex(fake_codex)

    completed = _run_update_script(
        update_script,
        fake_codex,
        log_path,
        "-CheckOnly",
    )

    assert completed.returncode == 0, completed.stderr
    log = log_path.read_text(encoding="utf-8")
    assert "codex-cli 9.9.9" in log
    assert "Check-only mode completed" in log
    assert not (tmp_path / "codex-real.exe").exists()


@windows_only
def test_codex_update_script_smoke_tests_bundled_models_without_logging_output(
    tmp_path,
) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    update_script = repo_root / "scripts" / "Update-Codex.ps1"
    fake_codex = tmp_path / "codex.cmd"
    real_codex = tmp_path / "codex-real.exe"
    log_path = tmp_path / "codex-update.log"
    fake_tools = tmp_path / "fake-tools"
    fake_tools.mkdir()
    (fake_tools / "tar.cmd").write_text("@exit /b 99\n", encoding="utf-8")
    environment = dict(os.environ)
    path_key = next(key for key in environment if key.lower() == "path")
    environment[path_key] = f"{fake_tools}{os.pathsep}{environment[path_key]}"
    _write_fake_codex(fake_codex)
    real_codex.write_bytes(b"original-real")

    completed = _run_update_script(
        update_script,
        fake_codex,
        log_path,
        environment=environment,
    )

    assert completed.returncode == 0, completed.stderr
    log = log_path.read_text(encoding="utf-8")
    assert "Bundled model catalog smoke test passed" in log
    assert "SECRET_SENTINEL_FROM_UPDATE" not in log
    assert real_codex.read_text(encoding="utf-8").strip() == "changed-by-update"
    configured_install_dir = (tmp_path / "update-install-dir.txt").read_text(encoding="utf-8").strip()
    assert Path(configured_install_dir).resolve() == tmp_path.resolve()
    expected_system32 = str(Path(os.environ["SystemRoot"]) / "System32")
    selected_tar = (tmp_path / "update-tar-path.txt").read_text(encoding="utf-8").splitlines()[0]
    assert Path(selected_tar).parent.resolve() == Path(expected_system32).resolve()


@windows_only
def test_codex_update_script_rolls_back_wrapper_layout_when_update_fails(
    tmp_path, monkeypatch
) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    update_script = repo_root / "scripts" / "Update-Codex.ps1"
    fake_codex = tmp_path / "codex.cmd"
    real_codex = tmp_path / "codex-real.exe"
    log_path = tmp_path / "codex-update.log"
    _write_fake_codex(fake_codex)
    original_launcher = fake_codex.read_bytes()
    real_codex.write_bytes(b"original-real")
    monkeypatch.setenv("FAKE_CODEX_FAILURE", "update")

    completed = _run_update_script(
        update_script,
        fake_codex,
        log_path,
        environment=dict(os.environ),
    )

    assert completed.returncode == 1
    assert fake_codex.read_bytes() == original_launcher
    assert real_codex.read_bytes() == b"original-real"
    assert "Rollback completed" in log_path.read_text(encoding="utf-8")


@windows_only
def test_codex_update_script_rolls_back_when_model_smoke_test_fails(
    tmp_path, monkeypatch
) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    update_script = repo_root / "scripts" / "Update-Codex.ps1"
    fake_codex = tmp_path / "codex.cmd"
    real_codex = tmp_path / "codex-real.exe"
    log_path = tmp_path / "codex-update.log"
    _write_fake_codex(fake_codex)
    real_codex.write_bytes(b"original-real")
    monkeypatch.setenv("FAKE_CODEX_FAILURE", "smoke")

    completed = _run_update_script(
        update_script,
        fake_codex,
        log_path,
        environment=dict(os.environ),
    )

    assert completed.returncode == 1
    assert real_codex.read_bytes() == b"original-real"
    log = log_path.read_text(encoding="utf-8")
    assert "Bundled model catalog smoke test failed" in log
    assert "Rollback completed" in log


@windows_only
def test_codex_update_script_removes_new_real_binary_after_failed_smoke(
    tmp_path, monkeypatch
) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    update_script = repo_root / "scripts" / "Update-Codex.ps1"
    fake_codex = tmp_path / "codex.cmd"
    real_codex = tmp_path / "codex-real.exe"
    log_path = tmp_path / "codex-update.log"
    _write_fake_codex(fake_codex)
    monkeypatch.setenv("FAKE_CODEX_FAILURE", "smoke")

    completed = _run_update_script(
        update_script,
        fake_codex,
        log_path,
        environment=dict(os.environ),
    )

    assert completed.returncode == 1
    assert not real_codex.exists()
    assert "Rollback completed" in log_path.read_text(encoding="utf-8")


@windows_only
def test_codex_update_script_restores_managed_release_junction_on_failed_smoke(
    tmp_path,
) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    update_script = repo_root / "scripts" / "Update-Codex.ps1"
    releases = tmp_path / "releases"
    old_release = releases / "old" / "bin"
    new_release = releases / "new" / "bin"
    old_release.mkdir(parents=True)
    new_release.mkdir(parents=True)
    (old_release / "codex.exe").write_bytes(b"old-codex")
    (new_release / "codex.exe").write_bytes(b"new-codex")
    current = tmp_path / "current"
    visible_bin = tmp_path / "visible-bin"

    for link, target in ((current, old_release.parent), (visible_bin, current / "bin")):
        completed = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            check=False,
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 0, completed.stderr

    driver = tmp_path / "exercise-managed-rollback.ps1"
    driver.write_text(
        r'''param(
    [string]$UpdaterPath,
    [string]$CodexPath,
    [string]$NextTarget
)
$source = Get-Content -Raw -LiteralPath $UpdaterPath
$mainStart = $source.IndexOf('$logDirectory =')
if ($mainStart -lt 0) { throw 'Updater main boundary not found.' }
$definitions = [ScriptBlock]::Create($source.Substring(0, $mainStart))
. $definitions -CodexPath $CodexPath
$backup = New-CodexBackup
try {
    Set-ManagedReleaseJunction -Path $backup.ManagedCurrentPath -Target $NextTarget
    Restore-CodexBackup -Backup $backup
    [string](Get-Item -LiteralPath $backup.ManagedCurrentPath).Target
}
finally {
    Remove-CodexBackup -Backup $backup
}
''',
        encoding="utf-8",
    )
    completed = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(driver),
            "-UpdaterPath",
            str(update_script),
            "-CodexPath",
            str(visible_bin / "codex.exe"),
            "-NextTarget",
            str(new_release.parent),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert completed.returncode == 0, completed.stderr
    assert Path(completed.stdout.strip()).resolve() == old_release.parent.resolve()


@windows_only
def test_codex_update_script_skips_unchanged_locked_real_binary(tmp_path) -> None:
    from ctypes import wintypes

    repo_root = Path(__file__).resolve().parents[2]
    update_script = repo_root / "scripts" / "Update-Codex.ps1"
    fake_codex = tmp_path / "codex.cmd"
    real_codex = tmp_path / "codex-real.exe"
    log_path = tmp_path / "codex-update.log"
    _write_fake_codex(fake_codex)
    real_codex.write_bytes(b"running-real")

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel32.CreateFileW(
        str(real_codex),
        0x80000000,  # GENERIC_READ
        0x00000001,  # FILE_SHARE_READ only
        None,
        3,  # OPEN_EXISTING
        0,
        None,
    )
    invalid_handle = wintypes.HANDLE(-1).value
    assert handle != invalid_handle, ctypes.get_last_error()
    try:
        completed = _run_update_script(update_script, fake_codex, log_path)
    finally:
        kernel32.CloseHandle(handle)

    assert completed.returncode == 1
    assert real_codex.read_bytes() == b"running-real"
    log = log_path.read_text(encoding="utf-8")
    assert "Rollback completed" in log
    assert "ROLLBACK ERROR" not in log


@windows_only
def test_auto_update_installer_supports_non_mutating_preview(tmp_path) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    install_script = repo_root / "scripts" / "Install-CodexAutoUpdate.ps1"
    update_script = repo_root / "scripts" / "Update-Codex.ps1"
    fake_codex = tmp_path / "codex.exe"
    fake_codex.write_bytes(b"placeholder")

    completed = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(install_script),
            "-CodexPath",
            str(fake_codex),
            "-UpdaterPath",
            str(update_script),
            "-LogPath",
            str(tmp_path / "update.log"),
            "-TaskName",
            "CodexBridgeAutoUpdate-TestPreview",
            "-WhatIf",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert completed.returncode == 0, completed.stderr
    assert "CodexBridgeAutoUpdate-TestPreview" in completed.stdout


@windows_only
def test_auto_update_installer_resolves_default_updater_from_script_directory(tmp_path) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    install_script = repo_root / "scripts" / "Install-CodexAutoUpdate.ps1"
    fake_codex = tmp_path / "codex.exe"
    fake_codex.write_bytes(b"placeholder")

    completed = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(install_script),
            "-CodexPath",
            str(fake_codex),
            "-LogPath",
            str(tmp_path / "update.log"),
            "-TaskName",
            "CodexBridgeAutoUpdate-TestDefaultUpdater",
            "-WhatIf",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert completed.returncode == 0, completed.stderr
    assert "CodexBridgeAutoUpdate-TestDefaultUpdater" in completed.stdout


def test_auto_update_installer_registers_a_limited_principal() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    install_script = repo_root / "scripts" / "Install-CodexAutoUpdate.ps1"
    script = install_script.read_text(encoding="utf-8")

    assert "-RunLevel Limited" in script
    assert "-RunLevel Highest" not in script
