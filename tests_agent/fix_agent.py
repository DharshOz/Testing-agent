import csv
import datetime
import json
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

import yaml
from groq import Groq

from tests_agent.runner import TestRunner
from tests_agent.graph import get_stages_in_order, downstream_of, get_stage_info
from tests_agent.report import format_markdown_report

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("fix_agent")


def validate_diff_scope(diff_text: str) -> Tuple[bool, Optional[str]]:
    """
    STRICT GUARD: Ensures diff touches ONLY files in prompts/ directory.
    Rejects any diff touching code, config, tests, or anything outside prompts/.
    """
    target_files = []
    for line in diff_text.splitlines():
        if line.startswith("--- ") or line.startswith("+++ "):
            fname = line.split(maxsplit=1)[1].strip()
            # Clean common diff prefixes a/ and b/
            fname = re.sub(r"^[ab]/", "", fname)
            target_files.append(fname.replace("\\", "/"))

    if not target_files:
        # Check if text mentions non-prompt files
        return False, "Diff does not specify target files"

    for tf in target_files:
        if tf == "/dev/null":
            continue
        if not tf.startswith("prompts/"):
            return False, f"Diff targets illegal path outside prompts/: {tf}"

    return True, None


def apply_prompt_patch(prompt_path: Path, new_prompt_content: str, backup_path: Path) -> bool:
    """Safely writes updated prompt, keeping backup."""
    if not backup_path.exists():
        shutil.copy(prompt_path, backup_path)
    prompt_path.write_text(new_prompt_content, encoding="utf-8")
    return True


def generate_unified_diff(original: str, modified: str, filename: str) -> str:
    """Generates standard unified diff between original and modified text."""
    import difflib
    orig_lines = original.splitlines(keepends=True)
    mod_lines = modified.splitlines(keepends=True)
    diff = difflib.unified_diff(
        orig_lines,
        mod_lines,
        fromfile=f"a/{filename}",
        tofile=f"b/{filename}",
        n=3
    )
    return "".join(diff)


def call_groq_propose_fix(
    prompt_file_path: str,
    current_prompt: str,
    failing_input: Dict[str, Any],
    failing_output: Dict[str, Any],
    issue_desc: str,
    reason: str,
    failing_assertions: List[Dict[str, Any]],
    passing_examples: List[Dict[str, Any]],
    iteration: int,
    user_guidance: Optional[str] = None
) -> Tuple[str, str]:
    """
    Calls Groq to propose a generic prompt fix, incorporating user-provided solution guidance if present.
    Returns (new_prompt_content, diff_or_explanation).
    """
    api_key = os.environ.get("GROQ_API_KEY")
    model = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")
    fallback_model = "llama-3.3-70b-versatile"

    system_prompt = (
        "You are an expert prompt engineer fixing a multi-stage AI document processing prompt.\n"
        "RULES:\n"
        "1. Make the prompt change GENERIC for all data types. Do NOT hardcode specific names, IDs, dates, or values from the failing case.\n"
        "2. Modify as little of the existing prompt as possible while fixing the root cause.\n"
        "3. Output ONLY the complete, updated prompt file content inside a markdown codeblock ```markdown ... ```, with NO other text."
    )

    guidance_section = f"\nUSER-SUGGESTED SOLUTION / GUIDANCE:\n\"\"\"\n{user_guidance}\n\"\"\"\nIncorporate this solution idea cleanly and generically into the prompt.\n" if user_guidance else ""

    user_prompt = f"""
Prompt File: {prompt_file_path}
Current Prompt Content:
\"\"\"
{current_prompt}
\"\"\"

Issue Description:
{issue_desc}

Root Cause Reason:
{reason}
{guidance_section}
Failing Case Input:
{json.dumps(failing_input, indent=2, default=str)[:1200]}

Failing Case Output:
{json.dumps(failing_output, indent=2, default=str)[:1200]}

Failing Assertions:
{json.dumps(failing_assertions, indent=2)}

Passing Reference Examples from Other Data Types:
{json.dumps(passing_examples, indent=2, default=str)[:1000]}

Iteration Attempt: {iteration}
Please provide the complete, updated, improved prompt content that resolves this issue generically.
"""

    if not api_key:
        logger.warning("GROQ_API_KEY is not set. Simulating minor prompt clarification.")
        # Offline simulation: append a small generic instruction
        updated = current_prompt + "\n\n<!-- Generic clarification: Ensure all fields and consistency requirements are evaluated rigorously. -->\n"
        return updated, "Offline simulated prompt clarification."

    client = Groq(api_key=api_key)

    for selected_model in [model, fallback_model]:
        try:
            res = client.chat.completions.create(
                model=selected_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.0
            )
            raw = res.choices[0].message.content or ""
            # Extract content between markdown fences
            if "```" in raw:
                lines = raw.splitlines()
                start = 0
                end = len(lines)
                for idx, line in enumerate(lines):
                    if line.startswith("```"):
                        start = idx + 1
                        break
                for idx in range(len(lines) - 1, -1, -1):
                    if lines[idx].strip() == "```":
                        end = idx
                        break
                new_content = "\n".join(lines[start:end]).strip()
            else:
                new_content = raw.strip()

            return new_content, "Generated via Groq."
        except Exception as e:
            logger.warning(f"Groq fix proposal failed with model {selected_model}: {e}")
            continue

    raise RuntimeError("Failed to obtain prompt fix proposal from Groq.")


class FixAgentLoop:
    def __init__(self, config_path: str = "tests_agent/config.yaml"):
        with open(config_path, "r", encoding="utf-8") as f:
            self.config = yaml.safe_load(f) or {}

        self.runner = TestRunner(config_path)
        self.issues_csv_path = Path(self.config.get("paths", {}).get("issues_csv", "issues/issues.csv"))
        self.max_attempts = int(self.config.get("thresholds", {}).get("max_attempts", 4))
        self.max_tokens_budget = int(os.environ.get("MAX_TOKENS_BUDGET", self.config.get("thresholds", {}).get("max_tokens_budget", 100000)))
        self.tokens_used = 0

        self.timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        self.run_dir = Path(self.config.get("paths", {}).get("runs_dir", "tests_agent/runs")) / self.timestamp
        self.run_dir.mkdir(parents=True, exist_ok=True)

    def load_issues(self) -> List[Dict[str, str]]:
        if not self.issues_csv_path.is_file():
            return []
        with open(self.issues_csv_path, "r", encoding="utf-8") as f:
            return list(csv.DictReader(f))

    def save_issues(self, issues: List[Dict[str, str]]):
        if not issues:
            return
        fieldnames = list(issues[0].keys())
        with open(self.issues_csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(issues)

    def run(self):
        issues = self.load_issues()
        open_issues = [i for i in issues if i.get("status") == "open"]

        if not open_issues:
            print("No open issues in issues/issues.csv.")
            return

        print(f"🔧 Starting Fix Agent Loop for {len(open_issues)} open issues...\n")

        all_cases = [p.name for p in self.runner.cases_dir.iterdir() if p.is_dir()]
        tune_cases = [c for c in all_cases if self.runner.load_case_meta(c).get("split", "tune") != "heldout"]
        heldout_cases = [c for c in all_cases if self.runner.load_case_meta(c).get("split") == "heldout"]

        for issue in open_issues:
            issue_id = issue.get("id")
            stage = issue.get("stage")
            test_case = issue.get("test_case")
            issue_desc = issue.get("issue")
            reason = issue.get("reason")

            print(f"============================================================")
            print(f"Processing Issue #{issue_id} [{stage}] - Case: {test_case}")
            print(f"============================================================")

            # a. REPRODUCE
            try:
                out, assertions = self.runner.run_stage_test(test_case, stage)
                failing_assertions = [a for a in assertions if not a.get("passed")]
            except Exception as e:
                failing_assertions = [{"name": "Crash", "passed": False, "reason": str(e), "evidence": str(e)}]
                out = {}

            if not failing_assertions:
                print(f"  -> Issue could NOT be reproduced on current code. Marking 'cannot_reproduce'.")
                issue["status"] = "cannot_reproduce"
                issue["resolution_note"] = "All assertions passed during reproduction step."
                self.save_issues(issues)
                continue

            print(f"  -> Reproduced! {len(failing_assertions)} failing assertions.")
            issue["status"] = "reproduced"
            self.save_issues(issues)

            # Get stage prompt file
            stage_info = get_stage_info(stage)
            prompt_rel_path = stage_info.get("prompt_file")
            if not prompt_rel_path:
                print(f"  -> Stage {stage} has no prompt file (pure code stage). Marking 'needs_layer'.")
                issue["status"] = "needs_layer"
                issue["resolution_note"] = f"Stage {stage} is a deterministic code stage. Fix requires deterministic code modifications."
                self.save_issues(issues)
                continue

            prompt_path = Path(prompt_rel_path)
            if not prompt_path.is_file():
                prompt_path = Path(__file__).resolve().parent.parent / prompt_rel_path

            original_prompt = prompt_path.read_text(encoding="utf-8")
            backup_path = self.run_dir / f"backup_{prompt_path.name}"
            shutil.copy(prompt_path, backup_path)

            attempts = 0
            resolved = False
            resolution_note = ""

            # Passing examples from other tune cases
            passing_examples = []
            for tc in tune_cases:
                if tc != test_case:
                    try:
                        p_out, p_ass = self.runner.run_stage_test(tc, stage)
                        if all(a.get("passed") for a in p_ass):
                            passing_examples.append({"case_id": tc, "output": p_out.model_dump() if hasattr(p_out, "model_dump") else p_out})
                        if len(passing_examples) >= 3:
                            break
                    except Exception:
                        pass

            while attempts < self.max_attempts and not resolved:
                attempts += 1
                print(f"\n[Attempt {attempts}/{self.max_attempts}] Generating generic prompt patch...")

                try:
                    new_prompt, note = call_groq_propose_fix(
                        prompt_file_path=str(prompt_path),
                        current_prompt=prompt_path.read_text(encoding="utf-8"),
                        failing_input=self.runner.load_case_input(test_case),
                        failing_output=out.model_dump() if hasattr(out, "model_dump") else out,
                        issue_desc=issue_desc,
                        reason=reason,
                        failing_assertions=failing_assertions,
                        passing_examples=passing_examples,
                        iteration=attempts
                    )
                except Exception as e:
                    print(f"  -> Fix proposal generation failed: {e}")
                    break

                # Apply fix
                apply_prompt_patch(prompt_path, new_prompt, backup_path)

                # d. VERIFY
                # Step 1: Issue case
                print("  Verifying issue case...")
                try:
                    v_out, v_ass = self.runner.run_stage_test(test_case, stage)
                    issue_failures = [a for a in v_ass if not a.get("passed")]
                except Exception as e:
                    issue_failures = [{"name": "Crash", "passed": False, "reason": str(e)}]

                if issue_failures:
                    print(f"  -> Failed to fix issue case ({len(issue_failures)} failures).")
                    failing_assertions = issue_failures
                    continue

                # Step 2: All tune cases for this stage (no regressions allowed)
                print("  Verifying all tune cases for stage (regression check)...")
                tune_regressions = []
                for tc in tune_cases:
                    try:
                        _, tc_ass = self.runner.run_stage_test(tc, stage)
                        regs = self.runner.detect_regressions(tc, stage, tc_ass)
                        tune_regressions.extend(regs)
                    except Exception as e:
                        tune_regressions.append({"name": "Crash", "passed": False, "reason": str(e)})

                if tune_regressions:
                    print(f"  -> Regressions detected in tune cases ({len(tune_regressions)}). Retrying...")
                    failing_assertions = tune_regressions
                    continue

                # Step 3: Propagation tests for all downstream stages
                print("  Verifying downstream propagation...")
                downstream_stages = downstream_of(stage)
                downstream_regressions = []
                for tc in tune_cases:
                    try:
                        prop_map = self.runner.run_propagation_test(tc, stage)
                        for d_stg, (_, d_ass) in prop_map.items():
                            regs = self.runner.detect_regressions(tc, d_stg, d_ass)
                            downstream_regressions.extend(regs)
                    except Exception as e:
                        downstream_regressions.append({"name": "PropCrash", "passed": False, "reason": str(e)})

                if downstream_regressions:
                    print(f"  -> Downstream propagation failure ({len(downstream_regressions)}). Retrying...")
                    failing_assertions = downstream_regressions
                    continue

                # Step 4: Held-out cases (check for overfitting)
                print("  Verifying heldout cases (overfitting check)...")
                heldout_failures = []
                for hc in heldout_cases:
                    try:
                        _, hc_ass = self.runner.run_stage_test(hc, stage)
                        f_list = [a for a in hc_ass if not a.get("passed")]
                        heldout_failures.extend(f_list)
                    except Exception as e:
                        heldout_failures.append({"name": "HeldoutCrash", "passed": False, "reason": str(e)})

                if heldout_failures:
                    print(f"  -> Overfitting detected on heldout cases ({len(heldout_failures)}). Retrying...")
                    failing_assertions = heldout_failures
                    continue

                # All green!
                resolved = True
                resolution_note = f"Prompt successfully updated on attempt {attempts}. Passed issue, tune, downstream propagation, and heldout verification."
                print(f"  🎉 SUCCESS! {resolution_note}")

            # DECIDE & ESCALATE
            issue["attempts"] = str(attempts)
            if resolved:
                issue["status"] = "fixed"
                issue["resolution_note"] = resolution_note
                # Save audit log
                audit_file = self.run_dir / f"issue_{issue_id}_audit.md"
                audit_file.write_text(f"# Issue #{issue_id} Fixed\n\n{resolution_note}\n\nDiff:\n{prompt_path.name} updated.", encoding="utf-8")
            else:
                # Revert to backup
                prompt_path.write_text(original_prompt, encoding="utf-8")
                # Classify escalation
                if "unreadable" in reason.lower() or "missing rule" in reason.lower() or "scan" in reason.lower():
                    issue["status"] = "needs_layer"
                    issue["resolution_note"] = f"Fix cap exceeded. Structural limitation detected in {stage}. Suggest adding deterministic extraction/rule layer."
                else:
                    issue["status"] = "needs_human"
                    issue["resolution_note"] = f"Fix cap ({self.max_attempts} attempts) reached without green verification across all cases. Requires human prompt engineer review."

            self.save_issues(issues)

        print("\nFix Agent execution completed. Updated issues.csv.")

    def solve_single_issue(
        self,
        issue_id: str,
        user_guidance: Optional[str] = None,
        max_attempts: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Solves a single issue selected by the user.
        Incorporates user-provided solution guidance and returns a detailed before/after comparison.
        """
        issues = self.load_issues()
        target_issue = None
        for i in issues:
            if str(i.get("id")) == str(issue_id):
                target_issue = i
                break

        if not target_issue:
            raise ValueError(f"Issue #{issue_id} not found in issues sheet.")

        stage = target_issue.get("stage")
        test_case = target_issue.get("test_case")
        issue_desc = target_issue.get("issue")
        reason = target_issue.get("reason")
        attempts_limit = max_attempts or self.max_attempts

        # Initial baseline evaluation (BEFORE)
        try:
            out_before, assertions_before = self.runner.run_stage_test(test_case, stage)
            failing_assertions = [a for a in assertions_before if not a.get("passed")]
        except Exception as e:
            failing_assertions = [{"name": "Crash", "passed": False, "reason": str(e), "evidence": str(e)}]
            out_before = {}
            assertions_before = failing_assertions

        stage_info = get_stage_info(stage)
        prompt_rel_path = stage_info.get("prompt_file")
        if not prompt_rel_path:
            return {
                "success": False,
                "status": "needs_layer",
                "message": f"Stage '{stage}' is deterministic code without a prompt template. Requires code layer changes.",
                "before_assertions": assertions_before,
                "after_assertions": assertions_before
            }

        prompt_path = Path(prompt_rel_path)
        if not prompt_path.is_file():
            prompt_path = Path(__file__).resolve().parent.parent / prompt_rel_path

        original_prompt = prompt_path.read_text(encoding="utf-8")
        backup_path = self.run_dir / f"backup_{prompt_path.name}"
        shutil.copy(prompt_path, backup_path)

        all_cases = [p.name for p in self.runner.cases_dir.iterdir() if p.is_dir()]
        tune_cases = [c for c in all_cases if self.runner.load_case_meta(c).get("split", "tune") != "heldout"]
        heldout_cases = [c for c in all_cases if self.runner.load_case_meta(c).get("split") == "heldout"]

        # Passing examples
        passing_examples = []
        for tc in tune_cases:
            if tc != test_case:
                try:
                    p_out, p_ass = self.runner.run_stage_test(tc, stage)
                    if all(a.get("passed") for a in p_ass):
                        passing_examples.append({"case_id": tc, "output": p_out.model_dump() if hasattr(p_out, "model_dump") else p_out})
                    if len(passing_examples) >= 3:
                        break
                except Exception:
                    pass

        attempts = 0
        resolved = False
        resolution_note = ""
        final_prompt = original_prompt
        assertions_after = assertions_before

        while attempts < attempts_limit and not resolved:
            attempts += 1
            try:
                new_prompt, _ = call_groq_propose_fix(
                    prompt_file_path=str(prompt_path),
                    current_prompt=prompt_path.read_text(encoding="utf-8"),
                    failing_input=self.runner.load_case_input(test_case),
                    failing_output=out_before.model_dump() if hasattr(out_before, "model_dump") else out_before,
                    issue_desc=issue_desc,
                    reason=reason,
                    failing_assertions=failing_assertions,
                    passing_examples=passing_examples,
                    iteration=attempts,
                    user_guidance=user_guidance
                )
            except Exception as e:
                resolution_note = f"Proposal error: {e}"
                break

            apply_prompt_patch(prompt_path, new_prompt, backup_path)
            final_prompt = new_prompt

            # Verify on issue case
            try:
                _, v_ass = self.runner.run_stage_test(test_case, stage)
                assertions_after = v_ass
                issue_failures = [a for a in v_ass if not a.get("passed")]
            except Exception as e:
                issue_failures = [{"name": "Crash", "passed": False, "reason": str(e)}]

            if issue_failures:
                failing_assertions = issue_failures
                continue

            # Verify on tune cases
            tune_regs = []
            for tc in tune_cases:
                try:
                    _, tc_ass = self.runner.run_stage_test(tc, stage)
                    regs = self.runner.detect_regressions(tc, stage, tc_ass)
                    tune_regs.extend(regs)
                except Exception as e:
                    tune_regs.append({"name": "Crash", "passed": False, "reason": str(e)})

            if tune_regs:
                failing_assertions = tune_regs
                continue

            # Downstream propagation
            prop_regs = []
            for tc in tune_cases:
                try:
                    prop_map = self.runner.run_propagation_test(tc, stage)
                    for d_stg, (_, d_ass) in prop_map.items():
                        regs = self.runner.detect_regressions(tc, d_stg, d_ass)
                        prop_regs.extend(regs)
                except Exception as e:
                    prop_regs.append({"name": "PropCrash", "passed": False, "reason": str(e)})

            if prop_regs:
                failing_assertions = prop_regs
                continue

            # Held-out cases
            heldout_failures = []
            for hc in heldout_cases:
                try:
                    _, hc_ass = self.runner.run_stage_test(hc, stage)
                    f_list = [a for a in hc_ass if not a.get("passed")]
                    heldout_failures.extend(f_list)
                except Exception as e:
                    heldout_failures.append({"name": "HeldoutCrash", "passed": False, "reason": str(e)})

            if heldout_failures:
                failing_assertions = heldout_failures
                continue

            # Success!
            resolved = True
            resolution_note = f"Prompt successfully fixed on attempt {attempts}. Passed issue verification, tune cases, downstream propagation, and held-out checks."

        target_issue["attempts"] = str(attempts)
        if resolved:
            target_issue["status"] = "fixed"
            target_issue["resolution_note"] = resolution_note
        else:
            prompt_path.write_text(original_prompt, encoding="utf-8")
            final_prompt = original_prompt
            target_issue["status"] = "needs_human" if attempts >= attempts_limit else "fixing"
            target_issue["resolution_note"] = resolution_note or "Could not resolve within attempt limit without regressions."

        self.save_issues(issues)
        # Also sync to issues.xlsx
        try:
            pd.DataFrame(issues).to_excel("issues/issues.xlsx", index=False)
        except Exception:
            pass

        diff_text = generate_unified_diff(original_prompt, final_prompt, prompt_path.name)

        return {
            "success": resolved,
            "issue_id": issue_id,
            "stage": stage,
            "test_case": test_case,
            "prompt_file": str(prompt_path.name),
            "before_prompt": original_prompt,
            "after_prompt": final_prompt,
            "diff": diff_text,
            "before_assertions": assertions_before,
            "after_assertions": assertions_after,
            "attempts": attempts,
            "status": target_issue["status"],
            "resolution_note": target_issue["resolution_note"]
        }


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Fix Agent Loop for resolving logged issues")
    parser.add_argument("--max-attempts", type=int, default=4, help="Max fix attempts per issue")
    args = parser.parse_args()

    agent = FixAgentLoop()
    agent.max_attempts = args.max_attempts
    agent.run()


if __name__ == "__main__":
    main()
