import json
import logging
from pathlib import Path
from typing import Optional, Dict, Any, List
from sqlmodel import Session, select

from db.database import engine
from db.models import Ticket, TicketFile, StageOutput
from pipeline.contracts import (
    FileInfo,
    ExtractTextInput, ExtractTextOutput,
    ExtractFieldsInput, ExtractFieldsOutput,
    CheckDocumentsInput, CheckDocumentsOutput,
    ValidateTicketInput, ValidateTicketOutput,
    GenerateReportInput, GenerateReportOutput
)
from pipeline.stages import (
    extract_text, extract_fields, check_documents, validate_ticket, generate_report,
    STAGE_REGISTRY
)
from pipeline.llm_client import load_and_render_prompt

logger = logging.getLogger("pipeline.runner")


def save_stage_snapshot(
    ticket_id: str,
    stage_name: str,
    output_obj: Any,
    prompt_file: Optional[str] = None
) -> None:
    """Saves serialized stage output JSON snapshot and prompt hash into SQLite."""
    prompt_hash = None
    if prompt_file:
        try:
            _, prompt_hash = load_and_render_prompt(prompt_file, {})
        except Exception:
            prompt_hash = None

    json_str = output_obj.model_dump_json() if hasattr(output_obj, "model_dump_json") else json.dumps(output_obj)

    with Session(engine) as session:
        # Delete existing snapshot for this stage if rerunning
        existing = session.exec(
            select(StageOutput).where(
                StageOutput.ticket_id == ticket_id,
                StageOutput.stage_name == stage_name
            )
        ).all()
        for rec in existing:
            session.delete(rec)

        stage_record = StageOutput(
            ticket_id=ticket_id,
            stage_name=stage_name,
            output_json=json_str,
            prompt_version_hash=prompt_hash
        )
        session.add(stage_record)
        session.commit()


def get_stage_snapshot(ticket_id: str, stage_name: str) -> Optional[Dict[str, Any]]:
    """Loads saved stage snapshot from SQLite."""
    with Session(engine) as session:
        rec = session.exec(
            select(StageOutput).where(
                StageOutput.ticket_id == ticket_id,
                StageOutput.stage_name == stage_name
            )
        ).first()
        if rec and rec.output_json:
            return json.loads(rec.output_json)
    return None


def run_pipeline(ticket_id: str, from_stage: Optional[str] = None) -> Ticket:
    """
    Executes pipeline stages in order with snapshot persistence.
    If from_stage is provided, loads preceding stages from snapshots and starts from from_stage.
    """
    with Session(engine) as session:
        ticket = session.get(Ticket, ticket_id)
        if not ticket:
            raise ValueError(f"Ticket {ticket_id} not found")

        db_files = session.exec(select(TicketFile).where(TicketFile.ticket_id == ticket_id)).all()
        files_info: List[FileInfo] = [
            FileInfo(filename=f.filename, path=f.path, sha256=f.sha256)
            for f in db_files
        ]

        ticket.status = "processing"
        session.add(ticket)
        session.commit()
        session.refresh(ticket)

    logger.info(f"Starting pipeline execution for ticket {ticket_id} (from_stage={from_stage})")

    stage_names = ["extract_text", "extract_fields", "check_documents", "validate_ticket", "generate_report"]
    start_index = stage_names.index(from_stage) if from_stage in stage_names else 0

    # Intermediate outputs
    out_extract_text: Optional[ExtractTextOutput] = None
    out_extract_fields: Optional[ExtractFieldsOutput] = None
    out_check_docs: Optional[CheckDocumentsOutput] = None
    out_validate: Optional[ValidateTicketOutput] = None

    # Load prior stages from snapshot if resuming
    if start_index > 0:
        if start_index >= 1:
            data = get_stage_snapshot(ticket_id, "extract_text")
            if data:
                out_extract_text = ExtractTextOutput.model_validate(data)
        if start_index >= 2:
            data = get_stage_snapshot(ticket_id, "extract_fields")
            if data:
                out_extract_fields = ExtractFieldsOutput.model_validate(data)
        if start_index >= 3:
            data = get_stage_snapshot(ticket_id, "check_documents")
            if data:
                out_check_docs = CheckDocumentsOutput.model_validate(data)
        if start_index >= 4:
            data = get_stage_snapshot(ticket_id, "validate_ticket")
            if data:
                out_validate = ValidateTicketOutput.model_validate(data)

    # 1. extract_text
    if start_index <= 0 or not out_extract_text:
        logger.info(f"Running stage: extract_text for ticket {ticket_id}")
        inp1 = ExtractTextInput(ticket_id=ticket_id, files=files_info)
        out_extract_text = extract_text(inp1)
        save_stage_snapshot(ticket_id, "extract_text", out_extract_text, prompt_file=None)

    # 2. extract_fields
    if start_index <= 1 or not out_extract_fields:
        logger.info(f"Running stage: extract_fields for ticket {ticket_id}")
        inp2 = ExtractFieldsInput(ticket_id=ticket_id, extracted_files=out_extract_text.files)
        out_extract_fields = extract_fields(inp2)
        save_stage_snapshot(ticket_id, "extract_fields", out_extract_fields, prompt_file="prompts/extract_fields.md")

    # 3. check_documents
    if start_index <= 2 or not out_check_docs:
        logger.info(f"Running stage: check_documents for ticket {ticket_id}")
        inp3 = CheckDocumentsInput(
            ticket_id=ticket_id,
            category=ticket.category,
            files=files_info,
            extracted_files=out_extract_text.files
        )
        out_check_docs = check_documents(inp3)
        save_stage_snapshot(ticket_id, "check_documents", out_check_docs, prompt_file="prompts/classify_doc.md")

    # 4. validate_ticket
    if start_index <= 3 or not out_validate:
        logger.info(f"Running stage: validate_ticket for ticket {ticket_id}")
        inp4 = ValidateTicketInput(
            ticket_id=ticket_id,
            category=ticket.category,
            extracted_fields=out_extract_fields.fields,
            document_check=out_check_docs,
            extracted_files=out_extract_text.files
        )
        out_validate = validate_ticket(inp4)
        save_stage_snapshot(ticket_id, "validate_ticket", out_validate, prompt_file="prompts/validate_ticket.md")

    # Update ticket verdict and status based on validation
    with Session(engine) as session:
        t = session.get(Ticket, ticket_id)
        t.verdict = out_validate.verdict

        if out_validate.verdict == "invalid":
            t.status = "invalid"
            session.add(t)
            session.commit()
            session.refresh(t)
            logger.info(f"Ticket {ticket_id} validated as INVALID. Halting.")
            return t

        elif out_validate.verdict == "needs_review":
            t.status = "needs_review"
            session.add(t)
            session.commit()
            session.refresh(t)
            logger.info(f"Ticket {ticket_id} routed to NEEDS_REVIEW. Halting.")
            return t

        else:
            t.status = "approved_for_report"
            session.add(t)
            session.commit()
            session.refresh(t)

    # 5. generate_report (runs if verdict == valid)
    logger.info(f"Running stage: generate_report for ticket {ticket_id}")
    report_docx_path = f"./reports/ticket_{ticket_id}_report.docx"
    inp5 = GenerateReportInput(
        ticket_id=ticket_id,
        category=ticket.category,
        created_at=ticket.created_at,
        extracted_fields=out_extract_fields.fields,
        document_check=out_check_docs,
        validation=out_validate,
        output_docx_path=report_docx_path
    )
    out_report = generate_report(inp5)
    save_stage_snapshot(ticket_id, "generate_report", out_report, prompt_file="prompts/generate_report.md")

    with Session(engine) as session:
        t = session.get(Ticket, ticket_id)
        t.status = "report_ready"
        session.add(t)
        session.commit()
        session.refresh(t)
        logger.info(f"Pipeline finished for ticket {ticket_id}: status={t.status}")
        return t
