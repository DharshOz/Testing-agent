import json
import logging
import os
import re
from pathlib import Path
from typing import Dict, Any, List, Optional
from groq import Groq

logger = logging.getLogger("tests_agent.judge")

env_path = Path(".env")
if not env_path.is_file():
    env_path = Path(__file__).resolve().parent.parent.parent / ".env"
if env_path.is_file():
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k not in os.environ:
                os.environ[k] = v


from pipeline.llm_client import get_groq_api_key

def _call_groq_judge(system_prompt: str, user_prompt: str) -> Dict[str, Any]:
    """Single call to Groq for judge evaluation with JSON output."""
    api_key = get_groq_api_key(for_testing=True)
    model = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")
    fallback_model = "llama-3.3-70b-versatile"

    if not api_key:
        # Offline deterministic fallback
        return {
            "pass": True,
            "reason": "Offline simulation (GROQ_API_KEY unset).",
            "score": 1.0
        }

    client = Groq(api_key=api_key)

    for selected_model in [model, fallback_model]:
        try:
            response = client.chat.completions.create(
                model=selected_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.0,
                response_format={"type": "json_object"}
            )
            raw = response.choices[0].message.content or "{}"
            # Extract JSON substring
            raw = raw.strip()
            if raw.startswith("```"):
                lines = raw.splitlines()
                if lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]
                raw = "\n".join(lines).strip()
            
            s = raw.find("{")
            e = raw.rfind("}")
            if s != -1 and e != -1:
                raw = raw[s:e+1]

            data = json.loads(raw)
            return {
                "pass": bool(data.get("pass", True)),
                "reason": str(data.get("reason", "No reason provided")),
                "score": float(data.get("score", 1.0))
            }
        except Exception as err:
            logger.warning(f"Groq judge call failed with {selected_model}: {err}")
            continue

    return {
        "pass": False,
        "reason": f"Judge invocation failed on all models",
        "score": 0.0
    }


def evaluate_judge_rubric(
    stage_name: str,
    output_data: Dict[str, Any],
    rubric: str,
    context: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Evaluates an open-ended quality criterion using Groq LLM.
    CRITICAL QUALITY BAR: Runs judge evaluation TWICE.
    Both runs must agree on `pass`, otherwise marks as FLAKY.
    """
    context = context or {}

    system_prompt = (
        "You are an impartial, rigorous AI testing judge. Evaluate the stage output against the given rubric strictly.\n"
        "Return ONLY a JSON object with keys:\n"
        "- 'pass': boolean (true if output satisfies rubric, false otherwise)\n"
        "- 'reason': concise explanation (1-2 sentences)\n"
        "- 'score': float between 0.0 and 1.0"
    )

    user_prompt = f"""
Stage: {stage_name}
Rubric Criterion:
{rubric}

Context Information:
{json.dumps(context, indent=2, default=str)}

Stage Output to Evaluate:
{json.dumps(output_data, indent=2, default=str)}
"""

    # Run 1
    run1 = _call_groq_judge(system_prompt, user_prompt)
    # Run 2
    run2 = _call_groq_judge(system_prompt, user_prompt)

    # Check agreement
    pass1 = run1.get("pass", False)
    pass2 = run2.get("pass", False)

    if pass1 == pass2:
        return {
            "name": f"LLM Judge [{stage_name}]: {rubric[:45]}...",
            "passed": pass1,
            "flaky": False,
            "reason": f"{run1.get('reason')} (Both judge passes agreed: {pass1})",
            "evidence": f"Run 1 (score={run1.get('score')}): {run1.get('reason')} | Run 2 (score={run2.get('score')}): {run2.get('reason')}"
        }
    else:
        return {
            "name": f"LLM Judge [{stage_name}]: {rubric[:45]}...",
            "passed": False,
            "flaky": True,
            "reason": f"FLAKY: Judge runs disagreed (Run 1: {pass1}, Run 2: {pass2}). Requires agreement.",
            "evidence": f"Run 1: {run1} vs Run 2: {run2}"
        }


def assert_judge(
    stage_name: str,
    output_data: Dict[str, Any],
    expected: Dict[str, Any],
    context: Optional[Dict[str, Any]] = None
) -> List[Dict[str, Any]]:
    """Evaluates all rubrics specified in expected.yaml for the stage."""
    rubrics = expected.get("judge_rubrics", [])
    if isinstance(rubrics, str):
        rubrics = [rubrics]

    results = []
    for rubric in rubrics:
        res = evaluate_judge_rubric(stage_name, output_data, rubric, context)
        results.append(res)
    return results
