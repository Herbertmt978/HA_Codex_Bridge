#!/usr/bin/env python3
"""Create the hermetic Docker build context for the Codex Bridge App.

No Bridge source, package cache, or downloaded executable is committed. This
script builds a fresh wheel with a hash-locked build toolchain, materializes
the hash-locked Python runtime, and uses the strict Task 19 USTAR parser before
placing raw Codex/Bubblewrap binaries in the context. The Dockerfile performs
a second digest, size, and ELF verification before installation.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any, Mapping
from urllib.request import Request, urlopen
import uuid
import zipfile


ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = ROOT / "codex_bridge_app"
DEFAULT_OUTPUT = ROOT / ".build" / "app-context"
ARCHITECTURES = {
    "amd64": "x86_64-unknown-linux-musl",
    "aarch64": "aarch64-unknown-linux-musl",
}
COMPONENTS = ("codex", "bwrap")
SOURCE_DATE_EPOCH = "315532800"  # 1980-01-01, the first ZIP timestamp.
SANDBOX_CONTRACT_VERSION = 2


class StageError(RuntimeError):
    """The context cannot be produced safely."""


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_release_lock(path: Path) -> tuple[Mapping[str, Any], Any]:
    spec = importlib.util.spec_from_file_location(
        "stage_app_context_release_lock", ROOT / "scripts" / "update_codex_lock.py"
    )
    if spec is None or spec.loader is None:
        raise StageError("cannot load the release-lock validator")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    try:
        lock = json.loads(path.read_text(encoding="utf-8"))
        module.validate_lock(lock)
    except (OSError, ValueError, json.JSONDecodeError, module.ReleaseLockError) as exc:
        raise StageError("the Codex release lock is invalid") from exc
    if not isinstance(lock, dict):
        raise StageError("the Codex release lock is invalid")
    return lock, module


def _sandbox_contract(
    lock: Mapping[str, Any],
    arch: str,
    release_lock_digest: str,
) -> dict[str, object]:
    """Project the verified release lock into the runtime sandbox contract."""

    try:
        release = lock["release"]
        selected = lock["assets"][arch]
        codex_digest = selected["codex"]["decompressed_sha256"]
        bwrap_digest = selected["bwrap"]["decompressed_sha256"]
        codex_version = release["version"]
    except (KeyError, TypeError) as exc:
        raise StageError("the Codex release lock cannot form a sandbox contract") from exc
    bwrap_wrapper_digest = _sha256_bytes(
        _canonical_text_bytes(
            APP_ROOT
            / "rootfs"
            / "usr"
            / "local"
            / "libexec"
            / "codex-bridge"
            / "bwrap-wrapper.py"
        )
    )
    values = (
        release_lock_digest,
        codex_digest,
        bwrap_digest,
        bwrap_wrapper_digest,
    )
    if not all(
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
        for value in values
    ) or not isinstance(codex_version, str):
        raise StageError("the Codex release lock cannot form a sandbox contract")
    return {
        "schema_version": SANDBOX_CONTRACT_VERSION,
        "architecture": arch,
        "codex_version": codex_version,
        "release_lock_digest": release_lock_digest,
        "executables": {
            "codex": {
                "path": "/usr/local/bin/codex",
                "sha256": codex_digest,
            },
            "bwrap": {
                "path": "/usr/local/bin/bwrap",
                "sha256": bwrap_digest,
            },
            "bwrap_launcher": {
                "path": "/opt/codex/bin/bwrap",
                "sha256": bwrap_wrapper_digest,
            },
        },
        "apparmor": {
            "parent_profile_suffix": "codex_bridge",
            "bwrap_profile_suffix": "//codex_bwrap",
        },
    }


def _copy_file(source: Path, destination: Path) -> None:
    if source.is_symlink() or not source.is_file():
        raise StageError("build-context inputs must be regular non-symlink files")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    shutil.copymode(source, destination)


def _canonical_text_bytes(source: Path) -> bytes:
    try:
        text = source.read_bytes().decode("utf-8")
    except UnicodeDecodeError as exc:
        raise StageError("canonical context input is not UTF-8 text") from exc
    return text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")


def _copy_canonical_text_file(source: Path, destination: Path) -> None:
    if source.is_symlink() or not source.is_file():
        raise StageError("build-context inputs must be regular non-symlink files")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(_canonical_text_bytes(source))
    shutil.copymode(source, destination)


def _copy_canonical_text_tree(
    source: Path, destination: Path, *, ignore: Any = None
) -> None:
    """Copy a UTF-8 repository tree with checkout-independent LF bytes."""
    if source.is_symlink() or not source.is_dir():
        raise StageError("build-context inputs must be non-symlink directories")
    files: list[Path] = []
    directories: list[Path] = []
    for current, names, filenames in os.walk(source, followlinks=False):
        current_path = Path(current)
        ignored = (
            set(ignore(str(current_path), [*names, *filenames])) if ignore else set()
        )
        names[:] = [name for name in names if name not in ignored]
        for name in names:
            path = current_path / name
            if path.is_symlink():
                raise StageError("repository symlinks cannot enter the build context")
            directories.append(path)
        for name in filenames:
            if name in ignored:
                continue
            path = current_path / name
            if path.is_symlink() or not path.is_file():
                raise StageError("repository symlinks cannot enter the build context")
            files.append(path)
    destination.mkdir(parents=True, exist_ok=False)
    for directory in directories:
        (destination / directory.relative_to(source)).mkdir(parents=True, exist_ok=True)
    for path in files:
        _copy_canonical_text_file(path, destination / path.relative_to(source))


def _copy_tree(source: Path, destination: Path, *, ignore: Any = None) -> None:
    """Copy a repository tree without following checkout symlinks."""
    if source.is_symlink() or not source.is_dir():
        raise StageError("build-context inputs must be non-symlink directories")
    for current, directories, files in os.walk(source, followlinks=False):
        current_path = Path(current)
        for name in [*directories, *files]:
            if (current_path / name).is_symlink():
                raise StageError("repository symlinks cannot enter the build context")
    shutil.copytree(
        source,
        destination,
        copy_function=shutil.copy2,
        ignore=ignore,
        symlinks=True,
    )


def _remove_tree_with_retries(path: Path, *, required: bool) -> None:
    last_error: OSError | None = None
    for attempt in range(8):
        try:
            shutil.rmtree(path)
            return
        except FileNotFoundError:
            return
        except OSError as exc:
            last_error = exc
            time.sleep(min(0.05 * (2**attempt), 0.5))
    if required and last_error is not None:
        raise last_error


def _safe_output(output: Path, arch: str) -> Path:
    output = output.expanduser().resolve()
    allowed_roots = {(ROOT / ".build").resolve(), Path(tempfile.gettempdir()).resolve()}
    if output in allowed_roots or not any(
        output.is_relative_to(allowed_root) for allowed_root in allowed_roots
    ):
        raise StageError(
            "output must be a child of the repository .build or OS temp directory"
        )
    if output.is_symlink() or (output.exists() and not output.is_dir()):
        raise StageError("output must be a non-symlink build-context directory")
    if output.exists():
        manifest_path = output / "manifest.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StageError(
                "refusing to replace an unrelated output directory"
            ) from exc
        if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
            raise StageError("refusing to replace an unrelated output directory")
        if manifest.get("architecture") != arch:
            raise StageError("refusing to replace an output for another architecture")
    return output


def _download_asset(asset: Mapping[str, Any], destination: Path) -> None:
    url = asset.get("url")
    expected_size = asset.get("size")
    expected_digest = asset.get("sha256")
    if (
        not isinstance(url, str)
        or not isinstance(expected_size, int)
        or not isinstance(expected_digest, str)
    ):
        raise StageError("the Codex release lock has an invalid asset")
    cache_root = Path.home() / ".cache" / "codex-bridge" / "release-assets"
    cache_root.mkdir(parents=True, exist_ok=True)
    cache_path = cache_root / expected_digest
    destination.parent.mkdir(parents=True, exist_ok=True)
    if (
        not cache_path.is_symlink()
        and cache_path.is_file()
        and cache_path.stat().st_size == expected_size
    ):
        if _sha256_file(cache_path) == expected_digest:
            _copy_file(cache_path, destination)
            if (
                destination.stat().st_size == expected_size
                and _sha256_file(destination) == expected_digest
            ):
                return
            destination.unlink(missing_ok=True)
    request = Request(url, headers={"User-Agent": "codex-bridge-context-stage/1"})
    digest = hashlib.sha256()
    written = 0
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".partial", dir=destination.parent
    )
    temporary = Path(temporary_name)
    try:
        with (
            urlopen(request, timeout=60) as response,
            os.fdopen(descriptor, "wb") as output,
        ):
            descriptor = -1
            while chunk := response.read(min(1024 * 1024, expected_size - written + 1)):
                written += len(chunk)
                if written > expected_size:
                    raise StageError(
                        "a locked Codex archive exceeded its recorded size"
                    )
                digest.update(chunk)
                output.write(chunk)
        if written != expected_size or digest.hexdigest() != expected_digest:
            raise StageError("a locked Codex archive did not match its recorded digest")
        cache_descriptor, cache_temporary_name = tempfile.mkstemp(
            prefix=f".{expected_digest}.", suffix=".partial", dir=cache_root
        )
        os.close(cache_descriptor)
        cache_temporary = Path(cache_temporary_name)
        try:
            shutil.copyfile(temporary, cache_temporary)
            if (
                cache_temporary.stat().st_size != expected_size
                or _sha256_file(cache_temporary) != expected_digest
            ):
                raise StageError("the release cache copy failed verification")
            os.replace(cache_temporary, cache_path)
        finally:
            cache_temporary.unlink(missing_ok=True)
        os.replace(temporary, destination)
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)
        raise


def _build_bridge_wheel(destination: Path, scratch: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    # setuptools writes its build tree beside pyproject.toml.  Build from a
    # disposable source copy so concurrent CI contexts never contend for the
    # repository's build/ directory, and so no duplicate source can leak into
    # the final context.
    build_source = scratch / "bridge-service-source"
    build_output = scratch / "wheel-output"
    build_output.mkdir()
    build_source.mkdir()
    _copy_canonical_text_file(
        ROOT / "bridge_service" / "pyproject.toml",
        build_source / "pyproject.toml",
    )
    _copy_canonical_text_tree(
        ROOT / "bridge_service" / "src",
        build_source / "src",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
    )
    build_tools = scratch / "build-tools"
    _materialize_locked_requirements(
        build_tools, APP_ROOT / "requirements-build.txt", platform=None
    )
    environment = dict(
        os.environ,
        SOURCE_DATE_EPOCH=SOURCE_DATE_EPOCH,
        PYTHONHASHSEED="0",
        PYTHONNOUSERSITE="1",
        PYTHONPATH=str(build_tools),
    )
    subprocess.run(
        [
            sys.executable,
            "-m",
            "build",
            "--wheel",
            "--no-isolation",
            "--outdir",
            str(build_output),
            str(build_source),
        ],
        check=True,
        env=environment,
    )
    built_wheels = sorted(build_output.glob("codex_bridge_service-*.whl"))
    if len(built_wheels) != 1:
        raise StageError("the Bridge build did not produce exactly one wheel")
    wheel = destination / built_wheels[0].name
    _copy_file(built_wheels[0], wheel)
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
    required = {
        "codex_bridge_service/codex_app_server_contract.json",
        "codex_bridge_service/codex_app_server_protocol.schema.json",
        "codex_bridge_service/codex_app_server_protocol.v2.schema.json",
    }
    if not required.issubset(names):
        raise StageError("the fresh Bridge wheel is missing required package data")
    return wheel


def _materialize_locked_requirements(
    destination: Path, requirements: Path, *, platform: str | None
) -> None:
    uv = shutil.which("uv")
    if uv is None:
        raise StageError("uv is required to materialize the hash-locked target runtime")
    command = [
        uv,
        "pip",
        "install",
        "--target",
        str(destination),
        "--python-version",
        "3.14",
        "--require-hashes",
        "--no-build",
        "--no-compile-bytecode",
    ]
    if platform is not None:
        command.extend(["--python-platform", platform])
    command.extend(["-r", str(requirements)])
    subprocess.run(command, check=True)


def _materialize_runtime(destination: Path, arch: str) -> None:
    _materialize_locked_requirements(
        destination,
        APP_ROOT / "requirements-runtime.txt",
        platform=ARCHITECTURES[arch],
    )


def _strict_extract_asset(
    archive: Path,
    destination: Path,
    asset: Mapping[str, Any],
    arch: str,
    release_module: Any,
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    details = release_module._archive_details(
        archive.read_bytes(), str(asset["name"]), arch, destination
    )
    if details.get("decompressed_sha256") != asset.get(
        "decompressed_sha256"
    ) or details.get("decompressed_size") != asset.get("decompressed_size"):
        destination.unlink(missing_ok=True)
        raise StageError("strictly extracted release binary does not match its lock")
    destination.chmod(0o755)


def _replace_with_retries(source: Path, destination: Path) -> None:
    last_error: OSError | None = None
    for attempt in range(8):
        try:
            os.replace(source, destination)
            return
        except OSError as exc:
            last_error = exc
            time.sleep(min(0.05 * (2**attempt), 0.5))
    assert last_error is not None
    raise last_error


def _publish_context(context: Path, output: Path) -> None:
    """Replace only a prior complete context and preserve it on failure."""
    backup = output.parent / f".{output.name}.backup-{uuid.uuid4().hex}"
    sibling = output.parent / f".{output.name}.publish-{uuid.uuid4().hex}"
    context_drive = os.path.splitdrive(str(context))[0].casefold()
    output_drive = os.path.splitdrive(str(output))[0].casefold()
    same_filesystem = context_drive == output_drive and (
        context.parent.stat().st_dev == output.parent.stat().st_dev
    )
    publish_source = context
    if not same_filesystem:
        _copy_tree(context, sibling)
        _verify_manifest(sibling)
        publish_source = sibling
    previous_moved = False
    try:
        if output.exists():
            _replace_with_retries(output, backup)
            previous_moved = True
        _replace_with_retries(publish_source, output)
    except BaseException:
        if previous_moved and backup.exists() and not output.exists():
            _replace_with_retries(backup, output)
        raise
    finally:
        if sibling.exists():
            _remove_tree_with_retries(sibling, required=False)
    if previous_moved:
        _remove_tree_with_retries(backup, required=False)


def _manifest(root: Path, arch: str) -> dict[str, object]:
    files: list[dict[str, str]] = []
    for path in sorted(
        candidate for candidate in root.rglob("*") if candidate.is_file()
    ):
        relative = path.relative_to(root).as_posix()
        if relative == "manifest.json":
            continue
        files.append({"path": relative, "sha256": _sha256_file(path)})
    return {"schema_version": 1, "architecture": arch, "files": files}


def _verify_manifest(root: Path) -> None:
    try:
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        entries = manifest["files"]
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise StageError("published context manifest is invalid") from exc
    expected: dict[str, str] = {}
    if not isinstance(entries, list):
        raise StageError("published context manifest is invalid")
    for entry in entries:
        if not isinstance(entry, dict):
            raise StageError("published context manifest is invalid")
        path, digest = entry.get("path"), entry.get("sha256")
        if not isinstance(path, str) or not isinstance(digest, str) or path in expected:
            raise StageError("published context manifest is invalid")
        expected[path] = digest
    actual = {
        path.relative_to(root).as_posix(): _sha256_file(path)
        for path in root.rglob("*")
        if path.is_file() and path.name != "manifest.json"
    }
    if actual != expected:
        raise StageError("published context does not match its manifest")


def stage(*, arch: str, output: Path) -> Path:
    if arch not in ARCHITECTURES:
        raise StageError(f"unsupported App architecture: {arch}")
    # The published App metadata advertises amd64 only.  Keeping aarch64 in
    # the release lock supports future review, but must not produce an image
    # users cannot install today.
    if arch != "amd64":
        raise StageError("the Codex Bridge App currently publishes amd64 only")

    lock_path = APP_ROOT / "codex-release.json"
    lock, release_module = _load_release_lock(lock_path)
    release_lock_digest = _sha256_bytes(_canonical_text_bytes(lock_path))
    output = _safe_output(output, arch)
    output.parent.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix="codex-bridge-app-scratch-"))
    try:
        context = scratch / "context"
        context.mkdir()
        _copy_canonical_text_file(APP_ROOT / "Dockerfile", context / "Dockerfile")
        _copy_canonical_text_file(lock_path, context / "codex-release.json")
        _copy_canonical_text_file(
            APP_ROOT / "requirements-runtime.txt",
            context / "requirements-runtime.txt",
        )
        _copy_canonical_text_tree(
            APP_ROOT / "rootfs",
            context / "rootfs",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
        )
        sandbox_contract_path = (
            context
            / "rootfs"
            / "usr"
            / "local"
            / "share"
            / "codex-bridge"
            / "sandbox-contract.json"
        )
        sandbox_contract_path.parent.mkdir(parents=True, exist_ok=True)
        sandbox_contract_path.write_bytes(
            (
                json.dumps(
                    _sandbox_contract(lock, arch, release_lock_digest),
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n"
            ).encode("utf-8")
        )
        sandbox_contract_path.chmod(0o444)
        wheel = _build_bridge_wheel(context / "wheel", scratch)
        _materialize_runtime(context / "runtime-site-packages", arch)

        assets = lock["assets"][arch]
        for component in COMPONENTS:
            asset = assets[component]
            archive = scratch / "downloads" / f"{component}.tar.gz"
            _download_asset(asset, archive)
            _strict_extract_asset(
                archive,
                context / "assets" / component,
                asset,
                arch,
                release_module,
            )
        if wheel.stat().st_size == 0:
            raise StageError("the fresh Bridge wheel is empty")
        (context / "manifest.json").write_bytes(
            (
                json.dumps(
                    _manifest(context, arch), sort_keys=True, separators=(",", ":")
                )
                + "\n"
            ).encode("utf-8")
        )
        with release_module._exclusive_lock(output):
            _publish_context(context, output)
    finally:
        # A transient Windows scanner/Dropbox handle must not invalidate an
        # otherwise complete context; scratch is outside the published tree.
        _remove_tree_with_retries(scratch, required=False)
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arch", required=True, choices=sorted(ARCHITECTURES))
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    arguments = parser.parse_args(argv)
    try:
        result = stage(arch=arguments.arch, output=arguments.output)
    except (OSError, StageError, subprocess.CalledProcessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
