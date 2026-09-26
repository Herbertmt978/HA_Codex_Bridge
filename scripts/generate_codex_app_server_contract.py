from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BRIDGE_SOURCE = REPOSITORY_ROOT / "bridge_service" / "src"
sys.path.insert(0, str(BRIDGE_SOURCE))

from codex_bridge_service.codex_app_server_contract import (  # noqa: E402
    ProtocolContractError,
    extract_protocol_contract,
    extract_runtime_schema_documents,
)
from codex_bridge_service.codex_process import (  # noqa: E402
    codex_command_prefix,
    codex_subprocess_environment,
    resolve_codex_home,
)

DEFAULT_OUTPUT = (
    BRIDGE_SOURCE
    / "codex_bridge_service"
    / "codex_app_server_contract.json"
)
DEFAULT_STABLE_SCHEMA_OUTPUT = DEFAULT_OUTPUT.with_name(
    "codex_app_server_protocol.schema.json"
)
DEFAULT_V2_SCHEMA_OUTPUT = DEFAULT_OUTPUT.with_name(
    "codex_app_server_protocol.v2.schema.json"
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate or check the locked Codex app-server method contract."
    )
    parser.add_argument("--codex-command", default="codex")
    parser.add_argument("--codex-home")
    parser.add_argument("--schema-dir", type=Path)
    parser.add_argument("--experimental-schema-dir", type=Path)
    parser.add_argument("--codex-version")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--stable-schema-out",
        type=Path,
    )
    parser.add_argument(
        "--v2-schema-out",
        type=Path,
    )
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    if (args.schema_dir is None) != (args.codex_version is None):
        parser.error("--schema-dir and --codex-version must be supplied together")

    try:
        if args.schema_dir is not None:
            with tempfile.TemporaryDirectory(prefix="codex-plan-schema-") as raw:
                schema_root = Path(raw) / "schema"
                extract_runtime_schema_documents(args.schema_dir.resolve())
                shutil.copytree(args.schema_dir.resolve(), schema_root, symlinks=True)
                if args.experimental_schema_dir is not None:
                    _project_plan_schema(schema_root, args.experimental_schema_dir.resolve())
                contract = extract_protocol_contract(schema_root, codex_version=args.codex_version)
                stable_schema, v2_schema = extract_runtime_schema_documents(schema_root)
        else:
            codex_command = _resolve_command(args.codex_command)
            codex_home = resolve_codex_home(args.codex_home, codex_command)
            environment = codex_subprocess_environment(codex_home)
            version = _read_codex_version(codex_command, environment)
            with tempfile.TemporaryDirectory(prefix="codex-app-server-schema-") as raw:
                schema_root = Path(raw)
                completed = subprocess.run(
                    [
                        *codex_command_prefix(codex_command),
                        "app-server",
                        "generate-json-schema",
                        "--out",
                        str(schema_root),
                    ],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    env=environment,
                    check=False,
                    timeout=60,
                )
                if completed.returncode != 0:
                    raise ProtocolContractError("Codex schema generation failed")
                experimental_root = schema_root.parent / (schema_root.name + "-experimental")
                try:
                    completed = subprocess.run(
                        [*codex_command_prefix(codex_command), "app-server", "generate-json-schema", "--experimental", "--out", str(experimental_root)],
                        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL, env=environment, check=False, timeout=60,
                    )
                    if completed.returncode != 0:
                        raise ProtocolContractError("Codex experimental schema generation failed")
                    _project_plan_schema(schema_root, experimental_root)
                finally:
                    if experimental_root.is_dir():
                        shutil.rmtree(experimental_root)
                contract = extract_protocol_contract(
                    schema_root,
                    codex_version=version,
                )
                stable_schema, v2_schema = extract_runtime_schema_documents(schema_root)
        expected = contract.to_json()
        output = args.out.resolve()
        stable_output = (
            args.stable_schema_out.resolve()
            if args.stable_schema_out is not None
            else output.with_name(DEFAULT_STABLE_SCHEMA_OUTPUT.name)
        )
        v2_output = (
            args.v2_schema_out.resolve()
            if args.v2_schema_out is not None
            else output.with_name(DEFAULT_V2_SCHEMA_OUTPUT.name)
        )
        if args.check:
            if not _matches_text(output, expected):
                return _fail("protocol contract differs from the bundled Codex schema")
            if not _matches_bytes(stable_output, stable_schema) or not _matches_bytes(
                v2_output,
                v2_schema,
            ):
                return _fail("runtime protocol schemas differ from the bundled Codex schema")
            return 0
        _write_atomic(output, expected.encode("utf-8"))
        _write_atomic(stable_output, stable_schema)
        _write_atomic(v2_output, v2_schema)
        return 0
    except (ProtocolContractError, OSError, subprocess.TimeoutExpired) as error:
        return _fail(str(error))


def _project_plan_schema(stable_root: Path, experimental_root: Path) -> None:
    """Include only the verified native turn/start planning field.

    Keep all stable method envelopes and unrelated experimental fields unchanged.
    The native initialise handshake already opts into experimentalApi.
    """
    extract_runtime_schema_documents(experimental_root)
    for filename, definition in (
        ("codex_app_server_protocol.schemas.json", "v2/TurnStartParams"),
        ("codex_app_server_protocol.v2.schemas.json", "TurnStartParams"),
    ):
        stable_path = stable_root / filename
        stable = json.loads(stable_path.read_text(encoding="utf-8"))
        experimental = json.loads((experimental_root / filename).read_text(encoding="utf-8"))
        stable_definitions = stable["definitions"].get("v2", stable["definitions"])
        experimental_definitions = experimental["definitions"].get("v2", experimental["definitions"])
        name = definition.rsplit("/", 1)[-1]
        field = experimental_definitions[name]["properties"].get("collaborationMode")
        if not isinstance(field, dict):
            raise ProtocolContractError("native collaboration mode schema is unavailable")
        # Dependencies already exist in the stable schema. Reject changed dependencies
        # rather than importing a broader experimental contract accidentally.
        for dependency in ("CollaborationMode", "ModeKind", "Settings", "ReasoningEffort"):
            if stable_definitions.get(dependency) != experimental_definitions.get(dependency):
                raise ProtocolContractError("native collaboration schema dependencies changed")
        stable_definitions[name]["properties"]["collaborationMode"] = field
        stable_path.write_text(json.dumps(stable, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


def _read_codex_version(command: str, environment: dict[str, str]) -> str:
    completed = subprocess.run(
        [*codex_command_prefix(command), "--version"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=environment,
        check=False,
        timeout=15,
    )
    if completed.returncode != 0 or len(completed.stdout) > 256:
        raise ProtocolContractError("Codex version probe failed")
    try:
        return completed.stdout.decode("utf-8", errors="strict").strip()
    except UnicodeDecodeError:
        raise ProtocolContractError("Codex version probe was invalid") from None


def _resolve_command(command: str) -> str:
    target = Path(command)
    if target.suffix or target.parent != Path("."):
        return command
    discovered = shutil.which(command)
    if discovered is None:
        return command
    if os.name == "nt":
        powershell_wrapper = Path(discovered).with_suffix(".ps1")
        if powershell_wrapper.is_file():
            return str(powershell_wrapper)
    return discovered


def _fail(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 1


def _matches_text(path: Path, expected: str) -> bool:
    try:
        return path.read_text(encoding="utf-8") == expected
    except (FileNotFoundError, OSError, UnicodeDecodeError):
        return False


def _matches_bytes(path: Path, expected: bytes) -> bool:
    try:
        return path.read_bytes() == expected
    except (FileNotFoundError, OSError):
        return False


def _write_atomic(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(content)
    temporary.replace(path)


if __name__ == "__main__":
    raise SystemExit(main())
