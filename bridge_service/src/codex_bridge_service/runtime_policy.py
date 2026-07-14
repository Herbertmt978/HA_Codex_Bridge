from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import re
from typing import Any, Literal

from .models import (
    InteractionDisplayRecord,
    InteractionOptionRecord,
    InteractionQuestionRecord,
    RunMode,
)
from .workspace import WorkspaceInputError, normalize_portable_relative_path


class RuntimeProtocolMismatchError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("The Codex runtime returned an unexpected configuration.")


@dataclass(frozen=True, slots=True)
class RuntimeModePolicy:
    thread_sandbox: Literal["read-only", "workspace-write"]
    approval_policy: Literal["on-request", "never"]
    sandbox_policy: dict[str, object]


def mode_policy(mode: RunMode, workspace: Path) -> RuntimeModePolicy:
    if mode is RunMode.OBSERVE:
        return RuntimeModePolicy(
            thread_sandbox="read-only",
            approval_policy="on-request",
            sandbox_policy={"type": "readOnly", "networkAccess": False},
        )
    writable = {
        "type": "workspaceWrite",
        "writableRoots": [str(workspace)],
        "networkAccess": False,
        "excludeSlashTmp": True,
        "excludeTmpdirEnvVar": True,
    }
    return RuntimeModePolicy(
        thread_sandbox="workspace-write",
        approval_policy=("never" if mode is RunMode.FULL_AUTO else "on-request"),
        sandbox_policy=writable,
    )


def validate_thread_result(
    result: object,
    *,
    expected_cwd: Path,
    expected_model: str,
    policy: RuntimeModePolicy,
) -> str:
    if not isinstance(result, dict):
        raise RuntimeProtocolMismatchError()
    thread = result.get("thread")
    thread_id = thread.get("id") if isinstance(thread, dict) else None
    if not _bounded_id(thread_id, 256):
        raise RuntimeProtocolMismatchError()
    assert isinstance(thread_id, str)
    if (
        result.get("cwd") != str(expected_cwd)
        or result.get("model") != expected_model
        or result.get("modelProvider") != "openai"
        or result.get("approvalPolicy") != policy.approval_policy
        or result.get("approvalsReviewer") != "user"
        or not _sandbox_matches(result.get("sandbox"), policy, expected_cwd)
        or not _thread_environment_matches(thread, expected_cwd)
        or not _instruction_sources_match(
            result.get("instructionSources", []), expected_cwd
        )
    ):
        raise RuntimeProtocolMismatchError()
    return thread_id


def validate_turn_result(result: object) -> tuple[str, str, dict[str, Any]]:
    if not isinstance(result, dict):
        raise RuntimeProtocolMismatchError()
    turn = result.get("turn")
    turn_id = turn.get("id") if isinstance(turn, dict) else None
    status = turn.get("status") if isinstance(turn, dict) else None
    if not _bounded_id(turn_id, 256) or status not in {
        "inProgress",
        "completed",
        "interrupted",
        "failed",
    }:
        raise RuntimeProtocolMismatchError()
    assert isinstance(turn_id, str)
    assert isinstance(status, str)
    assert isinstance(turn, dict)
    return turn_id, status, turn


def validate_steer_result(result: object, expected_turn_id: str) -> None:
    if not isinstance(result, dict) or result.get("turnId") != expected_turn_id:
        raise RuntimeProtocolMismatchError()


def interaction_correlation(
    params: object,
) -> tuple[str, str, str] | None:
    if not isinstance(params, dict):
        return None
    values = (params.get("threadId"), params.get("turnId"), params.get("itemId"))
    if not all(_bounded_id(value, 256) for value in values):
        return None
    return values  # type: ignore[return-value]


def approval_display(
    method: str,
    params: dict[str, Any],
    *,
    expected_cwd: Path,
) -> (
    tuple[
        Literal["command_approval", "file_change_approval"],
        InteractionDisplayRecord,
    ]
    | None
):
    if method == "item/commandExecution/requestApproval":
        if _command_must_be_denied(params, expected_cwd=expected_cwd):
            return None
        command = _safe_command(params.get("command"))
        if command is None:
            return None
        reason = _safe_text(params.get("reason"), 320)
        return (
            "command_approval",
            InteractionDisplayRecord(
                title="Command approval",
                summary=reason or "Codex wants to run a command in this workspace.",
                command=command,
            ),
        )
    if method == "item/fileChange/requestApproval":
        if params.get("grantRoot") is not None or _contains_absolute_path_text(
            params.get("reason")
        ):
            return None
        reason = _safe_text(params.get("reason"), 320)
        return (
            "file_change_approval",
            InteractionDisplayRecord(
                title="File change approval",
                summary=reason or "Codex wants to change files in this workspace.",
            ),
        )
    return None


def approval_workspace_paths(
    params: dict[str, Any],
    *,
    workspace: Path,
) -> list[str]:
    actions = params.get("commandActions")
    if not isinstance(actions, list):
        return []
    paths: list[str] = []
    for action in actions:
        if not isinstance(action, dict) or action.get("path") is None:
            continue
        normalized = normalize_workspace_path(action.get("path"), workspace)
        if normalized is not None and normalized not in paths:
            paths.append(normalized)
    return paths[:128]


def question_display(params: dict[str, Any]) -> InteractionDisplayRecord | None:
    raw_questions = params.get("questions")
    if not isinstance(raw_questions, list) or not 1 <= len(raw_questions) <= 3:
        return None
    questions: list[InteractionQuestionRecord] = []
    seen: set[str] = set()
    for raw in raw_questions:
        if not isinstance(raw, dict) or raw.get("isSecret") is True:
            return None
        if any(
            _contains_absolute_path_text(raw.get(field))
            for field in ("header", "question")
        ):
            return None
        question_id = _safe_text(raw.get("id"), 128)
        header = _safe_text(raw.get("header"), 160)
        prompt = _safe_text(raw.get("question"), 2048)
        if not question_id or not header or not prompt or question_id in seen:
            return None
        seen.add(question_id)
        raw_options = raw.get("options")
        if raw_options is None:
            raw_options = []
        if not isinstance(raw_options, list) or len(raw_options) > 3:
            return None
        options: list[InteractionOptionRecord] = []
        for raw_option in raw_options:
            if not isinstance(raw_option, dict):
                return None
            if any(
                _contains_absolute_path_text(raw_option.get(field))
                for field in ("label", "description")
            ):
                return None
            label = _safe_text(raw_option.get("label"), 160)
            description = _safe_text(raw_option.get("description"), 512)
            if not label or not description:
                return None
            options.append(
                InteractionOptionRecord(label=label, description=description)
            )
        questions.append(
            InteractionQuestionRecord(
                question_id=question_id,
                header=header,
                prompt=prompt,
                options=options,
                multiple=False,
                allow_free_text=bool(raw.get("isOther")) or not options,
            )
        )
    return InteractionDisplayRecord(
        title="Codex has a question",
        summary="Answer to continue this Codex turn.",
        questions=questions,
    )


def normalize_workspace_path(value: object, workspace: Path) -> str | None:
    if not isinstance(value, str) or not value or len(value) > 4096:
        return None
    try:
        candidate = Path(value)
        if not candidate.is_absolute():
            candidate = workspace / candidate
        resolved = candidate.resolve(strict=False)
        root = workspace.resolve(strict=False)
        relative = resolved.relative_to(root)
    except (OSError, ValueError):
        return None
    portable = PurePosixPath(*relative.parts).as_posix()
    if not portable or portable == ".":
        return None
    try:
        normalized = normalize_portable_relative_path(portable)
    except WorkspaceInputError:
        return None
    return normalized if len(normalized) <= 240 else None


def bounded_text(value: object, limit: int) -> str | None:
    return _safe_text(value, limit)


def bounded_raw_text(value: object, limit_bytes: int) -> str | None:
    if not isinstance(value, str):
        return None
    encoded = value.encode("utf-8")
    if len(encoded) <= limit_bytes:
        return value
    return encoded[:limit_bytes].decode("utf-8", errors="ignore")


def _sandbox_matches(value: object, policy: RuntimeModePolicy, expected_cwd: Path) -> bool:
    if not isinstance(value, dict):
        return False
    sandbox_type = value.get("type")
    if sandbox_type == "readOnly":
        return set(value) == {"type", "networkAccess"} and (
            value.get("networkAccess") is False
        )
    if sandbox_type != "workspaceWrite" or set(value) != {
        "type",
        "networkAccess",
        "writableRoots",
        "excludeSlashTmp",
        "excludeTmpdirEnvVar",
    }:
        return False
    if value.get("networkAccess") is not False:
        return False
    if value.get("writableRoots") != [str(expected_cwd)]:
        return False
    return all(
        value.get(name) is True for name in ("excludeSlashTmp", "excludeTmpdirEnvVar")
    )


def _command_must_be_denied(params: dict[str, Any], *, expected_cwd: Path) -> bool:
    if params.get("networkApprovalContext") is not None:
        return True
    if params.get("proposedExecpolicyAmendment"):
        return True
    if params.get("proposedNetworkPolicyAmendments"):
        return True
    cwd = params.get("cwd")
    if cwd is not None and cwd != str(expected_cwd):
        return True
    if _contains_absolute_path_text(
        params.get("command")
    ) or _contains_absolute_path_text(params.get("reason")):
        return True
    actions = params.get("commandActions")
    if actions is None:
        return True
    if not isinstance(actions, list) or not actions:
        return True
    for action in actions:
        if not isinstance(action, dict):
            return True
        if action.get("type") not in {"read", "listFiles", "search"}:
            return True
        path = action.get("path")
        if path is None:
            continue
        if not _path_is_safe_and_contained(path, expected_cwd):
            return True
    return False


def _contains_absolute_path_text(value: object) -> bool:
    if not isinstance(value, str):
        return False
    if re.search(r"[A-Za-z]:[\\/]", value):
        return True
    if re.search(r"\\\\[^\\/\s]+[\\/][^\\/\s]+", value):
        return True
    for index, character in enumerate(value):
        if character not in {"/", "~"}:
            continue
        if index + 1 >= len(value):
            continue
        following = value[index + 1]
        if character == "/" and (following in {"/", "\\"} or following.isspace()):
            continue
        if character == "~" and following not in {"/", "\\"}:
            continue
        if index == 0:
            return True
        previous = value[index - 1]
        if not (previous.isalnum() or previous in "._-/\\"):
            return True
    return False


def _thread_environment_matches(value: object, expected_cwd: Path) -> bool:
    if not isinstance(value, dict):
        return False
    if (
        value.get("cwd") != str(expected_cwd)
        or value.get("modelProvider") != "openai"
        or value.get("ephemeral") is not False
    ):
        return False
    status = value.get("status")
    if not isinstance(status, dict) or status.get("type") != "idle":
        return False
    return True


def _instruction_sources_match(sources: object, expected_cwd: Path) -> bool:
    if sources is None:
        sources = []
    if not isinstance(sources, list) or len(sources) > 128:
        return False
    root = expected_cwd.resolve(strict=False)
    for source in sources:
        if not isinstance(source, str):
            return False
        candidate = Path(source)
        if not candidate.is_absolute():
            return False
        try:
            candidate.resolve(strict=False).relative_to(root)
        except (OSError, ValueError):
            return False
    return True


def _contains_escaping_absolute_path(value: object, workspace: Path) -> bool:
    if isinstance(value, dict):
        return any(
            _contains_escaping_absolute_path(item, workspace) for item in value.values()
        )
    if isinstance(value, list):
        return any(_contains_escaping_absolute_path(item, workspace) for item in value)
    if not isinstance(value, str) or not Path(value).is_absolute():
        return False
    try:
        Path(value).resolve(strict=False).relative_to(workspace.resolve(strict=False))
    except (OSError, ValueError):
        return True
    return False


def _path_is_safe_and_contained(value: object, workspace: Path) -> bool:
    if not isinstance(value, str) or not value:
        return False
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = workspace / candidate
    try:
        relative = candidate.resolve(strict=False).relative_to(
            workspace.resolve(strict=False)
        )
    except (OSError, ValueError):
        return False
    if not relative.parts:
        return True
    return normalize_workspace_path(candidate, workspace) is not None


def _bounded_id(value: object, limit: int) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and value == value.strip()
        and len(value.encode("utf-8")) <= limit
    )


def _safe_text(value: object, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    compact = " ".join(value.split())
    if not compact:
        return None
    return compact[:limit]


def _safe_command(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip() or len(value) > 512:
        return None
    return value
