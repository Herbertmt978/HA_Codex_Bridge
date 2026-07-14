"""Policy contracts for the repository's user-facing documentation."""

from __future__ import annotations

from pathlib import Path
import re
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[2]

REQUIRED_SECTIONS: dict[str, tuple[str, ...]] = {
    "README.md": (
        "What it is",
        "Two components, two installation paths",
        "Install and first run",
        "Updates and recovery",
        "Security boundary",
    ),
    "CONTEXT.md": ("Purpose", "Terms", "Current compatibility statement", "Product language"),
    "docs/installation.md": (
        "Status before you start",
        "Prerequisites",
        "Install the Integration",
        "Install the App",
        "First run",
        "After installation",
    ),
    "docs/remote-access.md": (
        "The invariant",
        "ChatGPT device authentication",
        "Before enabling remote use",
        "Provider notes",
        "Test and recover",
    ),
    "docs/backup-restore.md": (
        "What to protect",
        "Current recovery plan",
        "Create a cold backup",
        "Restore safely",
    ),
    "docs/migration-from-windows.md": ("Safe migration sequence", "Recovery during cutover", "Windows appendix"),
    "SECURITY.md": ("Reporting a vulnerability", "Security boundaries", "Scope notes"),
    "SUPPORT.md": ("Where to ask", "Include this information", "Fast checks"),
    "CONTRIBUTING.md": ("Before opening an issue or pull request", "Development expectations", "Local checks"),
    "CODE_OF_CONDUCT.md": ("Our commitment", "Expected behaviour", "Unacceptable behaviour", "Enforcement"),
    "THIRD_PARTY_NOTICES.md": ("Third-party notices",),
    "LICENSE": (),
}


def _user_facing_docs() -> tuple[Path, ...]:
    """Return maintained product/project docs, excluding historical Aegis notes."""

    candidates = {
        ROOT / "README.md",
        ROOT / "CONTEXT.md",
        ROOT / "SECURITY.md",
        ROOT / "SUPPORT.md",
        ROOT / "CONTRIBUTING.md",
        ROOT / "CODE_OF_CONDUCT.md",
        ROOT / "THIRD_PARTY_NOTICES.md",
        ROOT / "LICENSE",
        *(ROOT / "docs").glob("*.md"),
        *(ROOT / "codex_bridge_app").glob("*.md"),
    }
    return tuple(sorted(path for path in candidates if path.is_file()))


def _read(path: Path) -> str:
    """Read a documentation file as UTF-8 with a useful assertion on failure."""

    try:
        return path.read_bytes().decode("utf-8")
    except UnicodeDecodeError as exc:  # pragma: no cover - assertion gives the useful output
        raise AssertionError(f"{path.relative_to(ROOT)} must be valid UTF-8") from exc


def _headings(text: str) -> tuple[str, ...]:
    return tuple(match.group(1).strip() for match in re.finditer(r"^#{1,6}\s+(.+?)\s*$", text, re.MULTILINE))


def _corpus(paths: tuple[Path, ...] | list[Path]) -> str:
    return "\n".join(_read(path) for path in paths)


def test_required_repository_docs_and_sections_exist() -> None:
    for relative, sections in REQUIRED_SECTIONS.items():
        path = ROOT / relative
        assert path.is_file(), f"required documentation file is missing: {relative}"
        text = _read(path)
        headings = tuple(heading.casefold() for heading in _headings(text))
        for section in sections:
            assert any(section.casefold() in heading for heading in headings), (
                f"{relative} is missing the '{section}' section"
            )


def test_relative_markdown_links_and_images_resolve() -> None:
    markdown_link = re.compile(r"!?\[[^\]]*\]\(\s*([^\s)]+)")
    uri_scheme = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
    failures: list[str] = []

    for document in _user_facing_docs():
        text = _read(document)
        for match in markdown_link.finditer(text):
            target = match.group(1).strip("<>\"'")
            if not target or target.startswith("#") or target.startswith("//") or uri_scheme.match(target):
                continue
            target = target.split("#", 1)[0].split("?", 1)[0]
            if not target:
                continue
            resolved = (document.parent / target).resolve()
            if not resolved.exists():
                failures.append(f"{document.relative_to(ROOT)} -> {target}")

    assert not failures, "unresolved relative Markdown links/images: " + "; ".join(failures)


def test_user_facing_docs_are_utf8_and_free_of_common_mojibake() -> None:
    mojibake_markers = ("Ã", "Â", "â€", "â€™", "â€œ", "â€�", "ðŸ", "�")
    for document in _user_facing_docs():
        text = _read(document)
        found = [marker for marker in mojibake_markers if marker in text]
        assert not found, f"{document.relative_to(ROOT)} contains mojibake markers: {found}"


def test_readme_explains_the_integration_app_and_chatgpt_login_contract() -> None:
    readme = _read(ROOT / "README.md")
    assert re.search(r"\*\*HACS Integration:\*\*", readme)
    assert re.search(r"\*\*Supervisor App:\*\*", readme)
    assert re.search(r"ChatGPT.{0,100}device", readme, re.IGNORECASE | re.DOTALL)
    assert re.search(
        r"(?:does not use|without|no).{0,60}OpenAI\s+API\s+key",
        readme,
        re.IGNORECASE | re.DOTALL,
    )


def test_remote_access_stays_on_home_assistant_and_is_provider_neutral() -> None:
    documents = (
        ROOT / "README.md",
        ROOT / "CONTEXT.md",
        ROOT / "docs" / "installation.md",
        ROOT / "docs" / "remote-access.md",
        ROOT / "SECURITY.md",
        ROOT / "codex_bridge_app" / "DOCS.md",
    )
    corpus = _corpus(documents)
    assert re.search(r"Nabu Casa", corpus, re.IGNORECASE)
    assert re.search(r"Cloudflare", corpus, re.IGNORECASE)
    assert re.search(r"reverse[- ]proxy", corpus, re.IGNORECASE)
    remote = _read(ROOT / "docs" / "remote-access.md")
    assert re.search(r"Browser.{0,120}Home Assistant", remote, re.IGNORECASE | re.DOTALL)
    assert re.search(r"Do not publish the App.{0,120}Bridge", remote, re.IGNORECASE | re.DOTALL)


def test_app_availability_and_rollback_claims_remain_honest() -> None:
    app_documents = (
        ROOT / "README.md",
        ROOT / "CONTEXT.md",
        ROOT / "docs" / "installation.md",
        ROOT / "docs" / "backup-restore.md",
        ROOT / "codex_bridge_app" / "README.md",
        ROOT / "codex_bridge_app" / "DOCS.md",
    )
    corpus = _corpus(app_documents)
    assert re.search(
        r"(?:public|distributed).{0,120}(?:signed|immutable).{0,120}(?:image|SBOM|provenance)",
        corpus,
        re.IGNORECASE | re.DOTALL,
    )
    assert not re.search(
        r"public.{0,100}(?:not available|not a public|not published)",
        corpus,
        re.IGNORECASE | re.DOTALL,
    )
    assert re.search(
        r"Supervisor.{0,140}arbitrary (?:prior|earlier)(?: App)? image",
        corpus,
        re.IGNORECASE | re.DOTALL,
    )
    assert re.search(r"rollback.{0,100}(?:not|unvalidated|unsupported)", corpus, re.IGNORECASE | re.DOTALL)


def test_current_setup_does_not_make_a_windows_vm_the_default() -> None:
    installation = _read(ROOT / "docs" / "installation.md")
    readme = _read(ROOT / "README.md")
    assert re.search(r"Windows VM.{0,100}optional", installation, re.IGNORECASE | re.DOTALL)
    assert re.search(r"not a requirement|not required", installation, re.IGNORECASE)
    assert not re.search(r"Windows VM.{0,80}(?:is )?(?:required|mandatory)", readme, re.IGNORECASE | re.DOTALL)
    assert not re.search(r"(?:install|setup).{0,80}Windows VM", readme, re.IGNORECASE | re.DOTALL)


def test_license_and_third_party_attribution_are_present() -> None:
    assert re.search(r"MIT", _read(ROOT / "LICENSE"), re.IGNORECASE)
    notices = _read(ROOT / "THIRD_PARTY_NOTICES.md")
    assert re.search(r"Codex", notices, re.IGNORECASE)
    assert re.search(r"OpenAI", notices, re.IGNORECASE)
    assert re.search(r"Apache-2\.0", notices, re.IGNORECASE)


def test_user_facing_docs_contain_no_obvious_private_urls_or_credentials() -> None:
    private_url = re.compile(
        r"https?://(?:localhost|127(?:\.\d+){3}|0\.0\.0\.0|10(?:\.\d+){3}|"
        r"192\.168(?:\.\d+){2}|172\.(?:1[6-9]|2\d|3[01])(?:\.\d+){2}|"
        r"(?:[A-Za-z0-9-]+\.local)|home-?assistant)(?::\d+)?(?=[/\s)>]|$)",
        re.IGNORECASE,
    )
    windows_user_path = re.compile(r"(?:[A-Z]:\\Users\\|%USERPROFILE%|/Users/[^/\s]+)", re.IGNORECASE)
    bearer_value = re.compile(r"\bBearer\s+[A-Za-z0-9][A-Za-z0-9._~+/=-]{15,}", re.IGNORECASE)
    api_key_value = re.compile(r"\b(?:sk|rk)-[A-Za-z0-9_-]{16,}\b", re.IGNORECASE)

    failures: list[str] = []
    for document in _user_facing_docs():
        text = _read(document)
        for pattern, label in (
            (private_url, "private URL"),
            (windows_user_path, "Windows user path"),
            (bearer_value, "bearer credential"),
            (api_key_value, "API credential"),
        ):
            if pattern.search(text):
                failures.append(f"{document.relative_to(ROOT)} contains an obvious {label}")

    assert not failures, "; ".join(failures)


def test_aegis_runtime_records_are_indexed() -> None:
    """Keep the HA-native implementation record complete and navigable."""

    records = (
        "docs/aegis/baseline/2026-07-14-ha-native-implementation-baseline.md",
        "docs/aegis/adr/0001-ha-app-runtime-ownership.md",
        "docs/aegis/adr/0002-ha-origin-transport-and-trust.md",
        "docs/aegis/adr/0003-private-state-and-device-auth.md",
        "docs/aegis/adr/0004-immutable-app-distribution.md",
        "docs/aegis/adr/0005-external-bridge-retirement.md",
    )
    index = _read(ROOT / "docs" / "aegis" / "INDEX.md")

    for relative in records:
        record = ROOT / relative
        assert record.is_file(), f"missing Aegis governance record: {relative}"
        index_target = relative.removeprefix("docs/aegis/")
        assert f"]({index_target})" in index, f"INDEX.md does not link to {relative}"
        for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", _read(record)):
            if target.startswith(("#", "http://", "https://")):
                continue
            assert (record.parent / target.split("#", 1)[0]).resolve().exists(), (
                f"{relative} has an unresolved local link: {target}"
            )


def test_generated_bridge_distributions_are_not_tracked() -> None:
    """Keep generated Bridge wheels and source archives out of Git."""

    git = shutil.which("git")
    if git is None:
        pytest.skip("Git is unavailable in this source-only test environment")
    tracked = subprocess.run(
        [git, "ls-files", "--", "bridge_service/dist"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    assert not tracked, f"generated Bridge distribution artifacts must not be committed: {tracked}"
