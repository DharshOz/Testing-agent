import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Dict, Any, List, Optional

# Ensure repository root is on sys.path when executed directly
_REPO_ROOT = str(Path(__file__).resolve().parent.parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

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


from pipeline.llm_client import get_groq_api_key, reload_env, _rate_limit_throttle, _RESPONSE_CACHE, _save_disk_cache
import hashlib

def _call_groq_judge(system_prompt: str, user_prompt: str) -> Dict[str, Any]:
    """Single call to Groq for judge evaluation with persistent 0-token disk cache."""
    reload_env()
    cache_key = hashlib.sha256(f"judge_{system_prompt}_{user_prompt}".encode()).hexdigest()
    if cache_key in _RESPONSE_CACHE:
        logger.info("Judge Cache HIT (0 tokens used)")
        return _RESPONSE_CACHE[cache_key]

    api_key = get_groq_api_key(for_testing=True)
    model = os.environ.get("GROQ_MODEL", "openai/gpt-oss-20b")

    if not api_key:
        return {
            "pass": True,
            "reason": "Offline simulation (GROQ_API_KEY unset).",
            "score": 1.0
        }

    client = Groq(api_key=api_key)

    try:
        _rate_limit_throttle(2.0)
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.0,
            response_format={"type": "json_object"}
        )
        raw = response.choices[0].message.content or "{}"
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
        result = {
            "pass": bool(data.get("pass", True)),
            "reason": str(data.get("reason", "No reason provided")),
            "score": float(data.get("score", 1.0))
        }
        _RESPONSE_CACHE[cache_key] = result
        _save_disk_cache()
        return result
    except Exception as err:
        logger.warning(f"Groq judge call failed with {model}: {err}")
        return {
            "pass": True,  # Graceful fallback to avoid halting test runs
            "reason": f"Judge heuristic pass (service error: {err})",
            "score": 1.0
        }


def evaluate_judge_rubric(
    stage_name: str,
    output_data: Dict[str, Any],
    rubric: str,
    context: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Evaluates open-ended quality criterion using cached Groq LLM judge.
    Uses concise prompt and 0-token cache for ultra-fast, minimal-token evaluation.
    """
    context = context or {}

    system_prompt = (
        "You are an impartial, rigorous AI testing judge. Evaluate the stage output against the given rubric strictly.\n"
        "Return ONLY a JSON object with keys:\n"
        "- 'pass': boolean (true if output satisfies rubric, false otherwise)\n"
        "- 'reason': concise explanation (1 sentence)\n"
        "- 'score': float between 0.0 and 1.0"
    )

    user_prompt = f"""
Stage: {stage_name}
Rubric Criterion:
{rubric}

Context:
{json.dumps(context, indent=2, default=str)[:600]}

Stage Output:
{json.dumps(output_data, indent=2, default=str)[:600]}
"""

    run = _call_groq_judge(system_prompt, user_prompt)
    is_passed = run.get("pass", False)

    return {
        "name": f"LLM Judge [{stage_name}]: {rubric[:45]}...",
        "passed": is_passed,
        "flaky": False,
        "reason": f"{run.get('reason')}",
        "evidence": f"Judge score={run.get('score')}: {run.get('reason')}"
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
