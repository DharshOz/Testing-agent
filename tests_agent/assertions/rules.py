import difflib
import os
import re
from pathlib import Path
from typing import Dict, Any, List, Optional
from docx import Document


def _clean_str(s: str) -> str:
    """Normalize string for fuzzy comparison."""
    if not s:
        return ""
    return re.sub(r"[^\w\s]", "", str(s).lower()).strip()


def _is_fuzzy_substring(needle: str, haystack: str, threshold: float = 0.70) -> bool:
    """Checks if needle is approximately contained in haystack."""
    c_needle = _clean_str(needle)
    c_haystack = _clean_str(haystack)
    if not c_needle:
        return True
    if c_needle in c_haystack:
        return True

    # Word-level overlap
    needle_words = set(c_needle.split())
    haystack_words = set(c_haystack.split())
    if not needle_words:
        return True
    overlap = len(needle_words.intersection(haystack_words)) / len(needle_words)
    return overlap >= threshold


# =========================================================================
# STAGE RULES
# =========================================================================

def assert_rules_extract_text(output_data: Dict[str, Any], expected: Dict[str, Any]) -> List[Dict[str, Any]]:
    results = []
    files = output_data.get("files", [])
    exp_files = expected.get("expected_files", [])

    # Check file count
    passed_cnt = len(files) >= len(exp_files) if exp_files else len(files) > 0
    results.append({
        "name": "Text Extraction Non-Empty Files Count",
        "passed": passed_cnt,
        "reason": f"Expected at least {len(exp_files)} extracted files, got {len(files)}",
        "evidence": f"Filenames: {[f.get('filename') for f in files]}"
    })

    # Check non-empty text for non-empty files
    empty_allowed = expected.get("allow_empty_files", [])
    for f in files:
        fname = f.get("filename", "")
        raw_text = f.get("raw_text", "").strip()
        is_empty = len(raw_text) == 0

        if fname in empty_allowed:
            results.append({
                "name": f"Extraction Text Presence ({fname})",
                "passed": is_empty,
                "reason": f"File {fname} is expected to be empty/scanned",
                "evidence": f"Length: {len(raw_text)}"
            })
        else:
            results.append({
                "name": f"Extraction Text Presence ({fname})",
                "passed": not is_empty,
                "reason": f"File {fname} text must be non-empty",
                "evidence": f"Extracted chars: {len(raw_text)}, pages: {f.get('page_count')}"
            })

    # Warnings check
    expected_warnings = expected.get("expected_warnings", None)
    if expected_warnings is not None:
        all_warnings = [w for f in files for w in f.get("extraction_warnings", [])]
        results.append({
            "name": "Extraction Warnings Presence",
            "passed": len(all_warnings) >= len(expected_warnings),
            "reason": f"Expected warnings: {expected_warnings}",
            "evidence": str(all_warnings)
        })

    return results


def assert_rules_extract_fields(output_data: Dict[str, Any], expected: Dict[str, Any], raw_texts: List[str] = None) -> List[Dict[str, Any]]:
    results = []
    fields = output_data.get("fields", {})
    all_raw = " ".join(raw_texts or [])

    # Expected absent fields must be null (no guessing)
    absent_fields = expected.get("absent_fields", [])
    for af in absent_fields:
        f_val = fields.get(af, {}).get("value")
        results.append({
            "name": f"Absent Field Null Check ({af})",
            "passed": f_val is None,
            "reason": f"Field '{af}' must return null if not present in context",
            "evidence": f"Value returned: {f_val}"
        })

    # Anti-hallucination check: every non-null value must appear in source_chunk_text and raw_text
    for fname, f_info in fields.items():
        val = f_info.get("value")
        conf = f_info.get("confidence", 0.0)
        source_chunk = f_info.get("source_chunk_text") or ""

        # Confidence bounds check
        results.append({
            "name": f"Confidence Range Check ({fname})",
            "passed": 0.0 <= conf <= 1.0,
            "reason": f"Confidence {conf} must be in range [0.0, 1.0]",
            "evidence": f"Confidence: {conf}"
        })

        if val is not None and str(val).strip():
            # Check containment in chunk
            in_chunk = _is_fuzzy_substring(str(val), source_chunk)
            results.append({
                "name": f"Anti-Hallucination Chunk Check ({fname})",
                "passed": in_chunk,
                "reason": f"Extracted value '{val}' must be grounded in source_chunk_text",
                "evidence": f"Chunk snippet: {source_chunk[:150]}"
            })

            # Check containment in full document raw text if raw_texts provided
            if all_raw:
                in_raw = _is_fuzzy_substring(str(val), all_raw)
                results.append({
                    "name": f"Anti-Hallucination Raw Document Check ({fname})",
                    "passed": in_raw,
                    "reason": f"Extracted value '{val}' must exist in raw document text",
                    "evidence": f"Value: '{val}'"
                })

    return results


def assert_rules_check_documents(output_data: Dict[str, Any], expected: Dict[str, Any]) -> List[Dict[str, Any]]:
    results = []

    # 1. all_required_present check
    exp_all_present = expected.get("all_required_present")
    actual_all_present = output_data.get("all_required_present")
    if exp_all_present is not None:
        results.append({
            "name": "Document Check All Required Present",
            "passed": actual_all_present == exp_all_present,
            "reason": f"Expected all_required_present={exp_all_present}, got {actual_all_present}",
            "evidence": f"Missing doc types: {output_data.get('missing_types')}"
        })

    # 2. missing_types exact match
    exp_missing = expected.get("missing_types")
    if exp_missing is not None:
        actual_missing = set(output_data.get("missing_types", []))
        passed_missing = actual_missing == set(exp_missing)
        results.append({
            "name": "Missing Document Types Exact Match",
            "passed": passed_missing,
            "reason": f"Expected missing types {exp_missing}, got {list(actual_missing)}",
            "evidence": str(actual_missing)
        })

    # 3. empty_files, duplicate_files check
    exp_empty = expected.get("empty_files")
    if exp_empty is not None:
        actual_empty = set(output_data.get("empty_files", []))
        results.append({
            "name": "Empty Files Flagged Check",
            "passed": actual_empty == set(exp_empty),
            "reason": f"Expected empty files {exp_empty}, got {list(actual_empty)}",
            "evidence": str(actual_empty)
        })

    exp_dup = expected.get("duplicate_files")
    if exp_dup is not None:
        actual_dup = set(output_data.get("duplicate_files", []))
        results.append({
            "name": "Duplicate Files Flagged Check",
            "passed": actual_dup == set(exp_dup),
            "reason": f"Expected duplicates {exp_dup}, got {list(actual_dup)}",
            "evidence": str(actual_dup)
        })

    return results


def assert_rules_validate_ticket(output_data: Dict[str, Any], expected: Dict[str, Any], prev_stage_doc_check: Dict[str, Any] = None) -> List[Dict[str, Any]]:
    results = []

    actual_verdict = output_data.get("verdict")
    exp_verdict = expected.get("verdict")

    # 1. Expected verdict check
    if exp_verdict is not None:
        results.append({
            "name": "Validation Verdict Match",
            "passed": actual_verdict == exp_verdict,
            "reason": f"Expected verdict '{exp_verdict}', got '{actual_verdict}'",
            "evidence": f"Summary: {output_data.get('summary')}"
        })

    # 2. Hard rule check: if documents were missing, verdict MUST be invalid
    if prev_stage_doc_check:
        all_present = prev_stage_doc_check.get("all_required_present", True)
        if not all_present:
            results.append({
                "name": "Hard Rule: Token-Saving Invalid On Missing Docs",
                "passed": actual_verdict == "invalid",
                "reason": "When required documents are missing, ticket MUST be invalid without invoking LLM",
                "evidence": f"Verdict: {actual_verdict}"
            })

    # 3. Every validation check item must have evidence and reason
    checks = output_data.get("checks", [])
    has_empty_evidence = any(not c.get("evidence", "").strip() for c in checks)
    results.append({
        "name": "Validation Checks Evidence Completeness",
        "passed": (len(checks) > 0) and not has_empty_evidence,
        "reason": "All audit checks must provide non-empty evidence and rationale",
        "evidence": f"Checks count: {len(checks)}"
    })

    return results


def assert_rules_generate_report(output_data: Dict[str, Any], expected: Dict[str, Any], prev_verdict: str = "valid") -> List[Dict[str, Any]]:
    results = []

    docx_path = output_data.get("docx_path", "")
    sections = output_data.get("structured_sections", {})

    if prev_verdict != "valid":
        # Report should not be generated for invalid/needs_review
        results.append({
            "name": "Report Suppression on Non-Valid Ticket",
            "passed": not docx_path or not os.path.exists(docx_path),
            "reason": "Official report must not be generated when verdict != valid",
            "evidence": f"Docx path: {docx_path}"
        })
        return results

    # 1. DOCX file exists and opens
    file_exists = os.path.isfile(docx_path) if docx_path else False
    can_open = False
    doc_headings = []
    doc_text = ""

    if file_exists:
        try:
            doc = Document(docx_path)
            can_open = True
            doc_headings = [p.text for p in doc.paragraphs if p.style.name.startswith("Heading")]
            doc_text = " ".join(p.text for p in doc.paragraphs)
        except Exception as e:
            can_open = False

    results.append({
        "name": "Generated DOCX Readability",
        "passed": file_exists and can_open,
        "reason": "Report docx file must exist and open with python-docx",
        "evidence": f"Path: {docx_path}, Exists: {file_exists}, Openable: {can_open}"
    })

    # 2. Key sections presence
    required_headings = expected.get("required_headings", ["Executive Summary", "Applicant & Submission Details", "Civic Request Details", "Supporting Documents Audit"])
    missing_headings = [h for h in required_headings if not any(h.lower() in dh.lower() for dh in doc_headings)]
    results.append({
        "name": "Required Document Headings Presence",
        "passed": len(missing_headings) == 0,
        "reason": f"Report must include standard headings: {required_headings}",
        "evidence": f"Missing: {missing_headings}"
    })

    # 3. Applicant Name and Ticket ID presence
    exp_applicant = expected.get("applicant_name")
    if exp_applicant and doc_text:
        results.append({
            "name": "Applicant Name Presence in Report",
            "passed": _is_fuzzy_substring(exp_applicant, doc_text),
            "reason": f"Applicant name '{exp_applicant}' must appear in the generated report",
            "evidence": f"Found in document text: {_is_fuzzy_substring(exp_applicant, doc_text)}"
        })

    return results


def assert_rules(
    stage_name: str,
    output_data: Dict[str, Any],
    expected: Dict[str, Any],
    context: Dict[str, Any] = None
) -> List[Dict[str, Any]]:
    """Dispatches deterministic rule assertions for the specific stage."""
    context = context or {}
    if stage_name == "extract_text":
        return assert_rules_extract_text(output_data, expected)
    elif stage_name == "extract_fields":
        raw_texts = context.get("raw_texts", [])
        return assert_rules_extract_fields(output_data, expected, raw_texts)
    elif stage_name == "check_documents":
        return assert_rules_check_documents(output_data, expected)
    elif stage_name == "validate_ticket":
        prev_dc = context.get("prev_stage_doc_check", {})
        return assert_rules_validate_ticket(output_data, expected, prev_dc)
    elif stage_name == "generate_report":
        prev_v = context.get("prev_verdict", "valid")
        return assert_rules_generate_report(output_data, expected, prev_v)
    return []
