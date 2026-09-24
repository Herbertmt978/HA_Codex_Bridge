"""Modern Office previews remain bounded and separate from artifact downloads."""

import os
from io import BytesIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from fastapi.testclient import TestClient

from codex_bridge_service.app import create_app
from codex_bridge_service.models import RunMode, RuntimeProfile
from codex_bridge_service.office_preview import OfficePreviewError, preview_office
from codex_bridge_service.storage import ThreadNotFoundError


def _office_file(parts: dict[str, str]) -> bytes:
    output = BytesIO()
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        for name, content in parts.items():
            archive.writestr(name, content)
    return output.getvalue()


def test_docx_extracts_text_without_rendering_markup() -> None:
    data = _office_file({"word/document.xml": (
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:body><w:p><w:r><w:t>Hello</w:t></w:r></w:p>'
        '<w:p><w:r><w:t>&lt;script&gt;alert(1)&lt;/script&gt;</w:t></w:r></w:p>'
        '</w:body></w:document>'
    )})
    assert preview_office(BytesIO(data), "hello.docx", len(data)) == {
        "kind": "document", "paragraphs": ["Hello", "<script>alert(1)</script>"], "truncated": False,
    }


def test_xlsx_extracts_shared_and_numeric_cells() -> None:
    data = _office_file({
        "xl/sharedStrings.xml": (
            '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<si><t>Heading</t></si></sst>'
        ),
        "xl/worksheets/sheet1.xml": (
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<sheetData><row r="1"><c r="A1" t="s"><v>0</v></c>'
            '<c r="B1"><v>42</v></c></row></sheetData></worksheet>'
        ),
    })
    assert preview_office(BytesIO(data), "table.xlsx", len(data))["sheets"] == [
        {"name": "Sheet 1", "rows": [["Heading", "42"]]},
    ]


def test_pptx_extracts_slide_text() -> None:
    data = _office_file({"ppt/slides/slide1.xml": (
        '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
        'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
        '<a:p><a:r><a:t>Introduction</a:t></a:r></a:p></p:sld>'
    )})
    assert preview_office(BytesIO(data), "slides.pptx", len(data))["slides"] == [
        {"name": "Slide 1", "paragraphs": ["Introduction"]},
    ]


@pytest.mark.parametrize("content", [
    b"not a zip",
    _office_file({"word/document.xml": '<!DOCTYPE x [<!ENTITY e "boom">]><x>&e;</x>'}),
    _office_file({"word/document.xml": "a" * (1024 * 1024 + 1)}),
])
def test_unsafe_or_invalid_office_content_is_rejected(content: bytes) -> None:
    with pytest.raises(OfficePreviewError):
        preview_office(BytesIO(content), "unsafe.docx", len(content))


def test_preview_rejects_oversized_artifact_before_reading() -> None:
    with pytest.raises(OfficePreviewError, match="8 MB"):
        preview_office(BytesIO(), "large.xlsx", 8 * 1024 * 1024 + 1)


def test_preview_route_requires_auth_and_artifact_ownership(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "state"
    workspace_root = tmp_path / "workspaces"
    app = create_app(
        root_path=root, auth_token="secret", runtime_profile=RuntimeProfile.HOME_ASSISTANT,
        workspace_root=workspace_root, runner_factory=lambda _storage: object(),
    )
    storage = app.state.storage
    project = storage.create_project(name="Preview", root_path="projects/preview")
    first = storage.create_thread(title="First", project_id=project.project_id, mode=RunMode.EDIT)
    second = storage.create_thread(title="Second", project_id=project.project_id, mode=RunMode.EDIT)
    workspace = workspace_root.joinpath(*first.workspace_path.split("/"))
    workspace.mkdir(parents=True, exist_ok=True)
    data = _office_file({"word/document.xml": (
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:p><w:t>Hello</w:t></w:p></w:document>'
    )})
    (workspace / "hello.docx").write_bytes(data)
    artifact = storage.sync_thread_artifacts(first.thread_id)[0]
    def open_owned_artifact(thread_id: str, artifact_id: str):
        if thread_id != first.thread_id or artifact_id != artifact.artifact_id:
            raise ThreadNotFoundError()
        return artifact, BytesIO(data), len(data)

    monkeypatch.setattr(storage, "open_artifact", open_owned_artifact)
    client = TestClient(app)
    path = f"/threads/{first.thread_id}/artifacts/{artifact.artifact_id}/preview"
    assert client.get(path).status_code == 401
    headers = {"Authorization": "Bearer secret", "X-Codex-Bridge-Api": "1"}
    response = client.get(path, headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["paragraphs"] == ["Hello"]
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert client.get(
        f"/threads/{second.thread_id}/artifacts/{artifact.artifact_id}/preview",
        headers=headers,
    ).status_code == 404


@pytest.mark.skipif(
    os.name == "nt" or not hasattr(os, "memfd_create"),
    reason="the sealed HA artifact stream requires Linux memfd support",
)
def test_preview_reads_a_real_sealed_home_assistant_artifact(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspaces"
    app = create_app(
        root_path=tmp_path / "state", auth_token="secret",
        runtime_profile=RuntimeProfile.HOME_ASSISTANT, workspace_root=workspace_root,
        runner_factory=lambda _storage: object(),
    )
    storage = app.state.storage
    project = storage.create_project(name="Preview", root_path="projects/preview")
    thread = storage.create_thread(title="Word", project_id=project.project_id, mode=RunMode.EDIT)
    workspace = workspace_root.joinpath(*thread.workspace_path.split("/"))
    workspace.mkdir(parents=True, exist_ok=True)
    data = _office_file({"word/document.xml": (
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:p><w:t>hello</w:t></w:p></w:document>'
    )})
    (workspace / "hello.docx").write_bytes(data)
    artifact = storage.sync_thread_artifacts(thread.thread_id)[0]

    response = TestClient(app).get(
        f"/threads/{thread.thread_id}/artifacts/{artifact.artifact_id}/preview",
        headers={"Authorization": "Bearer secret", "X-Codex-Bridge-Api": "1"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["paragraphs"] == ["hello"]
