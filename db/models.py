from datetime import datetime
from typing import Optional
from sqlmodel import SQLModel, Field


class Ticket(SQLModel, table=True):
    __tablename__ = "tickets"

    id: str = Field(primary_key=True, index=True)
    category: str
    status: str = "submitted"  # submitted, processing, invalid, needs_review, approved_for_report, report_ready
    verdict: Optional[str] = None  # valid, invalid, needs_review
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())


class TicketFile(SQLModel, table=True):
    __tablename__ = "files"

    id: Optional[int] = Field(default=None, primary_key=True)
    ticket_id: str = Field(index=True)
    filename: str
    path: str
    sha256: str


class StageOutput(SQLModel, table=True):
    __tablename__ = "stage_outputs"

    id: Optional[int] = Field(default=None, primary_key=True)
    ticket_id: str = Field(index=True)
    stage_name: str = Field(index=True)
    output_json: str
    prompt_version_hash: Optional[str] = None
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
