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


def test_codex_updater_uses_scoped_app_token_and_guarded_auto_merge() -> None:
    document, source = _workflow("codex-update")
    pull_request_job = document["jobs"]["pull-request"]
    assert pull_request_job["permissions"] == {"contents": "read"}
    steps = pull_request_job["steps"]
    credential_steps = [
        step
        for step in steps
        if isinstance(step, dict) and step.get("id") == "updater-credentials"
    ]
    assert len(credential_steps) == 1
    credential_step = credential_steps[0]
    assert credential_step.get("env") == {
        "UPDATER_APP_CLIENT_ID": "${{ vars.CODEX_UPDATER_APP_CLIENT_ID }}",
        "UPDATER_APP_ACTOR": "${{ vars.CODEX_UPDATER_APP_ACTOR }}",
        "UPDATER_APP_PRIVATE_KEY": "${{ secrets.CODEX_UPDATER_APP_PRIVATE_KEY }}",
    }
    credential_check = str(credential_step.get("run", ""))
    assert '-z "${UPDATER_APP_ACTOR}"' in credential_check
    assert 'echo "available=false" >> "$GITHUB_OUTPUT"' in credential_check
    assert 'echo "available=true" >> "$GITHUB_OUTPUT"' in credential_check
    assert "::notice title=Codex updater skipped::" in credential_check

    token_steps = [
        step
        for step in steps
        if isinstance(step, dict)
        and str(step.get("uses", "")).startswith("actions/create-github-app-token@")
    ]
    assert len(token_steps) == 1, "the updater must mint one repository-scoped App token"
    token_step = token_steps[0]
    assert token_step.get("id") == "updater-token"
    assert token_step.get("if") == (
        "steps.updater-credentials.outputs.available == 'true'"
    )
    assert token_step.get("env") == {
        "UPDATER_APP_PRIVATE_KEY": "${{ secrets.CODEX_UPDATER_APP_PRIVATE_KEY }}"
    }
    token_inputs = token_step.get("with", {})
    assert token_inputs == {
        "client-id": "${{ vars.CODEX_UPDATER_APP_CLIENT_ID }}",
        "private-key": "${{ env.UPDATER_APP_PRIVATE_KEY }}",
        "owner": "${{ github.repository_owner }}",
        "repositories": "${{ github.event.repository.name }}",
        "permission-contents": "write",
        "permission-pull-requests": "write",
    }

    pull_request_steps = [
        step
        for step in steps
        if isinstance(step, dict)
        and str(step.get("uses", "")).startswith("peter-evans/create-pull-request@")
    ]
    assert pull_request_steps, "the updater must submit a reviewable pull request"
    pull_request_step = pull_request_steps[0]
    assert pull_request_step.get("if") == (
        "steps.updater-credentials.outputs.available == 'true'"
    )
    assert pull_request_step.get("with", {}).get("base") == "main", (
        "the updater must explicitly set the PR base because checkout uses an exact SHA"
    )
    assert pull_request_step.get("id") == "create-update-pr"
    assert pull_request_step.get("with", {}).get("token") == (
        "${{ steps.updater-token.outputs.token }}"
    )
    assert pull_request_step.get("with", {}).get("sign-commits") is True
    normalized = source.lower()
    assert not re.search(r"git\s+push[^\n]*\bmain\b", normalized)
    assert "github.token" not in normalized
    merge_steps = [
        step
        for step in steps
        if isinstance(step, dict) and "gh pr merge" in str(step.get("run", ""))
    ]
    assert len(merge_steps) == 1
    merge_step = merge_steps[0]
    merge_command = str(merge_step["run"]).lower()
    assert "--auto" in merge_command and "--squash" in merge_command
    assert "--match-head-commit" in merge_command
    assert "${pr_head_sha}" in merge_command
    assert merge_step.get("env", {}).get("PR_HEAD_SHA") == (
        "${{ steps.create-update-pr.outputs.pull-request-head-sha }}"
    )
    merge_condition = str(merge_step.get("if", ""))
    assert "steps.updater-credentials.outputs.available == 'true'" in merge_condition
    assert "steps.create-update-pr.outputs.pull-request-number != ''" in merge_condition
    assert "steps.create-update-pr.outputs.pull-request-commits-verified == 'true'" in merge_condition


def test_workflow_policy_rejects_mutated_automation_update_pull_requests() -> None:
    document, _ = _workflow("ci")
    policy_job = document["jobs"]["workflow-policy"]
    assert policy_job["permissions"] == {"contents": "read"}
    steps = policy_job["steps"]
    checkout_steps = [
        step
        for step in steps
        if isinstance(step, dict)
        and str(step.get("uses", "")).startswith("actions/checkout@")
    ]
    assert checkout_steps[0].get("with", {}).get("fetch-depth") == 0

    gate_steps = [
        step
        for step in steps
        if isinstance(step, dict)
        and step.get("name") == "Enforce automatic Codex updater pull-request policy"
    ]
    assert len(gate_steps) == 1
    gate = gate_steps[0]
    assert gate.get("if") == "startsWith(github.head_ref, 'automation/codex-')"
    assert gate.get("env") == {
        "PR_AUTHOR": "${{ github.event.pull_request.user.login }}",
        "PR_BASE_SHA": "${{ github.event.pull_request.base.sha }}",
        "PR_EVENT_ACTOR": "${{ github.actor }}",
        "PR_HEAD_REF": "${{ github.head_ref }}",
        "PR_HEAD_SHA": "${{ github.event.pull_request.head.sha }}",
        "UPDATER_APP_ACTOR": "${{ vars.CODEX_UPDATER_APP_ACTOR }}",
    }
    gate_script = str(gate.get("run", ""))
    assert 'git diff --name-only "${PR_BASE_SHA}" "${PR_HEAD_SHA}"' in gate_script
    assert 'git diff --diff-filter=D --name-only "${PR_BASE_SHA}" "${PR_HEAD_SHA}"' in gate_script
    assert "UPDATER_APP_ACTOR" in gate_script
    assert "PR_AUTHOR" in gate_script and "PR_EVENT_ACTOR" in gate_script
    for path_pattern in (
        "codex-release\\.json",
        "config\\.yaml",
        "Dockerfile",
        "CHANGELOG\\.md",
        "rootfs/etc/s6-overlay/s6-rc\\.d/codex-bridge/run",
        "codex_app_server_(contract\\.json|protocol\\.schema\\.json|protocol\\.v2\\.schema\\.json)",
    ):
        assert path_pattern in gate_script


def test_release_publishes_an_idempotent_exact_main_integration_release() -> None:
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
    paired_release = document["jobs"]["release-integration"]
    assert paired_release["needs"] == ["validate", "publish"], (
        "the Integration tag/release must wait for the signed manifest and its "
        "provenance/SBOM verification"
    )
    assert paired_release["permissions"] == {"contents": "write"}
    assert all(
        job.get("permissions") != {"contents": "write"}
        for name, job in document["jobs"].items()
        if name != "release-integration"
    ), "contents: write must be granted only after App publication succeeds"
    paired_condition = str(paired_release["if"])
    assert "refs/heads/main" in paired_condition

    paired_steps = paired_release["steps"]
    checkout = next(
        step
        for step in paired_steps
        if isinstance(step, dict) and str(step.get("uses", "")).startswith("actions/checkout@")
    )
    assert checkout["with"] == {
        "persist-credentials": False,
        "ref": "${{ needs.validate.outputs.expected_sha }}",
    }

    current_main_source = str(
        next(
            step["run"]
            for step in paired_steps
            if isinstance(step, dict)
            and step.get("name") == "Require the checkout to remain the published App source"
        )
    )
    assert "git rev-parse HEAD" in current_main_source
    assert "git ls-remote" not in current_main_source, (
        "a later main advance must not strand an already-published immutable App"
    )

    paired_source = str(
        next(
            step["run"]
            for step in paired_steps
            if isinstance(step, dict)
            and step.get("name") == "Create or verify the exact paired Integration release"
        )
    )
    assert "scripts/publish_integration_release.py" in paired_source
    assert "GITHUB_API_URL" in source
    helper = ROOT / "scripts" / "publish_integration_release.py"
    helper_source = helper.read_text(encoding="utf-8").lower()
    assert "urllib.request" in helper_source
    assert "refs/tags/" in helper_source
    assert "target_commitish" in helper_source
    assert "github_run_id" in helper_source
    assert "draft" in helper_source and "prerelease" in helper_source
    assert "max_tag_depth" in helper_source
    assert "git ls-remote" not in paired_source


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
