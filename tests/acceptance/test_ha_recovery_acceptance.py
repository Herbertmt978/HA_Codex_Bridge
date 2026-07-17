"""Acceptance contract tests for the offline recovery evidence collector."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import stat

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "acceptance" / "collect_recovery_acceptance.py"
SPEC = importlib.util.spec_from_file_location("collect_recovery_acceptance", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
collector = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(collector)


def digest(character: str) -> str:
    return f"sha256:{character * 64}"


def fingerprint(character: str) -> str:
    return character * 64


def components(version: str, seed: str) -> dict[str, dict[str, str]]:
    return {
        name: {"version": version, "digest": digest(character)}
        for name, character in zip(("app", "integration", "bridge", "codex"), seed, strict=True)
    }


def snapshot(
    phase: str,
    *,
    capture_id: str | None = None,
    captured_at: str | None = None,
    component_values: dict[str, dict[str, str]] | None = None,
    retained: dict[str, dict[str, str]] | None = None,
    rollback: dict[str, object] | None = None,
    backup_id: str = "backup-acceptance-001",
    test_ha: str = "acceptance-ha-01",
    supervisor_uuid: str = "11111111-1111-1111-1111-111111111111",
) -> dict[str, object]:
    component_values = component_values or components("0.9.0", "abcd")
    retained = retained or components("0.8.3", "ef01")
    capture_id = capture_id or (
        "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        if phase == "pre"
        else "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    )
    captured_at = captured_at or (
        "2026-07-17T10:00:00Z" if phase == "pre" else "2026-07-17T11:00:00+00:00"
    )
    return {
        "schema_version": 1,
        "capture": {"id": capture_id, "phase": phase, "captured_at": captured_at},
        "test_ha": test_ha,
        "supervisor_uuid": supervisor_uuid,
        "components": component_values,
        "backup": {"id": backup_id, "verified": True},
        "readiness": {"home_assistant": "ready", "app": "ready", "bridge": "ready"},
        "sandbox": {"status": "passed"},
        "account": {"state": "authenticated"},
        "fingerprints": {
            "workspace": fingerprint("1"),
            "chat": fingerprint("2"),
            "artifact": fingerprint("3"),
            "automation": fingerprint("4"),
        },
        "recovery": {
            "retained_image": {"healthy": True, "components": retained},
            "rollback": rollback,
        },
    }


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def run(tmp_path: Path, pre: object, post: object, *extra: str) -> tuple[int, Path]:
    pre_path, post_path, output = tmp_path / "pre.json", tmp_path / "post.json", tmp_path / "result.json"
    write_json(pre_path, pre)
    write_json(post_path, post)
    return collector.main(["--pre", str(pre_path), "--post", str(post_path), "--output", str(output), *extra]), output


def test_happy_cold_restore_emits_canonical_redacted_manifest(tmp_path: Path) -> None:
    pre = snapshot("pre")
    code, output = run(tmp_path, pre, snapshot("post"))

    assert code == 0
    raw = output.read_text(encoding="utf-8")
    assert raw == json.dumps(json.loads(raw), sort_keys=True, separators=(",", ":")) + "\n"
    result = json.loads(raw)
    assert result["status"] == "evidence_format_validated"
    assert result["evidence_scope"] == "offline_snapshot_consistency"
    assert result["mode"] == "cold-restore"
    assert result["components"] == pre["components"]
    assert pre["test_ha"] not in raw
    assert pre["supervisor_uuid"] not in raw
    assert pre["backup"]["id"] not in raw  # type: ignore[index]
    assert pre["capture"]["id"] not in raw  # type: ignore[index]
    assert result["capture"]["pre_captured_at"] == "2026-07-17T10:00:00Z"
    assert "capture" in result["checks"]


def test_retained_image_recovery_is_separate_and_requires_exact_target(tmp_path: Path) -> None:
    pre = snapshot("pre")
    target = pre["recovery"]["retained_image"]["components"]  # type: ignore[index]
    post = snapshot(
        "post",
        component_values=target,  # type: ignore[arg-type]
        rollback={
            "verified": True,
            "from_components": pre["components"],
            "target_components": target,
        },
    )

    code, output = run(tmp_path, pre, post, "--mode", "retained-image")

    assert code == 0
    assert json.loads(output.read_text(encoding="utf-8"))["mode"] == "retained-image"


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (lambda pre, post: pre["backup"].update({"verified": False}), "backup"),
        (lambda pre, post: post.update({"test_ha": "other-test-ha"}), "identity"),
        (
            lambda pre, post: post["components"]["app"].update({"digest": digest("f")}),
            "versions and digests",
        ),
        (lambda pre, post: post["fingerprints"].update({"chat": fingerprint("9")}), "fingerprints"),
    ],
)
def test_rejects_missing_backup_wrong_target_or_moved_evidence(
    tmp_path: Path, mutate: object, expected: str, capsys: pytest.CaptureFixture[str]
) -> None:
    pre, post = snapshot("pre"), snapshot("post")
    mutate(pre, post)  # type: ignore[operator]

    code, output = run(tmp_path, pre, post)

    assert code == 1
    assert not output.exists()
    assert expected in capsys.readouterr().err


def test_rejects_interrupted_partial_snapshot(tmp_path: Path) -> None:
    pre_path, post_path, output = tmp_path / "pre.json", tmp_path / "post.json", tmp_path / "result.json"
    write_json(pre_path, snapshot("pre"))
    post_path.write_text('{"schema_version": 1,', encoding="utf-8")

    assert collector.main(["--pre", str(pre_path), "--post", str(post_path), "--output", str(output)]) == 1
    assert not output.exists()


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.update(schema_version=True),
        lambda value: value["capture"].update(phase=[]),
    ],
)
def test_rejects_malformed_schema_or_phase_without_traceback(
    tmp_path: Path,
    mutation: object,
    capsys: pytest.CaptureFixture[str],
) -> None:
    pre, post = snapshot("pre"), snapshot("post")
    mutation(post)  # type: ignore[operator]

    code, output = run(tmp_path, pre, post)

    captured = capsys.readouterr()
    assert code == 1
    assert not output.exists()
    assert "Traceback" not in captured.err


def test_rejects_duplicate_keys_and_oversized_input(tmp_path: Path) -> None:
    pre_path, post_path, output = tmp_path / "pre.json", tmp_path / "post.json", tmp_path / "result.json"
    post_path.write_text(json.dumps(snapshot("post")), encoding="utf-8")
    pre_path.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")

    assert collector.main(["--pre", str(pre_path), "--post", str(post_path), "--output", str(output)]) == 1
    assert not output.exists()
    pre_path.write_bytes(b" " * (collector.MAX_SNAPSHOT_BYTES + 1))
    assert collector.main(["--pre", str(pre_path), "--post", str(post_path), "--output", str(output)]) == 1
    assert not output.exists()


@pytest.mark.parametrize("unsafe_key", ["url", "token", "cookie", "authorization"])
def test_rejects_unsafe_fields_without_echoing_them(
    tmp_path: Path, unsafe_key: str, capsys: pytest.CaptureFixture[str]
) -> None:
    pre, post = snapshot("pre"), snapshot("post")
    post[unsafe_key] = "https://private.invalid/secret"  # type: ignore[index]

    code, output = run(tmp_path, pre, post)

    captured = capsys.readouterr()
    assert code == 1
    assert not output.exists()
    assert "private.invalid" not in captured.err
    assert "secret" not in captured.err


def test_failure_preserves_existing_output_and_atomic_writer_cleans_temporary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pre_path, post_path, output = tmp_path / "pre.json", tmp_path / "post.json", tmp_path / "result.json"
    write_json(pre_path, snapshot("pre"))
    invalid = snapshot("post")
    invalid["account"] = {"state": "https://private.invalid"}
    write_json(post_path, invalid)
    output.write_text("existing-result\n", encoding="utf-8")

    assert collector.main(["--pre", str(pre_path), "--post", str(post_path), "--output", str(output)]) == 1
    assert output.read_text(encoding="utf-8") == "existing-result\n"

    manifest = collector.collect(
        collector.validate_snapshot(snapshot("pre"), "pre"),
        collector.validate_snapshot(snapshot("post"), "post"),
        "cold-restore",
    )
    monkeypatch.setattr(collector.os, "replace", lambda source, target: (_ for _ in ()).throw(OSError("replace failed")))
    with pytest.raises(OSError, match="replace failed"):
        collector.write_manifest(output, manifest)
    assert output.read_text(encoding="utf-8") == "existing-result\n"
    assert not list(tmp_path.glob(".result.json.*.tmp"))


@pytest.mark.parametrize(
    ("pre_capture", "post_capture", "expected"),
    [
        (
            {"id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "phase": "post", "captured_at": "2026-07-17T10:00:00Z"},
            None,
            "phase",
        ),
        (
            None,
            {"id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", "phase": "post", "captured_at": "2026-07-17T11:00:00"},
            "timestamp",
        ),
        (
            None,
            {
                "id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
                "phase": "post",
                "captured_at": "2026-07-17T11:00:00.1234567Z",
            },
            "timestamp",
        ),
        (
            None,
            {"id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "phase": "post", "captured_at": "2026-07-17T11:00:00Z"},
            "replayed",
        ),
        (
            None,
            {"id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", "phase": "post", "captured_at": "2026-07-17T09:59:59Z"},
            "ordered",
        ),
    ],
)
def test_rejects_invalid_replayed_or_unordered_capture_evidence(
    tmp_path: Path,
    pre_capture: dict[str, str] | None,
    post_capture: dict[str, str] | None,
    expected: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    pre, post = snapshot("pre"), snapshot("post")
    if pre_capture is not None:
        pre["capture"] = pre_capture
    if post_capture is not None:
        post["capture"] = post_capture

    code, output = run(tmp_path, pre, post)

    assert code == 1
    assert not output.exists()
    assert expected in capsys.readouterr().err


def test_rejects_same_file_and_hard_linked_snapshots(tmp_path: Path) -> None:
    source, output = tmp_path / "snapshot.json", tmp_path / "result.json"
    write_json(source, snapshot("pre"))
    args = ["--pre", str(source), "--post", str(source), "--output", str(output)]
    assert collector.main(args) == 1
    assert not output.exists()

    linked = tmp_path / "linked.json"
    try:
        os.link(source, linked)
    except OSError as exc:
        pytest.skip(f"hard links are unavailable: {exc}")
    args = ["--pre", str(source), "--post", str(linked), "--output", str(output)]
    assert collector.main(args) == 1
    assert not output.exists()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("readiness", {"home_assistant": "ready", "app": "starting", "bridge": "ready"}),
        ("sandbox", {"status": "degraded"}),
        ("account", {"state": "logged_out"}),
    ],
)
def test_requires_exact_healthy_pre_and_post_categories(
    tmp_path: Path, field: str, value: object
) -> None:
    pre, post = snapshot("pre"), snapshot("post")
    pre[field] = value
    post[field] = value

    code, output = run(tmp_path, pre, post)

    assert code == 1
    assert not output.exists()


def test_rejects_preflight_retained_app_equal_to_current(tmp_path: Path) -> None:
    current = components("0.9.0", "abcd")
    pre = snapshot("pre", component_values=current, retained=current)

    code, output = run(tmp_path, pre, snapshot("post", component_values=current, retained=current))

    assert code == 1
    assert not output.exists()


def test_snapshot_reader_uses_descriptor_and_detects_path_identity_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, other = tmp_path / "snapshot.json", tmp_path / "other.json"
    write_json(source, snapshot("pre"))
    write_json(other, snapshot("post"))
    monkeypatch.setattr(Path, "open", lambda *args, **kwargs: pytest.fail("Path.open reopens the path"))
    real_lstat = os.lstat
    monkeypatch.setattr(collector.os, "lstat", lambda path: real_lstat(other))

    with pytest.raises(collector.RecoveryEvidenceError, match="changed"):
        collector.read_snapshot(source, "snapshot")


def test_snapshot_reader_sets_nofollow_when_platform_supports_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "snapshot.json"
    write_json(source, snapshot("pre"))
    fake_nofollow = 1 << 29
    observed: list[int] = []
    real_open = os.open

    def tracked_open(path: object, flags: int) -> int:
        observed.append(flags)
        return real_open(path, flags & ~fake_nofollow)

    monkeypatch.setattr(collector.os, "O_NOFOLLOW", fake_nofollow, raising=False)
    monkeypatch.setattr(collector.os, "open", tracked_open)

    assert collector.read_snapshot(source, "snapshot")["schema_version"] == 1
    assert observed and observed[0] & fake_nofollow


def test_snapshot_reader_rejects_non_regular_open_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "snapshot.json"
    write_json(source, snapshot("pre"))
    real_fstat = os.fstat

    def non_regular_fstat(descriptor: int) -> os.stat_result:
        metadata = real_fstat(descriptor)
        values = list(metadata)
        values[0] = stat.S_IFDIR | 0o700
        return os.stat_result(values)

    monkeypatch.setattr(collector.os, "fstat", non_regular_fstat)

    with pytest.raises(collector.RecoveryEvidenceError, match="descriptor is not a regular file"):
        collector.read_snapshot(source, "snapshot")


def test_snapshot_reader_rejects_same_inode_content_metadata_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "snapshot.json"
    write_json(source, snapshot("pre"))
    real_fstat = os.fstat
    calls = 0

    def changing_fstat(descriptor: int) -> os.stat_result:
        nonlocal calls
        metadata = real_fstat(descriptor)
        calls += 1
        if calls < 2:
            return metadata
        values = list(metadata)
        # Preserve st_dev/st_ino while simulating an in-place rewrite.
        values[6] = metadata.st_size + 1
        return os.stat_result(values)

    monkeypatch.setattr(collector.os, "fstat", changing_fstat)

    with pytest.raises(collector.RecoveryEvidenceError, match="changed while it was read"):
        collector.read_snapshot(source, "snapshot")
