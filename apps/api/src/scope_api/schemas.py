"""Pydantic schemas for the API layer."""

from __future__ import annotations

from typing import Optional
from typing import TypeVar, Generic

from pydantic import BaseModel, Field, field_validator

from scope_api.application.run_service import ALL_VALID_PILLARS

T = TypeVar("T")


class StartResearchRunRequest(BaseModel):
    company_name: str = Field(min_length=1)
    ticker: str = Field(min_length=1)
    selected_pillars: list[str] = Field(default_factory=list)

    @field_validator("selected_pillars")
    @classmethod
    def validate_pillars(cls, value: list[str]) -> list[str]:
        if not value:
            return value
        invalid = [pillar for pillar in value if pillar not in ALL_VALID_PILLARS]
        if invalid:
            raise ValueError(f"Unsupported pillars: {', '.join(invalid)}")
        return value


class StartResearchRunResponse(BaseModel):
    run_id: str
    status: str


class RunStatusResponse(BaseModel):
    id: str
    company_name: str
    ticker: str
    selected_pillars: list[str]
    status: str
    current_stage: str
    current_substep: str = ""
    progress: float
    stage_progress: float = 0
    activity_count: int = 0
    error_message: str = ""
    created_at: str
    updated_at: str
    last_activity_at: str = ""
    is_stalled: bool = False
    completed_at: str = ""
    summary: Optional[dict] = None
    profile_snapshot: Optional[dict] = None
    profile_snapshot_captured_at: str = ""


class RunResultResponse(BaseModel):
    id: str
    status: str
    result: Optional[dict] = None


class ApiEnvelope(BaseModel, Generic[T]):
    """A small wrapper that keeps API envelopes consistent.

    In production this improves traceability and observability by passing the request
    id alongside all payloads without changing business data structures.
    """

    request_id: str = Field(description="Correlation id for this request.")
    data: T


class RunListResponse(BaseModel):
    """Metadata wrapper for list endpoints."""

    items: list[RunStatusResponse]
    total: int
