import os
import re
from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, field_validator

_VERSION_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+\-]{0,63}\Z", re.ASCII)
_IMAGE_REVISION_PATTERN = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9._:@+\-]{0,127}\Z",
    re.ASCII,
)
_RELEASE_LOCK_DIGEST_PATTERN = re.compile(r"[A-Fa-f0-9]{64}\Z", re.ASCII)
_EMAIL_PATTERN = re.compile(
    r"[A-Za-z0-9._+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,63}\Z",
    re.ASCII,
)
_SUPPORTED_ARCHITECTURES = frozenset({"amd64", "aarch64"})


class BuildInfo(BaseModel):
    model_config = ConfigDict(frozen=True)

    app_version: str | None = None
    bridge_version: str | None = None
    codex_version: str | None = None
    image_revision: str | None = None
    architecture: str = "unknown"
    release_lock_digest: str | None = None

    @field_validator("app_version", "bridge_version", "codex_version", mode="before")
    @classmethod
    def validate_version(cls, value: object) -> str | None:
        if not isinstance(value, str) or _VERSION_PATTERN.fullmatch(value) is None:
            return None
        return value

    @field_validator("image_revision", mode="before")
    @classmethod
    def validate_image_revision(cls, value: object) -> str | None:
        if (
            not isinstance(value, str)
            or _IMAGE_REVISION_PATTERN.fullmatch(value) is None
            or _EMAIL_PATTERN.fullmatch(value) is not None
        ):
            return None
        return value

    @field_validator("architecture", mode="before")
    @classmethod
    def validate_architecture(cls, value: object) -> str:
        return value if value in _SUPPORTED_ARCHITECTURES else "unknown"

    @field_validator("release_lock_digest", mode="before")
    @classmethod
    def validate_release_lock_digest(cls, value: object) -> str | None:
        if (
            not isinstance(value, str)
            or _RELEASE_LOCK_DIGEST_PATTERN.fullmatch(value) is None
        ):
            return None
        return value.lower()

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
        )
