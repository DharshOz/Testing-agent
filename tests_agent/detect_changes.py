import subprocess
from pathlib import Path
from typing import Set, List, Tuple, Dict
from tests_agent.graph import downstream_of, get_stages_in_order

# Exact file-to-stage mapping
FILE_TO_STAGE_MAP: Dict[str, List[str]] = {
    "prompts/extract_fields.md": ["extract_fields"],
    "prompts/classify_doc.md": ["check_documents"],
    "prompts/validate_ticket.md": ["validate_ticket"],
    "prompts/generate_report.md": ["generate_report"],
    "config/required_docs.yaml": ["check_documents"],
    "pipeline/stages.py": ["extract_text", "extract_fields", "check_documents", "validate_ticket", "generate_report"],
    "pipeline/contracts.py": ["extract_text", "extract_fields", "check_documents", "validate_ticket", "generate_report"],
    "pipeline/llm_client.py": ["extract_fields", "check_documents", "validate_ticket", "generate_report"]
}


def get_git_changed_files(base_ref: str = "HEAD") -> List[str]:
    """
    Retrieves modified, staged, and untracked files via git diff.
    Falls back gracefully if git is not initialized or error occurs.
    """
    changed: Set[str] = set()
    commands = [
        ["git", "diff", "--name-only", base_ref],
        ["git", "diff", "--name-only", "--cached"],
        ["git", "diff", "--name-only", "origin/main...HEAD"],
        ["git", "status", "--porcelain"]
    ]

    for cmd in commands:
        try:
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if res.returncode == 0:
                for line in res.stdout.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    # Handle porcelain status "M file.txt" or "?? file.txt"
                    if line.startswith(("M ", "A ", "D ", "R ", "?? ")):
                        parts = line.split(maxsplit=1)
                        if len(parts) == 2:
                            changed.add(parts[1].replace("\\", "/"))
                    else:
                        changed.add(line.replace("\\", "/"))
        except Exception:
            pass

    return list(changed)


def map_files_to_stages(file_paths: List[str]) -> Set[str]:
    """Maps a list of changed file paths to the set of affected stages."""
    affected: Set[str] = set()

    for path in file_paths:
        path_norm = path.replace("\\", "/").strip()
        # Direct match
        if path_norm in FILE_TO_STAGE_MAP:
            affected.update(FILE_TO_STAGE_MAP[path_norm])
            continue

        # Prefix/pattern matches
        if path_norm.startswith("prompts/"):
            fname = Path(path_norm).name
            if "extract_fields" in fname:
                affected.add("extract_fields")
            elif "classify_doc" in fname:
                affected.add("check_documents")
            elif "validate_ticket" in fname:
                affected.add("validate_ticket")
            elif "generate_report" in fname:
                affected.add("generate_report")

        elif path_norm.startswith("config/"):
            affected.add("check_documents")

        elif path_norm.startswith("pipeline/"):
            # Any core pipeline logic change triggers all stages
            affected.update(get_stages_in_order())

    return affected


def get_changed_stages(base_ref: str = "HEAD") -> Set[str]:
    """Detects changed stages using git diff."""
    files = get_git_changed_files(base_ref)
    return map_files_to_stages(files)


def get_test_plan(changed_stages: Set[str]) -> Tuple[List[str], List[str]]:
    """
    Computes dependency-aware test plan:
    - Stage tests: for the changed stages themselves (tested in isolation on frozen inputs).
    - Propagation tests: for all downstream stages (to verify changes didn't leak downstream).
    - Upstream stages are skipped entirely.
    """
    all_order = get_stages_in_order()
    stage_tests = [s for s in all_order if s in changed_stages]

    propagation_stages: Set[str] = set()
    for s in changed_stages:
        downstreams = downstream_of(s)
        propagation_stages.update(downstreams)

    # Order propagation stages topologically
    propagation_tests = [s for s in all_order if s in propagation_stages]

    return stage_tests, propagation_tests
