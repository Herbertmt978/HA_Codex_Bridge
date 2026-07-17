#!/usr/bin/env python3
"""Publish the Integration release paired with an exact App commit.

This module deliberately uses the GitHub REST API instead of git/gh.  Every
object is validated after mutation, and immutable tags are never repaired.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import sys
import time
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


SHA_RE = re.compile(r"^[0-9a-f]{40}$")
VERSION_RE = re.compile(r"^(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)$")
MAX_TAG_DEPTH = 8
TAG_READBACK_RETRY_DELAYS = (0.25, 0.5, 1.0, 2.0)


class PublishError(RuntimeError):
    """The GitHub API state is absent, malformed, or inconsistent."""


@dataclass(frozen=True)
class Response:
    status: int
    body: Any


class GitHubClient:
    """Small, strict REST client with an injectable transport for tests."""

    def __init__(
        self,
        repository: str,
        token: str,
        *,
        api_url: str = "https://api.github.com",
        transport: Callable[[Request], tuple[int, bytes]] | None = None,
    ) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
            raise PublishError("GITHUB_REPOSITORY is malformed")
        if not token:
            raise PublishError("GitHub token is missing")
        self.repository = repository
        self.token = token
        self.api_url = api_url.rstrip("/")
        self._transport = transport or self._default_transport

    def _default_transport(self, request: Request) -> tuple[int, bytes]:
        try:
            with urlopen(request, timeout=30) as response:
                return int(response.status), response.read()
        except HTTPError as exc:
            return int(exc.code), exc.read()
        except (OSError, URLError) as exc:
            raise PublishError("GitHub API request failed") from exc

    def request(self, method: str, path: str, payload: Mapping[str, Any] | None = None) -> Response:
        if not path.startswith("/") or "//" in path[1:]:
            raise PublishError("invalid GitHub API path")
        data = None if payload is None else json.dumps(payload, separators=(",", ":")).encode()
        request = Request(
            f"{self.api_url}{path}",
            data=data,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "User-Agent": "ha-codex-bridge-release/1",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            method=method,
        )
        status, raw = self._transport(request)
        try:
            body = json.loads(raw.decode("utf-8")) if raw else None
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PublishError(f"GitHub API returned malformed JSON ({status})") from exc
        return Response(status, body)


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PublishError(f"GitHub API {label} is malformed")
    return value


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise PublishError(f"GitHub API {label} is malformed")
    return value


def _tag_ref_path(version: str) -> str:
    # Stable versions contain no URL-sensitive characters.
    if VERSION_RE.fullmatch(version) is None:
        raise PublishError("release version is malformed")
    return f"/repos/{{repo}}/git/ref/tags/{version}"


def _validate_version(version: str) -> None:
    if VERSION_RE.fullmatch(version) is None:
        raise PublishError("release version is malformed")


def _resolve_tag_commit(client: GitHubClient, version: str, *, max_depth: int = MAX_TAG_DEPTH) -> str | None:
    """Resolve a lightweight or annotated tag to its commit, bounded safely."""
    if max_depth < 1:
        raise PublishError("tag peeling depth is invalid")
    path = _tag_ref_path(version).format(repo=client.repository)
    response = client.request("GET", path)
    if response.status == 404:
        return None
    if response.status != 200:
        raise PublishError(f"could not inspect Integration tag (HTTP {response.status})")
    reference = _mapping(response.body, "tag reference")
    if reference.get("ref") != f"refs/tags/{version}":
        raise PublishError("GitHub API returned the wrong tag reference")
    obj = _mapping(reference.get("object"), "tag reference object")
    current_type = _string(obj.get("type"), "tag reference object type")
    current_sha = _string(obj.get("sha"), "tag reference object SHA")
    if not SHA_RE.fullmatch(current_sha):
        raise PublishError("GitHub API tag object SHA is malformed")
    for _depth in range(max_depth):
        if current_type == "commit":
            return current_sha
        if current_type != "tag":
            raise PublishError("Integration tag must resolve to a commit or tag object")
        tag_response = client.request(
            "GET", f"/repos/{client.repository}/git/tags/{current_sha}"
        )
        if tag_response.status != 200:
            raise PublishError(f"could not peel Integration tag object (HTTP {tag_response.status})")
        tag = _mapping(tag_response.body, "annotated tag object")
        target = _mapping(tag.get("object"), "annotated tag target")
        current_type = _string(target.get("type"), "annotated tag target type")
        current_sha = _string(target.get("sha"), "annotated tag target SHA")
        if not SHA_RE.fullmatch(current_sha):
            raise PublishError("GitHub API annotated tag target SHA is malformed")
    if current_type == "commit":
        return current_sha
    raise PublishError("annotated Integration tag exceeds peeling depth")


def _release(client: GitHubClient, version: str) -> Mapping[str, Any] | None:
    response = client.request("GET", f"/repos/{client.repository}/releases/tags/{version}")
    if response.status == 404:
        return None
    if response.status != 200:
        raise PublishError(f"could not inspect Integration release (HTTP {response.status})")
    return _mapping(response.body, "release")


def _verify_release(release: Mapping[str, Any], version: str) -> None:
    if (
        release.get("tag_name") != version
        or release.get("draft") is not False
        or release.get("prerelease") is not False
    ):
        raise PublishError("Integration release metadata is not an exact published release")


def _verify_postconditions(
    client: GitHubClient, version: str, expected_sha: str
) -> None:
    """Re-read both public objects after all release mutations."""
    release = _release(client, version)
    if release is None:
        raise PublishError("Integration release disappeared during post-verification")
    _verify_release(release, version)
    if _resolve_tag_commit(client, version) != expected_sha:
        raise PublishError("Integration tag post-verification failed")


def _ensure_tag(client: GitHubClient, version: str, expected_sha: str, *, existing_release: bool) -> None:
    actual = _resolve_tag_commit(client, version)
    if actual is not None:
        if actual != expected_sha:
            raise PublishError(f"Integration tag {version} points to an unexpected commit")
        return
    if existing_release:
        raise PublishError("published Integration release exists but its tag is missing")
    response = client.request(
        "POST",
        f"/repos/{client.repository}/git/refs",
        {"ref": f"refs/tags/{version}", "sha": expected_sha},
    )
    if response.status == 201:
        created = _mapping(response.body, "created tag reference")
        if created.get("ref") != f"refs/tags/{version}":
            raise PublishError("GitHub API created the wrong tag reference")
        created_object = _mapping(created.get("object"), "created tag reference object")
        if created_object.get("type") != "commit" or created_object.get("sha") != expected_sha:
            raise PublishError("GitHub API created an unexpected tag object")
    elif response.status not in (409, 422):
        raise PublishError(f"could not create Integration tag (HTTP {response.status})")
    # A newly-created ref can take a moment to become readable through GitHub's
    # ref endpoint. Poll that read-after-write window briefly, accepting only the
    # exact immutable commit. A visible mismatch still fails immediately and is
    # never moved or repaired.
    actual = _resolve_tag_commit(client, version)
    for delay in TAG_READBACK_RETRY_DELAYS:
        if actual is not None:
            break
        time.sleep(delay)
        actual = _resolve_tag_commit(client, version)
    if actual != expected_sha:
        raise PublishError("Integration tag creation race did not resolve to expected commit")


def build_release_notes(changelog: str, version: str, run_url: str) -> str:
    """Build notes from exactly one canonical matching CHANGELOG section."""
    if VERSION_RE.fullmatch(version) is None or not run_url.startswith(("https://", "http://")):
        raise PublishError("release notes inputs are malformed")
    matches = list(re.finditer(rf"(?m)^## {re.escape(version)}[ \t]*$", changelog))
    if len(matches) != 1:
        raise PublishError("CHANGELOG has no unique canonical section for release version")
    start = matches[0].end()
    following = re.search(r"(?m)^## ", changelog[start:])
    end = start + following.start() if following else len(changelog)
    section = changelog[start:end].strip()
    if not section:
        raise PublishError("CHANGELOG release section is empty")
    return f"## Codex Bridge {version}\n\n{section}\n\nPublished from the signed App workflow: {run_url}\n"


def publish(*, client: GitHubClient, version: str, expected_sha: str, changelog: str, run_url: str) -> None:
    _validate_version(version)
    if SHA_RE.fullmatch(expected_sha) is None:
        raise PublishError("expected commit SHA must be a full lowercase SHA")
    existing = _release(client, version)
    _ensure_tag(client, version, expected_sha, existing_release=existing is not None)
    if existing is not None:
        _verify_release(existing, version)
    else:
        notes = build_release_notes(changelog, version, run_url)
        response = client.request(
            "POST",
            f"/repos/{client.repository}/releases",
            {
                "tag_name": version,
                "target_commitish": expected_sha,
                "name": f"Codex Bridge {version}",
                "body": notes,
                "draft": False,
                "prerelease": False,
            },
        )
        if response.status == 201:
            _verify_release(_mapping(response.body, "created release"), version)
        elif response.status not in (409, 422):
            raise PublishError(
                f"could not create Integration release (HTTP {response.status})"
            )
        # A 409/422 can be a concurrent creator or a validation failure. The
        # final readback below accepts it only if the exact published release
        # now exists.
    _verify_postconditions(client, version, expected_sha)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--changelog", type=Path, default=Path("codex_bridge_app/CHANGELOG.md"))
    args = parser.parse_args(argv)
    try:
        repository = os.environ["GITHUB_REPOSITORY"]
        token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""
        version = os.environ["VERSION"]
        expected_sha = os.environ["EXPECTED_SHA"]
        server = os.environ.get("GITHUB_SERVER_URL", "https://github.com").rstrip("/")
        run_url = f"{server}/{repository}/actions/runs/{os.environ['GITHUB_RUN_ID']}"
        changelog = args.changelog.read_text(encoding="utf-8")
        publish(
            client=GitHubClient(repository, token, api_url=os.environ.get("GITHUB_API_URL", "https://api.github.com")),
            version=version,
            expected_sha=expected_sha,
            changelog=changelog,
            run_url=run_url,
        )
    except (KeyError, OSError, PublishError) as exc:
        print(f"Integration release failed: {exc}", file=sys.stderr)
        return 1
    print(f"Verified paired Integration release {version} at {expected_sha}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
