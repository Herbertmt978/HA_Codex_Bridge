from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
import httpx
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
from codex_bridge_service.models import RuntimeProfile

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
        ensure_direct_project=lambda: SimpleNamespace(project_id="direct")
    )
    app = SimpleNamespace(state=SimpleNamespace(auth_token="a" * 40, storage=storage))
    manager = DiscordChannelManager(app, tmp_path)
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
    assert allowed == ["Your latest task is completed."] * 2
    assert called == [row["task_id"]] * 2
    manager.state.close()


def test_discord_ingress_replay_mapping_and_revoke_survive_restart(
    tmp_path: Path,
) -> None:
    store = DiscordState(tmp_path)
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
