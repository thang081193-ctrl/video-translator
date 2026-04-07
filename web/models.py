"""Pydantic models for request/response validation."""

from __future__ import annotations

from pydantic import BaseModel, Field


class JobStatus(BaseModel):
    """Job status response."""
    id: str
    status: str  # queued | running | done | error
    step: int = 0
    total_steps: int = 0
    step_label: str = ""
    progress: int = 0
    error: str | None = None
    files: list[dict] = Field(default_factory=list)


class JobCreateResponse(BaseModel):
    """Response after creating a translation job."""
    job_id: str


class LanguagesResponse(BaseModel):
    """Available languages and voices."""
    languages: list[dict]
    voices: dict[str, str]


class ErrorResponse(BaseModel):
    """Error response."""
    error: str
