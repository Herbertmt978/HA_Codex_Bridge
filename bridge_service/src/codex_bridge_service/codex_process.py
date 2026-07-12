import os
import sys
from pathlib import Path


def resolve_codex_home(
    configured_home: Path | str | None,
    codex_command: str,
) -> Path:
    if configured_home:
        return Path(configured_home)

    inherited_home = os.environ.get("CODEX_HOME")
    if inherited_home:
        return Path(inherited_home)

    command_path = Path(codex_command)
    if command_path.suffix:
        for parent in command_path.parents:
            if parent.name == ".codex":
                return parent
    return Path.home() / ".codex"


def codex_command_prefix(codex_command: str) -> list[str]:
    target = Path(codex_command)
    suffix = target.suffix.lower()
    if suffix == ".ps1":
        return ["powershell", "-File", str(target)]
    if suffix == ".py":
        return [sys.executable, str(target)]
    return [str(target)]


def codex_subprocess_environment(codex_home: Path | str | None = None) -> dict[str, str]:
    environment = {
        name: value
        for name, value in os.environ.items()
        if not name.upper().startswith("CODEX_BRIDGE_")
    }
    if codex_home is not None:
        environment["CODEX_HOME"] = str(codex_home)
    return environment
