import sys
from pathlib import Path
from typing import List, Dict, Any, Set

# Ensure repository root is on sys.path when executed directly
_REPO_ROOT = str(Path(__file__).resolve().parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from pipeline.stages import STAGE_REGISTRY


# Direct explicit graph mapping based on pipeline contracts:
# extract_text -> extract_fields, check_documents
# extract_fields -> validate_ticket
# check_documents -> validate_ticket
# validate_ticket -> generate_report
EXPLICIT_EDGES = {
    "extract_text": ["extract_fields", "check_documents"],
    "extract_fields": ["validate_ticket"],
    "check_documents": ["validate_ticket"],
    "validate_ticket": ["generate_report"],
    "generate_report": []
}


def get_stages_in_order() -> List[str]:
    """Returns all stage names registered in order."""
    return [s["name"] for s in STAGE_REGISTRY]


def get_stage_info(stage_name: str) -> Dict[str, Any]:
    """Returns registration metadata for a specific stage."""
    for s in STAGE_REGISTRY:
        if s["name"] == stage_name:
            return s
    raise ValueError(f"Unknown stage: {stage_name}")


def downstream_of(stage_name: str) -> List[str]:
    """
    Returns all stages downstream of the given stage in topological pipeline order.
    A change in `stage_name` may leak or propagate to any of these stages.
    """
    order = get_stages_in_order()
    if stage_name not in order:
        raise ValueError(f"Unknown stage: {stage_name}")

    visited: Set[str] = set()
    queue = [stage_name]

    while queue:
        curr = queue.pop(0)
        direct_downstreams = EXPLICIT_EDGES.get(curr, [])
        for d in direct_downstreams:
            if d not in visited:
                visited.add(d)
                queue.append(d)

    # Return in pipeline canonical order
    return [s for s in order if s in visited]


def upstream_of(stage_name: str) -> List[str]:
    """Returns all stages upstream that feed into `stage_name`."""
    order = get_stages_in_order()
    upstreams = []
    for s in order:
        if stage_name in downstream_of(s):
            upstreams.append(s)
    return upstreams
