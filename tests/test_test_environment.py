"""Keep the Home Assistant test environment tied to its versioned fixture."""

from importlib.metadata import requires, version
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version


FIXTURE = "pytest-homeassistant-custom-component"
REQUIREMENTS = Path(__file__).resolve().parents[1] / "requirements-test.txt"


def test_fixture_is_the_only_authority_for_its_pinned_dependencies():
    """Prevent independent Dependabot bumps from splitting the matched pair."""
    declared = {}
    for line in REQUIREMENTS.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith(("#", "-")):
            requirement = Requirement(line)
            declared[canonicalize_name(requirement.name)] = requirement
    fixture = declared[FIXTURE]
    pins = list(fixture.specifier)
    assert len(pins) == 1 and pins[0].operator == "=="
    assert "*" not in pins[0].version
    assert version(FIXTURE) in fixture.specifier
    assert not set(declared) & {
        "homeassistant", "pytest", "pytest-asyncio", "pytest-aiohttp", "pytest-timeout"
    }


def test_installed_home_assistant_matches_fixture_and_is_stable():
    """A fixture update must exercise its exact, stable Home Assistant release."""
    ha_requirements = [
        requirement
        for dependency in requires(FIXTURE) or []
        if canonicalize_name((requirement := Requirement(dependency)).name)
        == "homeassistant"
    ]
    assert len(ha_requirements) == 1
    pins = list(ha_requirements[0].specifier)
    assert len(pins) == 1 and pins[0].operator == "=="
    assert "*" not in pins[0].version
    installed = Version(version("homeassistant"))
    assert installed == Version(pins[0].version)
    assert not installed.is_prerelease and not installed.is_devrelease
