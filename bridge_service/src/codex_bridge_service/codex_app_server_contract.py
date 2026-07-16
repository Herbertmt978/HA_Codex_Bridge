from __future__ import annotations

import hashlib
import json
import re
import stat
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any, Literal

from jsonschema import Draft7Validator
from jsonschema.exceptions import SchemaError, ValidationError

_CONTRACT_FILE = "codex_app_server_contract.json"
_STABLE_SCHEMA_FILE = "codex_app_server_protocol.schema.json"
_V2_SCHEMA_FILE = "codex_app_server_protocol.v2.schema.json"
_SOURCE_STABLE_SCHEMA_FILE = "codex_app_server_protocol.schemas.json"
_SOURCE_V2_SCHEMA_FILE = "codex_app_server_protocol.v2.schemas.json"
_CONTRACT_VERSION = 1
_MAX_SCHEMA_FILES = 512
_MAX_SCHEMA_FILE_BYTES = 8 * 1024 * 1024
_MAX_SCHEMA_BUNDLE_BYTES = 32 * 1024 * 1024
_MAX_MANIFEST_BYTES = 256 * 1024
_MAX_METHODS_PER_DIRECTION = 512
_MAX_METHOD_BYTES = 512
_VERSION_PATTERN = re.compile(r"codex-cli [0-9]+\.[0-9]+\.[0-9]+\Z", re.ASCII)
_DIGEST_PATTERN = re.compile(r"[a-f0-9]{64}\Z", re.ASCII)
_METHOD_PATTERN = re.compile(r"[^\s\x00-\x1f\x7f]{1,512}\Z")
_DIRECTION_FILES: dict[str, str] = {
    "clientRequests": "ClientRequest.json",
    "clientNotifications": "ClientNotification.json",
    "serverRequests": "ServerRequest.json",
    "serverNotifications": "ServerNotification.json",
}
_CLIENT_RESPONSE_TYPES = {
    "initialize": "InitializeResponse",
    "account/read": "GetAccountResponse",
    "account/login/start": "LoginAccountResponse",
    "account/login/cancel": "CancelLoginAccountResponse",
    "account/logout": "LogoutAccountResponse",
    "account/rateLimits/read": "GetAccountRateLimitsResponse",
    "account/usage/read": "GetAccountTokenUsageResponse",
    "model/list": "ModelListResponse",
    "modelProvider/capabilities/read": "ModelProviderCapabilitiesReadResponse",
    "thread/start": "ThreadStartResponse",
    "thread/resume": "ThreadResumeResponse",
    "thread/fork": "ThreadForkResponse",
    "thread/read": "ThreadReadResponse",
    "thread/list": "ThreadListResponse",
    "turn/start": "TurnStartResponse",
    "turn/interrupt": "TurnInterruptResponse",
    "turn/steer": "TurnSteerResponse",
    "config/read": "ConfigReadResponse",
    "config/value/write": "ConfigWriteResponse",
    "config/batchWrite": "ConfigWriteResponse",
    "config/mcpServer/reload": "McpServerRefreshResponse",
    "mcpServerStatus/list": "ListMcpServerStatusResponse",
    "mcpServer/oauth/login": "McpServerOauthLoginResponse",
    "skills/list": "SkillsListResponse",
    "skills/config/write": "SkillsConfigWriteResponse",
    "skills/extraRoots/set": "SkillsExtraRootsSetResponse",
    "plugin/list": "PluginListResponse",
    "plugin/installed": "PluginInstalledResponse",
    "plugin/read": "PluginReadResponse",
    "plugin/install": "PluginInstallResponse",
    "plugin/uninstall": "PluginUninstallResponse",
    "plugin/skill/read": "PluginSkillReadResponse",
    "marketplace/add": "MarketplaceAddResponse",
    "marketplace/remove": "MarketplaceRemoveResponse",
    "marketplace/upgrade": "MarketplaceUpgradeResponse",
}
_SERVER_RESPONSE_TYPES = {
    "item/commandExecution/requestApproval": "CommandExecutionRequestApprovalResponse",
    "item/fileChange/requestApproval": "FileChangeRequestApprovalResponse",
    "item/tool/requestUserInput": "ToolRequestUserInputResponse",
    "mcpServer/elicitation/request": "McpServerElicitationRequestResponse",
    "item/permissions/requestApproval": "PermissionsRequestApprovalResponse",
    "item/tool/call": "DynamicToolCallResponse",
    "account/chatgptAuthTokens/refresh": "ChatgptAuthTokensRefreshResponse",
    "attestation/generate": "AttestationGenerateResponse",
    "applyPatchApproval": "ApplyPatchApprovalResponse",
    "execCommandApproval": "ExecCommandApprovalResponse",
}

ProtocolDirection = Literal[
    "clientRequests",
    "clientNotifications",
    "serverRequests",
    "serverNotifications",
]


class ProtocolContractError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class AppServerProtocolContract:
    codex_version: str
    schema_bundle_sha256: str
    stable_schema_sha256: str
    v2_schema_sha256: str
    client_requests: frozenset[str]
    client_notifications: frozenset[str]
    server_requests: frozenset[str]
    server_notifications: frozenset[str]

    def permits(self, direction: ProtocolDirection, method: str) -> bool:
        methods = {
            "clientRequests": self.client_requests,
            "clientNotifications": self.client_notifications,
            "serverRequests": self.server_requests,
            "serverNotifications": self.server_notifications,
        }[direction]
        return method in methods

    def require(self, direction: ProtocolDirection, method: str) -> None:
        if not self.permits(direction, method):
            raise ProtocolContractError("method is absent from the locked Codex schema")

    def response_type(
        self,
        direction: Literal["client", "server"],
        method: str,
    ) -> str:
        mapping = (
            _CLIENT_RESPONSE_TYPES if direction == "client" else _SERVER_RESPONSE_TYPES
        )
        response_type = mapping.get(method)
        if response_type is None:
            raise ProtocolContractError("method has no locked response schema")
        return response_type

    def to_manifest(self) -> dict[str, Any]:
        return {
            "contractVersion": _CONTRACT_VERSION,
            "codexVersion": self.codex_version,
            "schemaBundleSha256": self.schema_bundle_sha256,
            "stableSchemaSha256": self.stable_schema_sha256,
            "v2SchemaSha256": self.v2_schema_sha256,
            "clientRequests": sorted(self.client_requests),
            "clientNotifications": sorted(self.client_notifications),
            "serverRequests": sorted(self.server_requests),
            "serverNotifications": sorted(self.server_notifications),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_manifest(), indent=2, sort_keys=True) + "\n"


def extract_protocol_contract(
    schema_root: Path | str,
    *,
    codex_version: str,
) -> AppServerProtocolContract:
    version = _validate_codex_version(codex_version)
    root = Path(schema_root)
    try:
        if not root.is_absolute() or not root.is_dir() or root.is_symlink():
            raise ProtocolContractError(
                "schema root must be an absolute real directory"
            )
    except OSError:
        raise ProtocolContractError("schema root is unavailable") from None

    schema_files, bundle_digest = _inspect_schema_bundle(root)
    method_sets: dict[str, frozenset[str]] = {}
    for direction, filename in _DIRECTION_FILES.items():
        content = schema_files.get(filename)
        if content is None:
            raise ProtocolContractError("required app-server schema is missing")
        method_sets[direction] = _extract_methods(content)

    stable_schema = _canonical_schema(
        _required_schema(schema_files, _SOURCE_STABLE_SCHEMA_FILE)
    )
    v2_schema = _canonical_schema(
        _required_schema(schema_files, _SOURCE_V2_SCHEMA_FILE)
    )
    _validate_runtime_schema_contract(stable_schema, v2_schema, method_sets)
    return AppServerProtocolContract(
        codex_version=version,
        schema_bundle_sha256=bundle_digest,
        stable_schema_sha256=hashlib.sha256(stable_schema).hexdigest(),
        v2_schema_sha256=hashlib.sha256(v2_schema).hexdigest(),
        client_requests=method_sets["clientRequests"],
        client_notifications=method_sets["clientNotifications"],
        server_requests=method_sets["serverRequests"],
        server_notifications=method_sets["serverNotifications"],
    )


def parse_protocol_contract(content: bytes | str) -> AppServerProtocolContract:
    if isinstance(content, str):
        encoded = content.encode("utf-8")
    elif isinstance(content, bytes):
        encoded = content
    else:
        raise ProtocolContractError("protocol contract must be UTF-8 JSON")
    if not encoded or len(encoded) > _MAX_MANIFEST_BYTES:
        raise ProtocolContractError("protocol contract size is invalid")
    try:
        value = json.loads(
            encoded.decode("utf-8", errors="strict"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError):
        raise ProtocolContractError("protocol contract JSON is invalid") from None
    if not isinstance(value, dict) or set(value) != {
        "contractVersion",
        "codexVersion",
        "schemaBundleSha256",
        "stableSchemaSha256",
        "v2SchemaSha256",
        *_DIRECTION_FILES,
    }:
        raise ProtocolContractError("protocol contract fields are invalid")
    if value["contractVersion"] != _CONTRACT_VERSION:
        raise ProtocolContractError("protocol contract version is unsupported")
    digests = (
        value["schemaBundleSha256"],
        value["stableSchemaSha256"],
        value["v2SchemaSha256"],
    )
    if any(
        not isinstance(digest, str) or _DIGEST_PATTERN.fullmatch(digest) is None
        for digest in digests
    ):
        raise ProtocolContractError("protocol schema digest is invalid")
    return AppServerProtocolContract(
        codex_version=_validate_codex_version(value["codexVersion"]),
        schema_bundle_sha256=value["schemaBundleSha256"],
        stable_schema_sha256=value["stableSchemaSha256"],
        v2_schema_sha256=value["v2SchemaSha256"],
        client_requests=_parse_method_list(value["clientRequests"]),
        client_notifications=_parse_method_list(value["clientNotifications"]),
        server_requests=_parse_method_list(value["serverRequests"]),
        server_notifications=_parse_method_list(value["serverNotifications"]),
    )


def load_bundled_protocol_contract() -> AppServerProtocolContract:
    resource = files("codex_bridge_service").joinpath(_CONTRACT_FILE)
    try:
        content = resource.read_bytes()
    except (FileNotFoundError, OSError):
        raise ProtocolContractError(
            "bundled protocol contract is unavailable"
        ) from None
    return parse_protocol_contract(content)


class AppServerProtocolValidator:
    def __init__(self, contract: AppServerProtocolContract) -> None:
        stable = _load_bundled_schema(
            _STABLE_SCHEMA_FILE,
            contract.stable_schema_sha256,
        )
        v2 = _load_bundled_schema(_V2_SCHEMA_FILE, contract.v2_schema_sha256)
        stable_definitions = stable.get("definitions")
        v2_definitions = v2.get("definitions")
        if not isinstance(stable_definitions, dict) or not isinstance(
            v2_definitions,
            dict,
        ):
            raise ProtocolContractError("runtime protocol definitions are invalid")
        self.contract = contract
        self._stable_definitions = stable_definitions
        self._v2_definitions = v2_definitions
        try:
            self._client_requests = _method_validator_map(stable, "ClientRequest")
            self._client_notifications = _method_validator_map(
                stable,
                "ClientNotification",
            )
            self._server_requests = _method_validator_map(stable, "ServerRequest")
            self._server_notifications = _method_validator_map(
                stable,
                "ServerNotification",
            )
            self._rpc_error = _definition_validator(stable, "JSONRPCError")
        except SchemaError:
            raise ProtocolContractError("runtime protocol schema is invalid") from None

    def validate_client_request(self, message: object) -> None:
        self._validate_method_message(self._client_requests, message)

    def validate_client_notification(self, message: object) -> None:
        self._validate_method_message(self._client_notifications, message)

    def validate_server_request(self, message: object) -> None:
        self._validate_method_message(self._server_requests, message)

    def validate_server_notification(self, message: object) -> None:
        self._validate_method_message(self._server_notifications, message)

    def validate_client_response(
        self,
        method: str,
        *,
        result: object = None,
        error_message: object = None,
        is_error: bool = False,
    ) -> None:
        if is_error:
            self._validate(self._rpc_error, error_message)
            return
        response_type = self.contract.response_type("client", method)
        self._validate(self._response_validator(response_type), result)

    def validate_server_response(
        self,
        method: str,
        *,
        result: object = None,
        error_message: object = None,
        is_error: bool = False,
    ) -> None:
        if is_error:
            self._validate(self._rpc_error, error_message)
            return
        response_type = self.contract.response_type("server", method)
        self._validate(self._response_validator(response_type), result)

    def _response_validator(self, response_type: str) -> Draft7Validator:
        definitions = (
            self._v2_definitions
            if response_type in self._v2_definitions
            else self._stable_definitions
        )
        if response_type not in definitions:
            raise ProtocolContractError("response schema is absent")
        schema = {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "$ref": f"#/definitions/{response_type}",
            "definitions": definitions,
        }
        try:
            return Draft7Validator(schema)
        except SchemaError:
            raise ProtocolContractError("response schema is invalid") from None

    @staticmethod
    def _validate(validator: Draft7Validator, value: object) -> None:
        try:
            validator.validate(value)
        except (ValidationError, SchemaError):
            raise ProtocolContractError(
                "protocol payload does not match the lock"
            ) from None

    @classmethod
    def _validate_method_message(
        cls,
        validators: dict[str, Draft7Validator],
        value: object,
    ) -> None:
        if not isinstance(value, dict) or not isinstance(value.get("method"), str):
            raise ProtocolContractError("protocol method envelope is invalid")
        validator = validators.get(value["method"])
        if validator is None:
            raise ProtocolContractError("protocol method is absent from the lock")
        cls._validate(validator, value)


def extract_runtime_schema_documents(
    schema_root: Path | str,
) -> tuple[bytes, bytes]:
    root = Path(schema_root)
    if not root.is_absolute() or not root.is_dir():
        raise ProtocolContractError("schema root must be an absolute directory")
    schema_files, _digest = _inspect_schema_bundle(root)
    return (
        _canonical_schema(_required_schema(schema_files, _SOURCE_STABLE_SCHEMA_FILE)),
        _canonical_schema(_required_schema(schema_files, _SOURCE_V2_SCHEMA_FILE)),
    )


def _inspect_schema_bundle(root: Path) -> tuple[dict[str, bytes], str]:
    hasher = hashlib.sha256()
    schema_files: dict[str, bytes] = {}
    total_bytes = 0
    file_count = 0
    try:
        entries = sorted(
            root.rglob("*"),
            key=lambda path: path.relative_to(root).as_posix(),
        )
    except OSError:
        raise ProtocolContractError("schema bundle cannot be enumerated") from None
    paths: list[Path] = []
    for entry in entries:
        try:
            metadata = entry.lstat()
        except OSError:
            raise ProtocolContractError(
                "schema bundle entry cannot be inspected"
            ) from None
        if stat.S_ISDIR(metadata.st_mode) and not entry.is_symlink():
            continue
        if (
            entry.suffix != ".json"
            or not stat.S_ISREG(metadata.st_mode)
            or entry.is_symlink()
        ):
            raise ProtocolContractError("schema bundle contains an unexpected entry")
        paths.append(entry)
    for path in paths:
        try:
            metadata = path.lstat()
        except OSError:
            raise ProtocolContractError(
                "schema bundle entry cannot be inspected"
            ) from None
        if not stat.S_ISREG(metadata.st_mode) or path.is_symlink():
            raise ProtocolContractError("schema bundle contains a non-regular entry")
        if metadata.st_size <= 0 or metadata.st_size > _MAX_SCHEMA_FILE_BYTES:
            raise ProtocolContractError("schema file size is invalid")
        file_count += 1
        total_bytes += metadata.st_size
        if file_count > _MAX_SCHEMA_FILES or total_bytes > _MAX_SCHEMA_BUNDLE_BYTES:
            raise ProtocolContractError("schema bundle exceeds its limit")
        relative = path.relative_to(root).as_posix()
        if relative.startswith("/") or ".." in Path(relative).parts:
            raise ProtocolContractError("schema bundle path is invalid")
        try:
            content = path.read_bytes()
        except OSError:
            raise ProtocolContractError("schema bundle entry cannot be read") from None
        if len(content) != metadata.st_size:
            raise ProtocolContractError("schema bundle changed while being read")
        try:
            parsed = json.loads(
                content.decode("utf-8", errors="strict"),
                object_pairs_hook=_unique_object,
                parse_constant=_reject_constant,
            )
            canonical = json.dumps(
                parsed,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        except (
            UnicodeDecodeError,
            json.JSONDecodeError,
            TypeError,
            ValueError,
            RecursionError,
        ):
            raise ProtocolContractError("schema bundle JSON is invalid") from None
        hasher.update(relative.encode("utf-8"))
        hasher.update(b"\x00")
        hasher.update(len(canonical).to_bytes(8, "big"))
        hasher.update(canonical)
        if "/" not in relative:
            schema_files[relative] = content
    if file_count == 0:
        raise ProtocolContractError("schema bundle is empty")
    return schema_files, hasher.hexdigest()


def _required_schema(schema_files: dict[str, bytes], filename: str) -> bytes:
    content = schema_files.get(filename)
    if content is None:
        raise ProtocolContractError("required runtime schema is missing")
    return content


def _canonical_schema(content: bytes) -> bytes:
    try:
        value = json.loads(
            content.decode("utf-8", errors="strict"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
        return (
            json.dumps(
                value,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        TypeError,
        ValueError,
        RecursionError,
    ):
        raise ProtocolContractError("runtime schema JSON is invalid") from None


def _validate_runtime_schema_contract(
    stable_schema: bytes,
    v2_schema: bytes,
    method_sets: dict[str, frozenset[str]],
) -> None:
    stable = _parse_schema_object(stable_schema)
    v2 = _parse_schema_object(v2_schema)
    stable_definitions = stable.get("definitions")
    v2_definitions = v2.get("definitions")
    if not isinstance(stable_definitions, dict) or not isinstance(v2_definitions, dict):
        raise ProtocolContractError("runtime schema definitions are invalid")
    for definition in (
        "ClientRequest",
        "ClientNotification",
        "ServerRequest",
        "ServerNotification",
        "JSONRPCError",
    ):
        if definition not in stable_definitions:
            raise ProtocolContractError("runtime envelope schema is missing")
    client_response_types = {
        response_type
        for method, response_type in _CLIENT_RESPONSE_TYPES.items()
        if method in method_sets["clientRequests"]
    }
    if not method_sets["serverRequests"].issubset(_SERVER_RESPONSE_TYPES):
        raise ProtocolContractError("a server method has no response schema mapping")
    server_response_types = {
        _SERVER_RESPONSE_TYPES[method] for method in method_sets["serverRequests"]
    }
    available_definitions = set(stable_definitions) | set(v2_definitions)
    if not (client_response_types | server_response_types).issubset(
        available_definitions
    ):
        raise ProtocolContractError("a locked response schema is missing")


def _load_bundled_schema(filename: str, expected_digest: str) -> dict[str, Any]:
    resource = files("codex_bridge_service").joinpath(filename)
    try:
        content = resource.read_bytes()
    except (FileNotFoundError, OSError):
        raise ProtocolContractError("bundled runtime schema is unavailable") from None
    if not content or len(content) > _MAX_SCHEMA_FILE_BYTES:
        raise ProtocolContractError("bundled runtime schema size is invalid")
    if hashlib.sha256(content).hexdigest() != expected_digest:
        raise ProtocolContractError("bundled runtime schema digest differs")
    return _parse_schema_object(content)


def _parse_schema_object(content: bytes) -> dict[str, Any]:
    try:
        value = json.loads(
            content.decode("utf-8", errors="strict"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError):
        raise ProtocolContractError("runtime schema JSON is invalid") from None
    if not isinstance(value, dict):
        raise ProtocolContractError("runtime schema must be an object")
    return value


def _definition_validator(schema: dict[str, Any], definition: str) -> Draft7Validator:
    definitions = schema.get("definitions")
    if not isinstance(definitions, dict) or definition not in definitions:
        raise ProtocolContractError("runtime schema definition is missing")
    validator_schema = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "$ref": f"#/definitions/{definition}",
        "definitions": definitions,
    }
    Draft7Validator.check_schema(validator_schema)
    return Draft7Validator(validator_schema)


def _method_validator_map(
    schema: dict[str, Any],
    definition: str,
) -> dict[str, Draft7Validator]:
    definitions = schema.get("definitions")
    if not isinstance(definitions, dict):
        raise ProtocolContractError("runtime schema definitions are missing")
    union = definitions.get(definition)
    if not isinstance(union, dict) or not isinstance(union.get("oneOf"), list):
        raise ProtocolContractError("runtime method schema is invalid")
    validators: dict[str, Draft7Validator] = {}
    for entry in union["oneOf"]:
        try:
            enum = entry["properties"]["method"]["enum"]
        except (KeyError, TypeError):
            raise ProtocolContractError(
                "runtime method schema entry is invalid"
            ) from None
        if not isinstance(enum, list) or len(enum) != 1 or not isinstance(enum[0], str):
            raise ProtocolContractError("runtime method schema enum is invalid")
        method = enum[0]
        if method in validators:
            raise ProtocolContractError("runtime method schema is duplicated")
        validator_schema = {
            "$schema": "http://json-schema.org/draft-07/schema#",
            **entry,
            "definitions": definitions,
        }
        Draft7Validator.check_schema(validator_schema)
        validators[method] = Draft7Validator(validator_schema)
    if not validators:
        raise ProtocolContractError("runtime method schema is empty")
    return validators


def _extract_methods(content: bytes) -> frozenset[str]:
    try:
        schema = json.loads(
            content.decode("utf-8", errors="strict"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError):
        raise ProtocolContractError("method schema JSON is invalid") from None
    if not isinstance(schema, dict) or not isinstance(schema.get("oneOf"), list):
        raise ProtocolContractError("method schema shape is invalid")
    methods: list[str] = []
    for entry in schema["oneOf"]:
        try:
            enum = entry["properties"]["method"]["enum"]
        except (KeyError, TypeError):
            raise ProtocolContractError("method schema entry is invalid") from None
        if not isinstance(enum, list) or len(enum) != 1:
            raise ProtocolContractError("method schema enum is invalid")
        methods.append(_validate_method(enum[0]))
    result = frozenset(methods)
    if not result or len(result) != len(methods):
        raise ProtocolContractError(
            "method schema contains missing or duplicate methods"
        )
    if len(result) > _MAX_METHODS_PER_DIRECTION:
        raise ProtocolContractError("method schema exceeds its limit")
    return result


def _parse_method_list(value: object) -> frozenset[str]:
    if (
        not isinstance(value, list)
        or not value
        or len(value) > _MAX_METHODS_PER_DIRECTION
    ):
        raise ProtocolContractError("protocol method list is invalid")
    methods = [_validate_method(method) for method in value]
    if methods != sorted(methods) or len(set(methods)) != len(methods):
        raise ProtocolContractError("protocol method list must be sorted and unique")
    return frozenset(methods)


def _validate_codex_version(value: object) -> str:
    if not isinstance(value, str) or _VERSION_PATTERN.fullmatch(value) is None:
        raise ProtocolContractError("Codex version is invalid")
    return value


def _validate_method(value: object) -> str:
    if (
        not isinstance(value, str)
        or len(value.encode("utf-8")) > _MAX_METHOD_BYTES
        or _METHOD_PATTERN.fullmatch(value) is None
    ):
        raise ProtocolContractError("protocol method is invalid")
    return value


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def _reject_constant(_value: str) -> Any:
    raise ValueError("non-standard JSON constant")
