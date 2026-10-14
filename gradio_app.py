import os
import sys
import json
import uuid
import shutil
import hashlib
import subprocess
from pathlib import Path
from typing import List, Dict, Any, Tuple

# Ensure project root in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import gradio as gr
import pandas as pd
import yaml

from db.database import engine, init_db
from db.models import Ticket, TicketFile, StageOutput
from pipeline.runner import run_pipeline, get_stage_snapshot
from tests_agent.graph import get_stage_info, get_stages_in_order, downstream_of
from tests_agent.runner import TestRunner
from tests_agent.fix_agent import FixAgentLoop
from tests_agent.detect_changes import get_changed_stages, get_test_plan
from pipeline.llm_client import get_keys_status, set_testing_mode, reload_env
from sqlmodel import Session, select

init_db()

def get_key_status_markdown() -> str:
    status = get_keys_status()
    t_status = "🟢 Connected" if status["ticket_key_configured"] else "🔴 Unset"
    if status["is_dedicated_test_key"]:
        test_status = f"🟢 Dedicated Key (`{status['testing_key_masked']}`)"
    elif status["testing_key_configured"]:
        test_status = f"🟢 Connected (`{status['testing_key_masked']}`)"
    else:
        test_status = "🟡 Shared Quota (Fallback to Ticket Key)"

    return (
        f"> **🔑 Dual Groq API Key Engine:** "
        f"**Ticket Processing Agent:** {t_status} (`{status['ticket_key_masked']}`) &nbsp;|&nbsp; "
        f"**Autonomous Testing Agent:** {test_status} &nbsp;|&nbsp; "
        f"**Model:** `{os.environ.get('GROQ_MODEL', 'openai/gpt-oss-120b')}`"
    )

# Sample directory mapping
SAMPLE_PRESETS = {
    "Sample 1: Valid Solar Pump Grant": "sample_data/ticket_1_valid",
    "Sample 2: Missing Consent Document": "sample_data/ticket_2_missing_consent",
    "Sample 3: Inconsistent Names Across Docs": "sample_data/ticket_3_inconsistent_names",
    "Sample 4: Prefixed Identifier Issue": "tests_agent/cases/case_demo_id_prefix/files",
    "Sample 5: Currency Discrepancy Issue": "tests_agent/cases/case_demo_currency_format/files",
    "Sample 6: Same-Day Dates Issue": "tests_agent/cases/case_demo_date_order/files"
}


# =========================================================================
# HELPER FUNCTIONS
# =========================================================================
def load_issues_df() -> pd.DataFrame:
    """Loads issues from issues.xlsx or issues.csv."""
    xlsx_path = Path("issues/issues.xlsx")
    csv_path = Path("issues/issues.csv")
    if xlsx_path.is_file():
        try:
            return pd.read_excel(xlsx_path)
        except Exception:
            pass
    if csv_path.is_file():
        return pd.read_csv(csv_path)
    return pd.DataFrame(columns=[
        "id", "stage", "data_type", "issue", "reason", "test_case",
        "expected_behavior", "status", "attempts", "resolution_note", "linked_pr"
    ])


def get_issue_choices() -> List[str]:
    df = load_issues_df()
    if df.empty:
        return ["No issues found"]
    choices = []
    for _, r in df.iterrows():
        choices.append(f"Issue #{r['id']} - [{r['stage']}] {r['issue'][:60]} ({r['status']})")
    return choices


def on_preset_select(preset_name: str) -> List[str]:
    """Returns file paths for the selected preset."""
    if preset_name in SAMPLE_PRESETS:
        folder = Path(SAMPLE_PRESETS[preset_name])
        if folder.is_dir():
            return [str(p) for p in folder.iterdir() if p.is_file()]
    return []


def process_ticket_flow(
    preset_choice: str,
    uploaded_files: List[Any],
    category: str
) -> Tuple[str, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, str, str]:
    """
    Executes the 5-stage pipeline on uploaded or sample files.
    Returns:
    1. Overall Status HTML
    2. Stage 1 Text Extraction Table
    3. Stage 2 RAG Fields Table
    4. Stage 3 Document Check Table
    5. Stage 4 Validation Checks Table
    6. Stage 5 Report Preview Text
    7. Generated DOCX file path
    """
    set_testing_mode(False)
    files_to_use = []
    if uploaded_files:
        files_to_use = [f.name if hasattr(f, 'name') else str(f) for f in uploaded_files]
    elif preset_choice in SAMPLE_PRESETS:
        folder = Path(SAMPLE_PRESETS[preset_choice])
        if folder.is_dir():
            files_to_use = [str(p) for p in folder.iterdir() if p.is_file()]

    if not files_to_use:
        return (
            "⚠️ **Please upload files or select a sample preset.**",
            pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame(),
            "No files processed.",
            None
        )

    ticket_id = f"TICK-{uuid.uuid4().hex[:8].upper()}"
    u_dir = Path(f"./uploads/{ticket_id}")
    u_dir.mkdir(parents=True, exist_ok=True)

    file_records = []
    for fp in files_to_use:
        src = Path(fp)
        dest = u_dir / src.name
        shutil.copy(src, dest)
        sha = hashlib.sha256(dest.read_bytes()).hexdigest()
        file_records.append(TicketFile(
            ticket_id=ticket_id,
            filename=src.name,
            path=str(dest),
            sha256=sha
        ))

    with Session(engine) as session:
        t = Ticket(id=ticket_id, category=category, status="submitted")
        session.add(t)
        for fr in file_records:
            session.add(fr)
        session.commit()

    # Run pipeline
    ticket = run_pipeline(ticket_id)

    # 1. Overall Status Banner
    verdict = ticket.verdict or "needs_review"
    if verdict == "valid":
        status_html = f"### 🟢 Ticket Verdict: **VALID & APPROVED** (`{ticket.status}`)\n**Ticket ID:** `{ticket.id}` | **Category:** `{ticket.category}`"
    elif verdict == "invalid":
        status_html = f"### 🔴 Ticket Verdict: **INVALID / REJECTED** (`{ticket.status}`)\n**Ticket ID:** `{ticket.id}` | **Category:** `{ticket.category}`"
    else:
        status_html = f"### 🟡 Ticket Verdict: **NEEDS OFFICIAL REVIEW** (`{ticket.status}`)\n**Ticket ID:** `{ticket.id}` | **Category:** `{ticket.category}`"

    # Stage 1: Text extraction
    snap1 = get_stage_snapshot(ticket_id, "extract_text") or {}
    files_extracted = snap1.get("files", [])
    s1_rows = []
    for fe in files_extracted:
        s1_rows.append({
            "Filename": fe.get("filename"),
            "Pages": fe.get("page_count"),
            "Extracted Chars": len(fe.get("raw_text", "")),
            "Warnings": ", ".join(fe.get("extraction_warnings", [])) or "None"
        })
    df_s1 = pd.DataFrame(s1_rows)

    # Stage 2: RAG extracted fields
    snap2 = get_stage_snapshot(ticket_id, "extract_fields") or {}
    fields_dict = snap2.get("fields", {})
    s2_rows = []
    for fname, fval in fields_dict.items():
        s2_rows.append({
            "Field Name": fname.replace("_", " ").title(),
            "Extracted Value": str(fval.get("value")),
            "Confidence": f"{float(fval.get('confidence', 0)):.2f}",
            "Source Citation": str(fval.get("source_chunk_text", ""))[:120]
        })
    df_s2 = pd.DataFrame(s2_rows)

    # Stage 3: Document Check
    snap3 = get_stage_snapshot(ticket_id, "check_documents") or {}
    req_docs = snap3.get("required_types", [])
    s3_rows = []
    for rd in req_docs:
        s3_rows.append({
            "Document Requirement": rd.get("name"),
            "Status": "✅ Present" if rd.get("present") else "❌ Missing",
            "Matched File": rd.get("matched_filename") or "None",
            "Method": rd.get("classification_method")
        })
    df_s3 = pd.DataFrame(s3_rows)

    # Stage 4: Ticket Validation Audit
    snap4 = get_stage_snapshot(ticket_id, "validate_ticket") or {}
    checks = snap4.get("checks", [])
    s4_rows = []
    for c in checks:
        s4_rows.append({
            "Audit Check": c.get("name"),
            "Result": "PASSED" if c.get("passed") else "FAILED",
            "Reason": c.get("reason"),
            "Evidence Quote": c.get("evidence")
        })
    df_s4 = pd.DataFrame(s4_rows)

    # Stage 5: Generated Report
    snap5 = get_stage_snapshot(ticket_id, "generate_report") or {}
    docx_file = f"./reports/ticket_{ticket_id}_report.docx"
    report_preview = "Report not generated because ticket was rejected or requires review."
    docx_download = None

    if os.path.exists(docx_file):
        docx_download = docx_file
        sec = snap5.get("structured_sections", {})
        report_preview = f"**Report Title:** {sec.get('title')}\n\n**Executive Summary:**\n{sec.get('executive_summary')}\n\n**Recommendation:**\n{sec.get('recommendation')}"

    return status_html, df_s1, df_s2, df_s3, df_s4, report_preview, docx_download


def on_issue_selected(selected_issue_text: str) -> Tuple[str, str, str, str, str, str]:
    """Loads details when an issue is selected from the dropdown."""
    df = load_issues_df()
    if df.empty or not selected_issue_text or "Issue #" not in selected_issue_text:
        return "", "", "", "", "", ""

    issue_id = selected_issue_text.split("#")[1].split(" ")[0].strip()
    match = df[df["id"].astype(str) == str(issue_id)]
    if match.empty:
        return "", "", "", "", "", ""

    row = match.iloc[0]
    stage = str(row["stage"])
    stage_info = get_stage_info(stage)
    prompt_file = stage_info.get("prompt_file") or "None"

    current_prompt = ""
    if prompt_file and os.path.exists(prompt_file):
        current_prompt = Path(prompt_file).read_text(encoding="utf-8")

    meta_info = f"**Issue #{row['id']}** | **Stage:** `{row['stage']}` | **Data Type:** `{row['data_type']}` | **Status:** `{row['status']}`"
    issue_desc = f"**Problem:** {row['issue']}\n\n**Root Cause Reason:** {row['reason']}"

    # Suggested guidance
    suggested_guidance = str(row.get("reason", ""))

    return meta_info, issue_desc, str(row["test_case"]), prompt_file, current_prompt, suggested_guidance


def run_agent_solve_problem(
    selected_issue_text: str,
    user_guidance_text: str,
    max_attempts: int
) -> Tuple[str, str, pd.DataFrame, str]:
    """
    Executes the fix agent on the selected issue using the user's guidance idea.
    Returns:
    1. Outcome Status Markdown
    2. Unified Prompt Diff (Before vs After)
    3. Assertions Comparison Table (Before vs After)
    4. Updated Prompt Content
    """
    if not selected_issue_text or "Issue #" not in selected_issue_text:
        return "⚠️ Please select a valid issue first.", "", pd.DataFrame(), ""

    set_testing_mode(True)
    issue_id = selected_issue_text.split("#")[1].split(" ")[0].strip()

    agent = FixAgentLoop()
    result = agent.solve_single_issue(
        issue_id=issue_id,
        user_guidance=user_guidance_text.strip() if user_guidance_text else None,
        max_attempts=max_attempts
    )

    success = result.get("success", False)
    status_icon = "🎉 **SUCCESS: ISSUE RESOLVED & PROMPT HEALED!**" if success else "⚠️ **AGENT FINISHED WITH ESCALATION**"
    outcome_md = f"{status_icon}\n\n- **Issue ID:** `#{issue_id}`\n- **Stage:** `{result.get('stage')}`\n- **Status:** `{result.get('status')}`\n- **Attempts Used:** `{result.get('attempts')}`\n- **Resolution Note:** {result.get('resolution_note')}"

    # Diff
    diff_text = result.get("diff") or "No changes made to prompt."

    # Compare before vs after assertions
    before_ass = {a.get("name"): a for a in result.get("before_assertions", [])}
    after_ass = {a.get("name"): a for a in result.get("after_assertions", [])}

    all_names = list(before_ass.keys())
    for k in after_ass.keys():
        if k not in all_names:
            all_names.append(k)

    comp_rows = []
    for name in all_names:
        b_item = before_ass.get(name, {})
        a_item = after_ass.get(name, {})
        b_passed = b_item.get("passed", False)
        a_passed = a_item.get("passed", False)

        b_label = "✅ PASS" if b_passed else "❌ FAIL"
        a_label = "✅ PASS" if a_passed else "❌ FAIL"

        comp_rows.append({
            "Assertion Name": name,
            "Status BEFORE Fix": b_label,
            "Status AFTER Fix": a_label,
            "Rationale / Rationale Change": a_item.get("reason", b_item.get("reason", ""))
        })

    df_comp = pd.DataFrame(comp_rows)

    return outcome_md, diff_text, df_comp, result.get("after_prompt", "")


def run_full_test_suite_action(mode: str) -> Tuple[str, pd.DataFrame]:
    """Runs tests and returns summary status and table."""
    set_testing_mode(True)
    cmd = [sys.executable, "run_tests.py"]
    if mode == "Changed Stages Only":
        cmd.extend(["--mode", "changed"])
    elif mode == "Held-Out Cases Only":
        cmd.append("--heldout")
    else:
        cmd.append("--all")

    res = subprocess.run(cmd, capture_output=True, text=True)
    report_file = Path("test_report.json")
    if report_file.is_file():
        try:
            with open(report_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                rows = []
                for case_id, s_map in data.get("results", {}).items():
                    for stg, a_list in s_map.items():
                        for a in a_list:
                            rows.append({
                                "Case ID": case_id,
                                "Stage": stg,
                                "Assertion": a.get("name"),
                                "Status": "PASSED" if a.get("passed") else "FAILED",
                                "Reason": a.get("reason")
                            })
                df_results = pd.DataFrame(rows)
                summary_text = f"**Status:** {'🟢 ALL CHECKS PASSED' if res.returncode == 0 else '🔴 FAILURES DETECTED'}\n\nTotal Assertions: {len(rows)}"
                return summary_text, df_results
        except Exception:
            pass

    return f"Test Run Output:\n```\n{res.stdout[-600:]}\n```", pd.DataFrame()


def trigger_github_actions_push() -> str:
    """Pushes latest changes to GitHub repository to trigger regression.yml."""
    try:
        # Add and commit any updated prompts or issues
        subprocess.run(["git", "add", "."], capture_output=True)
        subprocess.run(["git", "commit", "-m", "chore: automated test and prompt update from dashboard"], capture_output=True)
        push_res = subprocess.run(["git", "push", "origin", "main"], capture_output=True, text=True)
        if push_res.returncode == 0:
            return "✅ **Successfully pushed latest prompt fixes and issues to GitHub!**\nGitHub Actions workflow `regression.yml` has been triggered on `origin/main`.\nRepo: [https://github.com/DharshOz/Testing-agent.git](https://github.com/DharshOz/Testing-agent.git)"
        else:
            return f"Push status: {push_res.stderr or push_res.stdout}"
    except Exception as e:
        return f"Error triggering git push: {e}"


# =========================================================================
# GRADIO INTERFACE LAYOUT
# =========================================================================
theme = gr.themes.Soft(
    primary_hue="blue",
    secondary_hue="slate",
    neutral_hue="slate"
)

with gr.Blocks(title="AI Document Workflow & Testing Agent") as demo:
    gr.Markdown("""
    # 🏛️ Civic AI Platform & Autonomous Testing Agent Dashboard
    ### Complete Multi-Stage Document Processing Pipeline, Failure-to-Test Flywheel, and Human-Guided Prompt Healing
    """)
    key_status_banner = gr.Markdown(get_key_status_markdown())

    with gr.Tabs():
        # -------------------------------------------------------------
        # TAB 1: LIVE TICKET PROCESSING
        # -------------------------------------------------------------
        with gr.TabItem("🎫 Live Ticket Processing & Stage Inspector"):
            gr.Markdown("Select a pre-loaded actual sample dataset or upload your own files to inspect execution across all 5 stages.")

            with gr.Row():
                with gr.Column(scale=1):
                    preset_dropdown = gr.Dropdown(
                        label="Pre-loaded Actual Sample Data Presets",
                        choices=list(SAMPLE_PRESETS.keys()),
                        value="Sample 1: Valid Solar Pump Grant"
                    )
                    category_input = gr.Dropdown(
                        label="Ticket Category",
                        choices=["grant_request", "complaint", "permission"],
                        value="grant_request"
                    )
                    file_uploader = gr.File(
                        label="Upload Document Files (or auto-populated from sample)",
                        file_count="multiple",
                        type="filepath"
                    )
                    run_ticket_btn = gr.Button("🚀 Run AI Verification Pipeline", variant="primary")

                with gr.Column(scale=2):
                    verdict_banner = gr.Markdown("### Ticket Status: Awaiting Submission")
                    
                    with gr.Accordion("Stage 1: Text Extraction & Document Parser", open=False):
                        s1_table = gr.DataFrame(headers=["Filename", "Pages", "Extracted Chars", "Warnings"], label="Parsed Documents")

                    with gr.Accordion("Stage 2: RAG Field Extraction (Qdrant & Groq)", open=False):
                        s2_table = gr.DataFrame(headers=["Field Name", "Extracted Value", "Confidence", "Source Citation"], label="Extracted Target Fields")

                    with gr.Accordion("Stage 3: Document Completeness Audit", open=False):
                        s3_table = gr.DataFrame(headers=["Document Requirement", "Status", "Matched File", "Method"], label="Mandatory Documents Checklist")

                    with gr.Accordion("Stage 4: Multi-Document Consistency & Fraud Audit", open=True):
                        s4_table = gr.DataFrame(headers=["Audit Check", "Result", "Reason", "Evidence Quote"], label="Validation Checklist")

                    with gr.Accordion("Stage 5: Official Report Generation (.docx)", open=True):
                        report_preview_box = gr.Markdown("No report generated yet.")
                        report_download_file = gr.File(label="Download Generated Word Report (.docx)")

            # Connect preset selection
            preset_dropdown.change(
                fn=on_preset_select,
                inputs=[preset_dropdown],
                outputs=[file_uploader]
            )

            # Connect pipeline execution
            run_ticket_btn.click(
                fn=process_ticket_flow,
                inputs=[preset_dropdown, file_uploader, category_input],
                outputs=[
                    verdict_banner, s1_table, s2_table, s3_table, s4_table,
                    report_preview_box, report_download_file
                ]
            )

        # -------------------------------------------------------------
        # TAB 2: INTERACTIVE ISSUE SHEET & HUMAN-GUIDED FIX AGENT
        # -------------------------------------------------------------
        with gr.TabItem("🧪 Issue Sheet & Human-Guided Fix Agent"):
            gr.Markdown("""
            ### 📝 Failure-to-Test Flywheel & Autonomous Prompt Healing
            Select a problem from the issue sheet below. You can provide your own **solution idea / guidance**, and the agent will use it to heal the prompt generically while verifying against the full cascade.
            """)

            issues_grid = gr.DataFrame(value=load_issues_df(), label="Live Tester Issue Sheet (issues/issues.xlsx)")
            refresh_issues_btn = gr.Button("🔄 Refresh Issue Sheet")

            with gr.Row():
                with gr.Column(scale=1):
                    issue_selector = gr.Dropdown(
                        label="Select a Problem to Inspect & Solve",
                        choices=get_issue_choices(),
                        value=get_issue_choices()[0] if get_issue_choices() else None
                    )
                    issue_meta_box = gr.Markdown("**Select an issue above to view details.**")
                    issue_details_box = gr.Markdown("")
                    test_case_box = gr.Textbox(label="Regression Test Case ID", interactive=False)
                    prompt_file_box = gr.Textbox(label="Target Prompt File", interactive=False)

                with gr.Column(scale=1):
                    gr.Markdown("#### 💡 Human-in-the-Loop Solution Guidance")
                    gr.Markdown("*(Edit this solution idea! The agent will use your suggestion along with Groq to formulate the generic prompt fix.)*")
                    user_guidance_input = gr.TextArea(
                        label="Your Solution / Guidance Idea for the Agent",
                        lines=4,
                        placeholder="e.g. Normalize identity numbers by stripping scheme prefixes like 'AADHAAR-' before comparing."
                    )
                    max_attempts_slider = gr.Slider(minimum=1, maximum=6, value=4, step=1, label="Max Fix Attempts")
                    solve_problem_btn = gr.Button("🤖 Solve Problem using Agent & My Idea", variant="primary")

            gr.Markdown("### 🔍 Before vs After Comparison & Verification Cascade")
            fix_outcome_box = gr.Markdown("No fix run yet.")

            with gr.Row():
                with gr.Column(scale=1):
                    gr.Markdown("#### 🔄 Visual Diff of Prompt Modifications (Before vs After)")
                    diff_viewer = gr.Code(label="Prompt Unified Diff", language=None, lines=14)
                with gr.Column(scale=1):
                    gr.Markdown("#### ⚖️ Assertion Status Comparison (Before vs After)")
                    comp_table = gr.DataFrame(headers=["Assertion Name", "Status BEFORE Fix", "Status AFTER Fix", "Rationale"], label="Assertion Comparison")

            with gr.Accordion("Inspect Full Updated Prompt", open=False):
                updated_prompt_box = gr.Code(label="Updated Prompt Content", language="markdown", lines=16)

            # Events
            refresh_issues_btn.click(fn=load_issues_df, outputs=[issues_grid])
            refresh_issues_btn.click(fn=lambda: gr.update(choices=get_issue_choices()), outputs=[issue_selector])

            issue_selector.change(
                fn=on_issue_selected,
                inputs=[issue_selector],
                outputs=[issue_meta_box, issue_details_box, test_case_box, prompt_file_box, updated_prompt_box, user_guidance_input]
            )

            solve_problem_btn.click(
                fn=run_agent_solve_problem,
                inputs=[issue_selector, user_guidance_input, max_attempts_slider],
                outputs=[fix_outcome_box, diff_viewer, comp_table, updated_prompt_box]
            )
            # Update issues grid after solve
            solve_problem_btn.click(fn=load_issues_df, outputs=[issues_grid])

        # -------------------------------------------------------------
        # TAB 3: REGRESSION TEST SUITE & GITHUB ACTIONS
        # -------------------------------------------------------------
        with gr.TabItem("🚀 Regression Test Suite & GitHub Actions Dispatcher"):
            gr.Markdown("### Autonomous Verification & CI/CD Pipeline Automation")

            with gr.Row():
                with gr.Column(scale=1):
                    test_mode_radio = gr.Radio(
                        label="Test Scope",
                        choices=["All Tune Cases", "Changed Stages Only", "Held-Out Cases Only"],
                        value="All Tune Cases"
                    )
                    run_tests_btn = gr.Button("▶️ Execute Local Test Suite", variant="primary")
                    test_summary_box = gr.Markdown("Click to run tests.")

                with gr.Column(scale=1):
                    gr.Markdown("#### 🔗 GitHub Actions Telemetry")
                    gr.Markdown(f"**Remote Repository:** [https://github.com/DharshOz/Testing-agent.git](https://github.com/DharshOz/Testing-agent.git)\n\n**Configured Workflows:**\n- `regression.yml` (PR Regression Checks)\n- `agent_fix.yml` (Autonomous Issue Resolver)")
                    trigger_git_btn = gr.Button("🚀 Trigger GitHub Actions (Push Latest Fixes)", variant="secondary")
                    git_status_box = gr.Markdown("")

            test_results_table = gr.DataFrame(headers=["Case ID", "Stage", "Assertion", "Status", "Reason"], label="Detailed Assertion Breakdown")

            run_tests_btn.click(
                fn=run_full_test_suite_action,
                inputs=[test_mode_radio],
                outputs=[test_summary_box, test_results_table]
            )

            trigger_git_btn.click(
                fn=trigger_github_actions_push,
                outputs=[git_status_box]
            )

        # -------------------------------------------------------------
        # TAB 4: ACTUAL SAMPLE DATA GUIDE
        # -------------------------------------------------------------
        with gr.TabItem("📖 Actual Sample Data & Step-by-Step Guide"):
            gr.Markdown("""
            ## 📂 Actual Sample Data Included in this Repository

            Every sample dataset is built with physical PDF, DOCX, and TXT files ready to test:

            ### 1. `sample_data/ticket_1_valid/` (Fully Valid Grant)
            - **Category:** `grant_request`
            - **Documents:**
              1. `application_form.pdf`: Infrastructure grant application for Community Solar Water Pump in Ward 4 by Aarav Sharma.
              2. `aadhaar_identity_proof.txt`: Official Aadhaar card for Aarav Sharma (`AADHAAR-8921-4321-9988`).
              3. `address_proof_utility_bill.docx`: Electricity domestic utility bill for Aarav Sharma at Ward 4.
              4. `sanction_order_approval.pdf`: Department sanction clearance order `#SANCTION-ORD-2026-449` for INR 2,50,000.
              5. `community_consent_resolution.docx`: Formal Gram Sabha resolution with 8 resident signatures.
            - **Expected Outcome:** Passes all 5 stages $\rightarrow$ Generates official Word report (`.docx`).

            ### 2. `sample_data/ticket_2_missing_consent/` (Missing Mandatory Document)
            - **Category:** `grant_request`
            - **Documents:** Application form, Aadhaar ID, utility bill, sanction order. *(Missing community consent doc)*.
            - **Expected Outcome:** Caught at Stage 3 / 4. **Token-saving hard rule immediately halts pipeline** before invoking LLM.

            ### 3. `sample_data/ticket_3_inconsistent_names/` (Identity Mismatch)
            - **Category:** `grant_request`
            - **Documents:** Application form has Vikram Malhotra, but ID proof and sanction order belong to Rajesh Kumar Verma.
            - **Expected Outcome:** Stage 4 identifies cross-document name conflict and rejects application.

            ### 4. `tests_agent/cases/case_demo_id_prefix/` (Demonstration Issue: Prefixed Identifiers)
            - **Issue:** Application form has `AADHAAR-8921-4321-9988`, while Aadhaar card states `8921-4321-9988`.
            - **Use in Tab 2:** Select Issue #4 in the Issue Sheet tab, provide the guidance idea: *"Normalize by stripping scheme prefixes like 'AADHAAR-' before comparing"*, and click **Solve Problem**.

            ---

            ### 🚀 How to Run the Gradio App
            ```powershell
            python gradio_app.py
            ```
            Open your browser at `http://localhost:7860`.
            """)


if __name__ == "__main__":
    demo.launch(theme=theme, server_name="127.0.0.1", server_port=7860, share=False)
