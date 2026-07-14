"""Task 20 contracts for the reproducible Home Assistant App build context.

These tests deliberately run without Docker.  The context stager is invoked in
temporary directories and the resulting manifest is treated as a reproducible
build attestation; image execution belongs to the later protected-HA gate.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import zipfile

import pytest


ROOT = Path(__file__).resolve().parents[2]
APP_ROOT = ROOT / "codex_bridge_app"
STAGER = ROOT / "scripts" / "stage_app_context.py"
LOCK = APP_ROOT / "codex-release.json"
BUILD_REQUIREMENTS = APP_ROOT / "requirements-build.txt"
ASSET_INSTALLER = (
    APP_ROOT
    / "rootfs"
    / "usr"
    / "local"
    / "libexec"
    / "codex-bridge"
    / "install_locked_assets.py"
)


def _stage(destination: Path, architecture: str = "amd64") -> Path:
    """Stage an App context without touching the repository's .build tree."""

    assert STAGER.is_file(), "Task 20 context stager is missing"
    result = subprocess.run(
        [
            sys.executable,
            str(STAGER),
            "--arch",
            architecture,
            "--output",
            str(destination),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    assert destination.is_dir(), "stager did not create the requested context"
    return destination


def _manifest(context: Path) -> dict[str, object]:
    path = context / "manifest.json"
    assert path.is_file(), "staged context must contain manifest.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _elf_header(path: Path) -> bytes:
    with path.open("rb") as source:
        return source.read(20)


def _files(manifest: dict[str, object]) -> list[dict[str, object]]:
    value = manifest.get("files")
    assert isinstance(value, list), "manifest files must be a list"
    assert all(isinstance(item, dict) for item in value)
    return [item for item in value if isinstance(item, dict)]


def test_staged_manifest_is_byte_for_byte_repeatable(tmp_path: Path) -> None:
    first = _stage(tmp_path / "first")
    second = _stage(tmp_path / "second")

    first_manifest = (first / "manifest.json").read_bytes()
    assert first_manifest == (second / "manifest.json").read_bytes()
    assert first_manifest.endswith(b"\n")
    assert b"\r\n" not in first_manifest
    assert _manifest(first).get("architecture") == "amd64"


def test_manifest_attests_every_staged_file_with_sha256(tmp_path: Path) -> None:
    context = _stage(tmp_path / "context")
    entries = _files(_manifest(context))
    assert entries, "manifest must not attest an empty context"

    for entry in entries:
        relative = entry.get("path")
        digest = entry.get("sha256")
        assert isinstance(relative, str) and not Path(relative).is_absolute()
        assert isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest)
        target = context / Path(relative)
        assert target.is_file(), f"manifest references missing {relative}"
        actual = _sha256(target)
        assert actual == digest, f"manifest digest mismatch for {relative}"


def test_stager_builds_a_fresh_bridge_wheel_with_package_data(tmp_path: Path) -> None:
    context = _stage(tmp_path / "context")
    wheels = sorted(context.rglob("*.whl"))
    assert len(wheels) == 1, (
        "context must contain exactly one freshly built Bridge wheel"
    )

    with zipfile.ZipFile(wheels[0]) as wheel:
        names = set(wheel.namelist())
        for name in names:
            if name.startswith("codex_bridge_service/") and name.endswith(
                (".py", ".json")
            ):
                assert b"\r\n" not in wheel.read(name), (
                    f"wheel member is checkout-EOL dependent: {name}"
                )
    assert "codex_bridge_service/__init__.py" in names
    for resource in (
        "codex_app_server_contract.json",
        "codex_app_server_protocol.schema.json",
        "codex_app_server_protocol.v2.schema.json",
    ):
        assert f"codex_bridge_service/{resource}" in names


def test_staged_context_contains_no_committed_bridge_source_or_binary(
    tmp_path: Path,
) -> None:
    context = _stage(tmp_path / "context")
    forbidden = {
        ".py",
        ".pyc",
        ".pyd",
        ".so",
        ".exe",
    }
    for path in context.rglob("*"):
        if not path.is_file() or path.name == "stage_app_context.py":
            continue
        assert not (
            "codex_bridge_service" in path.parts and path.suffix in forbidden
        ), f"duplicate Bridge source/binary staged at {path}"
        if path.suffix in forbidden:
            assert path.stem not in {
                "codex_bridge_service",
                "codex-bridge-service",
                "bridge",
            }, f"duplicate Bridge binary staged at {path}"
    assert not (context / "bridge_service").exists()
    assert not (context / "src").exists()
    assert not any("__pycache__" in path.parts for path in context.rglob("*"))
    assert not any(path.suffix == ".pyc" for path in context.rglob("*"))


def test_stager_uses_a_hash_locked_build_toolchain() -> None:
    lock = BUILD_REQUIREMENTS.read_text(encoding="utf-8")
    assert "build==" in lock
    assert "setuptools==" in lock
    assert "wheel==" in lock
    assert "--hash=sha256:" in lock
    stager = STAGER.read_text(encoding="utf-8")
    assert 'APP_ROOT / "requirements-build.txt"' in stager
    assert '"--require-hashes"' in stager


def test_bundled_app_server_contract_matches_the_locked_codex_version() -> None:
    release_lock = json.loads(LOCK.read_text(encoding="utf-8"))
    contract_path = (
        ROOT
        / "bridge_service"
        / "src"
        / "codex_bridge_service"
        / "codex_app_server_contract.json"
    )
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    assert contract["codexVersion"] == (
        f"codex-cli {release_lock['release']['version']}"
    )


def test_selected_task19_lock_and_architecture_are_attested(tmp_path: Path) -> None:
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    assert isinstance(lock, dict)
    context = _stage(tmp_path / "context", "amd64")
    manifest = _manifest(context)
    assert manifest.get("architecture") == "amd64"

    staged_lock = context / "codex-release.json"
    assert staged_lock.is_file(), "Task 19 lock must be copied into the context"
    assert json.loads(staged_lock.read_text(encoding="utf-8")) == lock

    selected = lock["assets"]["amd64"]
    assert isinstance(selected, dict)
    for tool in ("codex", "bwrap"):
        asset = selected[tool]
        assert isinstance(asset, dict)
        assert re.fullmatch(r"[0-9a-f]{64}", asset["sha256"])
        assert re.fullmatch(r"[0-9a-f]{64}", asset["decompressed_sha256"])
        assert asset["name"].endswith(".tar.gz")
        staged_binary = context / "assets" / tool
        assert staged_binary.is_file(), f"strictly extracted {tool} binary is missing"
        assert staged_binary.stat().st_size == asset["decompressed_size"]
        assert _sha256(staged_binary) == asset["decompressed_sha256"]
        header = _elf_header(staged_binary)
        assert header[:7] == b"\x7fELF\x02\x01\x01"
        assert int.from_bytes(header[18:20], "little") == 62

    assert not list((context / "assets").glob("*.tar.gz")), (
        "the final context must contain only strict-parser-verified raw binaries"
    )
    native_extensions = list((context / "runtime-site-packages").rglob("*.so"))
    assert native_extensions, "the Linux runtime must contain native musl dependencies"
    for extension in native_extensions:
        header = _elf_header(extension)
        assert header[:7] == b"\x7fELF\x02\x01\x01"
        assert int.from_bytes(header[18:20], "little") == 62


def test_image_installer_never_uses_a_permissive_archive_parser() -> None:
    assert ASSET_INSTALLER.is_file(), "locked-asset image verifier is missing"
    text = ASSET_INSTALLER.read_text(encoding="utf-8")
    assert "import tarfile" not in text
    assert "tarfile." not in text
    assert "decompressed_sha256" in text
    assert "decompressed_size" in text


def test_dockerfile_uses_an_explicit_pinned_home_assistant_base() -> None:
    dockerfile = APP_ROOT / "Dockerfile"
    assert dockerfile.is_file(), "Task 20 Dockerfile is missing"
    text = dockerfile.read_text(encoding="utf-8")
    from_lines = re.findall(r"(?im)^\s*FROM\s+([^\s]+)", text)
    assert from_lines, "Dockerfile must have an explicit FROM"
    assert any(
        "home-assistant" in image and re.search(r"@sha256:[0-9a-f]{64}$", image)
        for image in from_lines
    ), "Home Assistant base image must be pinned by immutable digest"
    assert not re.search(r"(?im)^\s*ARG\s+BUILD_FROM\b", text)
    assert 'io.hass.version="0.6.1"' in text


def test_dockerfile_never_copies_from_parent_or_repository_source() -> None:
    dockerfile = APP_ROOT / "Dockerfile"
    assert dockerfile.is_file(), "Task 20 Dockerfile is missing"
    for line in dockerfile.read_text(encoding="utf-8").splitlines():
        if not re.match(r"\s*COPY\s+", line, re.IGNORECASE):
            continue
        sources = line.split()[1:-1]
        assert sources, f"COPY has no source: {line}"
        for source in sources:
            assert not source.startswith("..")
            assert source != "." and source != "./"
            assert "bridge_service" not in Path(source).parts
            assert source not in {"src", "bridge_service"}


def test_stager_refuses_destructive_or_unrelated_outputs(tmp_path: Path) -> None:
    readme = ROOT / "README.md"
    readme_digest = hashlib.sha256(readme.read_bytes()).hexdigest()
    sentinel = tmp_path / "unrelated" / "keep.txt"
    sentinel.parent.mkdir()
    sentinel.write_text("keep", encoding="utf-8")

    for output in (ROOT, sentinel.parent):
        result = subprocess.run(
            [
                sys.executable,
                str(STAGER),
                "--arch",
                "amd64",
                "--output",
                str(output),
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode != 0

    assert sentinel.read_text(encoding="utf-8") == "keep"
    assert hashlib.sha256(readme.read_bytes()).hexdigest() == readme_digest


def test_repository_tree_copy_rejects_symlinks(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    outside = tmp_path / "outside-secret"
    outside.write_text("must-not-copy", encoding="utf-8")
    link = source / "linked-secret"
    try:
        os.symlink(outside, link)
    except (NotImplementedError, OSError):
        pytest.skip("this host cannot create a test symlink")

    spec = importlib.util.spec_from_file_location("task20_context_stager", STAGER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    with pytest.raises(module.StageError):
        module._copy_tree(source, tmp_path / "destination")


def test_stager_canonicalizes_text_trees_across_checkout_eols(tmp_path: Path) -> None:
    spec = importlib.util.spec_from_file_location("task20_eol_stager", STAGER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    crlf_source = tmp_path / "crlf"
    lf_source = tmp_path / "lf"
    for source, newline in ((crlf_source, b"\r\n"), (lf_source, b"\n")):
        (source / "nested").mkdir(parents=True)
        (source / "module.py").write_bytes(newline.join((b"one", b"two", b"")))
        (source / "nested" / "contract.json").write_bytes(
            newline.join((b"{", b'  "ready": true', b"}", b""))
        )

    crlf_output = tmp_path / "crlf-output"
    lf_output = tmp_path / "lf-output"
    module._copy_canonical_text_tree(crlf_source, crlf_output)
    module._copy_canonical_text_tree(lf_source, lf_output)

    for relative in (Path("module.py"), Path("nested/contract.json")):
        expected = (lf_output / relative).read_bytes()
        assert (crlf_output / relative).read_bytes() == expected
        assert b"\r\n" not in expected


@pytest.mark.skipif(os.name != "nt", reason="Windows sharing semantics regression")
def test_publish_preserves_the_last_context_when_a_file_is_locked(
    tmp_path: Path,
) -> None:
    spec = importlib.util.spec_from_file_location(
        "task20_locked_context_stager", STAGER
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    output = tmp_path / "published"
    output.mkdir()
    sentinel = output / "manifest.json"
    sentinel.write_text("previous-complete-context", encoding="utf-8")
    replacement = tmp_path / "replacement"
    replacement.mkdir()
    (replacement / "manifest.json").write_text("replacement", encoding="utf-8")

    with sentinel.open("rb"):
        with pytest.raises(OSError):
            module._publish_context(replacement, output)

    assert sentinel.read_text(encoding="utf-8") == "previous-complete-context"
    assert replacement.is_dir()
