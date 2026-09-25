from __future__ import annotations

import asyncio
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
import httpx
from fastapi import HTTPException
from fastapi.testclient import TestClient

from codex_bridge_service import discord_channel
from codex_bridge_service.api_contract import API_CURRENT
from codex_bridge_service.app import create_app
from codex_bridge_service.discord_channel import (
    DiscordChannelError,
    DiscordChannelManager,
    DiscordState,
    permitted,
    safe_answer,
)
from codex_bridge_service.models import (
    DEFAULT_MODEL,
    DEFAULT_THINKING_LEVEL,
    ProjectKind,
    RunMode,
    RuntimeProfile,
)
from codex_bridge_service.storage import BridgeStorage
from codex_bridge_service.routes.task_actions import (
    ContinueTaskRequest,
    StartTaskRequest,
)

USER = "123456789012345678"
OTHER = "123456789012345679"
GUILD = "223456789012345678"
CHANNEL = "323456789012345678"
WRONG_CHANNEL = "323456789012345679"
INTERACTION = "423456789012345678"
TOKEN = "test_bot_credential_" + "x" * 40


def policy() -> dict[str, object]:
    return {
        "enabled": True,
        "dm_user_ids": [USER],
        "guilds": [{"guild_id": GUILD, "channel_ids": [CHANNEL], "user_ids": [USER]}],
    }


def model_probe() -> SimpleNamespace:
    return SimpleNamespace(
        probe=lambda: SimpleNamespace(
            default_model=DEFAULT_MODEL,
            default_thinking_level=DEFAULT_THINKING_LEVEL,
            stale=False,
        )
    )


def test_discord_admin_api_authenticates_and_never_returns_credential(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspaces"
    workspace.mkdir()
    (tmp_path / "codex-home").mkdir()
    app = create_app(
        root_path=tmp_path / "state",
        auth_token="a" * 40,
        runtime_profile=RuntimeProfile.HOME_ASSISTANT,
        workspace_root=workspace,
        codex_home=tmp_path / "codex-home",
    )
    client = TestClient(app)
    headers = {
        "Authorization": "Bearer " + app.state.auth_token,
        "X-Codex-Bridge-Api": str(API_CURRENT),
    }
    assert client.get("/discord/config").status_code == 401
    response = client.put(
        "/discord/config", json={**policy(), "bot_token": TOKEN}, headers=headers
    )
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["credential_present"] is True
    assert TOKEN not in response.text
    assert TOKEN not in client.get("/discord/config", headers=headers).text
    malformed_secret = "sensitive!invalid!credential"
    invalid = client.put(
        "/discord/config",
        json={**policy(), "bot_token": malformed_secret},
        headers=headers,
    )
    assert invalid.status_code == 422
    assert malformed_secret not in invalid.text
    revoked = client.post("/discord/revoke", headers=headers)
    assert revoked.status_code == 200
    assert revoked.json()["credential_present"] is False
    app.state.discord_channel.state.close()


def test_discord_policy_is_closed_by_default_and_exact() -> None:
    closed = {"enabled": False, "dm_user_ids": [], "guilds": []}
    assert not permitted(
        closed, user_id=USER, guild_id=None, channel_id=CHANNEL, is_bot=False
    )
    assert permitted(
        policy(), user_id=USER, guild_id=None, channel_id=CHANNEL, is_bot=False
    )
    assert permitted(
        policy(), user_id=USER, guild_id=GUILD, channel_id=CHANNEL, is_bot=False
    )
    assert not permitted(
        policy(), user_id=OTHER, guild_id=GUILD, channel_id=CHANNEL, is_bot=False
    )
    assert not permitted(
        policy(), user_id=USER, guild_id=GUILD, channel_id=WRONG_CHANNEL, is_bot=False
    )
    assert not permitted(
        policy(), user_id=USER, guild_id=GUILD, channel_id=CHANNEL, is_bot=True
    )


def test_dm_identity_requires_a_one_to_one_bot_dm(tmp_path: Path) -> None:
    manager = DiscordChannelManager(SimpleNamespace(), tmp_path)
    manager.state.configure(policy(), TOKEN)
    user = SimpleNamespace(id=USER, bot=False)
    direct = SimpleNamespace(
        user=user, guild_id=None, channel_id=CHANNEL, channel=SimpleNamespace(type=1)
    )
    group = SimpleNamespace(
        user=user, guild_id=None, channel_id=CHANNEL, channel=SimpleNamespace(type=3)
    )
    assert manager._identity(direct) == (USER, None, CHANNEL)
    assert manager._identity(group) is None
    manager.state.close()


def test_invalid_update_does_not_cancel_existing_work(tmp_path: Path) -> None:
    manager = DiscordChannelManager(SimpleNamespace(), tmp_path)
    manager.state.configure(policy(), TOKEN)
    _, row = manager.state.claim(
        INTERACTION, user_id=USER, guild_id=None, channel_id=CHANNEL, prompt="hello"
    )
    called = False

    async def stop() -> None:
        nonlocal called
        called = True

    manager._stop_gateway_locked = stop  # type: ignore[method-assign]
    with pytest.raises(DiscordChannelError):
        asyncio.run(
            manager.configure({"enabled": True, "dm_user_ids": [], "guilds": []}, None)
        )
    assert not called
    assert manager.state.pending()[0]["task_id"] == row["task_id"]
    manager.state.close()


def test_revoke_suppresses_delivery_before_cancellation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = SimpleNamespace(state=SimpleNamespace(auth_token="a" * 40))
    manager = DiscordChannelManager(app, tmp_path)
    manager.state.configure(policy(), TOKEN)
    manager.state.claim(
        INTERACTION, user_id=USER, guild_id=None, channel_id=CHANNEL, prompt="hello"
    )
    manager.state.record_run(INTERACTION, thread_id="thr_private", run_id="run_private")
    observed: list[bool] = []

    def cancel(*_args: object) -> dict[str, str]:
        observed.append(manager.state.status()["credential_present"])
        assert manager.state.pending() == []
        return {"status": "cancelled"}

    monkeypatch.setattr(discord_channel, "cancel_task", cancel)
    asyncio.run(manager.revoke())
    assert observed == [False]
    manager.state.close()


@pytest.mark.parametrize("failure_point", ["receipt", "admission_response"])
def test_admitted_run_is_recovered_after_local_receipt_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_point: str,
) -> None:
    storage = SimpleNamespace(
        create_project=lambda **_kwargs: SimpleNamespace(project_id="isolated")
    )
    app = SimpleNamespace(
        state=SimpleNamespace(
            auth_token="a" * 40,
            storage=storage,
            model_catalog_probe=model_probe(),
        )
    )
    manager = DiscordChannelManager(app, tmp_path)
    monkeypatch.setattr(manager, "_preflight_new_project", lambda: None)
    manager.state.configure(policy(), TOKEN)
    notices: list[str] = []

    async def acknowledge(**_kwargs: object) -> None:
        pass

    async def followup(message: str, **_kwargs: object) -> None:
        notices.append(message)

    interaction = SimpleNamespace(
        id=INTERACTION,
        user=SimpleNamespace(id=USER, bot=False),
        guild_id=None,
        channel_id=CHANNEL,
        channel=SimpleNamespace(type=1),
        response=SimpleNamespace(defer=acknowledge),
        followup=SimpleNamespace(send=followup),
    )
    run = {"thread_id": "thr_private", "run_id": "run_private", "status": "accepted"}

    def start(*_args: object) -> dict[str, str]:
        if failure_point == "admission_response":
            raise OSError("response lost after admission")
        return run

    monkeypatch.setattr(discord_channel, "start_task", start)
    monkeypatch.setattr(discord_channel, "get_task", lambda *_args: run)
    original_record = manager.state.record_run
    if failure_point == "receipt":
        monkeypatch.setattr(
            manager.state,
            "record_run",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError()),
        )

    asyncio.run(manager._ask(interaction, "hello"))
    assert notices == [
        "Codex could not start this task. Check its status in Home Assistant."
    ]
    assert manager.state.pending()[0]["run_id"] is None

    monkeypatch.setattr(manager.state, "record_run", original_record)
    asyncio.run(manager._recover_claims())
    assert manager.state.pending()[0]["run_id"] == "run_private"
    assert manager.state.dm_thread(USER) == "thr_private"
    manager.state.close()


def test_discord_requests_use_distinct_confined_projects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if os.name == "nt":
        pytest.skip("secure Home Assistant workspace operations require POSIX dir_fd")
    workspace_root = tmp_path / "workspaces"
    storage = BridgeStorage(
        root_path=tmp_path / "state",
        runtime_profile=RuntimeProfile.HOME_ASSISTANT,
        workspace_root=workspace_root,
    )
    direct = storage.ensure_direct_project()
    direct_thread = storage.create_thread(title="HA direct", mode=RunMode.OBSERVE)
    assert direct.root_path == "."
    app = SimpleNamespace(
        state=SimpleNamespace(
            auth_token="a" * 40,
            storage=storage,
            model_catalog_probe=model_probe(),
        )
    )
    discord_root = tmp_path / "discord"
    discord_root.mkdir()
    manager = DiscordChannelManager(app, discord_root)
    allowed = policy()
    allowed["dm_user_ids"] = [USER, OTHER]
    manager.state.configure(allowed, TOKEN)
    monkeypatch.setattr(manager, "_preflight_new_project", lambda: None)
    started: list[tuple[object, object]] = []
    continued: list[str] = []

    def start(payload: StartTaskRequest, *_args: object) -> dict[str, str]:
        project = storage.load_project(payload.project_id)
        thread = storage.create_thread(
            title=payload.title,
            project_id=payload.project_id,
            mode=RunMode.OBSERVE,
        )
        started.append((project, thread))
        return {"thread_id": thread.thread_id, "run_id": "run_" + payload.task_id}

    def continue_run(payload: ContinueTaskRequest, *_args: object) -> dict[str, str]:
        continued.append(payload.thread_id)
        return {"thread_id": payload.thread_id, "run_id": "run_" + payload.task_id}

    monkeypatch.setattr(discord_channel, "start_task", start)
    monkeypatch.setattr(discord_channel, "continue_task", continue_run)

    async def acknowledge(**_kwargs: object) -> None:
        pass

    async def send(_message: str, **_kwargs: object) -> None:
        pass

    def interaction(offset: int, user_id: str, *, shared: bool) -> SimpleNamespace:
        return SimpleNamespace(
            id=str(int(INTERACTION) + offset),
            user=SimpleNamespace(id=user_id, bot=False),
            guild_id=GUILD if shared else None,
            channel_id=CHANNEL,
            channel=SimpleNamespace(type=0 if shared else 1),
            response=SimpleNamespace(defer=acknowledge),
            followup=SimpleNamespace(send=send),
        )

    async def exercise() -> None:
        await manager._ask(interaction(0, USER, shared=False), "first DM")
        await manager._ask(interaction(1, USER, shared=False), "continued DM")
        await manager._ask(interaction(2, OTHER, shared=False), "other DM")
        await manager._ask(interaction(3, USER, shared=True), "first shared")
        await manager._ask(interaction(4, USER, shared=True), "second shared")

    asyncio.run(exercise())
    assert len(started) == 4
    assert continued == [started[0][1].thread_id]
    assert manager.state.dm_thread(USER) == started[0][1].thread_id
    assert manager.state.dm_thread(OTHER) == started[1][1].thread_id
    direct_workspace = (workspace_root / direct_thread.workspace_path).resolve()
    roots = [(workspace_root / project.root_path).resolve() for project, _ in started]
    assert len(set(roots)) == len(roots)
    # ha_observe grants read access at the run's "." workspace root. No
    # Discord root may contain an HA direct or another Discord chat's file.
    direct_file = direct_workspace / "ha-private.txt"
    direct_file.write_text("private", encoding="utf-8")
    discord_files = []
    for (project, thread), root in zip(started, roots, strict=True):
        assert project.kind is ProjectKind.PROJECT
        assert project.root_path != "."
        assert thread.workspace_path == project.root_path
        assert root.is_dir()
        assert direct_workspace != root
        assert not direct_workspace.is_relative_to(root)
        assert not root.is_relative_to(direct_workspace)
        assert not direct_file.is_relative_to(root)
        marker = root / "private.txt"
        marker.write_text("private", encoding="utf-8")
        discord_files.append(marker)
    for index, root in enumerate(roots):
        assert all(
            not marker.is_relative_to(root)
            for other_index, marker in enumerate(discord_files)
            if other_index != index
        )
        assert not discord_files[index].is_relative_to(direct_workspace)
    manager.state.close()


@pytest.mark.parametrize("failure_point", ["preflight", "catalogue", "project"])
def test_discord_rejected_start_never_admits_a_task(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_point: str,
) -> None:
    created: list[str] = []

    def create_project(**_kwargs: object) -> SimpleNamespace:
        created.append("project")
        raise OSError("workspace unavailable")

    storage = SimpleNamespace(create_project=create_project)
    catalogue = model_probe()
    if failure_point == "catalogue":
        catalogue.probe = lambda: SimpleNamespace(stale=True)
    app = SimpleNamespace(
        state=SimpleNamespace(
            auth_token="a" * 40,
            storage=storage,
            model_catalog_probe=catalogue,
        )
    )
    manager = DiscordChannelManager(app, tmp_path)
    manager.state.configure(policy(), TOKEN)
    if failure_point == "preflight":
        monkeypatch.setattr(
            manager,
            "_preflight_new_project",
            lambda: (_ for _ in ()).throw(DiscordChannelError("not ready")),
        )
    else:
        monkeypatch.setattr(manager, "_preflight_new_project", lambda: None)
    admitted: list[str] = []
    monkeypatch.setattr(
        discord_channel,
        "start_task",
        lambda *_args: admitted.append("started"),
    )
    notices: list[str] = []

    async def acknowledge(**_kwargs: object) -> None:
        pass

    async def send(message: str, **_kwargs: object) -> None:
        notices.append(message)

    interaction = SimpleNamespace(
        id=INTERACTION,
        user=SimpleNamespace(id=USER, bot=False),
        guild_id=None,
        channel_id=CHANNEL,
        channel=SimpleNamespace(type=1),
        response=SimpleNamespace(defer=acknowledge),
        followup=SimpleNamespace(send=send),
    )
    asyncio.run(manager._ask(interaction, "rejected"))
    assert created == (["project"] if failure_point == "project" else [])
    assert admitted == []
    assert manager.state.dm_thread(USER) is None
    assert notices == [
        "Codex could not start this task. Check its status in Home Assistant."
    ]
    manager.state.close()


def test_discord_unready_runtime_does_not_create_a_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspaces"
    workspace.mkdir()
    (tmp_path / "codex-home").mkdir()
    app = create_app(
        root_path=tmp_path / "state",
        auth_token="a" * 40,
        runtime_profile=RuntimeProfile.HOME_ASSISTANT,
        workspace_root=workspace,
        codex_home=tmp_path / "codex-home",
    )
    manager = app.state.discord_channel
    manager.state.configure(policy(), TOKEN)
    created: list[str] = []
    monkeypatch.setattr(
        app.state.storage,
        "create_project",
        lambda **_kwargs: created.append("project"),
    )
    monkeypatch.setattr(
        discord_channel,
        "start_task",
        lambda *_args: pytest.fail("unready task was admitted"),
    )

    async def acknowledge(**_kwargs: object) -> None:
        pass

    notices: list[str] = []

    async def send(message: str, **_kwargs: object) -> None:
        notices.append(message)

    interaction = SimpleNamespace(
        id=INTERACTION,
        user=SimpleNamespace(id=USER, bot=False),
        guild_id=None,
        channel_id=CHANNEL,
        channel=SimpleNamespace(type=1),
        response=SimpleNamespace(defer=acknowledge),
        followup=SimpleNamespace(send=send),
    )
    asyncio.run(manager._ask(interaction, "runtime unavailable"))
    assert created == []
    assert notices == [
        "Codex could not start this task. Check its status in Home Assistant."
    ]
    manager.state.close()


def test_status_and_cancel_are_scoped_to_the_invoker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = SimpleNamespace(state=SimpleNamespace(auth_token="a" * 40))
    manager = DiscordChannelManager(app, tmp_path)
    manager.state.configure(policy(), TOKEN)
    _, row = manager.state.claim(
        INTERACTION, user_id=USER, guild_id=GUILD, channel_id=CHANNEL, prompt="hello"
    )
    manager.state.record_run(INTERACTION, thread_id="thr_shared", run_id="run_shared")
    called: list[str] = []

    def task_action(task_id: str, *_args: object) -> dict[str, str]:
        called.append(task_id)
        return {"status": "completed"}

    monkeypatch.setattr(discord_channel, "get_task", task_action)
    monkeypatch.setattr(discord_channel, "cancel_task", task_action)

    def interaction(user_id: str) -> tuple[SimpleNamespace, list[str]]:
        messages: list[str] = []

        async def send(message: str, **_kwargs: object) -> None:
            messages.append(message)

        async def defer(**_kwargs: object) -> None:
            pass

        return SimpleNamespace(
            user=SimpleNamespace(id=user_id, bot=False),
            guild_id=GUILD,
            channel_id=CHANNEL,
            response=SimpleNamespace(send_message=send, defer=defer),
            followup=SimpleNamespace(send=send),
        ), messages

    stranger, denied = interaction(OTHER)
    asyncio.run(manager._status_command(stranger))
    asyncio.run(manager._cancel_command(stranger))
    assert denied == ["Discord access is not allowed here."] * 2
    assert called == []

    owner, allowed = interaction(USER)
    asyncio.run(manager._status_command(owner))
    asyncio.run(manager._cancel_command(owner))
    assert allowed == [
        "Your latest task is completed.",
        "There is no active task for you in this destination.",
    ]
    assert called == [row["task_id"]] * 2
    manager.state.close()


def test_cancel_finds_older_active_run_after_newer_completed_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = SimpleNamespace(state=SimpleNamespace(auth_token="a" * 40))
    manager = DiscordChannelManager(app, tmp_path)
    manager.state.configure(policy(), TOKEN)
    older_id, newer_id = INTERACTION, str(int(INTERACTION) + 1)
    _, older = manager.state.claim(
        older_id, user_id=USER, guild_id=GUILD, channel_id=CHANNEL, prompt="long"
    )
    _, newer = manager.state.claim(
        newer_id, user_id=USER, guild_id=GUILD, channel_id=CHANNEL, prompt="short"
    )
    manager.state.record_run(older_id, thread_id="thr_older", run_id="run_older")
    manager.state.record_run(newer_id, thread_id="thr_newer", run_id="run_newer")
    inspected: list[str] = []
    cancelled: list[str] = []

    def get(task_id: str, *_args: object) -> dict[str, str]:
        inspected.append(task_id)
        return {"status": "completed" if task_id == newer["task_id"] else "running"}

    def cancel(task_id: str, *_args: object) -> dict[str, str]:
        cancelled.append(task_id)
        return {"status": "cancelled"}

    monkeypatch.setattr(discord_channel, "get_task", get)
    monkeypatch.setattr(discord_channel, "cancel_task", cancel)
    messages: list[str] = []

    async def send(message: str, **_kwargs: object) -> None:
        messages.append(message)

    async def defer(**_kwargs: object) -> None:
        pass

    interaction = SimpleNamespace(
        user=SimpleNamespace(id=USER, bot=False),
        guild_id=GUILD,
        channel_id=CHANNEL,
        response=SimpleNamespace(defer=defer),
        followup=SimpleNamespace(send=send),
    )
    asyncio.run(manager._cancel_command(interaction))
    assert inspected == [newer["task_id"], older["task_id"]]
    assert cancelled == [older["task_id"]]
    assert messages == ["Your active task is cancelled."]
    manager.state.close()


def test_deleted_dm_thread_is_cleared_for_next_interaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = SimpleNamespace(
        create_project=lambda **_kwargs: SimpleNamespace(project_id="isolated")
    )
    app = SimpleNamespace(
        state=SimpleNamespace(
            auth_token="a" * 40,
            storage=storage,
            model_catalog_probe=model_probe(),
        )
    )
    manager = DiscordChannelManager(app, tmp_path)
    monkeypatch.setattr(manager, "_preflight_new_project", lambda: None)
    manager.state.configure(policy(), TOKEN)
    manager.state.set_dm_thread(USER, "thr_deleted")
    continued: list[str] = []
    started: list[str] = []

    def continue_missing(payload: ContinueTaskRequest, *_args: object) -> None:
        continued.append(payload.task_id)
        raise HTTPException(404, detail={"code": "task_target_not_found"})

    def start_new(payload: StartTaskRequest, *_args: object) -> dict[str, str]:
        started.append(payload.task_id)
        return {"thread_id": "thr_new", "run_id": "run_new"}

    monkeypatch.setattr(discord_channel, "continue_task", continue_missing)
    monkeypatch.setattr(discord_channel, "start_task", start_new)

    async def acknowledge(**_kwargs: object) -> None:
        pass

    async def send(_message: str, **_kwargs: object) -> None:
        pass

    def interaction(interaction_id: str) -> SimpleNamespace:
        return SimpleNamespace(
            id=interaction_id,
            user=SimpleNamespace(id=USER, bot=False),
            guild_id=None,
            channel_id=CHANNEL,
            channel=SimpleNamespace(type=1),
            response=SimpleNamespace(defer=acknowledge),
            followup=SimpleNamespace(send=send),
        )

    async def exercise() -> None:
        first = interaction(INTERACTION)
        await manager._ask(first, "first")
        assert manager.state.dm_thread(USER) is None
        await manager._ask(first, "first")
        await manager._ask(interaction(str(int(INTERACTION) + 1)), "second")

    asyncio.run(exercise())
    assert len(continued) == 1
    assert len(started) == 1
    assert manager.state.dm_thread(USER) == "thr_new"
    manager.state.close()


def test_discord_ingress_replay_mapping_and_revoke_survive_restart(
    tmp_path: Path,
) -> None:
    store = DiscordState(tmp_path)
    assert store._db.execute("PRAGMA secure_delete").fetchone()[0] == 1
    assert store.status()["credential_present"] is False
    with pytest.raises(DiscordChannelError):
        store.configure(policy(), None)
    store.configure(policy(), TOKEN)
    assert TOKEN not in str(store.status())
    fresh, row = store.claim(
        INTERACTION, user_id=USER, guild_id=None, channel_id=CHANNEL, prompt="hello"
    )
    assert fresh
    again, same = store.claim(
        INTERACTION, user_id=USER, guild_id=None, channel_id=CHANNEL, prompt="hello"
    )
    assert not again and same["task_id"] == row["task_id"]
    with pytest.raises(DiscordChannelError, match="changed"):
        store.claim(
            INTERACTION,
            user_id=USER,
            guild_id=None,
            channel_id=CHANNEL,
            prompt="different",
        )
    store.record_run(INTERACTION, thread_id="thr_private", run_id="run_private")
    store.set_dm_thread(USER, "thr_private")
    assert store.dm_thread(USER) == "thr_private"
    store.close()

    reopened = DiscordState(tmp_path)
    assert reopened.dm_thread(USER) == "thr_private"
    assert len(reopened.pending()) == 1
    reopened.revoke()
    assert reopened.dm_thread(USER) is None
    assert reopened.pending() == []
    assert reopened.status()["credential_present"] is False
    assert TOKEN not in str(reopened.status())
    reopened.close()


def test_discord_delivery_is_fenced_before_external_send(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events = SimpleNamespace(
        latest_assistant_message=lambda _thread, _run: (
            "hello @everyone https://private.example/file"
        )
    )
    app = SimpleNamespace(
        state=SimpleNamespace(
            auth_token="a" * 40, storage=SimpleNamespace(event_store=events)
        )
    )
    manager = DiscordChannelManager(app, tmp_path)
    manager.state.configure(policy(), TOKEN)
    _, row = manager.state.claim(
        INTERACTION, user_id=USER, guild_id=GUILD, channel_id=CHANNEL, prompt="hello"
    )
    manager.state.record_run(INTERACTION, thread_id="thr_shared", run_id="run_shared")
    row = manager.state.pending()[0]
    monkeypatch.setattr(
        discord_channel,
        "get_task",
        lambda *_args: {
            "task_id": row["task_id"],
            "thread_id": "thr_shared",
            "run_id": "run_shared",
            "status": "completed",
        },
    )
    sent: list[tuple[str, str, str]] = []

    async def post(token: str, channel: str, content: str, nonce: str) -> str:
        sent.append((token, channel, content))
        assert nonce == row["task_id"][:25]
        return "523456789012345678"

    manager._post_result = post  # type: ignore[method-assign]
    asyncio.run(manager._deliver(row))
    asyncio.run(manager._deliver(row))
    assert len(sent) == 1
    assert sent[0][1] == CHANNEL
    assert "[link omitted]" in sent[0][2]
    assert manager.state.pending() == []
    manager.state.close()
    assert DiscordState(tmp_path).pending() == []


def test_shared_text_omits_artifact_links() -> None:
    answer = safe_answer(
        "See sandbox:/secret.png and /api/codex_bridge/file", shared=True
    )
    assert "secret.png" not in answer
    assert "/api/codex_bridge" not in answer


def test_discord_outbound_retries_only_definite_short_rate_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = SimpleNamespace(state=SimpleNamespace(auth_token="a" * 40))
    manager = DiscordChannelManager(app, tmp_path)
    requests: list[dict[str, object]] = []

    class Client:
        async def __aenter__(self) -> "Client":
            return self

        async def __aexit__(self, *_args: object) -> None:
            pass

        async def post(
            self, _url: str, *, json: dict[str, object], headers: dict[str, str]
        ) -> httpx.Response:
            requests.append({"body": json, "headers": headers})
            if len(requests) == 1:
                return httpx.Response(429, headers={"Retry-After": "0.001"})
            return httpx.Response(200, json={"id": "523456789012345678"})

    monkeypatch.setattr(
        discord_channel.httpx, "AsyncClient", lambda **_kwargs: Client()
    )
    result = asyncio.run(
        manager._post_result(TOKEN, CHANNEL, "result @everyone", "nonce")
    )
    assert result == "523456789012345678"
    assert len(requests) == 2
    assert requests[0]["body"] == requests[1]["body"]
    assert requests[0]["body"]["allowed_mentions"] == {"parse": []}
    assert requests[0]["body"]["enforce_nonce"] is True
    assert TOKEN not in str(manager.status())
    manager.state.close()


def test_discord_permission_failure_has_secret_free_diagnostic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = SimpleNamespace(state=SimpleNamespace(auth_token="a" * 40))
    manager = DiscordChannelManager(app, tmp_path)
    calls = 0

    class Client:
        async def __aenter__(self) -> "Client":
            return self

        async def __aexit__(self, *_args: object) -> None:
            pass

        async def post(self, _url: str, **_kwargs: object) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(403)

    monkeypatch.setattr(
        discord_channel.httpx, "AsyncClient", lambda **_kwargs: Client()
    )
    assert asyncio.run(manager._post_result(TOKEN, CHANNEL, "result", "nonce")) is None
    assert calls == 1
    assert manager.status()["diagnostic"] == "delivery_permission_denied"
    assert TOKEN not in str(manager.status())
    manager.state.close()
