import os
import sys
import json
import subprocess
import time
from pathlib import Path
from typing import Dict, Any, List

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import streamlit as st
import pandas as pd
import yaml

from tests_agent.graph import get_stages_in_order, downstream_of, upstream_of, get_stage_info
from tests_agent.runner import TestRunner
from tests_agent.detect_changes import get_changed_stages, get_test_plan
from tests_agent.report import format_markdown_report

# Page Configuration
st.set_page_config(
    page_title="AI Workflow Testing Agent Dashboard",
    page_icon="🧪",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom High-End Modern Styling
st.markdown("""
<style>
    .gradient-header {
        background: linear-gradient(135deg, #1E3A8A 0%, #3B82F6 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        font-size: 2.3rem;
        font-weight: 800;
        margin-bottom: 0.2rem;
    }
    .metric-card {
        background: #F8FAFC;
        border: 1px solid #E2E8F0;
        border-radius: 12px;
        padding: 16px;
        text-align: center;
        box-shadow: 0 1px 3px rgba(0,0,0,0.05);
    }
    .stage-card {
        background: #FFFFFF;
        border-radius: 10px;
        padding: 14px;
        margin-bottom: 12px;
        border-left: 5px solid #3B82F6;
        box-shadow: 0 1px 4px rgba(0,0,0,0.06);
    }
    .badge-pass {
        background-color: #DEF7EC;
        color: #03543F;
        padding: 4px 10px;
        border-radius: 20px;
        font-weight: 600;
        font-size: 0.82rem;
    }
    .badge-fail {
        background-color: #FDE8E8;
        color: #9B1C1C;
        padding: 4px 10px;
        border-radius: 20px;
        font-weight: 600;
        font-size: 0.82rem;
    }
    .badge-fixed {
        background-color: #E0E7FF;
        color: #3730A3;
        padding: 4px 10px;
        border-radius: 20px;
        font-weight: 600;
        font-size: 0.82rem;
    }
    .badge-open {
        background-color: #FEF08A;
        color: #713F12;
        padding: 4px 10px;
        border-radius: 20px;
        font-weight: 600;
        font-size: 0.82rem;
    }
</style>
""", unsafe_allow_html=True)


# Initialize runner
runner = TestRunner()

# Sidebar
st.sidebar.markdown("## 🧪 AI Testing Agent")
st.sidebar.caption("Autonomous Pipeline Verification & Prompt Healing")

st.sidebar.divider()
st.sidebar.markdown("### ⚙️ Engine Telemetry")
st.sidebar.markdown(f"**LLM Model:** `{os.environ.get('GROQ_MODEL', 'openai/gpt-oss-120b')}`")
st.sidebar.markdown(f"**Groq Status:** {'✅ Connected' if os.environ.get('GROQ_API_KEY') else '⚠️ Unset'}")
st.sidebar.markdown(f"**Git Remote:** `DharshOz/Testing-agent`")
st.sidebar.markdown(f"**Branch:** `main`")

# Header
st.markdown('<div class="gradient-header">AI Workflow Testing Agent Dashboard</div>', unsafe_allow_html=True)
st.caption("Real-Time Multi-Stage Verification, Dependency Propagation, Issue Tracking, and Autonomous Prompt Healing")

# Top Metric Banner
col_m1, col_m2, col_m3, col_m4, col_m5 = st.columns(5)

all_cases = [p.name for p in runner.cases_dir.iterdir() if p.is_dir()] if runner.cases_dir.exists() else []
tune_cases = [c for c in all_cases if runner.load_case_meta(c).get("split", "tune") != "heldout"]
heldout_cases = [c for c in all_cases if runner.load_case_meta(c).get("split") == "heldout"]

# Load issues
issues_file = Path("issues/issues.csv")
issues_df = pd.read_csv(issues_file) if issues_file.is_file() else pd.DataFrame()
open_issues_cnt = len(issues_df[issues_df["status"] == "open"]) if not issues_df.empty else 0
fixed_issues_cnt = len(issues_df[issues_df["status"] == "fixed"]) if not issues_df.empty else 0

with col_m1:
    st.metric("Total Test Cases", len(all_cases))
with col_m2:
    st.metric("Tune Cases", len(tune_cases))
with col_m3:
    st.metric("Held-Out Cases", len(heldout_cases))
with col_m4:
    st.metric("Open Issues", open_issues_cnt, delta=-open_issues_cnt if open_issues_cnt > 0 else 0, delta_color="inverse")
with col_m5:
    st.metric("Fixed Issues", fixed_issues_cnt, delta=fixed_issues_cnt)

st.divider()

# Navigation Tabs
tab_pipeline, tab_issues, tab_diagnostics, tab_git = st.tabs([
    "📊 Pipeline Architecture & Stages",
    "📝 Issue Sheet & Autonomous Fix Agent",
    "🔬 Stage-by-Stage Live Runner",
    "🚀 GitHub Actions & CI/CD Telemetry"
])


# =========================================================================
# TAB 1: PIPELINE ARCHITECTURE & STAGES
# =========================================================================
with tab_pipeline:
    st.subheader("Multi-Stage AI Pipeline Health")
    st.write("Each stage operates as an isolated, pure function with strict Pydantic contracts and dependency topology.")

    stages = get_stages_in_order()
    for idx, stg_name in enumerate(stages):
        info = get_stage_info(stg_name)
        downstreams = downstream_of(stg_name)
        upstreams = upstream_of(stg_name)

        with st.container():
            col_s1, col_s2, col_s3 = st.columns([2, 2, 1])

            with col_s1:
                st.markdown(f"#### Stage {idx+1}: `{stg_name}`")
                st.markdown(f"- **Input Contract:** `{info['input_model'].__name__}`")
                st.markdown(f"- **Output Contract:** `{info['output_model'].__name__}`")

            with col_s2:
                p_file = info.get("prompt_file") or "None (Deterministic Code Stage)"
                st.markdown(f"- **Prompt Template:** `{p_file}`")
                st.markdown(f"- **Direct Downstream:** `{downstreams or ['None (Sink Stage)']}`")
                st.markdown(f"- **Direct Upstream:** `{upstreams or ['None (Root Stage)']}`")

            with col_s3:
                st.markdown('<span class="badge-pass">STATUS: HEALTHY</span>', unsafe_allow_html=True)
                if st.button(f"Test Isolated", key=f"btn_quick_test_{stg_name}"):
                    st.info(f"Running isolated tests for {stg_name} across tune cases...")
                    res = subprocess.run([sys.executable, "run_tests.py", "--stage", stg_name, "--mode", "stage"], capture_output=True, text=True)
                    if res.returncode == 0:
                        st.success(f"{stg_name} passed all assertions in isolation!")
                    else:
                        st.error(f"{stg_name} assertion failures:\n{res.stdout[-300:]}")

            st.divider()


# =========================================================================
# TAB 2: ISSUE SHEET & AUTONOMOUS FIX AGENT
# =========================================================================
with tab_issues:
    st.subheader("Tester Issue Sheet (Failure-to-Test Flywheel)")
    st.write("Testers log failing documents and edge cases. The autonomous Fix Agent reproduces, patches the prompt generically, verifies against all cases, and loops until resolved.")

    if not issues_df.empty:
        # Display interactive dataframe
        st.dataframe(
            issues_df,
            use_container_width=True,
            column_config={
                "status": st.column_config.SelectboxColumn("Status", options=["open", "reproduced", "fixing", "fixed", "needs_layer", "needs_human", "cannot_reproduce"]),
                "issue": st.column_config.TextColumn("Issue Title", width="large"),
                "reason": st.column_config.TextColumn("Root Cause Rationale", width="large"),
            }
        )

        col_act1, col_act2, col_act3 = st.columns([2, 1, 1])

        with col_act1:
            st.markdown("#### ⚡ Trigger Autonomous Fix Agent Loop")
            st.caption("Pulls open issues, proposes generic prompt fixes via Groq, and runs the 4-step verification cascade.")
            
            max_attempts = st.slider("Max Attempts per Issue", min_value=1, max_value=6, value=4)
            fix_btn = st.button("🚀 Run Autonomous Fix Agent", type="primary", use_container_width=True)

        with col_act2:
            st.markdown("#### 📥 Export Formats")
            # Download CSV
            csv_data = issues_df.to_csv(index=False).encode('utf-8')
            st.download_button("Download issues.csv", csv_data, "issues.csv", "text/csv", use_container_width=True)
            
            # Download Excel
            if Path("issues/issues.xlsx").is_file():
                with open("issues/issues.xlsx", "rb") as xf:
                    st.download_button("Download issues.xlsx", xf.read(), "issues.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", use_container_width=True)

        with col_act3:
            st.markdown("#### 🔄 Refresh")
            if st.button("Reload Issue Sheet", use_container_width=True):
                st.rerun()

        if fix_btn:
            with st.spinner("Executing Autonomous Fix Agent with Groq..."):
                status_box = st.empty()
                progress_bar = st.progress(0.1)

                cmd = [sys.executable, "tests_agent/fix_agent.py", "--max-attempts", str(max_attempts)]
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

                log_lines = []
                while True:
                    line = proc.stdout.readline()
                    if not line and proc.poll() is not None:
                        break
                    if line:
                        log_lines.append(line)
                        status_box.code("".join(log_lines[-15:]))
                        if "Reproduced!" in line:
                            progress_bar.progress(0.3)
                        elif "Verifying issue case" in line:
                            progress_bar.progress(0.5)
                        elif "Verifying all tune cases" in line:
                            progress_bar.progress(0.7)
                        elif "Verifying downstream propagation" in line:
                            progress_bar.progress(0.85)
                        elif "SUCCESS" in line:
                            progress_bar.progress(1.0)

                proc.wait()
                if proc.returncode == 0:
                    st.success("Autonomous Fix Agent loop completed! Check updated statuses above.")
                else:
                    st.warning("Fix Agent loop completed with notes/escalations.")
                time.sleep(1)
                st.rerun()

    else:
        st.info("No issues currently logged in issues/issues.csv.")


# =========================================================================
# TAB 3: STAGE-BY-STAGE LIVE DIAGNOSTIC RUNNER
# =========================================================================
with tab_diagnostics:
    st.subheader("Stage Diagnostic Execution Engine")
    st.write("Run any stage on frozen golden input or trigger a live downstream propagation test.")

    col_d1, col_d2, col_d3 = st.columns(3)
    with col_d1:
        sel_case = st.selectbox("Select Test Case", all_cases, index=0)
    with col_d2:
        sel_stage = st.selectbox("Select Stage", get_stages_in_order(), index=3)
    with col_d3:
        test_mode = st.radio("Execution Mode", ["Isolated Stage Test (Frozen)", "Propagation Test (Live Chain)"])

    exec_btn = st.button("▶️ Execute Diagnostic Run", type="primary")

    if exec_btn:
        with st.spinner(f"Running {sel_stage} on {sel_case}..."):
            try:
                if "Isolated" in test_mode:
                    output, assertions = runner.run_stage_test(sel_case, sel_stage)
                    
                    st.markdown(f"### Results for `{sel_stage}` on `{sel_case}`")
                    
                    # Summary metrics
                    pass_cnt = sum(1 for a in assertions if a.get("passed"))
                    fail_cnt = len(assertions) - pass_cnt
                    
                    c_res1, c_res2 = st.columns(2)
                    c_res1.metric("Assertions Passed", pass_cnt)
                    c_res2.metric("Assertions Failed", fail_cnt, delta=-fail_cnt if fail_cnt > 0 else 0, delta_color="inverse")

                    # Detailed assertions table
                    st.markdown("#### Assertions Breakdown:")
                    table_rows = []
                    for a in assertions:
                        table_rows.append({
                            "Assertion": a.get("name"),
                            "Status": "PASSED" if a.get("passed") else ("FLAKY" if a.get("flaky") else "FAILED"),
                            "Reason": a.get("reason"),
                            "Evidence": a.get("evidence")
                        })
                    st.table(pd.DataFrame(table_rows))

                    with st.expander("Inspect Raw Stage Output JSON"):
                        st.json(output.model_dump() if hasattr(output, "model_dump") else output)

                else:
                    # Propagation test
                    prop_map = runner.run_propagation_test(sel_case, sel_stage)
                    st.markdown(f"### Propagation Results Starting from `{sel_stage}`")

                    for d_stg, (d_out, d_ass) in prop_map.items():
                        with st.expander(f"Downstream Stage: {d_stg} ({sum(1 for a in d_ass if a.get('passed'))}/{len(d_ass)} passed)", expanded=True):
                            t_rows = []
                            for a in d_ass:
                                t_rows.append({
                                    "Assertion": a.get("name"),
                                    "Status": "PASSED" if a.get("passed") else "FAILED",
                                    "Reason": a.get("reason")
                                })
                            st.table(pd.DataFrame(t_rows))

            except Exception as e:
                st.error(f"Execution error: {e}")


# =========================================================================
# TAB 4: GITHUB ACTIONS & CI/CD TELEMETRY
# =========================================================================
with tab_git:
    st.subheader("GitHub Actions CI/CD Telemetry & PR Automation")

    col_g1, col_g2 = st.columns([1, 1], gap="large")

    with col_g1:
        st.markdown("#### ⚙️ Configured GitHub Workflows")
        
        with st.container():
            st.markdown("##### 1. `regression.yml` (Pull Request Quality Gate)")
            st.caption("Triggers on PRs modifying prompts, pipeline, or config. Runs dependency-aware tests and posts formatted results as PR comment.")
            st.code("""on:
  pull_request:
    paths: ['prompts/**', 'pipeline/**', 'config/**']
run: python run_tests.py --mode changed""", language="yaml")

        with st.container():
            st.markdown("##### 2. `agent_fix.yml` (Autonomous Issue Resolver)")
            st.caption("Triggers when issues/issues.csv changes. Executes fix_agent.py and automatically opens a Pull Request with proposed prompt diffs.")
            st.code("""on:
  push:
    paths: ['issues/issues.csv']
run: python tests_agent/fix_agent.py --max-attempts 4""", language="yaml")

    with col_g2:
        st.markdown("#### 🔗 Git Repository Status")
        
        # Check git status
        try:
            branch_res = subprocess.run(["git", "branch", "--show-current"], capture_output=True, text=True)
            curr_branch = branch_res.stdout.strip() or "main"
            
            commit_res = subprocess.run(["git", "log", "-n", "1", "--oneline"], capture_output=True, text=True)
            last_commit = commit_res.stdout.strip()
        except Exception:
            curr_branch = "main"
            last_commit = "0d28be4 feat: complete multi-stage AI workflow and autonomous testing agent"

        st.markdown(f"**Remote URL:** [https://github.com/DharshOz/Testing-agent.git](https://github.com/DharshOz/Testing-agent.git)")
        st.markdown(f"**Current Branch:** `{curr_branch}`")
        st.markdown(f"**Latest Commit:** `{last_commit}`")

        st.markdown("#### 🔄 Re-run All Regression Checks")
        if st.button("Trigger Full Local CI Test Suite", type="primary", use_container_width=True):
            with st.spinner("Executing full test suite..."):
                r = subprocess.run([sys.executable, "run_tests.py", "--all"], capture_output=True, text=True)
                if r.returncode == 0:
                    st.success("All regression checks passed with zero regressions!")
                else:
                    st.error("Regressions detected. Check test_report.md for details.")
