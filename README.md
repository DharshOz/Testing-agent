# Civic Ticket AI Document-Processing Platform & Testing Agent

A production-grade AI document-processing platform and autonomous testing agent. Features an isolated multi-stage pipeline, local embedded vector search (Qdrant), fast local CPU embeddings (fastembed), Groq LLM integration (`openai/gpt-oss-120b`), formal Word report generation, and an autonomous dependency-aware regression testing & prompt-fixing agent.

---

## 🏛️ System Architecture

### 1. Multi-Stage Pipeline (`pipeline/stages.py`, `pipeline/contracts.py`)
- **Stage 1: `extract_text`**: Extracts text from PDF (`pdfplumber` / `pypdf` with optional OCR fallback via `pytesseract`), Word documents (`python-docx`), and plain text (`txt`).
- **Stage 2: `extract_fields` (RAG)**: Chunks text (~500 chars, 50 overlap), computes local embeddings with `fastembed` (`BAAI/bge-small-en-v1.5`), and indexes chunks into embedded local Qdrant collection `ticket_{ticket_id}`. Retrieves top-4 chunks per field and queries Groq with `prompts/extract_fields.md` (temperature=0, strict schema validation, retry-once).
- **Stage 3: `check_documents` (First Validation Layer)**: Compares uploads against category rules in `config/required_docs.yaml` (`identity_proof`, `address_or_supporting_proof`, `sanction_document`, `community_consent_document`, `application_form`). Scored keyword matching first; falls back to `prompts/classify_doc.md` only when rules are inconclusive. Flags duplicates, empty files, and missing mandatory documents.
- **Stage 4: `validate_ticket` (Agent)**:
  - **Hard Rule**: If mandatory documents are missing (`all_required_present == False`), skips LLM call and marks ticket `invalid` immediately to save tokens.
  - Otherwise, invokes `prompts/validate_ticket.md` to evaluate cross-document consistency (name/ID match across documents), sanction reference alignment, and formality of community consent signatures.
- **Stage 5: `generate_report`**: Only runs for approved tickets (`verdict == valid`). Generates structured executive report sections using `prompts/generate_report.md` and compiles an official `.docx` report with `python-docx`.

---

## 🤖 Testing Agent Architecture (`tests_agent/`)

Built specifically for multi-stage AI workflows where upstream changes must never cause false failures in downstream stages.

```
tests_agent/
  config.yaml                  # Thresholds, model settings, paths
  baseline.json                # Known pass/fail assertion state per (case, stage)
  cases/<case_id>/             # Isolated test cases
    input.json                 # Ticket metadata and file definitions
    files/                     # Physical documents (PDF, DOCX, TXT)
    golden/<stage>_output.json # Frozen stage outputs
    expected.yaml              # Multi-layer pass criteria per stage
    meta.yaml                  # Data type tag, split (tune or heldout), must_pass
  assertions/
    schema.py                  # Pydantic contract validation (cheapest check)
    rules.py                   # Deterministic anti-hallucination and integrity rules
    judge.py                   # LLM judge (dual-run agreement check; marks flaky on disagreement)
  graph.py                     # Dynamic dependency DAG from STAGE_REGISTRY
  runner.py                    # Isolated stage tests & live propagation tests
  detect_changes.py            # Git diff mapping changed files -> stages -> test plan
  fix_agent.py                 # Autonomous issue-sheet prompt-fixing loop
  report.py                    # Markdown tables and JSON test results
  run_tests.py                 # CLI test runner
snapshot.py                    # Frozen golden snapshot generator
add_issue.py                   # Failure-to-test flywheel helper
issues/issues.csv              # Tester issue sheet
.github/workflows/
  regression.yml               # PR regression testing workflow
  agent_fix.yml                # Autonomous prompt fixing & PR generator
```

### 1. Frozen Stage-Boundary Testing
- **STAGE TEST (Isolated)**: Tests stage N using the frozen golden output of stage N-1. Verifies stage N's prompt without upstream contamination.
- **PROPAGATION TEST**: Runs stage N live, feeds its output to stage N+1 live, then N+2 live, asserting that prompt improvements in stage N do not leak or regress downstream stages.
- **Dependency-Aware Test Selection**: `detect_changes.py` inspects `git diff`:
  - `prompts/extract_fields.md` -> re-tests `extract_fields` in isolation, runs propagation on `[validate_ticket, generate_report]`, and skips upstream `extract_text`.

### 2. Multi-Layer Assertions (Cheapest First)
1. **Schema Check**: Validates output structure against registered Pydantic contract. Hard-stops early on malformed output.
2. **Deterministic Rules**:
   - `extract_fields`: Grounding / anti-hallucination check (every non-null value must appear in `source_chunk_text` and document text); absent fields must return null; confidence in [0, 1].
   - `check_documents`: Exact matching of present vs missing document types against `expected.yaml`.
   - `validate_ticket`: Verdict matching; token-saving enforcement (LLM skipped on missing documents); non-empty evidence per audit check.
   - `generate_report`: `.docx` openability; required headings; applicant name presence; suppressed on non-valid tickets.
3. **LLM Judge**: Evaluates open-ended criteria. **Quality Bar**: Runs twice; both runs must agree on verdict, otherwise marked **FLAKY**.

### 3. Autonomous Fix Agent Loop (`fix_agent.py`)
For each open issue in `issues/issues.csv`:
1. **Reproduce**: Runs the stage on the issue's test case. If it passes, marks `cannot_reproduce`.
2. **Propose Generic Fix**: Calls Groq (`openai/gpt-oss-120b`) with the failing input/output, issue description, root cause reason, failing assertions, and 3 passing examples from other data types.
   - *Strict Guard*: Prompt change must be generic; cannot mention specific names or IDs; diffs targeting files outside `prompts/` are immediately rejected.
3. **Verify Cascade**:
   - Step 1: Issue case must pass.
   - Step 2: All `tune` cases for that stage must pass (zero regressions allowed).
   - Step 3: Propagation tests for all downstream stages must pass.
   - Step 4: Held-out cases must pass (verifies no overfitting).
4. **Decide or Escalate**:
   - If green: marks `fixed` and updates `issues.csv`.
   - If regressions: retries with feedback (up to 4 attempts).
   - If structural limitation (e.g. unreadable scans, missing rules): marks `needs_layer` with an architecture suggestion note.
   - Otherwise: marks `needs_human`.

---

## 🚀 How to Run

### 1. Prerequisites & Virtual Environment
```powershell
# Windows
.\venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### 2. Configure Environment Variables (Dual Groq Key Setup)
Create or edit `.env` in the project root:
```env
# Key 1: For Ticket Processing Workflow (user ticket submissions & official review)
GROQ_API_KEY=gsk_your_ticket_key_here
TICKET_AGENT_GROQ_KEY=gsk_your_ticket_key_here

# Key 2: Dedicated Key for Autonomous Testing & Fix Agent (avoids shared rate limits)
TESTING_AGENT_GROQ_KEY=gsk_your_testing_key_here

# Model Selection
GROQ_MODEL=openai/gpt-oss-120b
```
> **Tip:** If `TESTING_AGENT_GROQ_KEY` is left blank, the system automatically falls back to `GROQ_API_KEY`. Providing a dedicated second key isolates testing runs from ticket processing rate limits.

### 3. Generate Seed Cases & Golden Snapshots
```powershell
python scripts/make_seed_cases.py
```
This initializes 8 comprehensive test cases in `tests_agent/cases/` (6 tune cases, 2 held-out cases) and records golden outputs.

### 4. Running the Tests Locally

#### A. Dependency-Aware Changed Mode (Runs on Git Changes)
```powershell
python run_tests.py --mode changed
```

#### B. Run Specific Stage in Isolation or Propagation
```powershell
# Test validate_ticket in isolation on frozen inputs
python run_tests.py --stage validate_ticket --mode stage

# Test validate_ticket live and propagate downstream
python run_tests.py --stage validate_ticket --mode propagation
```

#### C. Run All Cases or Only Held-Out Cases
```powershell
# Run all tune cases
python run_tests.py --all

# Run held-out evaluation (overfitting detection)
python run_tests.py --heldout

# Run single case
python run_tests.py --case case_01_valid_grant
```

### 5. Running the Autonomous Fix Agent
```powershell
python tests_agent/fix_agent.py --max-attempts 4
```

### 6. Logging a New Issue (Failure-to-Test Flywheel)
When a tester identifies an issue, run:
```powershell
python add_issue.py --stage extract_fields --data-type handwritten_scan --issue "Applicant name omitted on handwritten forms" --reason "OCR misses cursive signature header" --files path/to/scan.pdf
```
This automatically:
1. Copies the input files into `tests_agent/cases/issue_case_XXX/`
2. Generates baseline stage snapshots
3. Creates `expected.yaml` skeleton
4. Appends a tracked row with status `open` in `issues/issues.csv`

### 7. Run Unit Tests for the Testing Agent
```powershell
pytest tests/test_tests_agent.py -v
```

### 8. Run Web Applications
- **Streamlit UI**: `streamlit run app.py`
- **FastAPI Backend**: `uvicorn backend.app:app --reload --port 8000`
