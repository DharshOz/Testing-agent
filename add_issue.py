import argparse
import csv
import hashlib
import json
import os
import shutil
from pathlib import Path
import yaml
from snapshot import create_snapshot_for_case


def add_issue(
    stage: str,
    data_type: str,
    issue_desc: str,
    reason: str,
    input_files: list[str],
    category: str = "grant_request",
    split: str = "tune"
):
    """
    Creates a new regression case in tests_agent/cases/<case_id>,
    runs snapshot to generate golden outputs, writes expected.yaml skeleton,
    and appends a new row to issues/issues.csv.
    """
    issues_csv_path = Path("issues/issues.csv")
    issues_csv_path.parent.mkdir(parents=True, exist_ok=True)

    # Determine next issue ID
    issue_id = 1
    if issues_csv_path.is_file():
        with open(issues_csv_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            existing_ids = [int(row["id"]) for row in reader if row.get("id") and row["id"].isdigit()]
            if existing_ids:
                issue_id = max(existing_ids) + 1

    case_id = f"issue_case_{issue_id:03d}_{data_type}"
    case_dir = Path("tests_agent/cases") / case_id
    files_dir = case_dir / "files"
    files_dir.mkdir(parents=True, exist_ok=True)

    # Copy input files
    file_entries = []
    for fpath in input_files:
        src = Path(fpath)
        if not src.is_file():
            raise FileNotFoundError(f"Input file not found: {fpath}")
        dst = files_dir / src.name
        shutil.copy(src, dst)
        sha = hashlib.sha256(dst.read_bytes()).hexdigest()
        file_entries.append({
            "filename": src.name,
            "path": f"files/{src.name}",
            "sha256": sha
        })

    # Write input.json
    input_data = {
        "ticket_id": f"TICK-{case_id.upper()}",
        "category": category,
        "files": file_entries
    }
    with open(case_dir / "input.json", "w", encoding="utf-8") as f:
        json.dump(input_data, f, indent=2)

    # Write meta.yaml
    meta_data = {
        "case_id": case_id,
        "data_type": data_type,
        "split": split,
        "must_pass": True
    }
    with open(case_dir / "meta.yaml", "w", encoding="utf-8") as f:
        yaml.safe_dump(meta_data, f)

    # Generate initial snapshot
    print(f"Running snapshot for new issue case {case_id}...")
    create_snapshot_for_case(case_id)

    # Write expected.yaml skeleton
    expected_skeleton = {
        "extract_text": {
            "expected_files": [f["filename"] for f in file_entries]
        },
        "extract_fields": {
            "absent_fields": []
        },
        "check_documents": {
            "all_required_present": True,
            "missing_types": []
        },
        "validate_ticket": {
            "verdict": "valid"
        },
        "generate_report": {
            "required_headings": ["Executive Summary", "Applicant & Submission Details"]
        }
    }
    with open(case_dir / "expected.yaml", "w", encoding="utf-8") as f:
        yaml.safe_dump(expected_skeleton, f)

    # Append to issues.csv
    file_exists = issues_csv_path.is_file()
    headers = [
        "id", "stage", "data_type", "issue", "reason", "test_case",
        "expected_behavior", "status", "attempts", "resolution_note", "linked_pr"
    ]

    with open(issues_csv_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        if not file_exists:
            writer.writeheader()

        writer.writerow({
            "id": str(issue_id),
            "stage": stage,
            "data_type": data_type,
            "issue": issue_desc,
            "reason": reason,
            "test_case": case_id,
            "expected_behavior": f"Stage {stage} should handle {data_type} gracefully and satisfy contracts.",
            "status": "open",
            "attempts": "0",
            "resolution_note": "",
            "linked_pr": ""
        })

    print(f"[OK] Issue #{issue_id} successfully logged and regression test case created at {case_dir}")


def main():
    parser = argparse.ArgumentParser(description="Log a new issue and auto-generate regression test case")
    parser.add_argument("--stage", required=True, help="Stage where issue occurs (e.g. extract_fields)")
    parser.add_argument("--data-type", required=True, help="Data type tag (e.g. scanned_pdf, multilingual)")
    parser.add_argument("--issue", required=True, help="Issue description")
    parser.add_argument("--reason", required=True, help="Why the failure occurs")
    parser.add_argument("--files", nargs="+", required=True, help="Input file paths")
    parser.add_argument("--category", default="grant_request", help="Ticket category")
    parser.add_argument("--split", default="tune", choices=["tune", "heldout"], help="Split tag")

    args = parser.parse_args()
    add_issue(
        stage=args.stage,
        data_type=args.data_type,
        issue_desc=args.issue,
        reason=args.reason,
        input_files=args.files,
        category=args.category,
        split=args.split
    )


if __name__ == "__main__":
    main()
