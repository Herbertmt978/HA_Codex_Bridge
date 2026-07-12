import os
from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict


class BuildInfo(BaseModel):
    model_config = ConfigDict(frozen=True)

    app_version: str | None = None
    bridge_version: str | None = None
    codex_version: str | None = None
    image_revision: str | None = None
    architecture: str = "unknown"
    release_lock_digest: str | None = None

    @classmethod
    def from_environment(
        cls,
        environment: Mapping[str, str] | None = None,
    ) -> "BuildInfo":
        source = os.environ if environment is None else environment
        return cls(
            app_version=_optional_value(source.get("CODEX_BRIDGE_APP_VERSION")),
            bridge_version=_optional_value(source.get("CODEX_BRIDGE_VERSION")),
            codex_version=_optional_value(source.get("CODEX_BRIDGE_CODEX_VERSION")),
            image_revision=_optional_value(source.get("CODEX_BRIDGE_IMAGE_REVISION")),
            architecture=_optional_value(source.get("CODEX_BRIDGE_ARCH")) or "unknown",
            release_lock_digest=_optional_value(
                source.get("CODEX_BRIDGE_RELEASE_LOCK_DIGEST")
            ),
        )


def _optional_value(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None
