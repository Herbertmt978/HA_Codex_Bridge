"""Offline contract tests for the immutable Codex release lock."""

from __future__ import annotations

import importlib.util
import gzip
import io
import json
import multiprocessing
from pathlib import Path
import tarfile

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "update_codex_lock.py"
FIXTURES = Path(__file__).with_name("fixtures") / "codex_releases"


def _updater():
    assert SCRIPT.is_file(), "Task 19 updater is missing"
    spec = importlib.util.spec_from_file_location("update_codex_lock", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fixture(name: str) -> dict[str, object]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _elf_binary(machine: int) -> bytes:
    return (
        b"\x7fELF\x02\x01\x01\x00"
        + (b"\x00" * 10)
        + machine.to_bytes(2, "little")
        + b"binary"
    )


def _gzip_tar(*members: tuple[str, bytes]) -> bytes:
    payload = io.BytesIO()
    with tarfile.open(fileobj=payload, mode="w:gz") as archive:
        for name, contents in members:
            metadata = tarfile.TarInfo(name)
            metadata.size = len(contents)
            archive.addfile(metadata, io.BytesIO(contents))
    return payload.getvalue()


def _pax_sparse_gzip_tar(name: str, contents: bytes) -> bytes:
    payload = io.BytesIO()
    with tarfile.open(
        fileobj=payload, mode="w:gz", format=tarfile.PAX_FORMAT
    ) as archive:
        metadata = tarfile.TarInfo(name)
        metadata.size = len(contents)
        metadata.pax_headers = {
            "GNU.sparse.major": "1",
            "GNU.sparse.minor": "0",
        }
        metadata.sparse = [(0, len(contents))]
        archive.addfile(metadata, io.BytesIO(contents))
    return payload.getvalue()


def _commit_candidate_in_process(
    lock_path: str,
    candidate: dict[str, object],
    attempted: multiprocessing.synchronize.Event,
    result: multiprocessing.queues.Queue[str],
) -> None:
    updater = _updater()
    attempted.set()
    try:
        updater.commit_lock_candidate(Path(lock_path), candidate)
    except updater.ReleaseLockError as exc:
        result.put(f"error:{exc}")
    else:
        result.put("committed")


def test_release_lock_updater_exists() -> None:
    _updater()


def test_build_lock_accepts_exact_stable_release_for_both_musl_architectures() -> None:
    updater = _updater()

    lock = updater.build_lock_from_metadata(
        _fixture("stable.json"),
        archive_details=updater.fixture_archive_details(),
        bundle_details=updater.fixture_bundle_details(),
    )

    assert lock["repository"] == "openai/codex"
    assert lock["release"]["tag"] == "rust-v1.2.3"
    assert lock["release"]["version"] == "1.2.3"
    assert lock["release"]["commit"] == "b" * 40
    assert lock["release"]["id"] == 42
    assert lock["release"]["channel"] == "stable"
    assert set(lock["assets"]) == {"amd64", "aarch64"}
    assert lock["assets"]["amd64"]["codex"]["name"] == (
        "codex-x86_64-unknown-linux-musl.tar.gz"
    )
    assert lock["assets"]["aarch64"]["bwrap"]["name"] == (
        "bwrap-aarch64-unknown-linux-musl.tar.gz"
    )
    evidence = lock["assets"]["amd64"]["codex"]["sigstore"]
    assert evidence["log_id"] == "a" * 64
    assert evidence["log_index"] == 1
    assert evidence["integrated_time"] == 1_784_003_665
    updater.validate_lock(lock)


@pytest.mark.parametrize("fixture", ["draft.json", "prerelease.json", "malicious.json"])
def test_metadata_rejects_untrusted_release_shapes(fixture: str) -> None:
    updater = _updater()

    with pytest.raises(updater.ReleaseLockError):
        updater.build_lock_from_metadata(
            _fixture(fixture),
            archive_details=updater.fixture_archive_details(),
            bundle_details=updater.fixture_bundle_details(),
        )


def test_metadata_rejects_missing_and_duplicate_target_assets() -> None:
    updater = _updater()
    metadata = _fixture("stable.json")
    assets = metadata["assets"]
    assert isinstance(assets, list)
    metadata["assets"] = [
        asset
        for asset in assets
        if asset["name"] != "bwrap-aarch64-unknown-linux-musl.sigstore"
    ]
    with pytest.raises(updater.ReleaseLockError, match="missing"):
        updater.build_lock_from_metadata(
            metadata,
            archive_details=updater.fixture_archive_details(),
            bundle_details=updater.fixture_bundle_details(),
        )

    duplicate = _fixture("stable.json")
    duplicate_assets = duplicate["assets"]
    assert isinstance(duplicate_assets, list)
    duplicate_assets.append(dict(duplicate_assets[0]))
    with pytest.raises(updater.ReleaseLockError, match="duplicate"):
        updater.build_lock_from_metadata(
            duplicate,
            archive_details=updater.fixture_archive_details(),
            bundle_details=updater.fixture_bundle_details(),
        )


def test_live_metadata_is_normalized_to_the_fixed_official_repository() -> None:
    updater = _updater()
    metadata = _fixture("stable.json")
    metadata.pop("repository")

    normalized = updater.normalize_github_release(metadata)

    assert normalized["repository"] == "openai/codex"
    assert normalized["tag_name"] == "rust-v1.2.3"


def test_lock_rejects_non_monotonic_or_malformed_tags() -> None:
    updater = _updater()
    candidate = updater.build_lock_from_metadata(
        _fixture("stable.json"),
        archive_details=updater.fixture_archive_details(),
        bundle_details=updater.fixture_bundle_details(),
    )
    previous = json.loads(json.dumps(candidate).replace("rust-v1.2.3", "rust-v1.3.0"))
    previous["release"]["version"] = "1.3.0"

    with pytest.raises(updater.ReleaseLockError, match="newer"):
        updater.require_monotonic_upgrade(candidate, previous)

    assert updater.require_monotonic_upgrade(candidate, candidate) is False

    candidate["release"]["tag"] = "latest"
    with pytest.raises(updater.ReleaseLockError, match="semver"):
        updater.validate_lock(candidate)


def test_lock_rejects_digest_size_decompression_and_sigstore_identity_tampering() -> (
    None
):
    updater = _updater()
    lock = updater.build_lock_from_metadata(
        _fixture("stable.json"),
        archive_details=updater.fixture_archive_details(),
        bundle_details=updater.fixture_bundle_details(),
    )

    cases = [
        ("sha256", "not-a-digest"),
        ("size", updater.MAX_ARCHIVE_BYTES + 1),
        ("decompressed_size", updater.MAX_DECOMPRESSED_BYTES + 1),
    ]
    for key, value in cases:
        tampered = json.loads(json.dumps(lock))
        tampered["assets"]["amd64"]["codex"][key] = value
        with pytest.raises(updater.ReleaseLockError):
            updater.validate_lock(tampered)

    tampered = json.loads(json.dumps(lock))
    tampered["sigstore"]["issuer"] = "https://issuer.example.invalid"
    with pytest.raises(updater.ReleaseLockError, match="issuer"):
        updater.validate_lock(tampered)

    tampered = json.loads(json.dumps(lock))
    tampered["sigstore"]["identity"] = (
        "https://github.com/attacker/codex/.github/workflows/rust-release.yml"
        "@refs/tags/rust-v1.2.3"
    )
    with pytest.raises(updater.ReleaseLockError, match="identity"):
        updater.validate_lock(tampered)

    tampered = json.loads(json.dumps(lock))
    tampered["sigstore"]["transparency_log"] = "not-rekor"
    with pytest.raises(updater.ReleaseLockError, match="transparency"):
        updater.validate_lock(tampered)

    tampered = json.loads(json.dumps(lock))
    tampered["assets"]["amd64"]["codex"]["sigstore"]["log_id"] = "not-a-rekor-log-id"
    with pytest.raises(updater.ReleaseLockError, match="log ID"):
        updater.validate_lock(tampered)


def test_atomic_write_never_replaces_existing_lock_before_full_validation(
    tmp_path: Path,
) -> None:
    updater = _updater()
    target = tmp_path / "codex-release.json"
    target.write_text('{"old": true}\n', encoding="utf-8")
    candidate = updater.build_lock_from_metadata(
        _fixture("stable.json"),
        archive_details=updater.fixture_archive_details(),
        bundle_details=updater.fixture_bundle_details(),
    )
    candidate["assets"]["amd64"]["codex"]["sha256"] = "bad"

    with pytest.raises(updater.ReleaseLockError):
        updater.write_lock_atomically(target, candidate)

    assert target.read_text(encoding="utf-8") == '{"old": true}\n'


def test_archive_extracts_a_single_expected_elf_binary_to_the_given_path(
    tmp_path: Path,
) -> None:
    updater = _updater()
    name = "codex-x86_64-unknown-linux-musl.tar.gz"
    binary = _elf_binary(62)
    destination = tmp_path / "codex"

    details = updater._archive_details(
        _gzip_tar((name.removesuffix(".tar.gz"), binary)),
        name,
        "amd64",
        destination,
    )

    assert details["decompressed_size"] == len(binary)
    assert destination.read_bytes() == binary


def test_archive_rejects_extra_members_and_removes_partial_extraction(
    tmp_path: Path,
) -> None:
    updater = _updater()
    name = "codex-x86_64-unknown-linux-musl.tar.gz"
    destination = tmp_path / "codex"
    archive = _gzip_tar(
        (name.removesuffix(".tar.gz"), _elf_binary(62)),
        ("unexpected", b"not allowed"),
    )

    with pytest.raises(updater.ReleaseLockError, match="exactly"):
        updater._archive_details(archive, name, "amd64", destination)

    assert not destination.exists()


def test_archive_rejects_pax_and_gnu_sparse_metadata_without_interpreting_it(
    tmp_path: Path,
) -> None:
    updater = _updater()
    name = "codex-x86_64-unknown-linux-musl.tar.gz"
    destination = tmp_path / "codex"

    with pytest.raises(updater.ReleaseLockError, match="regular binary"):
        updater._archive_details(
            _pax_sparse_gzip_tar(name.removesuffix(".tar.gz"), _elf_binary(62)),
            name,
            "amd64",
            destination,
        )

    assert not destination.exists()


def test_archive_rejects_nonzero_concatenated_gzip_data(tmp_path: Path) -> None:
    updater = _updater()
    name = "codex-x86_64-unknown-linux-musl.tar.gz"
    destination = tmp_path / "codex"
    archive = _gzip_tar((name.removesuffix(".tar.gz"), _elf_binary(62)))

    with pytest.raises(updater.ReleaseLockError, match="trailing data"):
        updater._archive_details(
            archive + gzip.compress(b"unexpected"),
            name,
            "amd64",
            destination,
        )

    assert not destination.exists()


def test_archive_never_removes_a_preexisting_destination(tmp_path: Path) -> None:
    updater = _updater()
    name = "codex-x86_64-unknown-linux-musl.tar.gz"
    destination = tmp_path / "codex"
    destination.write_bytes(b"preserve me")

    with pytest.raises(updater.ReleaseLockError, match="invalid gzip tar archive"):
        updater._archive_details(
            _gzip_tar((name.removesuffix(".tar.gz"), _elf_binary(62))),
            name,
            "amd64",
            destination,
        )

    assert destination.read_bytes() == b"preserve me"


def test_archive_enforces_aggregate_gzip_expansion_bound_before_tar_parsing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    updater = _updater()
    name = "codex-x86_64-unknown-linux-musl.tar.gz"
    binary = _elf_binary(62)
    archive = _gzip_tar((name.removesuffix(".tar.gz"), binary))
    # A normal tar stream includes headers and end-of-archive blocks; bounding
    # only TarInfo.size would miss this compressed expansion.
    monkeypatch.setattr(updater, "MAX_DECOMPRESSED_BYTES", len(binary))
    monkeypatch.setattr(updater, "MAX_TAR_OVERHEAD_BYTES", 1024)

    with pytest.raises(updater.ReleaseLockError, match="decompressed"):
        updater._archive_details(archive, name, "amd64", tmp_path / "codex")


def test_archive_accepts_exact_aggregate_gzip_expansion_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    updater = _updater()
    name = "codex-x86_64-unknown-linux-musl.tar.gz"
    binary = _elf_binary(62)
    archive = _gzip_tar((name.removesuffix(".tar.gz"), binary))
    expanded_size = len(gzip.decompress(archive))
    monkeypatch.setattr(updater, "MAX_DECOMPRESSED_BYTES", len(binary))
    monkeypatch.setattr(updater, "MAX_TAR_OVERHEAD_BYTES", expanded_size - len(binary))

    details = updater._archive_details(archive, name, "amd64", tmp_path / "codex")

    assert details["decompressed_size"] == len(_elf_binary(62))


@pytest.mark.parametrize(("arch", "machine"), (("amd64", 183), ("aarch64", 62)))
def test_archive_rejects_wrong_elf_architecture(
    tmp_path: Path, arch: str, machine: int
) -> None:
    updater = _updater()
    target = updater.ARCHITECTURES[arch]
    name = f"codex-{target}-unknown-linux-musl.tar.gz"

    with pytest.raises(updater.ReleaseLockError, match="ELF"):
        updater._archive_details(
            _gzip_tar((name.removesuffix(".tar.gz"), _elf_binary(machine))),
            name,
            arch,
            tmp_path / "codex",
        )


def test_competing_process_cannot_replace_a_newer_lock_after_waiting(
    tmp_path: Path,
) -> None:
    updater = _updater()
    target = tmp_path / "codex-release.json"
    lower = updater.build_lock_from_metadata(
        _fixture("stable.json"),
        archive_details=updater.fixture_archive_details(),
        bundle_details=updater.fixture_bundle_details(),
    )
    newer = json.loads(json.dumps(lower).replace("rust-v1.2.3", "rust-v1.2.4"))
    newer["release"]["version"] = "1.2.4"
    context = multiprocessing.get_context("spawn")
    attempted = context.Event()
    result = context.Queue()

    with updater._exclusive_lock(target):
        process = context.Process(
            target=_commit_candidate_in_process,
            args=(str(target), lower, attempted, result),
        )
        process.start()
        assert attempted.wait(timeout=10)
        updater.write_lock_atomically(target, newer)

    try:
        assert result.get(timeout=10).startswith(
            "error:candidate release must be newer"
        )
        process.join(timeout=10)
        assert process.exitcode == 0
    finally:
        if process.is_alive():
            process.terminate()
            process.join()
    assert json.loads(target.read_text(encoding="utf-8")) == newer


def test_legacy_cosign_bundle_records_rekor_evidence_and_verifies_raw_binary(
    tmp_path: Path,
) -> None:
    updater = _updater()
    details = updater.parse_legacy_cosign_bundle(
        (FIXTURES / "legacy.sigstore.json").read_bytes(),
        "codex-x86_64-unknown-linux-musl.sigstore",
        "rust-v1.2.3",
    )
    assert details["log_id"] == "a" * 64
    assert details["log_index"] == 1
    assert details["integrated_time"] == 1_784_003_665
    assert details["signed_sha256"] == "c" * 64

    binary = tmp_path / "codex-x86_64-unknown-linux-musl"
    bundle = tmp_path / "codex-x86_64-unknown-linux-musl.sigstore"
    binary.write_bytes(b"raw executable")
    bundle.write_bytes(b"bundle")
    command: list[str] = []

    def runner(arguments: list[str]) -> int:
        command.extend(arguments)
        return 0

    updater.verify_sigstore_blob(
        "cosign", binary, bundle, "rust-v1.2.3", "b" * 40, runner=runner
    )
    assert command[-1] == str(binary)
    assert str(bundle) in command
    assert (
        command[command.index("--certificate-github-workflow-name") + 1]
        == "rust-release"
    )
    assert (
        command[command.index("--certificate-github-workflow-repository") + 1]
        == "openai/codex"
    )
    assert (
        command[command.index("--certificate-github-workflow-ref") + 1]
        == "refs/tags/rust-v1.2.3"
    )
    assert command[command.index("--certificate-github-workflow-sha") + 1] == "b" * 40
    assert command[command.index("--certificate-github-workflow-trigger") + 1] == "push"
