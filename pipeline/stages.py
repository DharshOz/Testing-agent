import os
import re
import json
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional

import yaml
from docx import Document
from docx.shared import Inches, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT

from pipeline.contracts import (
    FileInfo,
    ExtractTextInput, ExtractTextOutput, ExtractedFileText,
    ExtractFieldsInput, ExtractFieldsOutput, FieldResult,
    CheckDocumentsInput, CheckDocumentsOutput, RequiredDocCheckResult,
    ValidateTicketInput, ValidateTicketOutput, ValidationCheckItem,
    GenerateReportInput, GenerateReportOutput
)
from pipeline.llm_client import llm_call, load_and_render_prompt

logger = logging.getLogger("pipeline.stages")

# =====================================================================
# STAGE 1: extract_text
# =====================================================================
def extract_text(input_data: ExtractTextInput) -> ExtractTextOutput:
    """
    Extracts text from uploaded files:
    - PDF: pypdf / pdfplumber with OCR fallback via pytesseract
    - DOCX: python-docx
    - TXT: standard text decoding
    No LLM used here.
    """
    extracted_files: List[ExtractedFileText] = []

    for file_info in input_data.files:
        path = Path(file_info.path)
        warnings: List[str] = []
        raw_text = ""
        page_count = 1

        if not path.is_file():
            extracted_files.append(ExtractedFileText(
                filename=file_info.filename,
                raw_text="",
                page_count=0,
                extraction_warnings=[f"File not found on disk: {file_info.path}"]
            ))
            continue

        ext = path.suffix.lower()

        if ext == ".pdf":
            try:
                import pdfplumber
                with pdfplumber.open(str(path)) as pdf:
                    page_count = len(pdf.pages)
                    text_pages = []
                    for idx, page in enumerate(pdf.pages):
                        page_text = page.extract_text() or ""
                        if not page_text.strip():
                            # Optional OCR fallback if page has no digital text
                            try:
                                import pytesseract
                                pil_image = page.to_image(resolution=150).original
                                ocr_text = pytesseract.image_to_string(pil_image)
                                if ocr_text.strip():
                                    page_text = ocr_text
                                    warnings.append(f"Page {idx+1} used OCR extraction.")
                            except Exception as ocr_err:
                                warnings.append(f"OCR skipped for page {idx+1}: {ocr_err}")
                        text_pages.append(page_text)
                    raw_text = "\n\n".join(text_pages)
            except Exception as e:
                warnings.append(f"pdfplumber extraction failed: {e}. Trying pypdf fallback.")
                try:
                    import pypdf
                    reader = pypdf.PdfReader(str(path))
                    page_count = len(reader.pages)
                    text_pages = [p.extract_text() or "" for p in reader.pages]
                    raw_text = "\n\n".join(text_pages)
                except Exception as pypdf_err:
                    warnings.append(f"pypdf extraction failed: {pypdf_err}")

        elif ext in [".docx", ".doc"]:
            try:
                doc = Document(str(path))
                paragraphs = [p.text for p in doc.paragraphs if p.text]
                # Also include table texts
                for table in doc.tables:
                    for row in table.rows:
                        row_text = " | ".join(cell.text.strip() for cell in row.cells if cell.text.strip())
                        if row_text:
                            paragraphs.append(row_text)
                raw_text = "\n\n".join(paragraphs)
                page_count = max(1, len(raw_text) // 2500)
            except Exception as e:
                warnings.append(f"docx extraction error: {e}")

        elif ext in [".txt", ".md", ".csv", ".json", ".yaml", ".yml"]:
            try:
                raw_text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                try:
                    raw_text = path.read_text(encoding="latin-1")
                except Exception as e:
                    warnings.append(f"Text reading error: {e}")
            page_count = max(1, len(raw_text) // 2500)

        else:
            warnings.append(f"Unsupported file format: {ext}")

        extracted_files.append(ExtractedFileText(
            filename=file_info.filename,
            raw_text=raw_text.strip(),
            page_count=page_count,
            extraction_warnings=warnings
        ))

    return ExtractTextOutput(files=extracted_files)


# =====================================================================
# STAGE 2: extract_fields (RAG)
# =====================================================================
TARGET_FIELDS = [
    {"name": "applicant_name", "description": "Full legal name of the applicant individual or organization"},
    {"name": "applicant_id", "description": "Government identification number, Aadhaar number, PAN card, or official registration ID"},
    {"name": "contact", "description": "Phone number, mobile number, or email address of the applicant"},
    {"name": "date", "description": "Date of the application, letter, or submission (e.g. YYYY-MM-DD)"},
    {"name": "issue_title", "description": "Short title or subject matter of the ticket, request, or complaint"},
    {"name": "issue_description", "description": "Detailed explanation of the issue, project scope, or requested support"},
    {"name": "amount_requested", "description": "Monetary or budget amount requested or sanctioned (e.g. Rs 50,000, 10 Lakhs, etc.)"},
    {"name": "department", "description": "Target municipal, civic, or government department (e.g. Public Works, Water Supply)"},
    {"name": "supporting_references", "description": "Reference numbers, sanction order numbers, or resolution numbers mentioned"}
]

def chunk_text(text: str, chunk_size: int = 500, overlap: int = 50) -> List[str]:
    """Simple robust character/word chunking (~500 chars, 50 overlap)."""
    words = text.split()
    if not words:
        return []
    chunks = []
    current_words = []
    current_len = 0
    
    # Approx 75-100 words ~ 500 chars
    words_per_chunk = 80
    words_overlap = 10
    
    i = 0
    while i < len(words):
        chunk = " ".join(words[i:i + words_per_chunk])
        chunks.append(chunk)
        i += max(1, words_per_chunk - words_overlap)
    return chunks


def extract_fields(input_data: ExtractFieldsInput) -> ExtractFieldsOutput:
    """
    RAG Stage:
    1. Chunk text from each extracted file (~500 tokens/chars, 50 overlap).
    2. Embed using fastembed (BAAI/bge-small-en-v1.5).
    3. Upsert into Qdrant in local embedded mode (collection: ticket_{ticket_id}).
    4. For each target field, query top-k=4 chunks.
    5. Call LLM with prompts/extract_fields.md.
    """
    from qdrant_client import QdrantClient
    from qdrant_client.models import Distance, VectorParams, PointStruct
    from fastembed import TextEmbedding

    # 1. Initialize local Qdrant
    qdrant_path = "./qdrant_data"
    os.makedirs(qdrant_path, exist_ok=True)
    client = QdrantClient(path=qdrant_path)

    collection_name = f"ticket_{input_data.ticket_id}".replace("-", "_")

    # Initialize fastembed embedding model
    embedding_model = TextEmbedding(model_name="BAAI/bge-small-en-v1.5")

    # 2. Prepare chunks
    all_chunks: List[Dict[str, Any]] = []
    chunk_texts: List[str] = []

    for file_doc in input_data.extracted_files:
        if not file_doc.raw_text:
            continue
        chunks = chunk_text(file_doc.raw_text)
        for idx, text in enumerate(chunks):
            payload = {
                "filename": file_doc.filename,
                "chunk_index": idx,
                "text": text
            }
            all_chunks.append(payload)
            chunk_texts.append(text)

    # 3. Embed and upsert into Qdrant
    if chunk_texts:
        embeddings = list(embedding_model.embed(chunk_texts))
        vector_dim = len(embeddings[0])

        client.recreate_collection(
            collection_name=collection_name,
            vectors_config=VectorParams(size=vector_dim, distance=Distance.COSINE),
        )

        points = [
            PointStruct(
                id=i,
                vector=embeddings[i].tolist(),
                payload=all_chunks[i]
            )
            for i in range(len(chunk_texts))
        ]
        client.upsert(collection_name=collection_name, points=points)

    # 4. Extract each target field
    field_results: Dict[str, FieldResult] = {}
    prompt_file = "prompts/extract_fields.md"

    for target in TARGET_FIELDS:
        field_name = target["name"]
        field_desc = target["description"]

        retrieved_chunks = []
        if chunk_texts:
            query_text = f"{field_name}: {field_desc}"
            query_vector = list(embedding_model.embed([query_text]))[0].tolist()
            
            if hasattr(client, "query_points"):
                query_res = client.query_points(
                    collection_name=collection_name,
                    query=query_vector,
                    limit=4
                )
                retrieved_chunks = [hit.payload for hit in query_res.points if hit.payload]
            else:
                search_results = client.search(
                    collection_name=collection_name,
                    query_vector=query_vector,
                    limit=4
                )
                retrieved_chunks = [hit.payload for hit in search_results if hit.payload]

        if not retrieved_chunks:
            field_results[field_name] = FieldResult(
                value=None,
                confidence=0.0,
                source_filename=None,
                source_chunk_text=None
            )
            continue

        variables = {
            "field_name": field_name,
            "field_description": field_desc,
            "chunks": retrieved_chunks
        }

        try:
            field_res, _ = llm_call(
                prompt_file=prompt_file,
                variables=variables,
                schema=FieldResult
            )
            field_results[field_name] = field_res
        except Exception as e:
            logger.error(f"Failed extracting field {field_name}: {e}")
            field_results[field_name] = FieldResult(
                value=None,
                confidence=0.0,
                source_filename=None,
                source_chunk_text=f"Extraction error: {e}"
            )
        import time
        time.sleep(0.15)

    return ExtractFieldsOutput(fields=field_results)


# =====================================================================
# STAGE 3: check_documents (First Validation Layer)
# =====================================================================
def load_required_docs_config(category: str) -> Dict[str, Any]:
    """Loads required document definitions from config/required_docs.yaml."""
    config_path = Path("config/required_docs.yaml")
    if not config_path.is_file():
        project_root = Path(__file__).resolve().parent.parent
        config_path = project_root / "config/required_docs.yaml"

    if config_path.is_file():
        with open(config_path, "r", encoding="utf-8") as f:
            full_config = yaml.safe_load(f) or {}
            return full_config.get(category, {})
    return {}


def check_documents(input_data: CheckDocumentsInput) -> CheckDocumentsOutput:
    """
    Checks documents against requirements in config/required_docs.yaml:
    - Flags empty/unreadable files
    - Flags duplicate files by sha256
    - Flags unsupported file extensions
    - Classifies files using keyword rules first
    - Fallback to LLM (prompts/classify_doc.md) only if rules are inconclusive
    """
    cat_config = load_required_docs_config(input_data.category)
    required_specs = cat_config.get("required_documents", [])

    empty_files: List[str] = []
    duplicate_files: List[str] = []
    unsupported_files: List[str] = []

    seen_hashes: Dict[str, str] = {}
    for f in input_data.files:
        if f.sha256 in seen_hashes:
            duplicate_files.append(f.filename)
        else:
            seen_hashes[f.sha256] = f.filename

        ext = Path(f.filename).suffix.lower()
        if ext not in [".pdf", ".docx", ".doc", ".txt", ".md"]:
            unsupported_files.append(f.filename)

    extracted_map: Dict[str, ExtractedFileText] = {
        ef.filename: ef for ef in input_data.extracted_files
    }

    for f in input_data.files:
        ef = extracted_map.get(f.filename)
        if not ef or not ef.raw_text.strip():
            empty_files.append(f.filename)

    # Classify files
    classified_files: Dict[str, Dict[str, Any]] = {}  # filename -> {doc_type, method, confidence}

    for ef in input_data.extracted_files:
        if not ef.raw_text.strip():
            continue
        
        text_lower = ef.raw_text.lower()
        fname_lower = ef.filename.lower()
        matched_type = None
        method = "none"
        conf = 0.0

        # 1. Scored rule-based keyword matching across all candidates
        best_score = 0
        best_type = None

        for req in required_specs:
            type_key = req["type"]
            keywords = req.get("keywords", [])
            score = 0
            for kw in keywords:
                if kw in fname_lower:
                    score += 3
                if kw in text_lower:
                    score += 1
            if score > best_score:
                best_score = score
                best_type = type_key

        if best_type and best_score >= 1:
            matched_type = best_type
            method = "rule_keyword"
            conf = min(0.99, 0.70 + (best_score * 0.05))

        # 2. LLM fallback if keyword rules are inconclusive
        if not matched_type and len(text_lower) > 30:
            prompt_file = "prompts/classify_doc.md"
            excerpt = ef.raw_text[:1200]
            variables = {
                "filename": ef.filename,
                "doc_excerpt": excerpt,
                "candidate_types": required_specs
            }
            try:
                res_dict, _ = llm_call(prompt_file=prompt_file, variables=variables)
                if res_dict.get("doc_type"):
                    matched_type = res_dict["doc_type"]
                    method = "llm_fallback"
                    conf = float(res_dict.get("confidence", 0.8))
            except Exception as e:
                logger.warning(f"Classification LLM fallback failed for {ef.filename}: {e}")

        classified_files[ef.filename] = {
            "doc_type": matched_type,
            "method": method,
            "confidence": conf
        }

    # Match requirements
    required_results: List[RequiredDocCheckResult] = []
    missing_types: List[str] = []
    assigned_files = set()

    for req in required_specs:
        type_key = req["type"]
        matched_file = None
        classification_method = "none"
        confidence = 0.0

        for fname, info in classified_files.items():
            if info["doc_type"] == type_key and fname not in empty_files and fname not in assigned_files:
                matched_file = fname
                classification_method = info["method"]
                confidence = info["confidence"]
                assigned_files.add(fname)
                break

        present = matched_file is not None
        if not present:
            missing_types.append(type_key)

        required_results.append(RequiredDocCheckResult(
            doc_type=type_key,
            name=req["name"],
            present=present,
            matched_filename=matched_file,
            classification_method=classification_method,
            confidence=confidence
        ))

    all_required_present = len(missing_types) == 0

    return CheckDocumentsOutput(
        category=input_data.category,
        required_types=required_results,
        missing_types=missing_types,
        all_required_present=all_required_present,
        empty_files=list(set(empty_files)),
        duplicate_files=list(set(duplicate_files)),
        unsupported_files=list(set(unsupported_files))
    )


# =====================================================================
# STAGE 4: validate_ticket (Agent)
# =====================================================================
def validate_ticket(input_data: ValidateTicketInput) -> ValidateTicketOutput:
    """
    Validation Agent:
    HARD RULE: If all_required_present is false, skip LLM, return invalid with missing list (save tokens).
    Otherwise, call prompts/validate_ticket.md to evaluate cross-document consistency,
    consent doc formality, sanction references, and contradictions.
    """
    # Hard rule: Skip LLM if required documents are missing
    if not input_data.document_check.all_required_present:
        missing_names = [r.name for r in input_data.document_check.required_types if not r.present]
        return ValidateTicketOutput(
            verdict="invalid",
            summary=f"Ticket rejected automatically: Missing mandatory documents: {', '.join(missing_names or input_data.document_check.missing_types)}.",
            checks=[
                ValidationCheckItem(
                    name="Mandatory Document Completeness",
                    passed=False,
                    evidence=f"Missing required doc types: {input_data.document_check.missing_types}",
                    reason="Required documents are missing from the submission bundle. Process halted before LLM validation."
                )
            ]
        )

    # Convert fields and doc_check to JSON strings for prompt rendering
    fields_dict = {k: v.model_dump() for k, v in input_data.extracted_fields.items()}
    doc_check_dict = input_data.document_check.model_dump()

    prompt_file = "prompts/validate_ticket.md"
    variables = {
        "category": input_data.category,
        "extracted_fields_json": json.dumps(fields_dict, indent=2),
        "document_check_json": json.dumps(doc_check_dict, indent=2),
        "documents": [doc.model_dump() for doc in input_data.extracted_files]
    }

    try:
        val_result, _ = llm_call(
            prompt_file=prompt_file,
            variables=variables,
            schema=ValidateTicketOutput
        )
        return val_result
    except Exception as e:
        logger.error(f"Validation LLM failed: {e}")
        return ValidateTicketOutput(
            verdict="needs_review",
            summary=f"Automated validation encountered an error ({e}). Routed to official queue for human verification.",
            checks=[
                ValidationCheckItem(
                    name="Automated Audit Execution",
                    passed=False,
                    evidence=str(e),
                    reason="LLM validator execution failure"
                )
            ]
        )


# =====================================================================
# STAGE 5: generate_report
# =====================================================================
def render_docx_report(
    ticket_id: str,
    category: str,
    created_at: str,
    sections: Dict[str, Any],
    output_path: str
) -> str:
    """Renders a formal .docx document using python-docx."""
    doc = Document()

    # Document Header
    title_p = doc.add_paragraph()
    title_run = title_p.add_run(sections.get("title", "OFFICIAL CIVIC ASSESSMENT REPORT"))
    title_run.font.size = Pt(20)
    title_run.font.bold = True
    title_run.font.color.rgb = RGBColor(26, 54, 93) # Navy blue
    title_p.alignment = WD_ALIGN_PARAGRAPH.CENTER

    sub_p = doc.add_paragraph()
    sub_run = sub_p.add_run(f"Ticket ID: {ticket_id}  |  Category: {category.upper()}  |  Date: {created_at[:10]}")
    sub_run.font.size = Pt(10)
    sub_run.font.italic = True
    sub_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    doc.add_paragraph().paragraph_format.space_after = Pt(12)

    # Section 1: Executive Summary
    h1 = doc.add_heading("1. Executive Summary", level=1)
    h1.paragraph_format.space_before = Pt(14)
    summary_text = sections.get("executive_summary", "No summary provided.")
    p = doc.add_paragraph(summary_text)
    p.paragraph_format.line_spacing = 1.15

    # Section 2: Applicant & Submission Details
    h2 = doc.add_heading("2. Applicant & Submission Details", level=1)
    h2.paragraph_format.space_before = Pt(14)
    applicant_details = sections.get("applicant_details", {})
    if isinstance(applicant_details, dict):
        table = doc.add_table(rows=1, cols=2)
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        hdr_cells = table.rows[0].cells
        hdr_cells[0].text = "Field"
        hdr_cells[1].text = "Verified Value"
        hdr_cells[0].paragraphs[0].runs[0].font.bold = True
        hdr_cells[1].paragraphs[0].runs[0].font.bold = True

        for k, v in applicant_details.items():
            row_cells = table.add_row().cells
            row_cells[0].text = k.replace("_", " ").title()
            row_cells[1].text = str(v)

    # Section 3: Civic Request Details
    h3 = doc.add_heading("3. Civic Request Details", level=1)
    h3.paragraph_format.space_before = Pt(14)
    issue = sections.get("issue_details", {})
    if isinstance(issue, dict):
        doc.add_paragraph(f"Title / Subject: {issue.get('title', 'N/A')}")
        doc.add_paragraph(f"Amount Requested / Sanctioned: {issue.get('amount_requested', 'N/A')}")
        doc.add_paragraph(f"Scope & Description: {issue.get('description', 'N/A')}")

    # Section 4: Supporting Documents Verification
    h4 = doc.add_heading("4. Supporting Documents Audit", level=1)
    h4.paragraph_format.space_before = Pt(14)
    docs_list = sections.get("supporting_documents_table", [])
    if isinstance(docs_list, list) and docs_list:
        doc_table = doc.add_table(rows=1, cols=4)
        doc_table.alignment = WD_TABLE_ALIGNMENT.CENTER
        hdr = doc_table.rows[0].cells
        hdr[0].text = "Document Type"
        hdr[1].text = "Filename"
        hdr[2].text = "Status"
        hdr[3].text = "Method"
        for i in range(4):
            hdr[i].paragraphs[0].runs[0].font.bold = True

        for item in docs_list:
            if isinstance(item, dict):
                r = doc_table.add_row().cells
                r[0].text = str(item.get("doc_type", ""))
                r[1].text = str(item.get("filename", ""))
                r[2].text = str(item.get("status", ""))
                r[3].text = str(item.get("classification_method", ""))

    # Section 5: Validation Findings
    h5 = doc.add_heading("5. Validation Findings & Verification Checks", level=1)
    h5.paragraph_format.space_before = Pt(14)
    checks = sections.get("validation_findings", [])
    if isinstance(checks, list):
        for chk in checks:
            if isinstance(chk, dict):
                status_symbol = "✔ PASSED" if chk.get("passed") else "✖ FAILED"
                p = doc.add_paragraph()
                r_status = p.add_run(f"[{status_symbol}] ")
                r_status.font.bold = True
                r_status.font.color.rgb = RGBColor(16, 120, 60) if chk.get("passed") else RGBColor(180, 20, 20)
                p.add_run(f"{chk.get('audit', 'Audit')}: {chk.get('evidence', '')}")

    # Section 6: Official Recommendation
    h6 = doc.add_heading("6. Official Recommendation", level=1)
    h6.paragraph_format.space_before = Pt(14)
    rec_text = sections.get("recommendation", "VERIFIED AND APPROVED")
    p_rec = doc.add_paragraph()
    rec_run = p_rec.add_run(rec_text)
    rec_run.font.bold = True
    rec_run.font.size = Pt(12)
    rec_run.font.color.rgb = RGBColor(20, 80, 160)

    # Save to disk
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    doc.save(output_path)
    return output_path


def generate_report(input_data: GenerateReportInput) -> GenerateReportOutput:
    """
    Stage 5: generate_report
    Only runs if verdict == valid (needs_review goes to a human queue).
    Uses prompts/generate_report.md to draft sections, then renders formal .docx.
    """
    fields_dict = {k: v.model_dump() for k, v in input_data.extracted_fields.items()}
    doc_check_dict = input_data.document_check.model_dump()
    validation_dict = input_data.validation.model_dump()

    prompt_file = "prompts/generate_report.md"
    variables = {
        "ticket_id": input_data.ticket_id,
        "category": input_data.category,
        "created_at": input_data.created_at,
        "extracted_fields_json": json.dumps(fields_dict, indent=2),
        "document_check_json": json.dumps(doc_check_dict, indent=2),
        "validation_json": json.dumps(validation_dict, indent=2)
    }

    sections_dict, _ = llm_call(
        prompt_file=prompt_file,
        variables=variables
    )

    docx_path = render_docx_report(
        ticket_id=input_data.ticket_id,
        category=input_data.category,
        created_at=input_data.created_at,
        sections=sections_dict,
        output_path=input_data.output_docx_path
    )

    return GenerateReportOutput(
        ticket_id=input_data.ticket_id,
        docx_path=docx_path,
        structured_sections=sections_dict
    )


# =====================================================================
# CENTRAL PIPELINE REGISTRY
# =====================================================================
STAGE_REGISTRY = [
    {
        "name": "extract_text",
        "func": extract_text,
        "prompt_file": None,
        "depends_on": None,
        "input_model": ExtractTextInput,
        "output_model": ExtractTextOutput
    },
    {
        "name": "extract_fields",
        "func": extract_fields,
        "prompt_file": "prompts/extract_fields.md",
        "depends_on": "extract_text",
        "input_model": ExtractFieldsInput,
        "output_model": ExtractFieldsOutput
    },
    {
        "name": "check_documents",
        "func": check_documents,
        "prompt_file": "prompts/classify_doc.md",
        "depends_on": "extract_text",
        "input_model": CheckDocumentsInput,
        "output_model": CheckDocumentsOutput
    },
    {
        "name": "validate_ticket",
        "func": validate_ticket,
        "prompt_file": "prompts/validate_ticket.md",
        "depends_on": "check_documents",
        "input_model": ValidateTicketInput,
        "output_model": ValidateTicketOutput
    },
    {
        "name": "generate_report",
        "func": generate_report,
        "prompt_file": "prompts/generate_report.md",
        "depends_on": "validate_ticket",
        "input_model": GenerateReportInput,
        "output_model": GenerateReportOutput
    }
]
