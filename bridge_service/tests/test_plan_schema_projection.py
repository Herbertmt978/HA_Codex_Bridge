"""Native planning is a narrow opt-in to the pinned experimental contract."""

import importlib.util
import json
from pathlib import Path

import pytest

from codex_bridge_service.codex_app_server_contract import (
    AppServerProtocolValidator,
    ProtocolContractError,
    load_bundled_protocol_contract,
)


def _generator():
    path = Path(__file__).resolve().parents[2] / "scripts/generate_codex_app_server_contract.py"
    spec = importlib.util.spec_from_file_location("plan_schema_generator", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_projection_imports_only_native_planning_field(tmp_path):
    stable = tmp_path / "stable"
    experimental = tmp_path / "experimental"
    stable.mkdir()
    experimental.mkdir()
    dependencies = {name: {"type": "object"} for name in ("CollaborationMode", "ModeKind", "Settings", "ReasoningEffort")}
    for filename, nested in (("codex_app_server_protocol.schemas.json", True), ("codex_app_server_protocol.v2.schemas.json", False)):
        base = {**dependencies, "TurnStartParams": {"properties": {"threadId": {"type": "string"}}}}
        new = {**dependencies, "TurnStartParams": {"properties": {"threadId": {"type": "string"}, "collaborationMode": {"type": "object"}, "unrelatedExperimental": {"type": "string"}}}, "UnrelatedRequest": {"type": "object"}}
        (stable / filename).write_text(json.dumps({"definitions": {"v2": base} if nested else base}))
        (experimental / filename).write_text(json.dumps({"definitions": {"v2": new} if nested else new}))
    _generator()._project_plan_schema(stable, experimental)
    projected = json.loads((stable / "codex_app_server_protocol.v2.schemas.json").read_text())["definitions"]
    assert set(projected["TurnStartParams"]["properties"]) == {"threadId", "collaborationMode"}
    assert "UnrelatedRequest" not in projected


def test_native_plan_request_is_validated_against_pinned_schema():
    validator = AppServerProtocolValidator(load_bundled_protocol_contract())
    request = {
        "id": 1, "method": "turn/start",
        "params": {
            "threadId": "test-thread",
            "input": [{"type": "text", "text": "Plan the change", "text_elements": []}],
            "collaborationMode": {"mode": "plan", "settings": {"model": "test-model", "reasoning_effort": "high", "developer_instructions": None}},
        },
    }
    validator.validate_client_request(request)
    request["params"]["collaborationMode"]["mode"] = "invented-mode"
    with pytest.raises(ProtocolContractError):
        validator.validate_client_request(request)
