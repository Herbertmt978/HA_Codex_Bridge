from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from codex_bridge_service.app import create_app
from codex_bridge_service.models import RuntimeProfile


AUTHORIZATION = {
    "Authorization": "Bearer secret",
    "X-Codex-Bridge-Api": "1",
}
INTERACTIONS_PATH = "/interactions/pending"


class _NoopLifecycle:
    def start(self) -> None:
        pass

    def close(self) -> None:
        pass


class _NoopAuthCoordinator(_NoopLifecycle):
    pass


def _problem(
    status_code: int,
    code: str,
    *,
    reason: str | None = None,
    secret_cause: str | None = None,
) -> HTTPException:
    detail: dict[str, object] = {"code": code, "retryable": False}
    if reason is not None:
        detail["reason"] = reason
    error = HTTPException(status_code=status_code, detail=detail)
    if secret_cause is not None:
        error.__cause__ = RuntimeError(secret_cause)
    return error


def _command_approval(
    interaction_id: str = "interaction-command-1",
    *,
    thread_id: str = "thread-alpha",
) -> dict[str, Any]:
    return {
        "interaction_id": interaction_id,
        "kind": "command_approval",
        "thread_id": thread_id,
        "run_id": "run-3",
        "turn_id": "turn-7",
        "item_id": "item-command-4",
        "event_id": 41,
        "status": "pending",
        "expires_at": "2026-07-13T12:05:00Z",
        "display": {
            "title": "Run the focused tests",
            "summary": "Codex wants to run a command inside this workspace.",
            "command": "python -m pytest -q bridge_service/tests/test_runner.py",
            "workspace_paths": [],
        },
        "allowed_actions": ["accept", "decline", "cancel"],
        # Broker-private correlation and execution data must never cross HTTP.
        "provider_method": "item/commandExecution/requestApproval",
        "provider_request_id": "provider-request-secret-1",
        "raw_command": (
            "python -m pytest --token reusable-secret "
            "C:\\Users\\Private\\outside-workspace"
        ),
        "raw_path": "C:\\Users\\Private\\outside-workspace",
    }


def _file_approval() -> dict[str, Any]:
    return {
        "interaction_id": "interaction-file-1",
        "kind": "file_change_approval",
        "thread_id": "thread-alpha",
        "run_id": "run-3",
        "turn_id": "turn-7",
        "item_id": "item-file-2",
        "event_id": 42,
        "status": "pending",
        "expires_at": "2026-07-13T12:05:00Z",
        "display": {
            "title": "Change two workspace files",
            "summary": "Codex wants to edit files inside this workspace.",
            "command": None,
            "workspace_paths": ["src/app.py", "tests/test_app.py"],
        },
        "allowed_actions": ["accept", "decline", "cancel"],
        "provider_method": "item/fileChange/requestApproval",
        "provider_request_id": "provider-request-secret-2",
        "raw_file_changes": {"/data/codex-home/auth.json": "must never be rendered"},
    }


def _user_input() -> dict[str, Any]:
    return {
        "interaction_id": "interaction-question-1",
        "kind": "user_input",
        "thread_id": "thread-alpha",
        "run_id": "run-3",
        "turn_id": "turn-7",
        "item_id": "item-question-3",
        "event_id": 43,
        "status": "pending",
        "expires_at": "2026-07-13T12:05:00Z",
        "display": {
            "title": "Choose the change scope",
            "summary": "Codex needs an answer before it can continue.",
            "questions": [
                {
                    "question_id": "scope",
                    "header": "Scope",
                    "prompt": "Which files should Codex update?",
                    "options": [
                        {
                            "label": "Source only",
                            "description": "Update source files and leave docs unchanged.",
                        },
                        {
                            "label": "Source and docs",
                            "description": "Keep the documentation aligned too.",
                        },
                    ],
                    "multiple": False,
                    "allow_free_text": True,
                }
            ],
        },
        "allowed_actions": ["answer", "cancel"],
        "provider_method": "item/tool/requestUserInput",
        "provider_request_id": "provider-request-secret-3",
        "raw_provider_params": {"private_prompt": "private@example.test"},
    }


class RuntimeBrokerDouble:
    def __init__(self, interactions: list[dict[str, Any]] | None = None) -> None:
        self.interactions = {
            item["interaction_id"]: deepcopy(item) for item in (interactions or [])
        }
        self.failures: dict[tuple[str, str], HTTPException] = {}
        self.processed: list[tuple[str, str, str]] = []
        self._idempotent_results: dict[tuple[str, str, str], dict[str, Any]] = {}

    def list_pending_interactions(
        self,
        *,
        thread_id: str | None = None,
    ) -> list[dict[str, Any]]:
        return [
            deepcopy(item)
            for item in self.interactions.values()
            if item["status"] == "pending"
            and (thread_id is None or item["thread_id"] == thread_id)
        ]

    def decide_approval(
        self,
        interaction_id: str,
        *,
        thread_id: str,
        decision: str,
        client_request_id: str,
    ) -> dict[str, Any]:
        failure = self.failures.get(("decision", interaction_id))
        if failure is not None:
            raise failure
        item = self._require_item(interaction_id, thread_id=thread_id)
        if item["kind"] not in {"command_approval", "file_change_approval"}:
            raise _problem(409, "interaction_kind_mismatch")
        key = ("decision", interaction_id, client_request_id)
        previous = self._idempotent_results.get(key)
        if previous is not None:
            return deepcopy(previous)
        if item["status"] != "pending":
            raise _problem(409, "interaction_already_resolved")
        item["status"] = {
            "accept": "accepted",
            "decline": "declined",
            "cancel": "cancelled",
        }[decision]
        result = {
            "interaction_id": interaction_id,
            "thread_id": thread_id,
            "status": item["status"],
            "client_request_id": client_request_id,
        }
        self.processed.append(("decision", interaction_id, client_request_id))
        self._idempotent_results[key] = result
        return deepcopy(result)

    def answer_user_input(
        self,
        interaction_id: str,
        *,
        thread_id: str,
        answers: list[Mapping[str, object]],
        client_request_id: str,
    ) -> dict[str, Any]:
        failure = self.failures.get(("answer", interaction_id))
        if failure is not None:
            raise failure
        item = self._require_item(interaction_id, thread_id=thread_id)
        if item["kind"] != "user_input":
            raise _problem(409, "interaction_kind_mismatch")
        key = ("answer", interaction_id, client_request_id)
        previous = self._idempotent_results.get(key)
        if previous is not None:
            return deepcopy(previous)
        if item["status"] != "pending":
            raise _problem(409, "interaction_already_resolved")
        assert answers == [{"question_id": "scope", "values": ["Source and docs"]}]
        item["status"] = "answered"
        result = {
            "interaction_id": interaction_id,
            "thread_id": thread_id,
            "status": "answered",
            "client_request_id": client_request_id,
        }
        self.processed.append(("answer", interaction_id, client_request_id))
        self._idempotent_results[key] = result
        return deepcopy(result)

    def _require_item(
        self,
        interaction_id: str,
        *,
        thread_id: str,
    ) -> dict[str, Any]:
        item = self.interactions.get(interaction_id)
        if item is None:
            raise _problem(404, "interaction_not_found")
        if item["thread_id"] != thread_id:
            raise _problem(409, "interaction_thread_mismatch")
        return item


def _ha_app(tmp_path: Path, broker: RuntimeBrokerDouble):
    workspace_root = tmp_path / "workspaces"
    workspace_root.mkdir()
    app_server = _NoopLifecycle()
    coordinator = _NoopAuthCoordinator()
    return create_app(
        root_path=tmp_path / "state",
        auth_token="secret",
        runtime_profile=RuntimeProfile.HOME_ASSISTANT,
        workspace_root=workspace_root,
        app_server_factory=lambda: app_server,
        auth_coordinator_factory=lambda _client: coordinator,
        runner_factory=lambda _storage: broker,
    )


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("get", INTERACTIONS_PATH, None),
        (
            "post",
            "/interactions/interaction-command-1/decision",
            {
                "thread_id": "thread-alpha",
                "run_id": "run-3",
                "turn_id": "turn-7",
                "item_id": "item-command-4",
                "decision": "accept",
                "client_request_id": "request-decision-1",
            },
        ),
        (
            "post",
            "/interactions/interaction-question-1/answer",
            {
                "thread_id": "thread-alpha",
                "run_id": "run-3",
                "turn_id": "turn-7",
                "item_id": "item-question-3",
                "answers": [
                    {
                        "question_id": "scope",
                        "values": ["Source and docs"],
                    }
                ],
                "client_request_id": "request-answer-1",
            },
        ),
    ],
)
@pytest.mark.parametrize("headers", [None, {"Authorization": "Bearer wrong"}])
def test_interaction_routes_require_the_admin_bridge_token(
    tmp_path: Path,
    method: str,
    path: str,
    body: dict[str, Any] | None,
    headers: dict[str, str] | None,
) -> None:
    app = _ha_app(tmp_path, RuntimeBrokerDouble())

    with TestClient(app) as client:
        response = client.request(method, path, headers=headers, json=body)

    assert response.status_code == 401


def test_pending_interactions_are_thread_scoped_provider_neutral_and_safe(
    tmp_path: Path,
) -> None:
    broker = RuntimeBrokerDouble(
        [
            _command_approval(),
            _file_approval(),
            _user_input(),
            _command_approval("interaction-other-thread", thread_id="thread-beta"),
        ]
    )
    app = _ha_app(tmp_path, broker)

    with TestClient(app) as client:
        response = client.get(
            f"{INTERACTIONS_PATH}?thread_id=thread-alpha",
            headers=AUTHORIZATION,
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["items"] == [
        {
            key: value
            for key, value in item.items()
            if key
            in {
                "interaction_id",
                "kind",
                "thread_id",
                "event_id",
                "status",
                "expires_at",
                "display",
                "allowed_actions",
            }
        }
        for item in [_command_approval(), _file_approval(), _user_input()]
    ]
    assert payload["count"] == 3
    assert payload["thread_id"] == "thread-alpha"
    serialized = response.text
    for private_value in (
        "provider-request-secret",
        "requestApproval",
        "requestUserInput",
        "reusable-secret",
        "private@example.test",
        "C:\\Users\\Private",
        "/data/codex-home/auth.json",
        "must never be rendered",
    ):
        assert private_value not in serialized
    for item in payload["items"]:
        display = item["display"]
        assert len(display["title"]) <= 160
        assert len(display["summary"]) <= 512
        if display.get("command") is not None:
            assert len(display["command"]) <= 512
        for relative_path in display.get("workspace_paths", []):
            assert not Path(relative_path).is_absolute()
            assert len(relative_path) <= 240


def test_malformed_broker_projection_is_redacted_at_http_boundary(
    tmp_path: Path,
) -> None:
    interaction = _command_approval()
    interaction["display"]["workspace_paths"] = [
        "C:/reusable-secret/private@example.test"
    ]
    app = _ha_app(tmp_path, RuntimeBrokerDouble([interaction]))

    with TestClient(app) as client:
        response = client.get(INTERACTIONS_PATH, headers=AUTHORIZATION)

    assert response.status_code == 500
    assert response.json()["detail"] == {
        "code": "runtime_projection_invalid",
        "retryable": False,
    }
    assert "reusable-secret" not in response.text
    assert "private@example.test" not in response.text


def test_pending_interaction_projection_never_exposes_provider_correlation(
    tmp_path: Path,
) -> None:
    """The browser can act on a local interaction id, never Codex turn/item ids."""
    app = _ha_app(tmp_path, RuntimeBrokerDouble([_command_approval()]))

    with TestClient(app) as client:
        response = client.get(INTERACTIONS_PATH, headers=AUTHORIZATION)

    assert response.status_code == 200
    interaction = response.json()["items"][0]
    assert interaction["interaction_id"] == "interaction-command-1"
    assert interaction["thread_id"] == "thread-alpha"
    assert "run_id" not in interaction
    assert "turn_id" not in interaction
    assert "item_id" not in interaction
    assert "turn-7" not in response.text
    assert "item-command-4" not in response.text


@pytest.mark.parametrize(
    ("interaction", "decision", "expected_status"),
    [
        (_command_approval(), "accept", "accepted"),
        (_file_approval(), "decline", "declined"),
    ],
)
def test_command_and_file_approvals_use_one_provider_neutral_decision_contract(
    tmp_path: Path,
    interaction: dict[str, Any],
    decision: str,
    expected_status: str,
) -> None:
    broker = RuntimeBrokerDouble([interaction])
    app = _ha_app(tmp_path, broker)
    interaction_id = interaction["interaction_id"]
    body = {
        "thread_id": "thread-alpha",
        "run_id": interaction["run_id"],
        "turn_id": interaction["turn_id"],
        "item_id": interaction["item_id"],
        "decision": decision,
        "client_request_id": f"request-{decision}-1",
    }

    with TestClient(app) as client:
        response = client.post(
            f"/interactions/{interaction_id}/decision",
            headers=AUTHORIZATION,
            json=body,
        )

    assert response.status_code == 200
    assert response.json() == {
        "interaction_id": interaction_id,
        "thread_id": "thread-alpha",
        "status": expected_status,
        "client_request_id": f"request-{decision}-1",
    }


def test_user_input_answers_are_structured_and_provider_neutral(tmp_path: Path) -> None:
    broker = RuntimeBrokerDouble([_user_input()])
    app = _ha_app(tmp_path, broker)
    body = {
        "thread_id": "thread-alpha",
        "run_id": "run-3",
        "turn_id": "turn-7",
        "item_id": "item-question-3",
        "answers": [{"question_id": "scope", "values": ["Source and docs"]}],
        "client_request_id": "request-answer-1",
    }

    with TestClient(app) as client:
        response = client.post(
            "/interactions/interaction-question-1/answer",
            headers=AUTHORIZATION,
            json=body,
        )

    assert response.status_code == 200
    assert response.json() == {
        "interaction_id": "interaction-question-1",
        "thread_id": "thread-alpha",
        "status": "answered",
        "client_request_id": "request-answer-1",
    }


@pytest.mark.parametrize(
    ("operation", "path", "interaction", "body"),
    [
        (
            "decision",
            "/interactions/interaction-command-1/decision",
            _command_approval(),
            {"decision": "accept"},
        ),
        (
            "answer",
            "/interactions/interaction-question-1/answer",
            _user_input(),
            {
                "answers": [
                    {
                        "question_id": "scope",
                        "values": ["Source and docs"],
                    }
                ]
            },
        ),
    ],
)
def test_client_request_id_makes_decisions_and_answers_idempotent(
    tmp_path: Path,
    operation: str,
    path: str,
    interaction: dict[str, Any],
    body: dict[str, Any],
) -> None:
    broker = RuntimeBrokerDouble([interaction])
    app = _ha_app(tmp_path, broker)
    request_body = {
        "thread_id": "thread-alpha",
        "run_id": interaction["run_id"],
        "turn_id": interaction["turn_id"],
        "item_id": interaction["item_id"],
        "client_request_id": "stable-client-request-1",
        **body,
    }

    with TestClient(app) as client:
        first = client.post(path, headers=AUTHORIZATION, json=request_body)
        replay = client.post(path, headers=AUTHORIZATION, json=request_body)

    assert first.status_code == replay.status_code == 200
    assert first.json() == replay.json()
    assert broker.processed == [
        (operation, interaction["interaction_id"], "stable-client-request-1")
    ]


def test_a_new_client_request_id_cannot_redecide_a_resolved_interaction(
    tmp_path: Path,
) -> None:
    broker = RuntimeBrokerDouble([_command_approval()])
    app = _ha_app(tmp_path, broker)
    path = "/interactions/interaction-command-1/decision"

    with TestClient(app) as client:
        first = client.post(
            path,
            headers=AUTHORIZATION,
            json={
                "thread_id": "thread-alpha",
                "run_id": "run-3",
                "turn_id": "turn-7",
                "item_id": "item-command-4",
                "decision": "accept",
                "client_request_id": "request-first",
            },
        )
        duplicate = client.post(
            path,
            headers=AUTHORIZATION,
            json={
                "thread_id": "thread-alpha",
                "run_id": "run-3",
                "turn_id": "turn-7",
                "item_id": "item-command-4",
                "decision": "decline",
                "client_request_id": "request-second",
            },
        )

    assert first.status_code == 200
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == {
        "code": "interaction_already_resolved",
        "retryable": False,
    }


@pytest.mark.parametrize(
    ("interaction_id", "thread_id", "status_code", "code", "reason"),
    [
        ("interaction-unknown", "thread-alpha", 404, "interaction_not_found", None),
        (
            "interaction-command-1",
            "thread-beta",
            409,
            "interaction_thread_mismatch",
            None,
        ),
        (
            "interaction-stale",
            "thread-alpha",
            410,
            "interaction_stale",
            "superseded",
        ),
        (
            "interaction-expired",
            "thread-alpha",
            410,
            "interaction_stale",
            "expired",
        ),
    ],
)
def test_unknown_cross_thread_stale_and_expired_decisions_are_typed(
    tmp_path: Path,
    interaction_id: str,
    thread_id: str,
    status_code: int,
    code: str,
    reason: str | None,
) -> None:
    raw_secret = "provider token reusable-secret private@example.test"
    broker = RuntimeBrokerDouble(
        [
            _command_approval(),
            _command_approval("interaction-stale"),
            _command_approval("interaction-expired"),
        ]
    )
    if reason is not None:
        broker.failures[("decision", interaction_id)] = _problem(
            status_code,
            code,
            reason=reason,
            secret_cause=raw_secret,
        )
    app = _ha_app(tmp_path, broker)

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            f"/interactions/{interaction_id}/decision",
            headers=AUTHORIZATION,
            json={
                "thread_id": thread_id,
                "run_id": "run-3",
                "turn_id": "turn-7",
                "item_id": "item-command-4",
                "decision": "accept",
                "client_request_id": "request-rejected-1",
            },
        )

    assert response.status_code == status_code
    expected_detail: dict[str, object] = {"code": code, "retryable": False}
    if reason is not None:
        expected_detail["reason"] = reason
    assert response.json()["detail"] == expected_detail
    assert raw_secret not in response.text
    assert "reusable-secret" not in response.text
    assert "private@example.test" not in response.text


@pytest.mark.parametrize(
    "reason",
    ["forbidden_network", "private_host", "outside_workspace"],
)
def test_policy_denied_escalations_are_not_pending_or_manually_acceptable(
    tmp_path: Path,
    reason: str,
) -> None:
    interaction_id = f"interaction-policy-{reason}"
    interaction = _command_approval(interaction_id)
    interaction["status"] = "policy_denied"
    interaction["raw_command"] = (
        "curl http://homeassistant.local --header 'Authorization: Bearer secret'"
    )
    interaction["raw_path"] = "/data/codex-home/auth.json"
    broker = RuntimeBrokerDouble([interaction])
    broker.failures[("decision", interaction_id)] = _problem(
        410,
        "interaction_policy_denied",
        reason=reason,
    )
    app = _ha_app(tmp_path, broker)

    with TestClient(app) as client:
        pending = client.get(
            f"{INTERACTIONS_PATH}?thread_id=thread-alpha",
            headers=AUTHORIZATION,
        )
        decision = client.post(
            f"/interactions/{interaction_id}/decision",
            headers=AUTHORIZATION,
            json={
                "thread_id": "thread-alpha",
                "run_id": "run-3",
                "turn_id": "turn-7",
                "item_id": "item-command-4",
                "decision": "accept",
                "client_request_id": "request-must-not-accept",
            },
        )

    assert pending.status_code == 200
    assert pending.json()["items"] == []
    assert decision.status_code == 410
    assert decision.json()["detail"] == {
        "code": "interaction_policy_denied",
        "retryable": False,
        "reason": reason,
    }
    assert "homeassistant.local" not in pending.text
    assert "/data/codex-home" not in pending.text


@pytest.mark.parametrize("reason", ["run_cancelled", "bridge_shutdown"])
def test_cancel_or_shutdown_invalidates_pending_interactions(
    tmp_path: Path,
    reason: str,
) -> None:
    raw_secret = "raw provider request reusable-secret"
    broker = RuntimeBrokerDouble([_user_input()])
    broker.failures[("answer", "interaction-question-1")] = _problem(
        410,
        "interaction_invalidated",
        reason=reason,
        secret_cause=raw_secret,
    )
    app = _ha_app(tmp_path, broker)

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            "/interactions/interaction-question-1/answer",
            headers=AUTHORIZATION,
            json={
                "thread_id": "thread-alpha",
                "run_id": "run-3",
                "turn_id": "turn-7",
                "item_id": "item-question-3",
                "answers": [
                    {
                        "question_id": "scope",
                        "values": ["Source and docs"],
                    }
                ],
                "client_request_id": "request-too-late",
            },
        )

    assert response.status_code == 410
    assert response.json()["detail"] == {
        "code": "interaction_invalidated",
        "retryable": False,
        "reason": reason,
    }
    assert raw_secret not in response.text
    assert "reusable-secret" not in response.text


@pytest.mark.parametrize(
    ("path", "body"),
    [
        (
            "/interactions/interaction-command-1/decision",
            {
                "thread_id": "thread-alpha",
                "run_id": "run-3",
                "turn_id": "turn-7",
                "item_id": "item-command-4",
                "decision": "always",
                "client_request_id": "request-invalid-decision",
            },
        ),
        (
            "/interactions/interaction-question-1/answer",
            {
                "thread_id": "thread-alpha",
                "run_id": "run-3",
                "turn_id": "turn-7",
                "item_id": "item-question-3",
                "answers": [],
                "client_request_id": "request-empty-answer",
            },
        ),
        (
            "/interactions/interaction-question-1/answer",
            {
                "thread_id": "thread-alpha",
                "run_id": "run-3",
                "turn_id": "turn-7",
                "item_id": "item-question-3",
                "answers": [{"question_id": "scope", "values": ["x" * 4097]}],
                "client_request_id": "request-overlong-answer",
            },
        ),
        (
            "/interactions/interaction-command-1/decision",
            {
                "thread_id": "thread-alpha",
                "run_id": "run-3",
                "turn_id": "turn-7",
                "item_id": "item-command-4",
                "decision": "accept",
                "client_request_id": "x" * 257,
            },
        ),
    ],
)
def test_interaction_mutations_reject_unbounded_or_invalid_payloads_before_broker(
    tmp_path: Path,
    path: str,
    body: dict[str, Any],
) -> None:
    broker = RuntimeBrokerDouble([_command_approval(), _user_input()])
    app = _ha_app(tmp_path, broker)

    with TestClient(app) as client:
        response = client.post(path, headers=AUTHORIZATION, json=body)

    assert response.status_code == 422
    assert broker.processed == []
