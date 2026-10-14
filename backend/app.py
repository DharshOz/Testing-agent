import hashlib
import json
import os
import shutil
import uuid
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from sqlmodel import Session, select

from db.database import engine, init_db
from db.models import Ticket, TicketFile, StageOutput
from pipeline.runner import run_pipeline

app = FastAPI(
    title="Civic Ticket AI Platform",
    description="Automated AI Document Verification & Ticket Processing Pipeline",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup():
    init_db()
    os.makedirs("./uploads", exist_ok=True)
    os.makedirs("./reports", exist_ok=True)
    os.makedirs("./qdrant_data", exist_ok=True)


@app.get("/")
def read_root():
    return {
        "status": "online",
        "service": "Civic Ticket Processing Platform",
        "endpoints": ["/tickets", "/tickets/{id}", "/tickets/{id}/report", "/tickets/{id}/rerun"]
    }


@app.post("/tickets")
async def create_ticket(
    background_tasks: BackgroundTasks,
    category: str = Form(...),
    files: List[UploadFile] = File(...)
):
    """
    POST /tickets (multipart: category + files)
    Stores files, records SHA256 hashes, creates ticket in DB,
    and launches AI verification pipeline.
    """
    if not files:
        raise HTTPException(status_code=400, detail="At least one file must be uploaded")

    ticket_id = f"TICK-{uuid.uuid4().hex[:8].upper()}"
    ticket_upload_dir = Path(f"./uploads/{ticket_id}")
    ticket_upload_dir.mkdir(parents=True, exist_ok=True)

    file_records: List[TicketFile] = []

    for file in files:
        file_path = ticket_upload_dir / file.filename
        content = await file.read()
        sha256_hash = hashlib.sha256(content).hexdigest()

        with open(file_path, "wb") as f_out:
            f_out.write(content)

        file_rec = TicketFile(
            ticket_id=ticket_id,
            filename=file.filename,
            path=str(file_path),
            sha256=sha256_hash
        )
        file_records.append(file_rec)

    with Session(engine) as session:
        ticket = Ticket(
            id=ticket_id,
            category=category,
            status="submitted",
            verdict=None
        )
        session.add(ticket)
        for fr in file_records:
            session.add(fr)
        session.commit()
        session.refresh(ticket)

    # Launch pipeline execution synchronously or as background task
    # We execute synchronously or directly so user sees immediate results or we can run synchronously
    # Running synchronously guarantees immediate results on upload response
    try:
        updated_ticket = run_pipeline(ticket_id)
        return {
            "ticket_id": updated_ticket.id,
            "category": updated_ticket.category,
            "status": updated_ticket.status,
            "verdict": updated_ticket.verdict,
            "message": "Ticket created and processed through AI verification pipeline."
        }
    except Exception as e:
        return {
            "ticket_id": ticket_id,
            "category": category,
            "status": "processing_failed",
            "verdict": "needs_review",
            "error": str(e)
        }


@app.get("/tickets")
def list_tickets():
    """GET /tickets: Returns all tickets with metadata, file lists, and stage summary."""
    with Session(engine) as session:
        tickets = session.exec(select(Ticket).order_by(Ticket.created_at.desc())).all()
        results = []
        for t in tickets:
            db_files = session.exec(select(TicketFile).where(TicketFile.ticket_id == t.id)).all()
            # Fetch doc_check snapshot if exists
            doc_check_stage = session.exec(
                select(StageOutput).where(
                    StageOutput.ticket_id == t.id,
                    StageOutput.stage_name == "check_documents"
                )
            ).first()
            missing_docs = []
            if doc_check_stage and doc_check_stage.output_json:
                try:
                    dc_data = json.loads(doc_check_stage.output_json)
                    missing_docs = dc_data.get("missing_types", [])
                except Exception:
                    pass

            results.append({
                "id": t.id,
                "category": t.category,
                "status": t.status,
                "verdict": t.verdict,
                "created_at": t.created_at,
                "files_count": len(db_files),
                "files": [f.filename for f in db_files],
                "missing_docs": missing_docs,
                "report_available": os.path.exists(f"./reports/ticket_{t.id}_report.docx")
            })
        return results


@app.get("/tickets/{ticket_id}")
def get_ticket(ticket_id: str):
    """GET /tickets/{id}: Returns comprehensive ticket details, snapshots, and validation checks."""
    with Session(engine) as session:
        ticket = session.get(Ticket, ticket_id)
        if not ticket:
            raise HTTPException(status_code=404, detail="Ticket not found")

        db_files = session.exec(select(TicketFile).where(TicketFile.ticket_id == ticket_id)).all()
        stage_records = session.exec(select(StageOutput).where(StageOutput.ticket_id == ticket_id)).all()

        stages_dict = {}
        for s in stage_records:
            try:
                stages_dict[s.stage_name] = {
                    "output": json.loads(s.output_json),
                    "prompt_version_hash": s.prompt_version_hash,
                    "created_at": s.created_at
                }
            except Exception:
                stages_dict[s.stage_name] = {
                    "output": s.output_json,
                    "prompt_version_hash": s.prompt_version_hash,
                    "created_at": s.created_at
                }

        report_path = f"./reports/ticket_{ticket_id}_report.docx"
        return {
            "id": ticket.id,
            "category": ticket.category,
            "status": ticket.status,
            "verdict": ticket.verdict,
            "created_at": ticket.created_at,
            "files": [{"filename": f.filename, "sha256": f.sha256} for f in db_files],
            "stages": stages_dict,
            "report_available": os.path.exists(report_path)
        }


@app.get("/tickets/{ticket_id}/report")
def download_report(ticket_id: str):
    """GET /tickets/{id}/report: Download generated Word .docx report."""
    report_path = f"./reports/ticket_{ticket_id}_report.docx"
    if not os.path.exists(report_path):
        raise HTTPException(status_code=404, detail="Report document not found or ticket was not approved for report.")

    return FileResponse(
        path=report_path,
        filename=f"Report_{ticket_id}.docx",
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )


@app.post("/tickets/{ticket_id}/rerun")
def rerun_ticket(ticket_id: str, from_stage: Optional[str] = "extract_text"):
    """
    POST /tickets/{id}/rerun?from_stage=...
    Re-executes the pipeline starting from from_stage using existing uploaded files and prior snapshots.
    """
    with Session(engine) as session:
        ticket = session.get(Ticket, ticket_id)
        if not ticket:
            raise HTTPException(status_code=404, detail="Ticket not found")

    try:
        updated = run_pipeline(ticket_id, from_stage=from_stage)
        return {
            "ticket_id": updated.id,
            "status": updated.status,
            "verdict": updated.verdict,
            "rerun_from_stage": from_stage,
            "message": f"Successfully re-executed pipeline from stage '{from_stage}'"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Pipeline rerun failed: {e}")
