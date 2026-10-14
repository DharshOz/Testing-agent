# Multi-Stage AI Workflow Test Report

### Status: **[PASS] ALL CHECKS PASSED**
- **Total Assertions:** 45
- **Passed:** 45 (100.0%)
- **Failed:** 0
- **Flaky:** 0
- **Regressions:** 0

## Detailed Assertion Results

| Case ID | Stage | Assertion Name | Status | Rationale | Evidence Snippet |
|---|---|---|---|---|---|
| `case_01_valid_grant` | `extract_text` | Schema Check [extract_text] | **PASS** | Output strictly adheres to ExtractTextOutput contract | `Fields validated against ExtractTextOutput` |
| `case_01_valid_grant` | `extract_text` | Text Extraction Non-Empty Files Count | **PASS** | Expected at least 5 extracted files, got 5 | `Filenames: ['application_form.pdf', 'aadhaar_identity_proof.txt', 'address_proof_utility_bill.doc...` |
| `case_01_valid_grant` | `extract_text` | Extraction Text Presence (application_form.pdf) | **PASS** | File application_form.pdf text must be non-empty | `Extracted chars: 464, pages: 1` |
| `case_01_valid_grant` | `extract_text` | Extraction Text Presence (aadhaar_identity_proof.txt) | **PASS** | File aadhaar_identity_proof.txt text must be non-empty | `Extracted chars: 296, pages: 1` |
| `case_01_valid_grant` | `extract_text` | Extraction Text Presence (address_proof_utility_bill.docx) | **PASS** | File address_proof_utility_bill.docx text must be non-empty | `Extracted chars: 238, pages: 1` |
| `case_01_valid_grant` | `extract_text` | Extraction Text Presence (sanction_order_approval.pdf) | **PASS** | File sanction_order_approval.pdf text must be non-empty | `Extracted chars: 268, pages: 1` |
| `case_01_valid_grant` | `extract_text` | Extraction Text Presence (community_consent_resolution.docx) | **PASS** | File community_consent_resolution.docx text must be non-empty | `Extracted chars: 498, pages: 1` |
| `case_01_valid_grant` | `extract_fields` | Schema Check [extract_fields] | **PASS** | Output strictly adheres to ExtractFieldsOutput contract | `Fields validated against ExtractFieldsOutput` |
| `case_01_valid_grant` | `extract_fields` | Confidence Range Check (applicant_name) | **PASS** | Confidence 1.0 must be in range [0.0, 1.0] | `Confidence: 1.0` |
| `case_01_valid_grant` | `extract_fields` | Anti-Hallucination Chunk Check (applicant_name) | **PASS** | Extracted value 'Aarav Sharma' must be grounded in source_chunk_text | `Chunk snippet: Applicant Name: Aarav Sharma` |
| `case_01_valid_grant` | `extract_fields` | Anti-Hallucination Raw Document Check (applicant_name) | **PASS** | Extracted value 'Aarav Sharma' must exist in raw document text | `Value: 'Aarav Sharma'` |
| `case_01_valid_grant` | `extract_fields` | Confidence Range Check (applicant_id) | **PASS** | Confidence 1.0 must be in range [0.0, 1.0] | `Confidence: 1.0` |
| `case_01_valid_grant` | `extract_fields` | Anti-Hallucination Chunk Check (applicant_id) | **PASS** | Extracted value 'AADHAAR-8921-4321-9988' must be grounded in source_chunk_text | `Chunk snippet: Applicant ID: AADHAAR-8921-4321-9988` |
| `case_01_valid_grant` | `extract_fields` | Anti-Hallucination Raw Document Check (applicant_id) | **PASS** | Extracted value 'AADHAAR-8921-4321-9988' must exist in raw document text | `Value: 'AADHAAR-8921-4321-9988'` |
| `case_01_valid_grant` | `extract_fields` | Confidence Range Check (contact) | **PASS** | Confidence 1.0 must be in range [0.0, 1.0] | `Confidence: 1.0` |
| `case_01_valid_grant` | `extract_fields` | Anti-Hallucination Chunk Check (contact) | **PASS** | Extracted value '+91-9876543210, aarav.sharma@example.gov.in' must be grounded in source_chunk_text | `Chunk snippet: Contact: +91-9876543210, aarav.sharma@example.gov.in` |
| `case_01_valid_grant` | `extract_fields` | Anti-Hallucination Raw Document Check (contact) | **PASS** | Extracted value '+91-9876543210, aarav.sharma@example.gov.in' must exist in raw document text | `Value: '+91-9876543210, aarav.sharma@example.gov.in'` |
| `case_01_valid_grant` | `extract_fields` | Confidence Range Check (date) | **PASS** | Confidence 1.0 must be in range [0.0, 1.0] | `Confidence: 1.0` |
| `case_01_valid_grant` | `extract_fields` | Anti-Hallucination Chunk Check (date) | **PASS** | Extracted value '2026-03-15' must be grounded in source_chunk_text | `Chunk snippet: Date of Submission: 2026-03-15` |
| `case_01_valid_grant` | `extract_fields` | Anti-Hallucination Raw Document Check (date) | **PASS** | Extracted value '2026-03-15' must exist in raw document text | `Value: '2026-03-15'` |
| `case_01_valid_grant` | `extract_fields` | Confidence Range Check (issue_title) | **PASS** | Confidence 1.0 must be in range [0.0, 1.0] | `Confidence: 1.0` |
| `case_01_valid_grant` | `extract_fields` | Anti-Hallucination Chunk Check (issue_title) | **PASS** | Extracted value 'Installation of Community Solar Water Pump in Ward 4' must be grounded in source_chunk_text | `Chunk snippet: Issue Title: Installation of Community Solar Water Pump in Ward 4` |
| `case_01_valid_grant` | `extract_fields` | Anti-Hallucination Raw Document Check (issue_title) | **PASS** | Extracted value 'Installation of Community Solar Water Pump in Ward 4' must exist in raw document text | `Value: 'Installation of Community Solar Water Pump in Ward 4'` |
| `case_01_valid_grant` | `extract_fields` | Confidence Range Check (issue_description) | **PASS** | Confidence 1.0 must be in range [0.0, 1.0] | `Confidence: 1.0` |
| `case_01_valid_grant` | `extract_fields` | Anti-Hallucination Chunk Check (issue_description) | **PASS** | Extracted value 'Infrastructure grant for drilling and installing community solar water pump.' must be grounded in source_chunk_text | `Chunk snippet: Project Description: Infrastructure grant for drilling and installing community so...` |
| `case_01_valid_grant` | `extract_fields` | Anti-Hallucination Raw Document Check (issue_description) | **PASS** | Extracted value 'Infrastructure grant for drilling and installing community solar water pump.' must exist in raw document text | `Value: 'Infrastructure grant for drilling and installing community solar water pump.'` |
| `case_01_valid_grant` | `extract_fields` | Confidence Range Check (amount_requested) | **PASS** | Confidence 1.0 must be in range [0.0, 1.0] | `Confidence: 1.0` |
| `case_01_valid_grant` | `extract_fields` | Anti-Hallucination Chunk Check (amount_requested) | **PASS** | Extracted value 'INR 2,50,000' must be grounded in source_chunk_text | `Chunk snippet: Amount Requested: INR 2,50,000` |
| `case_01_valid_grant` | `extract_fields` | Anti-Hallucination Raw Document Check (amount_requested) | **PASS** | Extracted value 'INR 2,50,000' must exist in raw document text | `Value: 'INR 2,50,000'` |
| `case_01_valid_grant` | `extract_fields` | Confidence Range Check (department) | **PASS** | Confidence 1.0 must be in range [0.0, 1.0] | `Confidence: 1.0` |
| `case_01_valid_grant` | `extract_fields` | Anti-Hallucination Chunk Check (department) | **PASS** | Extracted value 'Rural Water Supply and Sanitation' must be grounded in source_chunk_text | `Chunk snippet: Department: Rural Water Supply and Sanitation` |
| `case_01_valid_grant` | `extract_fields` | Anti-Hallucination Raw Document Check (department) | **PASS** | Extracted value 'Rural Water Supply and Sanitation' must exist in raw document text | `Value: 'Rural Water Supply and Sanitation'` |
| `case_01_valid_grant` | `extract_fields` | Confidence Range Check (supporting_references) | **PASS** | Confidence 1.0 must be in range [0.0, 1.0] | `Confidence: 1.0` |
| `case_01_valid_grant` | `extract_fields` | Anti-Hallucination Chunk Check (supporting_references) | **PASS** | Extracted value 'SANCTION-ORD-2026-449' must be grounded in source_chunk_text | `Chunk snippet: Supporting References: SANCTION-ORD-2026-449` |
| `case_01_valid_grant` | `extract_fields` | Anti-Hallucination Raw Document Check (supporting_references) | **PASS** | Extracted value 'SANCTION-ORD-2026-449' must exist in raw document text | `Value: 'SANCTION-ORD-2026-449'` |
| `case_01_valid_grant` | `check_documents` | Schema Check [check_documents] | **PASS** | Output strictly adheres to CheckDocumentsOutput contract | `Fields validated against CheckDocumentsOutput` |
| `case_01_valid_grant` | `check_documents` | Document Check All Required Present | **PASS** | Expected all_required_present=True, got True | `Missing doc types: []` |
| `case_01_valid_grant` | `check_documents` | Missing Document Types Exact Match | **PASS** | Expected missing types [], got [] | `set()` |
| `case_01_valid_grant` | `validate_ticket` | Schema Check [validate_ticket] | **PASS** | Output strictly adheres to ValidateTicketOutput contract | `Fields validated against ValidateTicketOutput` |
| `case_01_valid_grant` | `validate_ticket` | Validation Verdict Match | **PASS** | Expected verdict 'valid', got 'valid' | `Summary: All required fields are present and consistent; applicant name and Aadhaar match across ...` |
| `case_01_valid_grant` | `validate_ticket` | Validation Checks Evidence Completeness | **PASS** | All audit checks must provide non-empty evidence and rationale | `Checks count: 6` |
| `case_01_valid_grant` | `generate_report` | Schema Check [generate_report] | **PASS** | Output strictly adheres to GenerateReportOutput contract | `Fields validated against GenerateReportOutput` |
| `case_01_valid_grant` | `generate_report` | Generated DOCX Readability | **PASS** | Report docx file must exist and open with python-docx | `Path: ./reports/test_report_case_01_valid_grant_generate_report.docx, Exists: True, Openable: True` |
| `case_01_valid_grant` | `generate_report` | Required Document Headings Presence | **PASS** | Report must include standard headings: ['Executive Summary', 'Applicant & Submission Details', 'Civic Request Details'] | `Missing: []` |
| `case_01_valid_grant` | `generate_report` | Applicant Name Presence in Report | **PASS** | Applicant name 'Aarav Sharma' must appear in the generated report | `Found in document text: True` |