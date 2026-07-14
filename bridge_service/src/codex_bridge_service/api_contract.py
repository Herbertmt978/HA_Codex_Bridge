from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict

API_CURRENT = 1
API_MINIMUM = 1
API_MAXIMUM = 1
LEGACY_API_VERSION = 0


class ApiContractRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    current: int
    minimum: int
    maximum: int
    legacy_version: int
    legacy_supported: bool


class ProblemRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: Literal["api_incompatible"] = "api_incompatible"
    status: Literal[409] = 409
    message: str = "The client and server API ranges are incompatible."
    client_minimum: int | None
    client_maximum: int | None
    server_minimum: int = API_MINIMUM
    server_maximum: int = API_MAXIMUM


API_CONTRACT = ApiContractRecord(
    current=API_CURRENT,
    minimum=API_MINIMUM,
    maximum=API_MAXIMUM,
    legacy_version=LEGACY_API_VERSION,
    legacy_supported=True,
)


def _safe_version(value: object) -> int | None:
    return value if type(value) is int else None


class ApiIncompatibleError(Exception):
    code: ClassVar[str] = "api_incompatible"
    status_code: ClassVar[int] = 409

    def __init__(self, client_minimum: object, client_maximum: object) -> None:
        self.problem = ProblemRecord(
            client_minimum=_safe_version(client_minimum),
            client_maximum=_safe_version(client_maximum),
        )
        super().__init__(
            "API ranges are incompatible "
            f"(client {self.problem.client_minimum!r}-"
            f"{self.problem.client_maximum!r}; "
            f"server {API_MINIMUM}-{API_MAXIMUM})."
        )

    @property
    def client_minimum(self) -> int | None:
        return self.problem.client_minimum

    @property
    def client_maximum(self) -> int | None:
        return self.problem.client_maximum

    @property
    def server_minimum(self) -> int:
        return self.problem.server_minimum

    @property
    def server_maximum(self) -> int:
        return self.problem.server_maximum

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self})"


def negotiate_api(client_min: int, client_max: int) -> int:
    if (
        type(client_min) is not int
        or type(client_max) is not int
        or client_min < 0
        or client_max < 0
        or client_min > client_max
    ):
        raise ApiIncompatibleError(client_min, client_max)

    highest_overlap = min(client_max, API_MAXIMUM)
    if max(client_min, API_MINIMUM) > highest_overlap:
        raise ApiIncompatibleError(client_min, client_max)
    return highest_overlap
