import os
import re
from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, field_validator

_SEMVER_PATTERN = re.compile(
    r"""
    (?:0|[1-9][0-9]*)
    \.(?:0|[1-9][0-9]*)
    \.(?:0|[1-9][0-9]*)
    (?:-
        (?:0|[1-9][0-9]*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*)
        (?:\.(?:0|[1-9][0-9]*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*))*
    )?
    (?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?
    \Z
    """,
    re.ASCII | re.VERBOSE,
)
_GIT_REVISION_PATTERN = re.compile(
    r"(?:[A-Fa-f0-9]{40}|[A-Fa-f0-9]{64})\Z",
    re.ASCII,
)
_SHA256_REVISION_PATTERN = re.compile(
    r"sha256:[A-Fa-f0-9]{64}\Z",
    re.ASCII | re.IGNORECASE,
)
_RELEASE_LOCK_DIGEST_PATTERN = re.compile(r"[A-Fa-f0-9]{64}\Z", re.ASCII)
_SUPPORTED_ARCHITECTURES = frozenset({"amd64", "aarch64"})


class BuildInfo(BaseModel):
    model_config = ConfigDict(frozen=True)

    app_version: str | None = None
    bridge_version: str | None = None
    codex_version: str | None = None
    image_revision: str | None = None
    architecture: str = "unknown"
    release_lock_digest: str | None = None
    sandbox_contract_version: int | None = None

    @field_validator("app_version", "bridge_version", "codex_version", mode="before")
    @classmethod
    def validate_version(cls, value: object) -> str | None:
        if (
            not isinstance(value, str)
            or len(value) > 64
            or _SEMVER_PATTERN.fullmatch(value) is None
        ):
            return None
        return value

    @field_validator("image_revision", mode="before")
    @classmethod
    def validate_image_revision(cls, value: object) -> str | None:
        if (
            not isinstance(value, str)
            or (
                _GIT_REVISION_PATTERN.fullmatch(value) is None
                and _SHA256_REVISION_PATTERN.fullmatch(value) is None
            )
        ):
            return None
        return value.lower()

    @field_validator("architecture", mode="before")
    @classmethod
    def validate_architecture(cls, value: object) -> str:
        if isinstance(value, str) and value in _SUPPORTED_ARCHITECTURES:
            return value
        return "unknown"

    @field_validator("release_lock_digest", mode="before")
    @classmethod
    def validate_release_lock_digest(cls, value: object) -> str | None:
        if (
            not isinstance(value, str)
            or _RELEASE_LOCK_DIGEST_PATTERN.fullmatch(value) is None
        ):
            return None
        return value.lower()

    @field_validator("sandbox_contract_version", mode="before")
    @classmethod
    def validate_sandbox_contract_version(cls, value: object) -> int | None:
        if type(value) is int:
            return value if 1 <= value <= 999 else None
        if isinstance(value, str) and re.fullmatch(r"[1-9][0-9]{0,2}", value):
            return int(value)
        return None

    @classmethod
    def from_environment(
        cls,
        environment: Mapping[str, str] | None = None,
    ) -> "BuildInfo":
        source = os.environ if environment is None else environment
        return cls(
            app_version=source.get("CODEX_BRIDGE_APP_VERSION"),
            bridge_version=source.get("CODEX_BRIDGE_VERSION"),
            codex_version=source.get("CODEX_BRIDGE_CODEX_VERSION"),
            image_revision=source.get("CODEX_BRIDGE_IMAGE_REVISION"),
            architecture=source.get("CODEX_BRIDGE_ARCH"),
            release_lock_digest=source.get("CODEX_BRIDGE_RELEASE_LOCK_DIGEST"),
            sandbox_contract_version=source.get(
                "CODEX_BRIDGE_SANDBOX_CONTRACT_VERSION"
            ),
        )
