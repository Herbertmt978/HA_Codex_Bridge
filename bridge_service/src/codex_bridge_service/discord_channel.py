"""Private, closed-by-default Discord command state and delivery policy.

Discord is an untrusted input/output channel.  Its identifiers never confer a
Home Assistant identity or a privileged runtime grant.
"""

from __future__ import annotations

import hashlib
import asyncio
import json
import os
import re
import sqlite3
import stat
from pathlib import Path
from threading import RLock
from typing import Any

import httpx
from fastapi import HTTPException
from starlette.requests import Request

from .models import RuntimeProfile
from .readiness import evaluate_readiness
from .runtime_broker import RuntimeBroker
from .routes.task_actions import (
    ContinueTaskRequest,
    StartTaskRequest,
    cancel_task,
    continue_task,
    get_task,
    start_task,
)

_SNOWFLAKE = re.compile(r"[0-9]{17,20}\Z")
_TOKEN = re.compile(r"[A-Za-z0-9_.-]{30,200}\Z")
_MAX_REQUESTS = 20_000


class DiscordChannelError(ValueError):
    """A Discord operation was refused without exposing private input."""


def _snowflake(value: object) -> str:
    if not isinstance(value, str) or _SNOWFLAKE.fullmatch(value) is None:
        raise DiscordChannelError("A Discord identifier is invalid.")
    return value


def validate_policy(raw: dict[str, Any]) -> dict[str, Any]:
    """Validate exact IDs, separate DM/guild rules and closed defaults."""

    if set(raw) != {"enabled", "dm_user_ids", "guilds"}:
        raise DiscordChannelError("The Discord policy is invalid.")
    if type(raw["enabled"]) is not bool:
        raise DiscordChannelError("The Discord policy is invalid.")
    users = raw["dm_user_ids"]
    guilds = raw["guilds"]
    if (
        not isinstance(users, list)
        or len(users) > 32
        or not isinstance(guilds, list)
        or len(guilds) > 16
    ):
        raise DiscordChannelError("The Discord allow-lists are invalid.")
    dm_users = [_snowflake(value) for value in users]
    if len(set(dm_users)) != len(dm_users):
        raise DiscordChannelError("The Discord allow-lists contain duplicates.")
    normal_guilds = []
    seen_guilds: set[str] = set()
    for item in guilds:
        if not isinstance(item, dict) or set(item) != {
            "guild_id",
            "channel_ids",
            "user_ids",
        }:
            raise DiscordChannelError("A Discord server rule is invalid.")
        guild_id = _snowflake(item["guild_id"])
        if guild_id in seen_guilds:
            raise DiscordChannelError("The Discord allow-lists contain duplicates.")
        seen_guilds.add(guild_id)
        channels = item["channel_ids"]
        members = item["user_ids"]
        if (
            not isinstance(channels, list)
            or not 1 <= len(channels) <= 32
            or not isinstance(members, list)
            or not 1 <= len(members) <= 32
        ):
            raise DiscordChannelError(
                "A Discord server rule must name users and channels."
            )
        channel_ids = [_snowflake(value) for value in channels]
        user_ids = [_snowflake(value) for value in members]
        if len(set(channel_ids)) != len(channel_ids) or len(set(user_ids)) != len(
            user_ids
        ):
            raise DiscordChannelError("The Discord allow-lists contain duplicates.")
        normal_guilds.append(
            {"guild_id": guild_id, "channel_ids": channel_ids, "user_ids": user_ids}
        )
    if raw["enabled"] and not (dm_users or normal_guilds):
        raise DiscordChannelError(
            "Enable an explicit Discord user and destination first."
        )
    return {"enabled": raw["enabled"], "dm_user_ids": dm_users, "guilds": normal_guilds}


def permitted(
    policy: dict[str, Any],
    *,
    user_id: str,
    guild_id: str | None,
    channel_id: str,
    is_bot: bool,
) -> bool:
    """Never infer permission from Discord membership, role or a command's visibility."""

    if not policy["enabled"] or is_bot:
        return False
    try:
        _snowflake(user_id)
        _snowflake(channel_id)
        if guild_id is None:
            return user_id in policy["dm_user_ids"]
        _snowflake(guild_id)
    except DiscordChannelError:
        return False
    return any(
        guild_id == rule["guild_id"]
        and channel_id in rule["channel_ids"]
        and user_id in rule["user_ids"]
        for rule in policy["guilds"]
    )


def task_id_for(interaction_id: str) -> str:
    return hashlib.sha256(
        f"discord-interaction:{_snowflake(interaction_id)}".encode()
    ).hexdigest()[:32]


def safe_answer(value: str | None, *, shared: bool) -> str:
    """Publish one bounded text result; never transfer files or Bridge links."""

    if not value:
        return "Codex finished without a text answer. Open Home Assistant to review the task."
    text = value.replace("\x00", "").strip()
    # Local artifact links require HA administrator authentication and must not
    # be implied to work, or copied into a shared room.
    text = re.sub(r"(?:sandbox:|/api/codex_bridge/)[^\s)]+", "[artifact omitted]", text)
    if shared:
        text = re.sub(r"https?://[^\s)]+", "[link omitted]", text)
    return (text[:1750] + "…") if len(text) > 1750 else text


class DiscordState:
    """Single private SQLite journal for policy, ingress dedup and outbound fencing."""

    def __init__(self, root: Path) -> None:
        directory = Path(root) / "discord"
        if directory.is_symlink():
            raise DiscordChannelError("Discord private storage is unsafe.")
        directory.mkdir(mode=0o700, exist_ok=True)
        if os.name != "nt":
            info = directory.stat(follow_symlinks=False)
            if (
                not stat.S_ISDIR(info.st_mode)
                or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o700
            ):
                raise DiscordChannelError("Discord private storage is unsafe.")
        path = directory / "channel.sqlite3"
        if not path.exists():
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
            try:
                fd = os.open(path, flags, 0o600)
            except FileExistsError:
                pass
            else:
                os.close(fd)
        if path.is_symlink():
            raise DiscordChannelError("Discord private storage is unsafe.")
        if os.name != "nt":
            info = path.stat(follow_symlinks=False)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o600
            ):
                raise DiscordChannelError("Discord private storage is unsafe.")
        self._lock = RLock()
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA busy_timeout=5000")
        self._db.execute("PRAGMA synchronous=FULL")
        self._db.execute("PRAGMA secure_delete=ON")
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS policy (
                singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                epoch INTEGER NOT NULL, definition TEXT NOT NULL, bot_token TEXT
            );
            CREATE TABLE IF NOT EXISTS requests (
                interaction_id TEXT PRIMARY KEY, epoch INTEGER NOT NULL,
                user_id TEXT NOT NULL, guild_id TEXT, channel_id TEXT NOT NULL,
                prompt_digest TEXT NOT NULL, task_id TEXT NOT NULL UNIQUE,
                thread_id TEXT, run_id TEXT, delivery TEXT NOT NULL,
                message_id TEXT
            );
            CREATE TABLE IF NOT EXISTS dm_threads (
                user_id TEXT PRIMARY KEY, epoch INTEGER NOT NULL, thread_id TEXT NOT NULL
            );
        """)
        self._db.execute(
            "INSERT OR IGNORE INTO policy VALUES (1, 0, ?, NULL)",
            (json.dumps({"enabled": False, "dm_user_ids": [], "guilds": []}),),
        )

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def policy(self) -> tuple[int, dict[str, Any], str | None]:
        with self._lock:
            row = self._db.execute(
                "SELECT epoch, definition, bot_token FROM policy WHERE singleton=1"
            ).fetchone()
            assert row is not None
            try:
                definition = validate_policy(json.loads(row["definition"]))
            except (ValueError, TypeError, KeyError):
                raise DiscordChannelError(
                    "The saved Discord policy is invalid."
                ) from None
            if row["bot_token"] is not None and (
                not isinstance(row["bot_token"], str)
                or _TOKEN.fullmatch(row["bot_token"]) is None
            ):
                raise DiscordChannelError("The saved Discord credential is invalid.")
            return row["epoch"], definition, row["bot_token"]

    def status(self) -> dict[str, Any]:
        epoch, definition, token = self.policy()
        return {
            **definition,
            "credential_present": token is not None,
            "revision": epoch,
        }

    def validate_configuration(
        self, definition: dict[str, Any], token: str | None
    ) -> None:
        clean = validate_policy(definition)
        if token is not None and (
            _TOKEN.fullmatch(token) is None or token != token.strip()
        ):
            raise DiscordChannelError("The bot credential is invalid.")
        _, _, old_token = self.policy()
        if clean["enabled"] and token is None and old_token is None:
            raise DiscordChannelError("Add a bot credential before enabling Discord.")

    def configure(
        self, definition: dict[str, Any], token: str | None
    ) -> dict[str, Any]:
        self.validate_configuration(definition, token)
        clean = validate_policy(definition)
        with self._lock:
            _, _, old_token = self.policy()
            chosen = token if token is not None else old_token
            self._db.execute("BEGIN IMMEDIATE")
            try:
                self._db.execute(
                    "UPDATE policy SET epoch=epoch+1, definition=?, bot_token=? WHERE singleton=1",
                    (json.dumps(clean, separators=(",", ":")), chosen),
                )
                self._db.execute("DELETE FROM dm_threads")
                self._db.execute(
                    "UPDATE requests SET delivery='suppressed' WHERE delivery='pending'"
                )
                self._db.execute("COMMIT")
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
        return self.status()

    def revoke(self) -> dict[str, Any]:
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                self._db.execute(
                    "UPDATE policy SET epoch=epoch+1, definition=?, bot_token=NULL WHERE singleton=1",
                    (json.dumps({"enabled": False, "dm_user_ids": [], "guilds": []}),),
                )
                self._db.execute("DELETE FROM dm_threads")
                self._db.execute(
                    "UPDATE requests SET delivery='suppressed' WHERE delivery='pending'"
                )
                self._db.execute("COMMIT")
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
        return self.status()

    def claim(
        self,
        interaction_id: str,
        *,
        user_id: str,
        guild_id: str | None,
        channel_id: str,
        prompt: str,
    ) -> tuple[bool, dict[str, Any]]:
        interaction_id = _snowflake(interaction_id)
        digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        epoch, policy, _ = self.policy()
        if not permitted(
            policy,
            user_id=user_id,
            guild_id=guild_id,
            channel_id=channel_id,
            is_bot=False,
        ):
            raise DiscordChannelError("Discord access is not allowed here.")
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM requests WHERE interaction_id=?", (interaction_id,)
            ).fetchone()
            if row is not None:
                if (
                    row["user_id"],
                    row["guild_id"],
                    row["channel_id"],
                    row["prompt_digest"],
                ) != (user_id, guild_id, channel_id, digest):
                    raise DiscordChannelError(
                        "The Discord interaction changed during replay."
                    )
                return False, dict(row)
            count = self._db.execute("SELECT COUNT(*) FROM requests").fetchone()[0]
            if count >= _MAX_REQUESTS:
                raise DiscordChannelError("Discord request capacity is full.")
            task_id = task_id_for(interaction_id)
            self._db.execute(
                "INSERT INTO requests VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, 'pending', NULL)",
                (interaction_id, epoch, user_id, guild_id, channel_id, digest, task_id),
            )
            row = self._db.execute(
                "SELECT * FROM requests WHERE interaction_id=?", (interaction_id,)
            ).fetchone()
            assert row is not None
            return True, dict(row)

    def record_run(self, interaction_id: str, *, thread_id: str, run_id: str) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE requests SET thread_id=?, run_id=? WHERE interaction_id=? AND delivery='pending'",
                (thread_id, run_id, interaction_id),
            )

    def dm_thread(self, user_id: str) -> str | None:
        epoch, _, _ = self.policy()
        with self._lock:
            row = self._db.execute(
                "SELECT thread_id FROM dm_threads WHERE user_id=? AND epoch=?",
                (user_id, epoch),
            ).fetchone()
            return None if row is None else str(row[0])

    def set_dm_thread(self, user_id: str, thread_id: str) -> None:
        epoch, _, _ = self.policy()
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO dm_threads VALUES (?, ?, ?)",
                (user_id, epoch, thread_id),
            )

    def clear_dm_thread(self, user_id: str, thread_id: str) -> None:
        epoch, _, _ = self.policy()
        with self._lock:
            self._db.execute(
                "DELETE FROM dm_threads WHERE user_id=? AND epoch=? AND thread_id=?",
                (user_id, epoch, thread_id),
            )

    def pending(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                dict(row)
                for row in self._db.execute(
                    "SELECT * FROM requests WHERE delivery='pending' ORDER BY interaction_id LIMIT 100"
                )
            ]

    def unrecorded(self, after: str = "") -> list[dict[str, Any]]:
        with self._lock:
            return [
                dict(row)
                for row in self._db.execute(
                    "SELECT * FROM requests WHERE delivery='pending' AND run_id IS NULL "
                    "AND interaction_id > ? ORDER BY interaction_id LIMIT 100",
                    (after,),
                )
            ]

    def unfinished(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                dict(row)
                for row in self._db.execute(
                    "SELECT * FROM requests WHERE delivery='pending'"
                )
            ]

    def mark(
        self,
        interaction_id: str,
        expected: str,
        next_state: str,
        message_id: str | None = None,
    ) -> bool:
        with self._lock:
            cursor = self._db.execute(
                "UPDATE requests SET delivery=?, message_id=? WHERE interaction_id=? AND delivery=?",
                (next_state, message_id, interaction_id, expected),
            )
            return cursor.rowcount == 1

    def latest_for_user(
        self, user_id: str, guild_id: str | None, channel_id: str
    ) -> dict[str, Any] | None:
        epoch, _, _ = self.policy()
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM requests WHERE user_id=? AND guild_id IS ? AND channel_id=? "
                "AND epoch=? ORDER BY interaction_id DESC LIMIT 1",
                (user_id, guild_id, channel_id, epoch),
            ).fetchone()
            return None if row is None else dict(row)

    def pending_for_user(
        self, user_id: str, guild_id: str | None, channel_id: str
    ) -> list[dict[str, Any]]:
        epoch, _, _ = self.policy()
        with self._lock:
            return [
                dict(row)
                for row in self._db.execute(
                    "SELECT * FROM requests WHERE user_id=? AND guild_id IS ? AND channel_id=? "
                    "AND epoch=? AND delivery='pending' AND run_id IS NOT NULL "
                    "ORDER BY interaction_id DESC",
                    (user_id, guild_id, channel_id, epoch),
                )
            ]


class DiscordChannelManager:
    """Outbound Gateway client with durable, at-most-once result delivery."""

    def __init__(self, app: Any, root: Path) -> None:
        self.app = app
        self.state = DiscordState(root)
        self._lock = asyncio.Lock()
        self._client: Any = None
        self._gateway_task: asyncio.Task[None] | None = None
        self._poll_task: asyncio.Task[None] | None = None
        self._running = False
        self._connected = False
        self._last_error: str | None = None
        self._recover_after = ""

    def _request(self) -> Request:
        return Request(
            {
                "type": "http",
                "app": self.app,
                "method": "POST",
                "path": "/task-actions/start",
                "headers": [(b"x-codex-bridge-api", b"1")],
            }
        )

    def _authorisation(self) -> str:
        return "Bearer " + self.app.state.auth_token

    def _preflight_new_project(self) -> None:
        """Avoid creating an empty workspace for a known rejected admission."""

        state = self.app.state
        if (
            state.storage.runtime_profile is not RuntimeProfile.HOME_ASSISTANT
            or not isinstance(state.runner, RuntimeBroker)
            or evaluate_readiness(state, include_catalogue=False).state != "ready"
            or getattr(getattr(state, "mcp_manager", None), "enabled", None)
            is not False
        ):
            raise DiscordChannelError("The Discord task runtime is unavailable.")

    def status(self) -> dict[str, Any]:
        return {
            **self.state.status(),
            "connected": self._connected,
            "diagnostic": self._last_error,
        }

    async def start(self) -> None:
        async with self._lock:
            self._running = True
            await self._recover_claims()
            await self._start_locked()
            self._poll_task = asyncio.create_task(self._poll(), name="discord-results")

    async def _recover_claims(self) -> None:
        """Resolve the crash window after task admission but before local receipt."""

        rows = self.state.unrecorded(self._recover_after)
        self._recover_after = rows[-1]["interaction_id"] if len(rows) == 100 else ""
        for row in rows:
            try:
                run = await asyncio.to_thread(
                    get_task, row["task_id"], self._request(), self._authorisation()
                )
            except HTTPException as error:
                if error.status_code == 404:
                    # A known missing task is a definite failed admission.
                    self.state.mark(row["interaction_id"], "pending", "failed")
            except Exception:
                # An unavailable runtime is not proof the task was rejected.
                continue
            else:
                try:
                    self.state.record_run(
                        row["interaction_id"],
                        thread_id=run["thread_id"],
                        run_id=run["run_id"],
                    )
                    if row["guild_id"] is None:
                        self.state.set_dm_thread(row["user_id"], run["thread_id"])
                except Exception:
                    self._last_error = "task_recovery_unavailable"

    async def close(self) -> None:
        async with self._lock:
            self._running = False
            await self._stop_gateway_locked()
            if self._poll_task is not None:
                self._poll_task.cancel()
                try:
                    await self._poll_task
                except asyncio.CancelledError:
                    pass
                self._poll_task = None
            self.state.close()

    async def configure(
        self, definition: dict[str, Any], token: str | None
    ) -> dict[str, Any]:
        async with self._lock:
            self.state.validate_configuration(definition, token)
            await self._stop_gateway_locked()
            unfinished = self.state.unfinished()
            self.state.configure(definition, token)
            await self._cancel_pending(unfinished)
            self._last_error = None
            if self._running:
                await self._start_locked()
            return self.status()

    async def revoke(self) -> dict[str, Any]:
        async with self._lock:
            await self._stop_gateway_locked()
            unfinished = self.state.unfinished()
            self.state.revoke()
            await self._cancel_pending(unfinished)
            self._last_error = None
            return self.status()

    async def _cancel_pending(self, rows: list[dict[str, Any]]) -> None:
        for row in rows:
            if row["run_id"]:
                try:
                    await asyncio.to_thread(
                        cancel_task,
                        row["task_id"],
                        self._request(),
                        self._authorisation(),
                    )
                except Exception:
                    # Suppression is durable even if a completed turn cannot be cancelled.
                    pass

    async def _start_locked(self) -> None:
        _, policy, token = self.state.policy()
        if not policy["enabled"] or not token:
            return
        try:
            import discord
            from discord import app_commands
        except ImportError:
            self._last_error = "discord_dependency_unavailable"
            return

        manager = self

        class Client(discord.Client):
            def __init__(self) -> None:
                super().__init__(
                    intents=discord.Intents.none(),
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                self.tree = app_commands.CommandTree(self)

            async def setup_hook(self) -> None:
                await self.tree.sync()

            async def on_ready(self) -> None:
                manager._connected = True
                manager._last_error = None

            async def on_disconnect(self) -> None:
                manager._connected = False

        client = Client()

        @client.tree.command(
            name="codex", description="Ask Codex in this authorised destination"
        )
        async def ask(interaction: discord.Interaction, prompt: str) -> None:
            await manager._ask(interaction, prompt)

        @client.tree.command(
            name="codex_status", description="Check your most recent Codex task here"
        )
        async def task_status(interaction: discord.Interaction) -> None:
            await manager._status_command(interaction)

        @client.tree.command(
            name="codex_cancel",
            description="Cancel your most recent active Codex task here",
        )
        async def task_cancel(interaction: discord.Interaction) -> None:
            await manager._cancel_command(interaction)

        self._client = client

        async def run() -> None:
            try:
                await client.start(token, reconnect=True)
            except Exception:
                # Library/network errors may contain request details; diagnostics
                # deliberately expose only a fixed code.
                self._last_error = "gateway_unavailable"
            finally:
                self._connected = False

        self._gateway_task = asyncio.create_task(run(), name="discord-gateway")

    async def _stop_gateway_locked(self) -> None:
        client, task = self._client, self._gateway_task
        self._client = None
        self._gateway_task = None
        self._connected = False
        if client is not None:
            await client.close()
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    def _identity(self, interaction: Any) -> tuple[str, str | None, str] | None:
        user = getattr(interaction, "user", None)
        if user is None or getattr(user, "bot", False):
            return None
        user_id = str(getattr(user, "id", ""))
        guild = getattr(interaction, "guild_id", None)
        guild_id = str(guild) if guild is not None else None
        channel_id = str(getattr(interaction, "channel_id", ""))
        if guild_id is None:
            channel_type = getattr(getattr(interaction, "channel", None), "type", None)
            if getattr(channel_type, "value", channel_type) != 1:
                return None
        _, policy, _ = self.state.policy()
        if not permitted(
            policy,
            user_id=user_id,
            guild_id=guild_id,
            channel_id=channel_id,
            is_bot=False,
        ):
            return None
        return user_id, guild_id, channel_id

    async def _ask(self, interaction: Any, prompt: str) -> None:
        identity = self._identity(interaction)
        if identity is None:
            await interaction.response.send_message(
                "Discord access is not allowed here.", ephemeral=True
            )
            return
        if (
            not isinstance(prompt, str)
            or not prompt.strip()
            or len(prompt.encode("utf-8")) > 4000
        ):
            await interaction.response.send_message(
                "Enter a prompt of up to 4,000 bytes.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        message = "Codex could not start this task. Check its status in Home Assistant."
        async with self._lock:
            if self._identity(interaction) != identity:
                message = "Discord access is no longer allowed here."
            else:
                user_id, guild_id, channel_id = identity
                interaction_id = str(interaction.id)
                try:
                    fresh, record = self.state.claim(
                        interaction_id,
                        user_id=user_id,
                        guild_id=guild_id,
                        channel_id=channel_id,
                        prompt=prompt,
                    )
                    if not fresh:
                        message = "This command is already recorded."
                    else:
                        thread_id = (
                            self.state.dm_thread(user_id) if guild_id is None else None
                        )
                        if thread_id is None:
                            await asyncio.to_thread(self._preflight_new_project)
                            catalogue = await asyncio.to_thread(
                                self.app.state.model_catalog_probe.probe
                            )
                            if catalogue.stale is not False:
                                raise DiscordChannelError(
                                    "The Discord model catalogue is unavailable."
                                )
                            project = await asyncio.to_thread(
                                self.app.state.storage.create_project,
                                name="Discord private chat"
                                if guild_id is None
                                else "Discord shared request",
                                default_model=catalogue.default_model,
                                default_thinking_level=catalogue.default_thinking_level,
                            )
                            payload = StartTaskRequest(
                                task_id=record["task_id"],
                                project_id=project.project_id,
                                title="Discord private chat"
                                if guild_id is None
                                else "Discord shared request",
                                prompt=prompt,
                                mode="observe",
                                web_search="disabled",
                                assist=True,
                            )
                            run = await asyncio.to_thread(
                                start_task,
                                payload,
                                self._request(),
                                self._authorisation(),
                            )
                        else:
                            payload = ContinueTaskRequest(
                                task_id=record["task_id"],
                                thread_id=thread_id,
                                prompt=prompt,
                                web_search="disabled",
                                assist=True,
                            )
                            try:
                                run = await asyncio.to_thread(
                                    continue_task,
                                    payload,
                                    self._request(),
                                    self._authorisation(),
                                )
                            except HTTPException as error:
                                if (
                                    guild_id is None
                                    and error.status_code == 404
                                    and isinstance(error.detail, dict)
                                    and error.detail.get("code")
                                    == "task_target_not_found"
                                ):
                                    self.state.clear_dm_thread(user_id, thread_id)
                                raise
                        self.state.record_run(
                            interaction_id,
                            thread_id=run["thread_id"],
                            run_id=run["run_id"],
                        )
                        if guild_id is None:
                            self.state.set_dm_thread(user_id, run["thread_id"])
                        message = "Task accepted. The result will appear here when it finishes."
                except Exception:
                    # Admission may have succeeded before the local receipt
                    # failed. Reconciliation checks the durable task ID.
                    pass
        try:
            await interaction.followup.send(message, ephemeral=True)
        except Exception:
            # The task receipt is durable; a failed acknowledgement must not
            # turn an admitted Codex run into a failed Discord request.
            pass

    async def _status_command(self, interaction: Any) -> None:
        identity = self._identity(interaction)
        if identity is None:
            await interaction.response.send_message(
                "Discord access is not allowed here.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        async with self._lock:
            if self._identity(interaction) != identity:
                message = "Discord access is no longer allowed here."
            else:
                row = self.state.latest_for_user(*identity)
                if row is None:
                    message = "There is no task for you in this destination."
                elif row["run_id"] is None:
                    message = (
                        "Your latest task was not admitted."
                        if row["delivery"] == "failed"
                        else "Your latest task admission is being checked."
                    )
                else:
                    try:
                        result = await asyncio.to_thread(
                            get_task,
                            row["task_id"],
                            self._request(),
                            self._authorisation(),
                        )
                        message = f"Your latest task is {result['status']}."
                    except Exception:
                        message = "Task status is unavailable."
        try:
            await interaction.followup.send(message, ephemeral=True)
        except Exception:
            pass

    async def _cancel_command(self, interaction: Any) -> None:
        identity = self._identity(interaction)
        if identity is None:
            await interaction.response.send_message(
                "Discord access is not allowed here.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        async with self._lock:
            if self._identity(interaction) != identity:
                message = "Discord access is no longer allowed here."
            else:
                message = "There is no active task for you in this destination."
                for row in self.state.pending_for_user(*identity):
                    try:
                        current = await asyncio.to_thread(
                            get_task,
                            row["task_id"],
                            self._request(),
                            self._authorisation(),
                        )
                        if current["status"] in {
                            "completed",
                            "failed",
                            "cancelled",
                            "interrupted",
                        }:
                            continue
                        result = await asyncio.to_thread(
                            cancel_task,
                            row["task_id"],
                            self._request(),
                            self._authorisation(),
                        )
                        message = f"Your active task is {result['status']}."
                        break
                    except Exception:
                        message = "Cancellation is unavailable. Check Home Assistant."
                        break
        try:
            await interaction.followup.send(message, ephemeral=True)
        except Exception:
            pass

    async def _poll(self) -> None:
        try:
            while True:
                await asyncio.sleep(3)
                if not self._connected:
                    continue
                async with self._lock:
                    await self._recover_claims()
                    for row in self.state.pending():
                        if await self._deliver(row):
                            break
        except asyncio.CancelledError:
            raise

    async def _deliver(self, row: dict[str, Any]) -> bool:
        if row["run_id"] is None:
            return False
        epoch, policy, token = self.state.policy()
        if (
            row["epoch"] != epoch
            or not token
            or not permitted(
                policy,
                user_id=row["user_id"],
                guild_id=row["guild_id"],
                channel_id=row["channel_id"],
                is_bot=False,
            )
        ):
            self.state.mark(row["interaction_id"], "pending", "suppressed")
            return True
        try:
            result = await asyncio.to_thread(
                get_task, row["task_id"], self._request(), self._authorisation()
            )
        except Exception:
            self._last_error = "task_status_unavailable"
            return False
        if result["status"] not in {"completed", "failed", "cancelled", "interrupted"}:
            return False
        if result["status"] == "completed" and row["run_id"]:
            answer = self.app.state.storage.event_store.latest_assistant_message(
                result["thread_id"], result["run_id"]
            )
            content = safe_answer(answer, shared=row["guild_id"] is not None)
        else:
            content = f"Codex task {result['status']}. Review it in Home Assistant."
        # Commit the outbound attempt before sending. A lost HTTP response or
        # process crash cannot trigger an uncontrolled second message.
        if not self.state.mark(row["interaction_id"], "pending", "attempted"):
            return True
        message_id = await self._post_result(
            token, row["channel_id"], content, row["task_id"][:25]
        )
        if message_id is not None:
            self.state.mark(row["interaction_id"], "attempted", "delivered", message_id)
        return True

    async def _post_result(
        self, token: str, channel_id: str, content: str, nonce: str
    ) -> str | None:
        payload = {
            "content": content,
            "allowed_mentions": {"parse": []},
            "flags": 4,
            "nonce": nonce,
            "enforce_nonce": True,
        }
        try:
            async with httpx.AsyncClient(
                timeout=12, follow_redirects=False, trust_env=False
            ) as client:
                for attempt in range(2):
                    response = await client.post(
                        f"https://discord.com/api/v10/channels/{channel_id}/messages",
                        json=payload,
                        headers={"Authorization": f"Bot {token}"},
                    )
                    if response.status_code != 429 or attempt:
                        break
                    try:
                        retry_after = float(response.headers.get("Retry-After", ""))
                    except ValueError:
                        retry_after = 0
                    if not 0 < retry_after <= 5:
                        break
                    await asyncio.sleep(retry_after)
            if response.status_code == 200:
                body = response.json()
                message_id = body.get("id") if isinstance(body, dict) else None
                return _snowflake(message_id)
            self._last_error = (
                "delivery_permission_denied"
                if response.status_code == 403
                else "delivery_rate_limited"
                if response.status_code == 429
                else "credential_rejected"
                if response.status_code == 401
                else "delivery_unavailable"
            )
        except (httpx.HTTPError, ValueError, TypeError):
            self._last_error = "delivery_unavailable"
        return None
