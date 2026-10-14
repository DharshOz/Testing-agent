import argparse
import json
import logging
from pathlib import Path
from typing import Dict, Any, List

from pipeline.contracts import FileInfo, ExtractTextInput, ExtractFieldsInput, CheckDocumentsInput, ValidateTicketInput, GenerateReportInput
from pipeline.stages import extract_text, extract_fields, check_documents, validate_ticket, generate_report, STAGE_REGISTRY
from tests_agent.runner import TestRunner

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("snapshot")


def create_snapshot_for_case(case_id: str):
    """Executes each stage in order for case_id and saves golden/<stage>_output.json."""
    runner = TestRunner()
    case_dir = runner.cases_dir / case_id
    if not case_dir.is_dir():
        raise FileNotFoundError(f"Case directory {case_dir} does not exist")

    case_input = runner.load_case_input(case_id)
    ticket_id = case_input.get("ticket_id", case_id)
    category = case_input.get("category", "grant_request")

    files_info: List[FileInfo] = []
    for f in case_input.get("files", []):
        rel_path = f.get("path")
        abs_path = str(case_dir / rel_path) if rel_path else ""
        files_info.append(FileInfo(
            filename=f.get("filename", Path(rel_path).name),
            path=abs_path,
            sha256=f.get("sha256", "")
        ))

    logger.info(f"Generating snapshots for case: {case_id}")

    # Stage 1: extract_text
    inp1 = ExtractTextInput(ticket_id=ticket_id, files=files_info)
    out1 = extract_text(inp1)
    runner.save_golden_output(case_id, "extract_text", out1)
    logger.info(f"Saved golden/extract_text_output.json")

    # Stage 2: extract_fields
    inp2 = ExtractFieldsInput(ticket_id=ticket_id, extracted_files=out1.files)
    out2 = extract_fields(inp2)
    runner.save_golden_output(case_id, "extract_fields", out2)
    logger.info(f"Saved golden/extract_fields_output.json")

    # Stage 3: check_documents
    inp3 = CheckDocumentsInput(
        ticket_id=ticket_id,
        category=category,
        files=files_info,
        extracted_files=out1.files
    )
    out3 = check_documents(inp3)
    runner.save_golden_output(case_id, "check_documents", out3)
    logger.info(f"Saved golden/check_documents_output.json")

    # Stage 4: validate_ticket
    inp4 = ValidateTicketInput(
        ticket_id=ticket_id,
        category=category,
        extracted_fields=out2.fields,
        document_check=out3,
        extracted_files=out1.files
    )
    out4 = validate_ticket(inp4)
    runner.save_golden_output(case_id, "validate_ticket", out4)
    logger.info(f"Saved golden/validate_ticket_output.json")

    # Stage 5: generate_report
    report_docx = str(case_dir / f"golden_report_{case_id}.docx")
    inp5 = GenerateReportInput(
        ticket_id=ticket_id,
        category=category,
        created_at="2026-10-14T00:00:00",
        extracted_fields=out2.fields,
        document_check=out3,
        validation=out4,
        output_docx_path=report_docx
    )
    out5 = generate_report(inp5)
    runner.save_golden_output(case_id, "generate_report", out5)
    logger.info(f"Saved golden/generate_report_output.json")

    logger.info(f"All golden snapshots successfully generated for {case_id}.")


def main():
    parser = argparse.ArgumentParser(description="Snapshot script for creating frozen stage outputs")
    parser.add_argument("--case", type=str, default=None, help="Case ID to snapshot")
    parser.add_argument("--all", action="store_true", help="Snapshot all cases in tests_agent/cases")
    args = parser.parse_args()

    runner = TestRunner()
    cases_dir = runner.cases_dir

    if args.case:
        create_snapshot_for_case(args.case)
    elif args.all:
        cases = [p.name for p in cases_dir.iterdir() if p.is_dir()]
        for c in sorted(cases):
            create_snapshot_for_case(c)
    else:
        print("Please provide --case <id> or --all")


if __name__ == "__main__":
    main()
