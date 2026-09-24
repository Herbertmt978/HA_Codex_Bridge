"""Bounded, inert previews for modern Office workspace artifacts."""

from __future__ import annotations

import re
from typing import BinaryIO
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile

MAX_ARCHIVE_BYTES = 8 * 1024 * 1024
MAX_XML_BYTES = 1024 * 1024
MAX_ARCHIVE_ENTRIES = 1000
MAX_TEXT_CHARS = 30_000
_WORD = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_SHEET = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_DRAWING = "http://schemas.openxmlformats.org/drawingml/2006/main"
_OFFICE_TYPES = {"docx": "document", "xlsx": "spreadsheet", "pptx": "presentation"}


class OfficePreviewError(ValueError):
    """The artifact cannot be safely previewed."""


def preview_kind(filename: str) -> str | None:
    return _OFFICE_TYPES.get(filename.rsplit(".", 1)[-1].lower()) if "." in filename else None


def _xml(archive: ZipFile, path: str) -> ET.Element:
    try:
        info = archive.getinfo(path)
    except KeyError as exc:
        raise OfficePreviewError("required document content is missing") from exc
    if info.file_size > MAX_XML_BYTES:
        raise OfficePreviewError("document content exceeds the preview limit")
    with archive.open(info) as part:
        data = part.read(MAX_XML_BYTES + 1)
    if len(data) > MAX_XML_BYTES or re.search(rb"<!\s*(?:DOCTYPE|ENTITY)\b", data, re.I):
        raise OfficePreviewError("document content is unsafe or too large")
    try:
        return ET.fromstring(data)
    except ET.ParseError as exc:
        raise OfficePreviewError("document content is invalid") from exc


def _cap(text: str, budget: list[int]) -> str:
    available = max(0, budget[0])
    value = text[:available]
    budget[0] -= len(value)
    return value


def _paragraphs(root: ET.Element, paragraph_tag: str, text_tag: str, budget: list[int]) -> list[str]:
    result = []
    for paragraph in root.iter(paragraph_tag):
        if len(result) >= 200 or budget[0] <= 0:
            break
        value = "".join(node.text or "" for node in paragraph.iter(text_tag))
        if value.strip():
            result.append(_cap(value, budget))
    return result


def _document(archive: ZipFile, budget: list[int]) -> dict:
    root = _xml(archive, "word/document.xml")
    paragraphs = _paragraphs(root, f"{{{_WORD}}}p", f"{{{_WORD}}}t", budget)
    return {"kind": "document", "paragraphs": paragraphs, "truncated": budget[0] <= 0}


def _column_index(reference: str) -> int | None:
    match = re.match(r"[A-Z]{1,3}", reference.upper())
    if not match:
        return None
    index = 0
    for letter in match.group():
        index = index * 26 + ord(letter) - ord("A") + 1
    return index - 1


def _spreadsheet(archive: ZipFile, budget: list[int]) -> dict:
    shared = []
    if "xl/sharedStrings.xml" in archive.namelist():
        root = _xml(archive, "xl/sharedStrings.xml")
        for entry in root.iter(f"{{{_SHEET}}}si"):
            if len(shared) >= 10_000:
                raise OfficePreviewError("shared strings exceed the preview limit")
            shared.append("".join(node.text or "" for node in entry.iter(f"{{{_SHEET}}}t")))
    sheets = []
    names = sorted(
        (name for name in archive.namelist() if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", name)),
        key=lambda name: int(name.rsplit("sheet", 1)[1][:-4]),
    )
    for name in names[:3]:
        root = _xml(archive, name)
        rows = []
        for row in root.iter(f"{{{_SHEET}}}row"):
            if len(rows) >= 100 or budget[0] <= 0:
                break
            cells = [""] * 20
            for cell in row.iter(f"{{{_SHEET}}}c"):
                column = _column_index(cell.get("r", ""))
                if column is None or column >= 20:
                    continue
                if cell.get("t") == "inlineStr":
                    value = "".join(node.text or "" for node in cell.iter(f"{{{_SHEET}}}t"))
                else:
                    value = cell.findtext(f"{{{_SHEET}}}v", default="")
                    if cell.get("t") == "s":
                        try:
                            value = shared[int(value)]
                        except (ValueError, IndexError) as exc:
                            raise OfficePreviewError("shared string reference is invalid") from exc
                cells[column] = _cap(value, budget)
                if budget[0] <= 0:
                    break
            while cells and not cells[-1]:
                cells.pop()
            rows.append(cells)
        sheets.append({"name": f"Sheet {len(sheets) + 1}", "rows": rows})
    if not sheets:
        raise OfficePreviewError("worksheet content is missing")
    return {"kind": "spreadsheet", "sheets": sheets, "truncated": len(names) > 3 or budget[0] <= 0}


def _presentation(archive: ZipFile, budget: list[int]) -> dict:
    names = sorted(
        (name for name in archive.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)),
        key=lambda name: int(name.rsplit("slide", 1)[1][:-4]),
    )
    if not names:
        raise OfficePreviewError("slide content is missing")
    slides = []
    for name in names[:5]:
        root = _xml(archive, name)
        slides.append({
            "name": f"Slide {len(slides) + 1}",
            "paragraphs": _paragraphs(root, f"{{{_DRAWING}}}p", f"{{{_DRAWING}}}t", budget),
        })
    return {"kind": "presentation", "slides": slides, "truncated": len(names) > 5 or budget[0] <= 0}


def preview_office(stream: BinaryIO, filename: str, size_bytes: int) -> dict:
    kind = preview_kind(filename)
    if kind is None:
        raise OfficePreviewError("this file type has no Office preview")
    if size_bytes > MAX_ARCHIVE_BYTES:
        raise OfficePreviewError("file exceeds the 8 MB preview limit")
    try:
        with ZipFile(stream) as archive:
            if len(archive.infolist()) > MAX_ARCHIVE_ENTRIES:
                raise OfficePreviewError("archive has too many entries")
            budget = [MAX_TEXT_CHARS]
            if kind == "document":
                return _document(archive, budget)
            if kind == "spreadsheet":
                return _spreadsheet(archive, budget)
            return _presentation(archive, budget)
    except (BadZipFile, OSError, RuntimeError) as exc:
        raise OfficePreviewError("document archive is invalid") from exc
