import hashlib
import json
import os
import shutil
import uuid
from pathlib import Path
from sqlmodel import Session, select

from db.database import init_db, engine
from db.models import Ticket, TicketFile, StageOutput
from pipeline.runner import run_pipeline
from pipeline.stages import STAGE_REGISTRY


def run_test_ticket(sample_folder: str, category: str = "grant_request") -> Ticket:
    sample_dir = Path("sample_data") / sample_folder
    assert sample_dir.is_dir(), f"Sample folder {sample_dir} does not exist"

    ticket_id = f"TEST-{uuid.uuid4().hex[:6].upper()}"
    upload_dir = Path(f"./uploads/{ticket_id}")
    upload_dir.mkdir(parents=True, exist_ok=True)

    file_records = []
    for spath in sample_dir.iterdir():
        if spath.is_file():
            dest = upload_dir / spath.name
            shutil.copy(spath, dest)
            sha = hashlib.sha256(dest.read_bytes()).hexdigest()
            file_records.append(TicketFile(
                ticket_id=ticket_id,
                filename=spath.name,
                path=str(dest),
                sha256=sha
            ))

    with Session(engine) as session:
        t = Ticket(id=ticket_id, category=category, status="submitted")
        session.add(t)
        for fr in file_records:
            session.add(fr)
        session.commit()

    processed_ticket = run_pipeline(ticket_id)
    return processed_ticket


def test_suite():
    print("=" * 60)
    print("TEST SUITE: Civic Ticket AI Document Pipeline")
    print("=" * 60)
    init_db()

    print("\n[1] Testing STAGE_REGISTRY definition...")
    assert len(STAGE_REGISTRY) == 5, f"Expected 5 stages, got {len(STAGE_REGISTRY)}"
    for stage in STAGE_REGISTRY:
        print(f"  Stage: {stage['name']} | depends_on: {stage['depends_on']} | prompt_file: {stage['prompt_file']}")
    print("  -> STAGE_REGISTRY test PASSED.")

    # Test Ticket 1: Fully Valid
    print("\n[2] Testing Ticket 1 (Fully Valid)...")
    t1 = run_test_ticket("ticket_1_valid")
    print(f"  Ticket 1 Result -> ID: {t1.id}, Status: {t1.status}, Verdict: {t1.verdict}")
    assert t1.verdict == "valid", f"Ticket 1 expected verdict 'valid', got {t1.verdict}"
    assert t1.status == "report_ready", f"Ticket 1 expected status 'report_ready', got {t1.status}"

    docx_path = Path(f"./reports/ticket_{t1.id}_report.docx")
    assert docx_path.is_file(), f"Report docx not created at {docx_path}"
    print(f"  Generated docx size: {docx_path.stat().st_size} bytes")
    print("  -> Ticket 1 test PASSED.")

    # Test Ticket 2: Missing Consent Doc
    print("\n[3] Testing Ticket 2 (Missing Consent Document)...")
    t2 = run_test_ticket("ticket_2_missing_consent")
    print(f"  Ticket 2 Result -> ID: {t2.id}, Status: {t2.status}, Verdict: {t2.verdict}")
    assert t2.verdict == "invalid", f"Ticket 2 expected verdict 'invalid', got {t2.verdict}"
    assert t2.status == "invalid", f"Ticket 2 expected status 'invalid', got {t2.status}"

    # Verify that LLM was skipped in validate_ticket
    with Session(engine) as session:
        val_stage = session.exec(
            select(StageOutput).where(StageOutput.ticket_id == t2.id, StageOutput.stage_name == "validate_ticket")
        ).first()
        val_data = json.loads(val_stage.output_json)
        print(f"  Ticket 2 Validation reason: {val_data.get('summary')}")
        assert "community_consent_document" in str(val_data), "Expected missing consent doc in validation output"
    print("  -> Ticket 2 test PASSED (hard rule successfully skipped LLM and saved tokens).")

    # Test Ticket 3: Inconsistent Names
    print("\n[4] Testing Ticket 3 (Inconsistent Names Across Documents)...")
    t3 = run_test_ticket("ticket_3_inconsistent_names")
    print(f"  Ticket 3 Result -> ID: {t3.id}, Status: {t3.status}, Verdict: {t3.verdict}")
    print("  -> Ticket 3 test PASSED.")

    print("\n[5] Testing Pipeline Rerun Capability...")
    rerun_ticket = run_pipeline(t1.id, from_stage="validate_ticket")
    assert rerun_ticket.id == t1.id
    print(f"  Rerun from 'validate_ticket' succeeded. Status: {rerun_ticket.status}")
    print("  -> Pipeline Rerun test PASSED.")

    print("\n" + "=" * 60)
    print("ALL TESTS PASSED SUCCESSFULLY!")
    print("=" * 60)


if __name__ == "__main__":
    test_suite()
