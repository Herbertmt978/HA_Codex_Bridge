from enum import StrEnum

from pydantic import BaseModel, Field


class RunMode(StrEnum):
    OBSERVE = "observe"
    EDIT = "edit"
    FULL_AUTO = "full-auto"


class AttachmentRecord(BaseModel):
    attachment_id: str
    filename: str
    mime_type: str
    stored_path: str


class ArtifactRecord(BaseModel):
    artifact_id: str
    filename: str
    mime_type: str
    stored_path: str


class ThreadRecord(BaseModel):
    thread_id: str
    title: str
    workspace_id: str
    workspace_path: str
    status: str
    mode: RunMode = Field(default=RunMode.FULL_AUTO)
    attachments: list[AttachmentRecord] = Field(default_factory=list)
    artifacts: list[ArtifactRecord] = Field(default_factory=list)
