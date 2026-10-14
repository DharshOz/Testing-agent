from typing import List, Optional, Dict, Any, Literal
from pydantic import BaseModel, Field


class FileInfo(BaseModel):
    filename: str
    path: str
    sha256: str


# ==========================================
# STAGE 1: extract_text
# ==========================================
class ExtractedFileText(BaseModel):
    filename: str
    raw_text: str
    page_count: int = 1
    extraction_warnings: List[str] = Field(default_factory=list)


class ExtractTextInput(BaseModel):
    ticket_id: str
    files: List[FileInfo]


class ExtractTextOutput(BaseModel):
    files: List[ExtractedFileText]


# ==========================================
# STAGE 2: extract_fields (RAG)
# ==========================================
class FieldResult(BaseModel):
    value: Optional[str] = None
    confidence: float = 0.0
    source_filename: Optional[str] = None
    source_chunk_text: Optional[str] = None


class ExtractFieldsInput(BaseModel):
    ticket_id: str
    extracted_files: List[ExtractedFileText]


class ExtractFieldsOutput(BaseModel):
    fields: Dict[str, FieldResult]


# ==========================================
# STAGE 3: check_documents (First Validation Layer)
# ==========================================
class RequiredDocCheckResult(BaseModel):
    doc_type: str
    name: str
    present: bool
    matched_filename: Optional[str] = None
    classification_method: str = "none"  # "rule_keyword" | "llm_fallback" | "none"
    confidence: float = 0.0


class CheckDocumentsInput(BaseModel):
    ticket_id: str
    category: str
    files: List[FileInfo]
    extracted_files: List[ExtractedFileText]


class CheckDocumentsOutput(BaseModel):
    category: str
    required_types: List[RequiredDocCheckResult]
    missing_types: List[str] = Field(default_factory=list)
    all_required_present: bool = True
    empty_files: List[str] = Field(default_factory=list)
    duplicate_files: List[str] = Field(default_factory=list)
    unsupported_files: List[str] = Field(default_factory=list)


# ==========================================
# STAGE 4: validate_ticket (Agent)
# ==========================================
class ValidationCheckItem(BaseModel):
    name: str
    passed: bool
    evidence: str
    reason: str


class ValidateTicketInput(BaseModel):
    ticket_id: str
    category: str
    extracted_fields: Dict[str, FieldResult]
    document_check: CheckDocumentsOutput
    extracted_files: List[ExtractedFileText]


class ValidateTicketOutput(BaseModel):
    verdict: Literal["valid", "invalid", "needs_review"]
    summary: str
    checks: List[ValidationCheckItem]


# ==========================================
# STAGE 5: generate_report
# ==========================================
class GenerateReportInput(BaseModel):
    ticket_id: str
    category: str
    created_at: str
    extracted_fields: Dict[str, FieldResult]
    document_check: CheckDocumentsOutput
    validation: ValidateTicketOutput
    output_docx_path: str


class GenerateReportOutput(BaseModel):
    ticket_id: str
    docx_path: str
    structured_sections: Dict[str, Any]
