from typing import Dict, Any, List
from pydantic import ValidationError
from tests_agent.graph import get_stage_info


def assert_schema(stage_name: str, output_data: Any) -> List[Dict[str, Any]]:
    """
    Validates stage output against its registered Pydantic contract model.
    Cheapest check, executed first.
    Returns list of assertion result dicts: {name, passed, reason, evidence}.
    """
    stage_info = get_stage_info(stage_name)
    output_model = stage_info.get("output_model")

    if not output_model:
        return [{
            "name": f"Schema Check [{stage_name}]",
            "passed": True,
            "reason": "No Pydantic contract defined for stage",
            "evidence": "N/A"
        }]

    try:
        if isinstance(output_data, dict):
            output_model.model_validate(output_data)
        elif hasattr(output_data, "model_dump"):
            output_model.model_validate(output_data.model_dump())
        else:
            return [{
                "name": f"Schema Check [{stage_name}]",
                "passed": False,
                "reason": f"Expected dict or Pydantic instance, got {type(output_data)}",
                "evidence": str(output_data)[:200]
            }]

        return [{
            "name": f"Schema Check [{stage_name}]",
            "passed": True,
            "reason": f"Output strictly adheres to {output_model.__name__} contract",
            "evidence": f"Fields validated against {output_model.__name__}"
        }]

    except ValidationError as ve:
        return [{
            "name": f"Schema Check [{stage_name}]",
            "passed": False,
            "reason": f"Schema validation error in {output_model.__name__}",
            "evidence": str(ve.errors())[:400]
        }]
    except Exception as e:
        return [{
            "name": f"Schema Check [{stage_name}]",
            "passed": False,
            "reason": f"Unexpected schema verification error: {e}",
            "evidence": str(e)
        }]
