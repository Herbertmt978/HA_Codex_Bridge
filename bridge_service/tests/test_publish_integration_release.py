"""Adversarial tests for the paired Integration release publisher."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
from urllib.request import Request

import pytest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "publish_integration_release", ROOT / "scripts" / "publish_integration_release.py"
)
assert SPEC and SPEC.loader
publisher = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = publisher
SPEC.loader.exec_module(publisher)

SHA = "a" * 40
OTHER = "b" * 40
CHANGELOG = "## 1.2.3\n\n- Integration parity.\n\n## 1.2.2\n\n- Older.\n"
RUN = "https://github.com/acme/repo/actions/runs/42"


class QueueAPI:
    def __init__(self, *responses: tuple[int, object]) -> None:
        self.responses = list(responses)
        self.requests: list[tuple[str, str, object | None]] = []

    def __call__(self, request: Request) -> tuple[int, bytes]:
        body = None
        if request.data:
            body = json.loads(request.data.decode())
        self.requests.append((request.method, request.full_url, body))
        if not self.responses:
            raise AssertionError("unexpected API request")
        status, payload = self.responses.pop(0)
        return status, json.dumps(payload).encode() if payload is not None else b""


def client(api: QueueAPI) -> publisher.GitHubClient:
    return publisher.GitHubClient("acme/repo", "secret-token", transport=api)


def ref(sha: str = SHA, kind: str = "commit") -> dict[str, object]:
    return {"ref": "refs/tags/1.2.3", "object": {"type": kind, "sha": sha}}


def release(*, draft: bool = False, prerelease: bool = False, tag: str = "1.2.3") -> dict[str, object]:
    return {"tag_name": tag, "draft": draft, "prerelease": prerelease, "target_commitish": "some-branch"}


def test_absent_tag_and_release_are_created_and_post_verified() -> None:
    api = QueueAPI(
        (404, {"message": "Not Found"}),
        (404, {"message": "Not Found"}),
        (201, ref()),
        (200, ref()),
        (201, release()),
        (200, release()),
        (200, ref()),
    )
    publisher.publish(client=client(api), version="1.2.3", expected_sha=SHA, changelog=CHANGELOG, run_url=RUN)
    assert api.requests[2][0] == "POST"
    assert api.requests[2][2] == {"ref": "refs/tags/1.2.3", "sha": SHA}


def test_existing_exact_release_is_idempotent_even_with_ignored_target_commitish() -> None:
    api = QueueAPI((200, release()), (200, ref()), (200, release()), (200, ref()))
    publisher.publish(client=client(api), version="1.2.3", expected_sha=SHA, changelog=CHANGELOG, run_url=RUN)


def test_deleted_tag_with_existing_release_fails_closed() -> None:
    api = QueueAPI((200, release()), (404, {}))
    with pytest.raises(publisher.PublishError, match="tag is missing"):
        publisher.publish(client=client(api), version="1.2.3", expected_sha=SHA, changelog=CHANGELOG, run_url=RUN)


def test_annotated_tags_are_peeled_recursively() -> None:
    first = "c" * 40
    second = "d" * 40
    api = QueueAPI(
        (200, ref(first, "tag")),
        (200, {"object": {"type": "tag", "sha": second}}),
        (200, {"object": {"type": "commit", "sha": SHA}}),
    )
    assert publisher._resolve_tag_commit(client(api), "1.2.3") == SHA


def test_annotated_tag_malformed_and_bounded_cases_fail() -> None:
    malformed = QueueAPI((200, ref("not-a-sha", "tag")))
    with pytest.raises(publisher.PublishError, match="SHA is malformed"):
        publisher._resolve_tag_commit(client(malformed), "1.2.3")
    loop = QueueAPI(*([(200, ref("c" * 40, "tag"))] + [(200, {"object": {"type": "tag", "sha": "c" * 40}})] * 8))
    with pytest.raises(publisher.PublishError, match="exceeds peeling depth"):
        publisher._resolve_tag_commit(client(loop), "1.2.3")


def test_mismatched_existing_tag_fails() -> None:
    api = QueueAPI((404, {}), (200, ref(OTHER)))
    with pytest.raises(publisher.PublishError, match="unexpected commit"):
        publisher.publish(client=client(api), version="1.2.3", expected_sha=SHA, changelog=CHANGELOG, run_url=RUN)


def test_create_tag_race_accepts_only_exact_winner() -> None:
    exact = QueueAPI(
        (404, {}),
        (404, {}),
        (422, {}),
        (200, ref(SHA)),
        (201, release()),
        (200, release()),
        (200, ref(SHA)),
    )
    publisher.publish(client=client(exact), version="1.2.3", expected_sha=SHA, changelog=CHANGELOG, run_url=RUN)
    mismatch = QueueAPI((404, {}), (404, {}), (422, {}), (200, ref(OTHER)))
    with pytest.raises(publisher.PublishError, match="race"):
        publisher.publish(client=client(mismatch), version="1.2.3", expected_sha=SHA, changelog=CHANGELOG, run_url=RUN)


@pytest.mark.parametrize("create_status", [201, 422])
def test_create_tag_readback_retries_temporary_absence(
    monkeypatch: pytest.MonkeyPatch, create_status: int
) -> None:
    delays: list[float] = []
    monkeypatch.setattr(publisher.time, "sleep", delays.append)
    api = QueueAPI(
        (404, {}),
        (404, {}),
        (create_status, ref() if create_status == 201 else {}),
        (404, {}),
        (404, {}),
        (200, ref()),
        (201, release()),
        (200, release()),
        (200, ref()),
    )

    publisher.publish(
        client=client(api),
        version="1.2.3",
        expected_sha=SHA,
        changelog=CHANGELOG,
        run_url=RUN,
    )

    assert delays == [0.25, 0.5]


def test_create_tag_readback_fails_closed_after_bounded_absence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    delays: list[float] = []
    monkeypatch.setattr(publisher.time, "sleep", delays.append)
    api = QueueAPI(
        (404, {}),
        (404, {}),
        (201, ref()),
        *[(404, {}) for _ in range(1 + len(publisher.TAG_READBACK_RETRY_DELAYS))],
    )

    with pytest.raises(publisher.PublishError, match="race"):
        publisher.publish(
            client=client(api),
            version="1.2.3",
            expected_sha=SHA,
            changelog=CHANGELOG,
            run_url=RUN,
        )

    assert delays == list(publisher.TAG_READBACK_RETRY_DELAYS)


def test_create_tag_readback_rejects_visible_mismatch_without_waiting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    delays: list[float] = []
    monkeypatch.setattr(publisher.time, "sleep", delays.append)
    api = QueueAPI((404, {}), (404, {}), (201, ref()), (200, ref(OTHER)))

    with pytest.raises(publisher.PublishError, match="race"):
        publisher.publish(
            client=client(api),
            version="1.2.3",
            expected_sha=SHA,
            changelog=CHANGELOG,
            run_url=RUN,
        )

    assert delays == []


def test_create_release_race_refetches_and_validates_winner() -> None:
    api = QueueAPI((404, {}), (404, {}), (201, ref()), (200, ref()), (422, {}), (200, release()), (200, ref()))
    publisher.publish(client=client(api), version="1.2.3", expected_sha=SHA, changelog=CHANGELOG, run_url=RUN)


@pytest.mark.parametrize(
    ("final_response", "message"),
    [
        ((404, {}), "disappeared"),
        ((200, release(draft=True)), "exact published release"),
        ((200, release(prerelease=True)), "exact published release"),
    ],
)
def test_existing_release_is_refetched_for_final_postconditions(
    final_response: tuple[int, object], message: str
) -> None:
    api = QueueAPI((200, release()), (200, ref()), final_response)
    with pytest.raises(publisher.PublishError, match=message):
        publisher.publish(
            client=client(api),
            version="1.2.3",
            expected_sha=SHA,
            changelog=CHANGELOG,
            run_url=RUN,
        )


@pytest.mark.parametrize(
    ("final_response", "message"),
    [
        ((404, {}), "disappeared"),
        ((200, release(draft=True)), "exact published release"),
    ],
)
def test_created_release_is_refetched_for_final_postconditions(
    final_response: tuple[int, object], message: str
) -> None:
    api = QueueAPI(
        (404, {}),
        (404, {}),
        (201, ref()),
        (200, ref()),
        (201, release()),
        final_response,
    )
    with pytest.raises(publisher.PublishError, match=message):
        publisher.publish(
            client=client(api),
            version="1.2.3",
            expected_sha=SHA,
            changelog=CHANGELOG,
            run_url=RUN,
        )


def test_final_postcondition_rejects_a_moved_tag() -> None:
    api = QueueAPI(
        (200, release()),
        (200, ref()),
        (200, release()),
        (200, ref(OTHER)),
    )
    with pytest.raises(publisher.PublishError, match="post-verification"):
        publisher.publish(
            client=client(api),
            version="1.2.3",
            expected_sha=SHA,
            changelog=CHANGELOG,
            run_url=RUN,
        )


@pytest.mark.parametrize("bad", [release(draft=True), release(prerelease=True), release(tag="9.9.9")])
def test_existing_release_shape_failures(bad: dict[str, object]) -> None:
    api = QueueAPI((200, bad), (200, ref()))
    with pytest.raises(publisher.PublishError, match="exact published release"):
        publisher.publish(client=client(api), version="1.2.3", expected_sha=SHA, changelog=CHANGELOG, run_url=RUN)


def test_unexpected_api_failure_is_not_treated_as_absent() -> None:
    api = QueueAPI((500, {"message": "server error"}))
    with pytest.raises(publisher.PublishError, match="HTTP 500"):
        publisher.publish(client=client(api), version="1.2.3", expected_sha=SHA, changelog=CHANGELOG, run_url=RUN)


def test_notes_require_unique_canonical_section_and_include_signed_run_url() -> None:
    notes = publisher.build_release_notes(CHANGELOG, "1.2.3", RUN)
    assert "- Integration parity." in notes and RUN in notes
    with pytest.raises(publisher.PublishError):
        publisher.build_release_notes("## 1.2.3\n\n## 1.2.3\n", "1.2.3", RUN)
