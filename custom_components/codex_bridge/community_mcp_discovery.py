"""Discover one already-running community HA-MCP Supervisor App safely."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import hashlib
import hmac
import ipaddress
import re
import secrets
from typing import Any
from urllib.parse import urlsplit

from homeassistant.components.hassio import get_supervisor_client
from homeassistant.core import HomeAssistant
from homeassistant.helpers.network import get_url

_TIMEOUT = 8
_EXPECTED_NAME = "Home Assistant MCP Server"
_EXPECTED_REPOSITORY = "homeassistant-ai/ha-mcp"
_SLUG = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}\Z")
_VERSION = re.compile(r"[0-9]{1,5}(?:\.[0-9]{1,5}){0,3}\Z")
_HOST = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?\Z")
_PATH_SEGMENT = re.compile(r"[A-Za-z0-9._~-]{1,128}\Z")
_CONSENT_KEY = secrets.token_bytes(32)
_PUBLIC_MESSAGES = {
    "unavailable": "Community HA-MCP discovery is unavailable.",
    "not_installed": "Install the community HA-MCP App before connecting it.",
    "ambiguous": "More than one community HA-MCP App is installed. Remove the ambiguity before connecting.",
    "stopped": "The community HA-MCP App is installed but is not running.",
    "unsupported": "The installed App metadata could not be verified.",
    "secret_unavailable": "The community HA-MCP App has no usable private connection path.",
    "endpoint_unavailable": "A safe private community HA-MCP endpoint is unavailable.",
}


class CommunityMcpDiscoveryError(RuntimeError):
    """A fixed, safe discovery failure that never includes Supervisor data."""

    def __init__(self, code: str = "unavailable") -> None:
        self.code = code if code in _PUBLIC_MESSAGES else "unavailable"
        super().__init__(_PUBLIC_MESSAGES[self.code])


@dataclass(frozen=True, slots=True)
class CommunityMcpEndpoint:
    """Private connection data plus the safe values needed for administrator consent."""

    slug: str
    name: str
    url: str = field(repr=False)
    version: str | None = None

    @property
    def public_destination(self) -> str:
        """Return only the non-secret scheme, host and port for consent."""
        parsed = urlsplit(self.url)
        host = parsed.hostname or ""
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            rendered_host = host
        else:
            rendered_host = f"[{host}]" if address.version == 6 else host
        port = parsed.port or 9583
        return f"http://{rendered_host}:{port}"

    @property
    def consent_revision(self) -> str:
        """Bind administrator acknowledgement to this exact App and secret URL."""
        return hmac.new(
            _CONSENT_KEY,
            f"{self.slug}\n{self.url}".encode(),
            hashlib.sha256,
        ).hexdigest()


def _value(obj: object, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _repository_matches(value: object, repository_slug: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = urlsplit(value.strip())
        return (
            parsed.scheme == "https"
            and (parsed.hostname or "").lower() == "github.com"
            and parsed.username is None
            and parsed.password is None
            and parsed.query == ""
            and parsed.fragment == ""
            and parsed.port is None
            and parsed.path.rstrip("/").removesuffix(".git") == f"/{_EXPECTED_REPOSITORY}"
            and isinstance(repository_slug, str)
            and bool(_SLUG.fullmatch(repository_slug))
        )
    except (ValueError, AttributeError):
        return False


def _valid_secret_path(value: object) -> str | None:
    if not isinstance(value, str) or not value.startswith("/") or len(value) > 512:
        return None
    # Only literal URL-unreserved path segments are supported. This excludes
    # encoded separators, traversal, controls, query/fragment delimiters and
    # ambiguous URL normalisation while allowing a bounded custom secret path.
    parts = value[1:].split("/")
    if not parts or any(not _PATH_SEGMENT.fullmatch(part) or part in {".", ".."} for part in parts):
        return None
    return value


def _valid_ip(host: str) -> bool:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return bool(address.is_private and not (
        address.is_loopback or address.is_link_local or address.is_multicast
        or address.is_unspecified or address.is_reserved
    ))


def _valid_hostname(host: object) -> str | None:
    if not isinstance(host, str):
        return None
    host = host.rstrip(".")
    if not host or len(host) > 253 or not _HOST.fullmatch(host):
        return None
    if any(not label or len(label) > 63 or label.startswith("-") or label.endswith("-")
           for label in host.split(".")):
        return None
    lowered = host.lower()
    if (
        lowered in {"none", "null", "unknown", "localhost"}
        or lowered.endswith(".localhost")
        or lowered in {"supervisor", "hassio"}
        or lowered.endswith((".supervisor", ".hassio"))
    ):
        return None
    return host


def _host_network_host(hass: HomeAssistant) -> str | None:
    try:
        base = get_url(
            hass,
            prefer_external=False,
            allow_external=False,
            allow_cloud=False,
            allow_ip=True,
        )
        if not isinstance(base, str) or any(ord(char) < 32 for char in base) or "?" in base or "#" in base:
            return None
        parsed = urlsplit(base)
        host = parsed.hostname
        if (
            parsed.scheme not in {"http", "https"}
            or not host
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            return None
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address is not None:
            if not _valid_ip(host):
                return None
            return f"[{host}]" if address.version == 6 else host
        hostname = _valid_hostname(host)
        if hostname is None:
            return None
        return hostname
    except Exception:  # noqa: BLE001 - fixed safe failure for URL helper errors
        return None


def _non_host_network_host(info: object) -> str | None:
    host = _value(info, "hostname")
    if isinstance(host, str) and host:
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            return _valid_hostname(host)
        if not _valid_ip(host):
            return None
        return f"[{host}]" if address.version == 6 else host
    address = _value(info, "ip_address")
    if address is not None:
        host = str(address)
        try:
            parsed_address = ipaddress.ip_address(host)
        except ValueError:
            return None
        if not _valid_ip(host):
            return None
        return f"[{host}]" if parsed_address.version == 6 else host
    return None


async def async_discover_community_mcp(hass: HomeAssistant) -> CommunityMcpEndpoint:
    """Fetch and validate fresh Supervisor metadata for the installed HA-MCP App."""
    try:
        async with asyncio.timeout(_TIMEOUT):
            client = get_supervisor_client(hass)
            addons = client.addons
            installed = await addons.list()
            if not isinstance(installed, (list, tuple)):
                raise CommunityMcpDiscoveryError("unavailable")
            candidates = []
            for row in installed:
                candidate_slug = _value(row, "slug")
                if isinstance(candidate_slug, str) and (
                    candidate_slug in {"ha_mcp", "ha_mcp_dev"}
                    or candidate_slug.endswith("_ha_mcp")
                    or candidate_slug.endswith("_ha_mcp_dev")
                ):
                    candidates.append(row)
            if not candidates:
                raise CommunityMcpDiscoveryError("not_installed")
            if len(candidates) != 1:
                raise CommunityMcpDiscoveryError("ambiguous")

            slug = _value(candidates[0], "slug")
            if not isinstance(slug, str) or not _SLUG.fullmatch(slug):
                raise CommunityMcpDiscoveryError("unsupported")
            info = await addons.addon_info(slug)
            if (
                _value(info, "slug") != slug
                or _value(info, "name") != _EXPECTED_NAME
            ):
                raise CommunityMcpDiscoveryError("unsupported")
            repository_slug = _value(info, "repository")
            if not isinstance(repository_slug, str) or not _SLUG.fullmatch(repository_slug):
                raise CommunityMcpDiscoveryError("unsupported")
            repository = await client.store.repository_info(repository_slug)
            if (
                _value(repository, "slug") != repository_slug
                or not _repository_matches(_value(repository, "url"), repository_slug)
            ):
                raise CommunityMcpDiscoveryError("unsupported")
            if _value(info, "state") != "started":
                raise CommunityMcpDiscoveryError("stopped")

            options = _value(info, "options")
            if not isinstance(options, dict):
                raise CommunityMcpDiscoveryError("secret_unavailable")
            secret_path = _valid_secret_path(options.get("secret_path"))
            if secret_path is None:
                raise CommunityMcpDiscoveryError("secret_unavailable")

            host_network = _value(info, "host_network")
            if type(host_network) is not bool:
                raise CommunityMcpDiscoveryError("unsupported")
            if host_network:
                host = _host_network_host(hass)
                network = _value(info, "network")
                published_port = (
                    network.get("9583/tcp", network.get("9583"))
                    if isinstance(network, dict)
                    else None
                )
                # Quick connect supports the documented fixed port only. A
                # missing or changed host publication uses manual setup.
                if type(published_port) is not int or published_port != 9583:
                    raise CommunityMcpDiscoveryError("endpoint_unavailable")
            else:
                host = _non_host_network_host(info)
            if host is None:
                raise CommunityMcpDiscoveryError("endpoint_unavailable")

            version = _value(info, "version")
            if not isinstance(version, str) or not _VERSION.fullmatch(version):
                version = None
            return CommunityMcpEndpoint(
                slug=slug,
                name="ha-community-" + hashlib.sha256(slug.encode()).hexdigest()[:12],
                url=f"http://{host}:9583{secret_path}",
                version=version,
            )
    except CommunityMcpDiscoveryError:
        raise
    except TimeoutError:
        raise CommunityMcpDiscoveryError("unavailable") from None
    except Exception:  # noqa: BLE001 - Supervisor/network errors are never surfaced
        raise CommunityMcpDiscoveryError("unavailable") from None
