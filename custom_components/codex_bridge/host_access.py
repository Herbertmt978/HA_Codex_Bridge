"""Private Supervisor discovery validation for the optional host companion."""

from ipaddress import ip_address, ip_network
import re

from .protocol import EndpointError, validate_bridge_token

HOST_WORKER_KEY = "host_access_worker"
SUPERVISOR_SLUG_KEY = "supervisor_slug"


def validate_host_discovery(info, entry) -> dict[str, object]:
    if not isinstance(info.config, dict):
        raise EndpointError("payload_invalid")
    payload = dict(info.config)
    # HA's Supervisor discovery adapter adds the App's display name. It is
    # presentation metadata; authority comes from the wrapper's repository slug.
    payload.pop("addon", None)
    bridge_slug = entry.data.get(SUPERVISOR_SLUG_KEY, "")
    suffix = "_codex_bridge"
    if not isinstance(bridge_slug, str) or not bridge_slug.endswith(suffix) or info.slug != bridge_slug[:-len(suffix)] + "_codex_host_access":
        raise EndpointError("payload_invalid")
    if set(payload) != {
        "kind", "host", "port", "token", "api", "companion_id", "publication_id",
    }:
        raise EndpointError("payload_invalid")
    if payload["kind"] != "host_access" or type(payload["port"]) is not int or payload["port"] != 8767:
        raise EndpointError("payload_invalid")
    if payload["api"] != {"minimum": 1, "maximum": 1} or any(
        type(value) is not int for value in payload["api"].values()
    ):
        raise EndpointError("payload_invalid")
    identity = payload["companion_id"]
    if not isinstance(identity, str) or not re.fullmatch(r"[a-f0-9]{32}", identity):
        raise EndpointError("payload_invalid")
    marker = payload["publication_id"]
    if not isinstance(marker, str) or not re.fullmatch(r"[a-f0-9]{32}", marker):
        raise EndpointError("payload_invalid")
    try:
        address = ip_address(payload["host"])
    except (ValueError, TypeError):
        raise EndpointError("payload_invalid") from None
    if not any(address in ip_network(network) for network in (
        "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7",
    )):
        raise EndpointError("payload_invalid")
    return {
        "host": str(address), "port": 8767,
        "token": validate_bridge_token(payload["token"]), "companion_id": identity,
    }
