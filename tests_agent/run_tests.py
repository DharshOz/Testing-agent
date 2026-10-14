import argparse
import sys
from pathlib import Path
from typing import List, Dict, Any, Set

from tests_agent.runner import TestRunner
from tests_agent.graph import get_stages_in_order, downstream_of
from tests_agent.detect_changes import get_changed_stages, get_test_plan
from tests_agent.report import format_markdown_report, save_json_report


def main():
    parser = argparse.ArgumentParser(description="Multi-Stage AI Workflow Test Agent CLI")
    parser.add_argument("--mode", choices=["changed", "stage", "propagation", "all"], default=None,
                        help="Test execution mode: changed (detect from git), stage, propagation, or all")
    parser.add_argument("--stage", type=str, default=None,
                        help="Specific stage to test (e.g. validate_ticket)")
    parser.add_argument("--case", type=str, default=None,
                        help="Specific case ID to test (e.g. case_01_valid_grant)")
    parser.add_argument("--all", action="store_true",
                        help="Run all cases across all stages")
    parser.add_argument("--heldout", action="store_true",
                        help="Run only heldout cases (used to detect overfitting)")
    parser.add_argument("--update-baseline", action="store_true",
                        help="Update baseline.json with current results")
    parser.add_argument("--output-md", type=str, default="test_report.md",
                        help="Path to output markdown report")
    parser.add_argument("--output-json", type=str, default="test_report.json",
                        help="Path to output JSON report")

    args = parser.parse_args()
    runner = TestRunner()

    # Discover available cases
    cases_dir = runner.cases_dir
    if not cases_dir.exists():
        print(f"Cases directory not found at {cases_dir}")
        sys.exit(1)

    all_cases = [p.name for p in cases_dir.iterdir() if p.is_dir()]
    if not all_cases:
        print("No test cases found in cases/ directory.")
        sys.exit(0)

    # Filter cases
    selected_cases = all_cases
    if args.case:
        if args.case not in all_cases:
            print(f"Error: Case {args.case} not found in {cases_dir}")
            sys.exit(1)
        selected_cases = [args.case]
    elif args.heldout:
        selected_cases = [c for c in all_cases if runner.load_case_meta(c).get("split") == "heldout"]
    elif not args.all and args.mode != "all":
        # Default runs "tune" cases unless --heldout or --all is requested
        selected_cases = [c for c in all_cases if runner.load_case_meta(c).get("split", "tune") != "heldout"]

    all_stages = get_stages_in_order()

    # Determine execution plan
    stage_tests_to_run: List[str] = []
    prop_tests_to_run: List[str] = []

    if args.mode == "changed":
        changed_stages = get_changed_stages()
        if not changed_stages:
            print("No stage-related changes detected by git. Running full sanity check on tune cases.")
            stage_tests_to_run = all_stages
        else:
            print(f"Git detected changes in stages: {list(changed_stages)}")
            s_plan, p_plan = get_test_plan(changed_stages)
            stage_tests_to_run = s_plan
            prop_tests_to_run = p_plan
            print(f"Plan -> Isolated Stage Tests: {stage_tests_to_run} | Propagation Tests: {prop_tests_to_run}")

    elif args.stage:
        if args.stage not in all_stages:
            print(f"Error: Unknown stage '{args.stage}'. Registered: {all_stages}")
            sys.exit(1)
        if args.mode == "propagation":
            prop_tests_to_run = [args.stage]
        else:
            stage_tests_to_run = [args.stage]

    else:
        # Default or --all
        stage_tests_to_run = all_stages

    # Execute tests
    run_results: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
    detected_regressions: List[Dict[str, Any]] = []
    new_baseline: Dict[str, bool] = dict(runner.baseline)

    print(f"\n[RUN] Running Tests on {len(selected_cases)} cases...")
    print(f"   Cases: {selected_cases}")
    print(f"   Stages to test in isolation: {stage_tests_to_run}")
    if prop_tests_to_run:
        print(f"   Stages to test propagation: {prop_tests_to_run}\n")

    for case_id in selected_cases:
        run_results[case_id] = {}

        # 1. Isolated Stage Tests
        for stg in stage_tests_to_run:
            try:
                _, assertions = runner.run_stage_test(case_id, stg)
                run_results[case_id][stg] = assertions

                for a in assertions:
                    if args.update_baseline:
                        new_baseline[f"{case_id}::{stg}::{a['name']}"] = a["passed"]

                regs = runner.detect_regressions(case_id, stg, assertions)
                for r in regs:
                    r["case_id"] = case_id
                    r["stage"] = stg
                    detected_regressions.append(r)

            except Exception as e:
                err_assertion = {
                    "name": f"Stage Execution Error [{stg}]",
                    "passed": False,
                    "reason": f"Execution crashed: {e}",
                    "evidence": str(e)
                }
                run_results[case_id][stg] = [err_assertion]
                err_reg = dict(err_assertion)
                err_reg["case_id"] = case_id
                err_reg["stage"] = stg
                err_reg["regression_type"] = "crash_failure"
                detected_regressions.append(err_reg)

        # 2. Propagation Tests
        for start_stg in prop_tests_to_run:
            try:
                prop_map = runner.run_propagation_test(case_id, start_stg)
                for down_stg, (_, assertions) in prop_map.items():
                    tag_name = f"{down_stg} (prop from {start_stg})"
                    run_results[case_id][tag_name] = assertions

                    for a in assertions:
                        if args.update_baseline:
                            new_baseline[f"{case_id}::{down_stg}::{a['name']}"] = a["passed"]

                    regs = runner.detect_regressions(case_id, down_stg, assertions)
                    for r in regs:
                        r["case_id"] = case_id
                        r["stage"] = tag_name
                        detected_regressions.append(r)

            except Exception as e:
                err_assertion = {
                    "name": f"Propagation Execution Error [{start_stg}]",
                    "passed": False,
                    "reason": f"Propagation crashed: {e}",
                    "evidence": str(e)
                }
                run_results[case_id][f"prop_{start_stg}"] = [err_assertion]
                err_reg = dict(err_assertion)
                err_reg["case_id"] = case_id
                err_reg["stage"] = start_stg
                err_reg["regression_type"] = "crash_failure"
                detected_regressions.append(err_reg)

    if args.update_baseline:
        runner.save_baseline(new_baseline)
        print("[OK] Baseline updated successfully.")

    # Generate Reports
    md_report = format_markdown_report(run_results, detected_regressions)
    with open(args.output_md, "w", encoding="utf-8") as f:
        f.write(md_report)
    save_json_report(args.output_json, run_results, detected_regressions)

    print("\n" + md_report)
    print(f"\n[INFO] Reports saved to:\n   - Markdown: {args.output_md}\n   - JSON: {args.output_json}")

    # Exit code non-zero on regression
    if detected_regressions:
        print(f"\n[FAIL] FAILED: {len(detected_regressions)} regressions or must-pass failures detected!")
        sys.exit(1)
    else:
        print("\n[SUCCESS] All tests passed with zero regressions.")
        sys.exit(0)


if __name__ == "__main__":
    main()
