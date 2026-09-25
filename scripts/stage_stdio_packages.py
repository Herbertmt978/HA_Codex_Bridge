#!/usr/bin/env python3
"""Stage the reviewed stdio MCP catalogue into an offline App build context.

This script never resolves packages at image build or runtime. The package
source and every byte digest are fixed in the Bridge wheel catalogue; staging
fails if source files have changed without a reviewed lock update.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bridge_service" / "src"))
from codex_bridge_service.stdio_package_catalogue import (  # noqa: E402
    catalogue,
    manifest_for,
)


class StagePackageError(RuntimeError):
    """The package source no longer matches the reviewed image lock."""


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def stage_catalogue(destination: Path) -> None:
    """Create one immutable image tree from the compiled-in catalogue."""

    if destination.exists():
        raise StagePackageError("package destination must be new")
    source_root = ROOT / "codex_bridge_app" / "stdio_packages"
    for spec in catalogue():
        source = source_root / spec.package_id / spec.revision
        expected = {relative: digest for relative, digest in spec.files}
        if not source.is_dir() or source.is_symlink():
            raise StagePackageError("reviewed package source is unavailable")
        actual: dict[str, str] = {}
        for path in source.rglob("*"):
            if path.is_symlink():
                raise StagePackageError("package source contains a symlink")
            if path.is_file():
                relative = path.relative_to(source).as_posix()
                actual[relative] = _hash(path)
            elif not path.is_dir():
                raise StagePackageError("package source contains an unsafe entry")
        if actual != expected:
            raise StagePackageError("package source differs from the reviewed lock")
        target = destination / spec.package_id / spec.revision
        target.mkdir(parents=True)
        for relative in expected:
            output = target / relative
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / relative, output)
            if _hash(output) != expected[relative]:
                raise StagePackageError("package copy failed digest verification")
            output.chmod(0o444)
        manifest = target / "manifest.json"
        manifest.write_bytes(
            (json.dumps(manifest_for(spec), sort_keys=True, separators=(",", ":")) + "\n").encode()
        )
        manifest.chmod(0o444)
    # Postpone directory sealing until every revision has been staged. A later
    # signed image may carry both the current and prior revision for rollback.
    for path in sorted(destination.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        if path.is_dir():
            path.chmod(0o555)
    destination.chmod(0o555)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        stage_catalogue(args.output)
    except (OSError, StagePackageError) as exc:
        parser.exit(1, f"stdio package staging failed: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
