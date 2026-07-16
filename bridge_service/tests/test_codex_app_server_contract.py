from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

from codex_bridge_service.codex_app_server_contract import (
    AppServerProtocolValidator,
    ProtocolContractError,
    extract_protocol_contract,
    load_bundled_protocol_contract,
    parse_protocol_contract,
)
from codex_bridge_service.codex_app_server import (
    AppServerProtocolError,
    CodexAppServerClient,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
GENERATOR = REPOSITORY_ROOT / "scripts" / "generate_codex_app_server_contract.py"
LOCK = REPOSITORY_ROOT / "codex_bridge_app" / "codex-release.json"


def _canonical_codex_version() -> str:
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    return lock["release"]["version"]


def _write_schema(root: Path, filename: str, methods: list[str]) -> None:
    entries = [
        {
            "type": "object",
            "properties": {"method": {"type": "string", "enum": [method]}},
        }
        for method in methods
    ]
    (root / filename).write_text(
        json.dumps({"oneOf": entries}),
        encoding="utf-8",
    )


def _schema_bundle(tmp_path: Path) -> Path:
    root = tmp_path / "schema"
    root.mkdir()
    _write_schema(root, "ClientRequest.json", ["initialize", "model/list"])
    _write_schema(root, "ClientNotification.json", ["initialized"])
    _write_schema(root, "ServerRequest.json", ["item/tool/requestUserInput"])
    _write_schema(root, "ServerNotification.json", ["account/updated"])
    nested = root / "v2"
    nested.mkdir()
    (nested / "ModelListParams.json").write_text(
        json.dumps({"type": "object"}),
        encoding="utf-8",
    )
    stable_definitions = {
        name: {"type": "object"}
        for name in (
            "ClientRequest",
            "ClientNotification",
            "ServerRequest",
            "ServerNotification",
            "JSONRPCError",
            "InitializeResponse",
            "ToolRequestUserInputResponse",
        )
    }
    (root / "codex_app_server_protocol.schemas.json").write_text(
        json.dumps({"definitions": stable_definitions}),
        encoding="utf-8",
    )
    (root / "codex_app_server_protocol.v2.schemas.json").write_text(
        json.dumps({"definitions": {"ModelListResponse": {"type": "object"}}}),
        encoding="utf-8",
    )
    return root.resolve()


def test_extract_contract_locks_entire_bundle_and_exact_method_sets(tmp_path: Path) -> None:
    root = _schema_bundle(tmp_path)

    first = extract_protocol_contract(root, codex_version="codex-cli 0.139.0")
    second = extract_protocol_contract(root, codex_version="codex-cli 0.139.0")

    assert first == second
    assert first.client_requests == {"initialize", "model/list"}
    assert first.client_notifications == {"initialized"}
    assert first.server_requests == {"item/tool/requestUserInput"}
    assert first.server_notifications == {"account/updated"}
    original_digest = first.schema_bundle_sha256
    (root / "v2" / "ModelListParams.json").write_text(
        json.dumps({"type": "object", "properties": {"cursor": {"type": "string"}}}),
        encoding="utf-8",
    )
    changed = extract_protocol_contract(root, codex_version="codex-cli 0.139.0")
    assert changed.schema_bundle_sha256 != original_digest


def test_contract_rejects_duplicate_methods_and_noncanonical_manifest(tmp_path: Path) -> None:
    root = _schema_bundle(tmp_path)
    _write_schema(root, "ServerRequest.json", ["duplicate", "duplicate"])
    with pytest.raises(ProtocolContractError):
        extract_protocol_contract(root, codex_version="codex-cli 0.139.0")

    valid = load_bundled_protocol_contract().to_manifest()
    valid["clientRequests"] = list(reversed(valid["clientRequests"]))
    with pytest.raises(ProtocolContractError):
        parse_protocol_contract(json.dumps(valid))


def test_bundled_contract_contains_required_stable_bridge_methods() -> None:
    contract = load_bundled_protocol_contract()

    assert contract.codex_version == f"codex-cli {_canonical_codex_version()}"
    for method in {
        "initialize",
        "account/read",
        "account/login/start",
        "account/logout",
        "account/rateLimits/read",
        "model/list",
        "thread/start",
        "turn/start",
        "turn/interrupt",
    }:
        contract.require("clientRequests", method)
    contract.require("clientNotifications", "initialized")
    contract.require("serverRequests", "item/tool/requestUserInput")
    contract.require("serverNotifications", "account/updated")


def test_bundled_contract_validates_model_provider_capability_response() -> None:
    validator = AppServerProtocolValidator(load_bundled_protocol_contract())

    validator.validate_client_response(
        "modelProvider/capabilities/read",
        result={
            "imageGeneration": True,
            "namespaceTools": False,
            "webSearch": True,
        },
    )
    with pytest.raises(ProtocolContractError):
        validator.validate_client_response(
            "modelProvider/capabilities/read",
            result={
                "imageGeneration": 1,
                "namespaceTools": False,
                "webSearch": True,
            },
        )


def test_generator_check_detects_schema_drift(tmp_path: Path) -> None:
    root = _schema_bundle(tmp_path)
    output = tmp_path / "contract.json"
    command = [
        sys.executable,
        str(GENERATOR),
        "--schema-dir",
        str(root),
        "--codex-version",
        "codex-cli 0.139.0",
        "--out",
        str(output),
    ]

    assert subprocess.run(command, check=False).returncode == 0
    assert subprocess.run([*command, "--check"], check=False).returncode == 0
    output.write_text("{}\n", encoding="utf-8")
    assert subprocess.run([*command, "--check"], check=False).returncode == 1


def test_default_client_rejects_methods_absent_from_locked_schema(tmp_path: Path) -> None:
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    client = CodexAppServerClient(codex_home=codex_home)

    client.register_notification_handler("account/updated", lambda _message: None)
    client.register_request_handler(
        "item/tool/requestUserInput",
        lambda _message: {},
    )
    with pytest.raises(AppServerProtocolError):
        client.register_notification_handler("invented/notification", lambda _message: None)
    with pytest.raises(AppServerProtocolError):
        client.register_request_handler("invented/request", lambda _message: {})
    with pytest.raises(AppServerProtocolError):
        client.request("invented/clientMethod")


def test_runtime_validator_rejects_invalid_locked_payloads_and_results() -> None:
    contract = load_bundled_protocol_contract()
    validator = AppServerProtocolValidator(contract)

    validator.validate_client_request(
        {
            "method": "initialize",
            "id": 1,
            "params": {
                "clientInfo": {"name": "ha_codex_bridge", "version": "0.6.0"}
            },
        }
    )
    with pytest.raises(ProtocolContractError):
        validator.validate_client_request(
            {"method": "initialize", "id": 1, "params": {}}
        )
    with pytest.raises(ProtocolContractError):
        validator.validate_server_request(
            {
                "method": "item/tool/requestUserInput",
                "id": "question-1",
                "params": {},
            }
        )
    with pytest.raises(ProtocolContractError):
        validator.validate_server_notification(
            {"method": "account/updated", "params": {"authMode": "api-key"}}
        )
    with pytest.raises(ProtocolContractError):
        validator.validate_client_response("initialize", result={})
