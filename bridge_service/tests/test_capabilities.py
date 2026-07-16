from __future__ import annotations

from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

import codex_bridge_service.capabilities as capabilities_module
from codex_bridge_service.capabilities import (
    CapabilitiesInvalidError,
    CapabilitiesManager,
)
from codex_bridge_service.routes.agents import (
    AgentsMutationConflictError,
    WorkspaceAgentsManager,
)


class Boundary:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir()

    def normalize(self, value: str, *, allow_root: bool = False) -> str:
        if (
            not isinstance(value, str)
            or not value
            or value.startswith(("/", "\\"))
            or "\\" in value
        ):
            raise ValueError()
        parts = [part for part in value.split("/") if part not in ("", ".")]
        if any(part == ".." for part in parts) or (value == "." and not allow_root):
            raise ValueError()
        return "." if not parts else "/".join(parts)

    def resolve_relative(self, value: str, *, must_exist: bool, kind: str):
        normalized = self.normalize(value, allow_root=True)
        target = (
            self.root
            if normalized == "."
            else self.root.joinpath(*normalized.split("/"))
        )
        resolved = target.resolve(strict=False)
        if self.root not in resolved.parents and resolved != self.root:
            raise ValueError()
        if must_exist and (
            not target.exists() or (kind == "directory" and not target.is_dir())
        ):
            raise FileNotFoundError()
        return target

    def relative_from_path(self, value: str) -> str:
        relative = Path(value).resolve().relative_to(self.root)
        return "." if not relative.parts else relative.as_posix()

    def open_regular_file(self, value: str):
        try:
            target = self.resolve_relative(value, must_exist=True, kind="file")
        except FileNotFoundError:
            from codex_bridge_service.workspace import WorkspaceNotFoundError

            raise WorkspaceNotFoundError() from None
        if target.is_symlink():
            raise ValueError()
        return target.open("rb")

    def atomic_write_bytes(self, value: str, payload: bytes) -> None:
        target = self.resolve_relative(value, must_exist=False, kind="file")
        if target.exists() and (target.is_symlink() or not target.is_file()):
            raise ValueError()
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name("." + target.name + ".tmp")
        temporary.write_bytes(payload)
        temporary.replace(target)

    def unlink_regular_file(self, value: str) -> None:
        target = self.resolve_relative(value, must_exist=True, kind="file")
        if target.is_symlink():
            raise ValueError()
        target.unlink()

    def create_directory(self, value: str) -> str:
        target = self.resolve_relative(value, must_exist=False, kind="directory")
        if target.exists() and (target.is_symlink() or not target.is_dir()):
            raise ValueError()
        target.mkdir(parents=True, exist_ok=True)
        return self.normalize(value)

    def create_file_exclusive(self, value: str):
        target = self.resolve_relative(value, must_exist=False, kind="file")
        target.parent.mkdir(parents=True, exist_ok=True)
        return target.open("xb")

    def remove_empty_directory(self, value: str) -> None:
        target = self.resolve_relative(value, must_exist=True, kind="directory")
        target.rmdir()


class FakeServer:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []
        self.request_timeouts: list[float | None] = []
        self.responses: dict[str, object] = {}

    def request(
        self,
        method: str,
        params: object,
        *,
        timeout_seconds: float | None = None,
    ) -> object:
        self.calls.append((method, params))
        self.request_timeouts.append(timeout_seconds)
        return self.responses.get(method, {})


class Gate:
    def __init__(self, blocked: bool = False) -> None:
        self.blocked = blocked
        self.released = 0

    def acquire_auth_mutation(self):
        if self.blocked:
            from codex_bridge_service.runtime_gate import RuntimeMutationConflictError

            raise RuntimeMutationConflictError()
        gate = self

        class Lease:
            def release(self):
                gate.released += 1

        return Lease()

    acquire_config_mutation = acquire_auth_mutation


class Storage:
    def __init__(self, root: Path) -> None:
        self.root = root / "private"
        self.root.mkdir()
        self.workspace_boundary = Boundary(root / "workspace")
        self.project = SimpleNamespace(project_id="prj_one", root_path="project")

    def load_project(self, project_id: str):
        if project_id != self.project.project_id:
            from codex_bridge_service.storage import ProjectNotFoundError

            raise ProjectNotFoundError()
        return self.project


def test_skills_are_workspace_scoped_and_private_paths_are_omitted(
    tmp_path: Path,
) -> None:
    storage = Storage(tmp_path)
    (storage.workspace_boundary.root / "project" / ".agents" / "skills").mkdir(
        parents=True
    )
    server = FakeServer()
    server.responses["skills/list"] = {
        "data": [
            {
                "skills": [
                    {
                        "name": "local",
                        "description": "<b>safe</b>",
                        "enabled": True,
                        "scope": "repo",
                        "path": str(
                            storage.workspace_boundary.root
                            / "project"
                            / ".agents"
                            / "skills"
                            / "local"
                        ),
                    },
                    {
                        "name": "private",
                        "description": "Bearer private-secret",
                        "enabled": True,
                        "path": str(tmp_path / "private" / "skills"),
                    },
                ],
                "errors": [],
            }
        ]
    }
    manager = CapabilitiesManager(storage, server)
    result = manager.list_skills("project")
    assert result["data"][0]["cwd"] == "project"
    assert result["data"][0]["skills"][0]["path"] == "project/.agents/skills/local"
    assert result["data"][0]["skills"][1]["path"] is None
    assert result["data"][0]["skills"][1]["description"] == "[redacted]"
    assert server.calls[0] == (
        "skills/list",
        {
            "cwds": [str(storage.workspace_boundary.root / "project")],
            "forceReload": False,
        },
    )


def test_skills_at_workspace_root_project_a_safe_relative_path(tmp_path: Path) -> None:
    storage = Storage(tmp_path)
    skill = storage.workspace_boundary.root / ".agents" / "skills" / "local"
    skill.mkdir(parents=True)
    server = FakeServer()
    server.responses["skills/list"] = {
        "data": [
            {
                "skills": [{"name": "local", "path": str(skill), "enabled": True}],
                "errors": [],
            }
        ]
    }

    result = CapabilitiesManager(storage, server).list_skills(".")

    assert result["data"][0]["cwd"] == "."
    assert result["data"][0]["skills"][0]["path"] == ".agents/skills/local"


def test_skill_config_uses_native_params_and_gate(tmp_path: Path) -> None:
    storage = Storage(tmp_path)
    (storage.workspace_boundary.root / "project" / ".agents" / "skills").mkdir(
        parents=True
    )
    skill = storage.workspace_boundary.root / "project" / ".agents" / "skills" / "local"
    skill.write_text("x", encoding="utf-8")
    server = FakeServer()
    server.responses["skills/config/write"] = {"effectiveEnabled": False}
    gate = Gate()
    manager = CapabilitiesManager(storage, server, gate)
    assert manager.set_skill(
        "project", enabled=False, relative_path="project/.agents/skills/local"
    ) == {"effective_enabled": False}
    assert server.calls[-1][0] == "skills/config/write"
    assert Path(server.calls[-1][1]["path"]).resolve() == skill.resolve()
    assert gate.released == 1
    with pytest.raises(CapabilitiesInvalidError):
        manager.set_skill(
            "project",
            enabled=True,
            name="x",
            relative_path="project/.agents/skills/local",
        )


def test_marketplace_sources_reject_local_private_and_credentialed_urls(
    tmp_path: Path,
) -> None:
    manager = CapabilitiesManager(Storage(tmp_path), FakeServer())
    for source in (
        "file:///tmp/marketplace",
        "https://user:password@example.com/marketplace.git",
        "https://127.0.0.1/marketplace.git",
    ):
        with pytest.raises(CapabilitiesInvalidError):
            manager.add_marketplace(source)


def test_marketplace_source_rejects_private_dns_answer_without_network(
    tmp_path: Path,
) -> None:
    manager = CapabilitiesManager(
        Storage(tmp_path),
        FakeServer(),
        resolver=lambda _host: ("192.168.1.10",),
    )

    with pytest.raises(CapabilitiesInvalidError):
        manager.add_marketplace("https://marketplace.vendor.example/index.git")


def test_list_plugins_projects_payload_and_uses_workspace_cwd(tmp_path: Path) -> None:
    storage = Storage(tmp_path)
    (storage.workspace_boundary.root / "project").mkdir()
    server = FakeServer()
    server.responses["plugin/list"] = {
        "marketplaces": [
            {
                "name": "official",
                "plugins": [
                    {
                        "id": "plugin.one",
                        "name": "Plugin One",
                        "interface": {"shortDescription": "A useful plugin"},
                        "enabled": True,
                        "installed": False,
                        "version": "1.2.3",
                        "localVersion": "1.1.0",
                        "marketplaceName": "official",
                        "privateField": "must not leak",
                    }
                ],
            }
        ]
    }

    result = CapabilitiesManager(storage, server).list_plugins("project")

    assert server.calls == [
        (
            "plugin/list",
            {"cwds": [str(storage.workspace_boundary.root / "project")]},
        )
    ]
    assert server.request_timeouts == [60.0]
    assert result == {
        "cwd": "project",
        "marketplaces": [
            {
                "name": "official",
                "plugins": [
                    {
                        "id": "plugin.one",
                        "name": "Plugin One",
                        "description": "A useful plugin",
                        "enabled": True,
                        "installed": False,
                        "version": "1.2.3",
                        "local_version": "1.1.0",
                        "marketplace_name": "official",
                    }
                ],
            }
        ],
    }


def test_list_plugins_installed_only_uses_installed_endpoint(tmp_path: Path) -> None:
    storage = Storage(tmp_path)
    (storage.workspace_boundary.root / "project").mkdir()
    server = FakeServer()
    server.responses["plugin/installed"] = {
        "marketplaces": [
            {
                "name": "official",
                "plugins": [
                    {
                        "id": "installed.plugin",
                        "name": "Installed Plugin",
                        "installed": True,
                    }
                ],
            }
        ]
    }

    result = CapabilitiesManager(storage, server).list_plugins(
        "project", installed_only=True
    )

    assert server.calls == [
        (
            "plugin/installed",
            {"cwds": [str(storage.workspace_boundary.root / "project")]},
        )
    ]
    assert server.request_timeouts == [60.0]
    assert result["marketplaces"][0]["plugins"][0]["installed"] is True


def test_list_plugins_does_not_truncate_current_catalogue_above_512(
    tmp_path: Path,
) -> None:
    storage = Storage(tmp_path)
    (storage.workspace_boundary.root / "project").mkdir()
    server = FakeServer()
    server.responses["plugin/list"] = {
        "marketplaces": [
            {
                "name": "official",
                "plugins": [
                    {"id": f"plugin-{index}", "name": f"Plugin {index}"}
                    for index in range(1_916)
                ],
            }
        ]
    }

    result = CapabilitiesManager(storage, server).list_plugins("project")
    plugins = result["marketplaces"][0]["plugins"]

    assert len(plugins) == 1_916
    assert plugins[0]["id"] == "plugin-0"
    assert plugins[-1]["id"] == "plugin-1915"


def test_list_plugins_enforces_total_projection_cap_across_marketplaces(
    tmp_path: Path,
) -> None:
    storage = Storage(tmp_path)
    (storage.workspace_boundary.root / "project").mkdir()
    server = FakeServer()
    server.responses["plugin/list"] = {
        "marketplaces": [
            {
                "name": "first",
                "plugins": [
                    {"id": f"plugin-{index}", "name": f"Plugin {index}"}
                    for index in range(capabilities_module._MAX_PLUGINS - 1)
                ],
            },
            {
                "name": "second",
                "plugins": [
                    {"id": f"second-{index}", "name": f"Second {index}"}
                    for index in range(128)
                ],
            },
            {
                "name": "third",
                "plugins": [{"id": "third-0", "name": "Third 0"}],
            },
        ]
    }

    result = CapabilitiesManager(storage, server).list_plugins("project")
    marketplaces = result["marketplaces"]

    assert capabilities_module._MAX_PLUGINS >= 1_916
    assert [marketplace["name"] for marketplace in marketplaces] == [
        "first",
        "second",
        "third",
    ]
    assert sum(len(marketplace["plugins"]) for marketplace in marketplaces) == (
        capabilities_module._MAX_PLUGINS
    )
    assert len(marketplaces[0]["plugins"]) == capabilities_module._MAX_PLUGINS - 1
    assert marketplaces[0]["plugins"][-1]["id"] == (
        f"plugin-{capabilities_module._MAX_PLUGINS - 2}"
    )
    assert [plugin["id"] for plugin in marketplaces[1]["plugins"]] == ["second-0"]
    assert marketplaces[2]["plugins"] == []


def test_agents_are_atomic_backed_up_privately_and_gate_mutations(
    tmp_path: Path,
) -> None:
    storage = Storage(tmp_path)
    workspace = storage.workspace_boundary.root / "project"
    workspace.mkdir()
    manager = WorkspaceAgentsManager(storage, Gate())
    assert manager.read("prj_one")["exists"] is False
    manager.write("prj_one", "Do not leak <script>\n")
    manager.write("prj_one", "Updated")
    record = manager.read("prj_one")
    assert record["content"] == "Updated"
    assert record["backups"]
    assert not list(workspace.glob("*backup*"))
    assert list((storage.root / "agent-backups" / "prj_one").glob("*.bak"))
    manager.delete("prj_one")
    assert manager.read("prj_one")["exists"] is False


def test_agents_reject_traversal_symlink_and_busy_gate(tmp_path: Path) -> None:
    storage = Storage(tmp_path)
    (storage.workspace_boundary.root / "project").mkdir()
    manager = WorkspaceAgentsManager(storage, Gate(blocked=True))
    with pytest.raises(AgentsMutationConflictError):
        manager.write("prj_one", "blocked")
    storage.project.root_path = "../private"
    with pytest.raises(Exception):
        manager.read("prj_one")
    storage.project.root_path = "project"
    if hasattr(__import__("os"), "symlink") and __import__("sys").platform == "win32":
        pytest.skip("symlink fixture requires elevated Windows privileges")
    target = storage.workspace_boundary.root / "project" / "AGENTS.md"
    target.symlink_to(tmp_path / "outside")
    with pytest.raises(Exception):
        WorkspaceAgentsManager(storage).write("prj_one", "unsafe")


def test_managed_skill_create_delete_writes_escaped_frontmatter(tmp_path: Path) -> None:
    storage = Storage(tmp_path)
    (storage.workspace_boundary.root / "project").mkdir()
    server = FakeServer()
    manager = CapabilitiesManager(storage, server, Gate())
    created = manager.create_skill(
        project_id="prj_one",
        name="triage",
        description='line "one"\nline two',
        instructions="Follow the checklist.\n",
    )
    assert created["name"] == "triage"
    skill_file = (
        storage.workspace_boundary.root
        / "project"
        / ".agents"
        / "skills"
        / "triage"
        / "SKILL.md"
    )
    content = skill_file.read_text(encoding="utf-8")
    assert 'description: "line \\"one\\"\\nline two"' in content
    assert content.endswith("Follow the checklist.\n")
    manager.delete_skill(project_id="prj_one", name="triage")
    assert not skill_file.exists()


def test_global_agents_are_fixed_to_codex_home_and_backed_up(tmp_path: Path) -> None:
    storage = Storage(tmp_path)
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    manager = WorkspaceAgentsManager(storage, Gate(), codex_home=codex_home)
    manager.write_global("global one")
    manager.write_global("global two")
    assert manager.read_global()["content"] == "global two"
    if sys.platform != "win32":
        assert (codex_home / "AGENTS.md").stat().st_mode & 0o777 == 0o600
    assert manager.read_global()["backups"]
    manager.delete_global()
    assert not (codex_home / "AGENTS.md").exists()
