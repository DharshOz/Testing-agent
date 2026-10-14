import json
from pathlib import Path
from typing import Dict, Any, List


def format_markdown_report(
    run_results: Dict[str, Dict[str, List[Dict[str, Any]]]],
    regressions: List[Dict[str, Any]]
) -> str:
    """
    Renders a comprehensive, polished Markdown table report:
    Grouped by Case ID then Stage, showing individual assertions.
    """
    lines = []
    lines.append("# Multi-Stage AI Workflow Test Report\n")

    total_assertions = 0
    total_passed = 0
    total_failed = 0
    total_flaky = 0

    for case_id, stage_dict in run_results.items():
        for stage_name, assertions in stage_dict.items():
            for a in assertions:
                total_assertions += 1
                if a.get("passed"):
                    total_passed += 1
                else:
                    total_failed += 1
                if a.get("flaky"):
                    total_flaky += 1

    # Summary badge banner
    pass_rate = (total_passed / total_assertions * 100) if total_assertions > 0 else 100.0
    status_icon = "[PASS] ALL CHECKS PASSED" if (total_failed == 0 and len(regressions) == 0) else "[FAIL] REGRESSIONS / FAILURES DETECTED"

    lines.append(f"### Status: **{status_icon}**")
    lines.append(f"- **Total Assertions:** {total_assertions}")
    lines.append(f"- **Passed:** {total_passed} ({pass_rate:.1f}%)")
    lines.append(f"- **Failed:** {total_failed}")
    lines.append(f"- **Flaky:** {total_flaky}")
    lines.append(f"- **Regressions:** {len(regressions)}\n")

    if regressions:
        lines.append("## Detected Regressions\n")
        lines.append("| Case ID | Stage | Assertion | Regression Type | Reason |")
        lines.append("|---|---|---|---|---|")
        for r in regressions:
            lines.append(
                f"| `{r.get('case_id')}` | `{r.get('stage')}` | **{r.get('name')}** | `{r.get('regression_type')}` | {r.get('reason')} |"
            )
        lines.append("\n")

    lines.append("## Detailed Assertion Results\n")
    lines.append("| Case ID | Stage | Assertion Name | Status | Rationale | Evidence Snippet |")
    lines.append("|---|---|---|---|---|---|")

    for case_id, stage_dict in sorted(run_results.items()):
        for stage_name, assertions in stage_dict.items():
            for a in assertions:
                status = "PASS" if a.get("passed") else ("FLAKY" if a.get("flaky") else "FAIL")
                reason = a.get("reason", "").replace("\n", " ")
                evidence = a.get("evidence", "").replace("\n", " ")
                if len(evidence) > 100:
                    evidence = evidence[:97] + "..."
                lines.append(
                    f"| `{case_id}` | `{stage_name}` | {a.get('name')} | **{status}** | {reason} | `{evidence}` |"
                )

    return "\n".join(lines)


def save_json_report(
    filepath: str,
    run_results: Dict[str, Dict[str, List[Dict[str, Any]]]],
    regressions: List[Dict[str, Any]]
):
    """Saves structured test run results as JSON."""
    Path(filepath).parent.mkdir(parents=True, exist_ok=True)
    report_data = {
        "summary": {
            "regressions_count": len(regressions),
        },
        "regressions": regressions,
        "results": run_results
    }
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(report_data, f, indent=2)
