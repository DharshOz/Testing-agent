import hashlib
import json
import os
import shutil
import uuid
from pathlib import Path
from typing import List, Dict, Any

import streamlit as st
import yaml
import requests

from db.database import engine, init_db
from db.models import Ticket, TicketFile, StageOutput
from pipeline.runner import run_pipeline
from sqlmodel import Session, select

# Page config
st.set_page_config(
    page_title="Civic Ticket AI Platform",
    page_icon="📋",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS for polished government-grade / modern portal aesthetics
st.markdown("""
<style>
    .main-header {
        font-size: 2.2rem;
        font-weight: 700;
        color: #1E3A8A;
        margin-bottom: 0.2rem;
    }
    .sub-header {
        font-size: 1.05rem;
        color: #4B5563;
        margin-bottom: 1.5rem;
    }
    .badge-valid {
        background-color: #DEF7EC;
        color: #03543F;
        padding: 4px 10px;
        border-radius: 9999px;
        font-weight: 600;
        font-size: 0.85rem;
    }
    .badge-invalid {
        background-color: #FDE8E8;
        color: #9B1C1C;
        padding: 4px 10px;
        border-radius: 9999px;
        font-weight: 600;
        font-size: 0.85rem;
    }
    .badge-review {
        background-color: #FEF08A;
        color: #713F12;
        padding: 4px 10px;
        border-radius: 9999px;
        font-weight: 600;
        font-size: 0.85rem;
    }
    .badge-ready {
        background-color: #E0E7FF;
        color: #3730A3;
        padding: 4px 10px;
        border-radius: 9999px;
        font-weight: 600;
        font-size: 0.85rem;
    }
    .card {
        background-color: #FFFFFF;
        border: 1px solid #E5E7EB;
        border-radius: 8px;
        padding: 18px;
        margin-bottom: 14px;
        box-shadow: 0 1px 3px rgba(0,0,0,0.05);
    }
</style>
""", unsafe_allow_html=True)


# Initialize DB
init_db()
API_BASE_URL = os.environ.get("API_BASE_URL", "http://127.0.0.1:8000")


def load_yaml_config() -> Dict[str, Any]:
    with open("config/required_docs.yaml", "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


REQUIRED_DOCS_CONFIG = load_yaml_config()


def submit_ticket_locally(category: str, uploaded_files: list) -> Ticket:
    """Fallback direct database + pipeline processing if FastAPI is not running."""
    ticket_id = f"TICK-{uuid.uuid4().hex[:8].upper()}"
    upload_dir = Path(f"./uploads/{ticket_id}")
    upload_dir.mkdir(parents=True, exist_ok=True)

    file_records = []
    for f in uploaded_files:
        path = upload_dir / f.name
        content = f.getvalue()
        sha256 = hashlib.sha256(content).hexdigest()
        path.write_bytes(content)

        file_records.append(TicketFile(
            ticket_id=ticket_id,
            filename=f.name,
            path=str(path),
            sha256=sha256
        ))

    with Session(engine) as session:
        ticket = Ticket(id=ticket_id, category=category, status="submitted")
        session.add(ticket)
        for fr in file_records:
            session.add(fr)
        session.commit()
        session.refresh(ticket)

    return run_pipeline(ticket_id)


# Sidebar Navigation
st.sidebar.markdown("## 🏛️ Civic AI Platform")
st.sidebar.caption("Document Verification & Ticket Workflow")

page = st.sidebar.radio(
    "Navigation",
    ["Raise Ticket", "My Tickets", "Officials View"],
    index=0
)

st.sidebar.divider()
st.sidebar.markdown("### ⚙️ Engine Status")
model_name = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")
groq_key_present = bool(os.environ.get("GROQ_API_KEY"))
st.sidebar.markdown(f"**LLM Model:** `{model_name}`")
st.sidebar.markdown(f"**Groq API Key:** {'✅ Configured' if groq_key_present else '⚠️ Missing (Offline Mock)'}")
st.sidebar.markdown("**Embeddings:** `fastembed (bge-small-en-v1.5)`")
st.sidebar.markdown("**Vector Store:** `Qdrant Local (./qdrant_data)`")


# =========================================================================
# PAGE 1: RAISE TICKET
# =========================================================================
if page == "Raise Ticket":
    st.markdown('<div class="main-header">Raise a New Ticket</div>', unsafe_allow_html=True)
    st.markdown('<div class="sub-header">Upload your application and supporting documents for automated verification.</div>', unsafe_allow_html=True)

    col1, col2 = st.columns([1, 1], gap="large")

    with col1:
        st.subheader("1. Submission Details")
        category_options = list(REQUIRED_DOCS_CONFIG.keys())
        cat_format = {k: f"{REQUIRED_DOCS_CONFIG[k].get('name', k)} ({k})" for k in category_options}
        
        selected_category = st.selectbox(
            "Select Ticket Category",
            category_options,
            format_func=lambda x: cat_format.get(x, x)
        )

        cat_info = REQUIRED_DOCS_CONFIG.get(selected_category, {})
        st.info(cat_info.get("description", ""))

        st.subheader("2. Upload Documents")
        uploaded_files = st.file_uploader(
            "Upload application form and all supporting documents (PDF, DOCX, TXT)",
            type=["pdf", "docx", "doc", "txt", "md"],
            accept_multiple_files=True
        )

        # Quick Load from Sample Data button
        st.markdown("**Or quickly load synthetic sample:**")
        sample_choice = st.selectbox(
            "Select a pre-built test ticket bundle:",
            [
                "-- Choose a synthetic sample --",
                "Sample 1: Fully Valid (5 documents)",
                "Sample 2: Missing Consent Document (4 documents)",
                "Sample 3: Inconsistent Names Across Documents (5 documents)"
            ]
        )

        submit_btn = st.button("Submit Ticket & Run Verification", type="primary", use_container_width=True)

    with col2:
        st.subheader("Document Requirements Checklist")
        st.caption(f"Mandatory documents required for {cat_info.get('name', selected_category)}:")

        req_docs = cat_info.get("required_documents", [])
        for idx, doc in enumerate(req_docs):
            with st.container():
                st.markdown(f"**{idx+1}. {doc['name']}** (`{doc['type']}`)")
                st.write(f"*{doc['description']}*")
                st.caption(f"Keywords: {', '.join(doc.get('keywords', []))}")
                st.divider()

    if submit_btn:
        files_to_process = []
        is_sample = False

        if uploaded_files:
            files_to_process = uploaded_files
        elif sample_choice.startswith("Sample 1"):
            is_sample = True
            sample_dir = Path("sample_data/ticket_1_valid")
        elif sample_choice.startswith("Sample 2"):
            is_sample = True
            sample_dir = Path("sample_data/ticket_2_missing_consent")
        elif sample_choice.startswith("Sample 3"):
            is_sample = True
            sample_dir = Path("sample_data/ticket_3_inconsistent_names")
        else:
            st.error("Please upload files or pick a synthetic sample bundle.")

        if files_to_process or is_sample:
            with st.spinner("Processing documents through AI verification pipeline..."):
                try:
                    if is_sample:
                        # Copy sample files to a temporary ticket
                        t_id = f"TICK-{uuid.uuid4().hex[:8].upper()}"
                        u_dir = Path(f"./uploads/{t_id}")
                        u_dir.mkdir(parents=True, exist_ok=True)
                        file_records = []
                        for spath in sample_dir.iterdir():
                            if spath.is_file():
                                dest = u_dir / spath.name
                                shutil.copy(spath, dest)
                                sha = hashlib.sha256(dest.read_bytes()).hexdigest()
                                file_records.append(TicketFile(
                                    ticket_id=t_id,
                                    filename=spath.name,
                                    path=str(dest),
                                    sha256=sha
                                ))
                        with Session(engine) as session:
                            ticket = Ticket(id=t_id, category=selected_category, status="submitted")
                            session.add(ticket)
                            for fr in file_records:
                                session.add(fr)
                            session.commit()
                            session.refresh(ticket)
                        ticket = run_pipeline(t_id)
                    else:
                        ticket = submit_ticket_locally(selected_category, files_to_process)

                    st.success(f"Ticket submitted successfully! ID: **{ticket.id}**")
                    
                    # Display verdict summary
                    if ticket.verdict == "valid":
                        st.markdown(f'<span class="badge-valid">VERDICT: VALID & APPROVED ({ticket.status})</span>', unsafe_allow_html=True)
                    elif ticket.verdict == "invalid":
                        st.markdown(f'<span class="badge-invalid">VERDICT: INVALID ({ticket.status})</span>', unsafe_allow_html=True)
                    else:
                        st.markdown(f'<span class="badge-review">VERDICT: NEEDS HUMAN REVIEW ({ticket.status})</span>', unsafe_allow_html=True)

                    # Show Stage Snapshots
                    st.divider()
                    st.subheader("Audit & Extraction Results")
                    with Session(engine) as session:
                        stages = session.exec(select(StageOutput).where(StageOutput.ticket_id == ticket.id)).all()
                        for s in stages:
                            with st.expander(f"Stage: {s.stage_name} (Prompt hash: {s.prompt_version_hash or 'N/A'})"):
                                try:
                                    st.json(json.loads(s.output_json))
                                except Exception:
                                    st.text(s.output_json)

                    if ticket.status == "report_ready":
                        report_file = f"./reports/ticket_{ticket.id}_report.docx"
                        if os.path.exists(report_file):
                            with open(report_file, "rb") as rf:
                                st.download_button(
                                    label="📥 Download Generated Official Report (.docx)",
                                    data=rf.read(),
                                    file_name=f"Report_{ticket.id}.docx",
                                    mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                                )

                except Exception as ex:
                    st.error(f"Processing error: {ex}")


# =========================================================================
# PAGE 2: MY TICKETS
# =========================================================================
elif page == "My Tickets":
    st.markdown('<div class="main-header">My Tickets</div>', unsafe_allow_html=True)
    st.markdown('<div class="sub-header">Track your submitted tickets, verification status, and plain-language audit feedback.</div>', unsafe_allow_html=True)

    with Session(engine) as session:
        all_tickets = session.exec(select(Ticket).order_by(Ticket.created_at.desc())).all()

    if not all_tickets:
        st.info("No tickets submitted yet. Go to 'Raise Ticket' to submit your first ticket.")
    else:
        for t in all_tickets:
            with st.container():
                with Session(engine) as session:
                    files = session.exec(select(TicketFile).where(TicketFile.ticket_id == t.id)).all()
                    doc_check = session.exec(
                        select(StageOutput).where(StageOutput.ticket_id == t.id, StageOutput.stage_name == "check_documents")
                    ).first()
                    val_check = session.exec(
                        select(StageOutput).where(StageOutput.ticket_id == t.id, StageOutput.stage_name == "validate_ticket")
                    ).first()

                missing_docs = []
                if doc_check and doc_check.output_json:
                    try:
                        dc_data = json.loads(doc_check.output_json)
                        missing_docs = dc_data.get("missing_types", [])
                    except Exception:
                        pass

                val_summary = ""
                checks_list = []
                if val_check and val_check.output_json:
                    try:
                        val_data = json.loads(val_check.output_json)
                        val_summary = val_data.get("summary", "")
                        checks_list = val_data.get("checks", [])
                    except Exception:
                        pass

                # Ticket Card
                st.markdown(f"### Ticket #{t.id} - {t.category.upper()}")
                c1, c2, c3 = st.columns([1, 1, 1])
                c1.write(f"**Submitted:** {t.created_at[:19].replace('T', ' ')}")
                c2.write(f"**Files Uploaded:** {len(files)} files")

                # Badge
                if t.status == "report_ready":
                    c3.markdown('<span class="badge-ready">REPORT READY</span>', unsafe_allow_html=True)
                elif t.verdict == "valid":
                    c3.markdown('<span class="badge-valid">VALID</span>', unsafe_allow_html=True)
                elif t.verdict == "invalid":
                    c3.markdown('<span class="badge-invalid">INVALID</span>', unsafe_allow_html=True)
                else:
                    c3.markdown('<span class="badge-review">NEEDS REVIEW</span>', unsafe_allow_html=True)

                if val_summary:
                    st.info(f"**Audit Outcome:** {val_summary}")

                if missing_docs:
                    st.error(f"**Missing Required Documents:** {', '.join(missing_docs)}")
                    st.caption("Please re-upload this ticket with the mandatory missing documents to proceed.")

                with st.expander(f"View Details & Audit Logs for #{t.id}"):
                    st.write("**Uploaded Files:**")
                    for f in files:
                        st.markdown(f"- `{f.filename}` (SHA-256: `{f.sha256[:12]}...`)")

                    if checks_list:
                        st.write("**Audit Checks Detail:**")
                        for chk in checks_list:
                            status_icon = "✅" if chk.get("passed") else "❌"
                            st.write(f"{status_icon} **{chk.get('name')}**: {chk.get('reason')}")
                            if chk.get("evidence"):
                                st.caption(f"Evidence: {chk.get('evidence')}")

                    st.markdown("**Rerun Pipeline from Stage:**")
                    rerun_stage = st.selectbox(
                        "Choose starting stage:",
                        ["extract_text", "extract_fields", "check_documents", "validate_ticket", "generate_report"],
                        key=f"rerun_stage_{t.id}"
                    )
                    if st.button("Trigger Rerun", key=f"btn_rerun_{t.id}"):
                        with st.spinner("Rerunning pipeline..."):
                            run_pipeline(t.id, from_stage=rerun_stage)
                            st.success(f"Rerun completed from stage {rerun_stage}!")
                            st.rerun()

                st.divider()


# =========================================================================
# PAGE 3: OFFICIALS VIEW
# =========================================================================
elif page == "Officials View":
    st.markdown('<div class="main-header">Civic Officials Verification Portal</div>', unsafe_allow_html=True)
    st.markdown('<div class="sub-header">Review validated applications, audit checklists, and download generated official Word reports.</div>', unsafe_allow_html=True)

    with Session(engine) as session:
        officials_tickets = session.exec(
            select(Ticket).where(Ticket.status.in_(["report_ready", "approved_for_report", "needs_review", "invalid"]))
            .order_by(Ticket.created_at.desc())
        ).all()

    tab1, tab2 = st.tabs(["Ready for Official Action", "All Tickets Audit Log"])

    with tab1:
        ready_tickets = [t for t in officials_tickets if t.status in ["report_ready", "approved_for_report", "needs_review"]]
        if not ready_tickets:
            st.info("No tickets currently awaiting official action or report download.")
        else:
            for t in ready_tickets:
                st.markdown(f"### Ticket #{t.id} - {t.category.title()}")
                col_a, col_b = st.columns([2, 1])

                with col_a:
                    st.write(f"**Submitted At:** {t.created_at[:19].replace('T', ' ')}")
                    st.write(f"**Status:** `{t.status}` | **Verdict:** `{t.verdict}`")

                    # Load field extraction snapshot
                    with Session(engine) as session:
                        field_snap = session.exec(
                            select(StageOutput).where(StageOutput.ticket_id == t.id, StageOutput.stage_name == "extract_fields")
                        ).first()
                        val_snap = session.exec(
                            select(StageOutput).where(StageOutput.ticket_id == t.id, StageOutput.stage_name == "validate_ticket")
                        ).first()

                    if field_snap and field_snap.output_json:
                        f_data = json.loads(field_snap.output_json).get("fields", {})
                        app_name = f_data.get("applicant_name", {}).get("value", "N/A")
                        app_id = f_data.get("applicant_id", {}).get("value", "N/A")
                        amount = f_data.get("amount_requested", {}).get("value", "N/A")
                        issue = f_data.get("issue_title", {}).get("value", "N/A")
                        st.markdown(f"**Applicant:** {app_name} (ID: `{app_id}`) | **Requested Amount:** `{amount}`")
                        st.markdown(f"**Issue Title:** {issue}")

                with col_b:
                    report_file = f"./reports/ticket_{t.id}_report.docx"
                    if os.path.exists(report_file):
                        with open(report_file, "rb") as rf:
                            st.download_button(
                                label="📥 Download Official Report (.docx)",
                                data=rf.read(),
                                file_name=f"Official_Report_{t.id}.docx",
                                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                                key=f"dl_officials_{t.id}"
                            )
                    else:
                        st.caption("Report not generated (Ticket requires review or was rejected).")

                # Validation checks table
                if val_snap and val_snap.output_json:
                    val_data = json.loads(val_snap.output_json)
                    checks = val_data.get("checks", [])
                    if checks:
                        st.markdown("**Validation Checks Checklist:**")
                        table_data = []
                        for c in checks:
                            table_data.append({
                                "Check Name": c.get("name"),
                                "Status": "PASSED" if c.get("passed") else "FAILED",
                                "Reason": c.get("reason"),
                                "Evidence": c.get("evidence")
                            })
                        st.table(table_data)

                st.divider()

    with tab2:
        st.write(f"Total tickets in database: {len(officials_tickets)}")
        for t in officials_tickets:
            st.markdown(f"- **{t.id}** ({t.category}) - Status: `{t.status}`, Verdict: `{t.verdict}`")
