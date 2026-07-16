"""Policy contracts for the repository's GitHub release automation.

These tests intentionally inspect the workflow documents as policy rather than
trying to execute GitHub Actions locally.  Keep the assertions semantic (and
insensitive to YAML formatting) so a harmless workflow refactor does not make
the contract brittle.
"""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any, Iterator

import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
WORKFLOW_NAMES = ("ci", "build-app", "codex-update", "release")
FULL_SHA = re.compile(r"^[^@\s]+@[0-9a-f]{40}$")


class _Yaml11SafeLoader(yaml.SafeLoader):
    """SafeLoader with YAML 1.2-style ``on``/``off`` keys.

    GitHub workflow files use the YAML 1.2 spelling ``on``.  PyYAML's default
    YAML 1.1 resolver turns that key into ``True``; remove only the bool
    resolver so the tests check the document GitHub sees.
    """

    yaml_implicit_resolvers = {
        key: list(value)
        for key, value in yaml.SafeLoader.yaml_implicit_resolvers.items()
    }


for _initial, _resolvers in list(_Yaml11SafeLoader.yaml_implicit_resolvers.items()):
    _Yaml11SafeLoader.yaml_implicit_resolvers[_initial] = [
        (_tag, _pattern)
        for _tag, _pattern in _resolvers
        if _tag != "tag:yaml.org,2002:bool"
    ]

# Retain normal YAML booleans while deliberately not resolving YAML 1.1's
# ``yes``/``no``/``on``/``off`` spellings (GitHub's workflow key is ``on``).
_true_false = re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$")
for _initial in "tTfF":
    _Yaml11SafeLoader.yaml_implicit_resolvers.setdefault(_initial, []).append(
        ("tag:yaml.org,2002:bool", _true_false)
    )


def _load(path: Path) -> dict[str, Any]:
    assert path.is_file(), f"required policy file is missing: {path}"
    value = yaml.load(path.read_text(encoding="utf-8"), Loader=_Yaml11SafeLoader)
    assert isinstance(value, dict), f"{path} must contain a YAML mapping"
    return value


def _workflow(name: str) -> tuple[dict[str, Any], str]:
    path = WORKFLOWS / f"{name}.yml"
    return _load(path), path.read_text(encoding="utf-8")


def _walk_uses(value: Any) -> Iterator[str]:
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "uses" and isinstance(child, str):
                yield child
            yield from _walk_uses(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_uses(child)


def test_all_workflows_parse_with_on_key_and_minimal_defaults() -> None:
    for name in WORKFLOW_NAMES:
        document, _ = _workflow(name)
        assert "on" in document, f"{name} workflow must use the GitHub `on` key"
        assert document.get("permissions") == {}, (
            f"{name} must deny all default token permissions; grant only at job scope"
        )
        concurrency = document.get("concurrency")
        assert concurrency, f"{name} must define concurrency cancellation/isolation"
        assert isinstance(concurrency, (str, dict))


def test_every_external_action_is_pinned_to_a_full_commit_sha() -> None:
    for name in WORKFLOW_NAMES:
        document, _ = _workflow(name)
        unpinned = [
            action
            for action in _walk_uses(document)
            if not action.startswith("./") and not FULL_SHA.fullmatch(action)
        ]
        assert not unpinned, f"{name} has unpinned or non-immutable actions: {unpinned}"


def test_app_build_stages_reproducible_amd64_context_and_uses_official_builder() -> None:
    document, source = _workflow("build-app")
    normalized = source.lower()
    assert "amd64" in normalized, "App build must explicitly target amd64"
    assert "scripts/stage_app_context.py" in normalized
    assert any(
        "lock" in line and ("check" in line or "verif" in line)
        for line in normalized.splitlines()
    ), "App build must run the release-lock check before staging"
    assert any(
        action.startswith("home-assistant/builder@")
        or action.startswith("home-assistant/builder/")
        for action in _walk_uses(document)
    ), "App image must be built with the official Home Assistant builder action"


def test_app_publish_is_main_only_and_uses_exact_config_version_manifest() -> None:
    _, source = _workflow("build-app")
    normalized = source.lower()
    assert "ghcr.io/herbertmt978/ha-codex-bridge-app" in normalized
    assert "config.yaml" in normalized and "version" in normalized
    assert re.search(r"github\.ref\s*[^\n]*refs/heads/main", normalized)
    assert re.search(
        r"(?:imagetools\s+create|manifest\s+create|publish-multi-arch-manifest)",
        normalized,
    )
    assert re.search(r"(?:config.yaml|config_version)[^\n]{0,160}version", normalized)


def test_app_publish_signs_attests_sbom_and_verifies_published_digest() -> None:
    _, source = _workflow("build-app")
    normalized = source.lower()
    assert "cosign" in normalized and re.search(r"cosign[^\n]*(?:sign|verify)", normalized)
    assert "sbom" in normalized
    assert "attest" in normalized
    assert "gh attestation verify" in normalized
    assert "provenance-verification.json" in normalized
    assert re.search(r"(?:imagetools\s+inspect|manifest\s+inspect)", normalized)
    assert "digest" in normalized and "sha256" in normalized


def test_codex_updater_is_scheduled_manual_paused_and_narrowly_scoped() -> None:
    document, source = _workflow("codex-update")
    triggers = document["on"]
    assert isinstance(triggers, dict)
    assert "schedule" in triggers and triggers["schedule"]
    assert "workflow_dispatch" in triggers

    normalized = source.lower()
    assert "codex_update_paused" in normalized
    assert re.search(r"codex_update_paused[^\n]*(?:!=|==|false|true|0|1)", normalized)
    assert "git diff --name-only" in normalized
    assert "codex_bridge_app/codex-release.json" in normalized
    assert "source_sha" in normalized
    assert re.search(
        r"ref:\s*\$\{\{\s*needs\.generate\.outputs\.source_sha\s*\}\}",
        source,
    )
    assert "git ls-remote origin refs/heads/main" in normalized


def test_codex_updater_opens_pr_without_main_push_or_auto_merge() -> None:
    document, source = _workflow("codex-update")
    pull_request_steps = [
        step
        for job in document.get("jobs", {}).values()
        if isinstance(job, dict)
        for step in job.get("steps", [])
        if isinstance(step, dict)
        and str(step.get("uses", "")).startswith("peter-evans/create-pull-request@")
    ]
    assert pull_request_steps, "the updater must submit a reviewable pull request"
    assert pull_request_steps[0].get("with", {}).get("base") == "main", (
        "the updater must explicitly set the PR base because checkout uses an exact SHA"
    )
    normalized = source.lower()
    assert not re.search(r"git\s+push[^\n]*\bmain\b", normalized)
    assert "gh pr merge" not in normalized
    assert "enablepullrequestautomerge" not in normalized


def test_release_validates_main_sha_and_version_without_hacs_release_tag() -> None:
    document, source = _workflow("release")
    push = document["on"]["push"]
    assert push["paths"] == ["codex_bridge_app/config.yaml"], (
        "ordinary main/workflow changes must not fail by trying to republish an "
        "unchanged immutable App tag"
    )
    normalized = source.lower()
    assert (
        "head_sha" in normalized
        or "rev-parse refs/heads/main" in normalized
        or "commits/main" in normalized
    ), "release must bind publication to the exact main commit SHA"
    assert "config.yaml" in normalized and "version" in normalized
    assert not re.search(r"softprops/action-gh-release|gh\s+release\s+create", normalized)
    assert not re.search(r"(?:^|\n)\s*git\s+tag\b", normalized)


def test_ci_uses_the_hash_verified_official_actionlint_binary() -> None:
    _, source = _workflow("ci")
    normalized = source.lower()
    assert "rhysd/actionlint/releases/download/v${actionlint_version}" in normalized
    assert "sha256sum --check --strict" in normalized
    assert "npx --yes actionlint" not in normalized


def test_ci_repository_validators_use_digest_pinned_images() -> None:
    hacs_action = _load(ROOT / ".github" / "actions" / "hacs-validation" / "action.yml")
    runs = hacs_action.get("runs")
    assert isinstance(runs, dict)
    assert re.fullmatch(
        r"docker://ghcr\.io/hacs/action@sha256:[0-9a-f]{64}",
        str(runs.get("image")),
    )

    _, source = _workflow("ci")
    assert re.search(
        r"HASSFEST_IMAGE:\s*ghcr\.io/home-assistant/hassfest@sha256:[0-9a-f]{64}",
        source,
    )
    assert "hacs/action@" not in source
    assert "home-assistant/actions/hassfest@" not in source


def test_dependabot_and_codeowners_cover_ci_policy() -> None:
    dependabot = _load(ROOT / ".github" / "dependabot.yml")
    maintenance_groups = dependabot.get("multi-ecosystem-groups")
    assert maintenance_groups == {
        "weekly-maintenance": {"schedule": {"interval": "weekly"}}
    }, "routine version updates should arrive as one weekly maintenance PR"

    updates = dependabot.get("updates")
    assert isinstance(updates, list) and updates
    assert all(
        isinstance(item, dict)
        and item.get("multi-ecosystem-group") == "weekly-maintenance"
        and item.get("patterns") == ["*"]
        and "schedule" not in item
        for item in updates
    ), "every managed ecosystem must participate in the single maintenance group"

    github_actions = [
        item
        for item in updates
        if isinstance(item, dict) and item.get("package-ecosystem") == "github-actions"
    ]
    assert github_actions, "Dependabot must keep pinned GitHub Actions current"
    assert any(item.get("directory") == "/" for item in github_actions)

    root_pip = next(
        item
        for item in updates
        if isinstance(item, dict)
        and item.get("package-ecosystem") == "pip"
        and item.get("directory") == "/"
    )
    expected_pytest_ignore = [
        {"dependency-name": "pytest", "versions": [">=9.1.0"]}
    ]
    assert root_pip.get("ignore") == expected_pytest_ignore

    assert not any(
        isinstance(item, dict)
        and item.get("package-ecosystem") == "pip"
        and item.get("directory") == "/codex_bridge_app"
        for item in updates
    )

    bridge_pip = next(
        item
        for item in updates
        if isinstance(item, dict)
        and item.get("package-ecosystem") == "pip"
        and item.get("directory") == "/bridge_service"
    )
    assert bridge_pip.get("ignore") == expected_pytest_ignore

    codeowners = ROOT / ".github" / "CODEOWNERS"
    assert codeowners.is_file()
    rules = [
        line.split()
        for line in codeowners.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert any(
        tokens
        and tokens[0].startswith(".github/")
        and any(token.startswith("@") for token in tokens[1:])
        for tokens in rules
    )
    assert any(
        "@herbertmt978" in token.lower() for tokens in rules for token in tokens[1:]
    )
