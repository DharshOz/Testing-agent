import hashlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional, Type, TypeVar
import jinja2
from pydantic import BaseModel
from groq import Groq

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("llm_client")

def reload_env():
    """Reloads .env variables into os.environ."""
    env_path = Path(".env")
    if not env_path.is_file():
        env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.is_file():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip().strip('"').strip("'")
                os.environ[k] = v

reload_env()

# Context mode for separating Ticket Agent calls vs Testing Agent calls
_TESTING_MODE = False

def set_testing_mode(enabled: bool = True):
    """Sets whether subsequent LLM pipeline calls run in Testing Agent mode."""
    global _TESTING_MODE
    _TESTING_MODE = enabled

def is_testing_mode() -> bool:
    return _TESTING_MODE

def get_groq_api_key(for_testing: Optional[bool] = None) -> Optional[str]:
    """
    Returns the appropriate Groq API key:
    - If for_testing=True (or testing mode is active):
      returns TESTING_AGENT_GROQ_KEY, falling back to GROQ_API_KEY.
    - Otherwise (Ticket agent mode):
      returns TICKET_AGENT_GROQ_KEY or GROQ_API_KEY.
    """
    reload_env()
    is_test = _TESTING_MODE if for_testing is None else for_testing
    if is_test:
        test_key = (
            os.environ.get("TESTING_AGENT_GROQ_KEY", "").strip()
            or os.environ.get("TEST_AGENT_GROQ_KEY", "").strip()
        )
        if test_key:
            return test_key
        return os.environ.get("GROQ_API_KEY", "").strip() or None

    ticket_key = os.environ.get("TICKET_AGENT_GROQ_KEY", "").strip()
    if ticket_key:
        return ticket_key
    return os.environ.get("GROQ_API_KEY", "").strip() or None

def get_keys_status() -> Dict[str, Any]:
    """Returns current status of Ticket Agent and Testing Agent Groq keys."""
    reload_env()
    t_key = os.environ.get("TICKET_AGENT_GROQ_KEY", "").strip() or os.environ.get("GROQ_API_KEY", "").strip()
    test_key = os.environ.get("TESTING_AGENT_GROQ_KEY", "").strip() or os.environ.get("TEST_AGENT_GROQ_KEY", "").strip()
    
    return {
        "ticket_key_configured": bool(t_key),
        "ticket_key_masked": f"{t_key[:6]}...{t_key[-4:]}" if len(t_key) >= 12 else ("Set" if t_key else "Unset"),
        "testing_key_configured": bool(test_key),
        "testing_key_masked": f"{test_key[:6]}...{test_key[-4:]}" if len(test_key) >= 12 else ("Set" if test_key else "Unset"),
        "is_dedicated_test_key": bool(test_key and test_key != t_key),
    }

T = TypeVar("T", bound=BaseModel)

# Default to gpt-oss-120b as specified, with GROQ_MODEL env var override
DEFAULT_MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")


def get_prompt_hash(prompt_text: str) -> str:
    """Computes SHA256 hash of prompt text for version tracking."""
    return hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()[:16]


def load_and_render_prompt(prompt_file_path: str, variables: Dict[str, Any]) -> tuple[str, str]:
    """
    Loads prompt markdown file and renders variables using Jinja2.
    Returns (rendered_prompt, prompt_hash).
    """
    p = Path(prompt_file_path)
    if not p.is_file():
        # Try resolving relative to project root
        project_root = Path(__file__).resolve().parent.parent
        p = project_root / prompt_file_path
        if not p.is_file():
            raise FileNotFoundError(f"Prompt file not found at: {prompt_file_path}")

    raw_template = p.read_text(encoding="utf-8")
    prompt_hash = get_prompt_hash(raw_template)

    env = jinja2.Environment(undefined=jinja2.Undefined)
    template = env.from_string(raw_template)
    rendered_prompt = template.render(**variables)

    return rendered_prompt, prompt_hash


def _extract_json_substring(text: str) -> str:
    """Extracts JSON substring if wrapped in markdown code fence."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    
    # Try finding outer braces if any extra text exists
    start_brace = text.find("{")
    end_brace = text.rfind("}")
    if start_brace != -1 and end_brace != -1 and end_brace > start_brace:
        text = text[start_brace:end_brace + 1]
    return text


def llm_call(
    prompt_file: str,
    variables: Dict[str, Any],
    schema: Optional[Type[T]] = None,
    model: Optional[str] = None,
    for_testing: Optional[bool] = None,
    api_key_override: Optional[str] = None,
) -> tuple[Any, str]:
    """
    Unified LLM call function:
    - Loads prompt file from disk (never inline in code)
    - Records prompt version hash
    - Selects dedicated key for Ticket Agent vs Testing Agent
    - Calls Groq with temperature=0
    - Retries once on invalid JSON / schema parse failure
    - Logs input/output/latency/prompt hash
    
    Returns (parsed_result, prompt_hash).
    """
    rendered_prompt, prompt_hash = load_and_render_prompt(prompt_file, variables)
    api_key = api_key_override or get_groq_api_key(for_testing=for_testing)
    groq_model = model or os.environ.get("GROQ_MODEL", DEFAULT_MODEL)

    active_mode = "TestingAgent" if (_TESTING_MODE if for_testing is None else for_testing) else "TicketAgent"

    if not api_key:
        logger.warning(f"Groq API key for {active_mode} is not set. Simulating/mocking LLM response.")
        # If no API key, return a mock response conformant to schema if provided
        return _mock_llm_fallback(prompt_file, variables, schema), prompt_hash

    client = Groq(api_key=api_key)

    messages = [
        {"role": "system", "content": "You are a precise, structured AI system. You MUST output strictly valid JSON with no conversational text."},
        {"role": "user", "content": rendered_prompt}
    ]

    max_attempts = 2
    last_error = None

    for attempt in range(1, max_attempts + 1):
        start_time = time.time()
        logger.info(f"LLM Call attempt {attempt}/{max_attempts} | mode={active_mode} | prompt={prompt_file} | hash={prompt_hash} | model={groq_model}")

        try:
            # We attempt json_object response format
            response = client.chat.completions.create(
                model=groq_model,
                messages=messages,
                temperature=0.0,
                response_format={"type": "json_object"},
            )
            raw_content = response.choices[0].message.content or "{}"
            latency = time.time() - start_time
            logger.info(f"LLM Response received in {latency:.2f}s | prompt={prompt_file}")

            cleaned_json = _extract_json_substring(raw_content)
            parsed_dict = json.loads(cleaned_json)

            if schema is not None:
                validated = schema.model_validate(parsed_dict)
                return validated, prompt_hash
            return parsed_dict, prompt_hash

        except Exception as e:
            last_error = e
            latency = time.time() - start_time
            logger.warning(f"LLM Call attempt {attempt} failed ({e}) in {latency:.2f}s. Retrying...")
            if attempt < max_attempts:
                # Add retry feedback to messages
                messages.append({"role": "assistant", "content": str(raw_content if 'raw_content' in locals() else "")})
                messages.append({
                    "role": "user",
                    "content": f"Your previous response caused error: {e}. Output ONLY valid JSON matching the requested schema exactly."
                })

    logger.error(f"LLM Call failed after {max_attempts} attempts: {last_error}")
    raise RuntimeError(f"LLM call failed for prompt {prompt_file}: {last_error}")


def _mock_llm_fallback(prompt_file: str, variables: Dict[str, Any], schema: Optional[Type[T]]) -> Any:
    """Fallback generator for local offline environments or when GROQ_API_KEY is unset."""
    p_name = Path(prompt_file).name
    data = {}
    if "extract_fields" in p_name:
        field_name = variables.get("field_name", "")
        data = {
            "value": f"Sample {field_name.replace('_', ' ').title()}",
            "confidence": 0.95,
            "source_filename": "sample_doc.txt",
            "source_chunk_text": "Sample text verifying value."
        }
    elif "classify_doc" in p_name:
        data = {
            "doc_type": "application_form",
            "confidence": 0.85,
            "reasoning": "Keyword match fallback"
        }
    elif "validate_ticket" in p_name:
        data = {
            "verdict": "valid",
            "summary": "All required documents and field data appear consistent and valid.",
            "checks": [
                {"name": "Required Fields Completeness", "passed": True, "evidence": "All fields present", "reason": "OK"},
                {"name": "Applicant Name Consistency Across Documents", "passed": True, "evidence": "Applicant name consistent", "reason": "OK"},
                {"name": "Community Consent Document Formality", "passed": True, "evidence": "Consent documented", "reason": "OK"}
            ]
        }
    elif "generate_report" in p_name:
        data = {
            "title": "OFFICIAL VERIFICATION REPORT",
            "executive_summary": "Automated verification completed successfully.",
            "applicant_details": {
                "applicant_name": "Applicant",
                "applicant_id": "ID-12345",
                "contact": "555-0199",
                "department": "Public Works",
                "date": "2026-10-14"
            },
            "issue_details": {
                "title": "Community Project Request",
                "description": "Project implementation request.",
                "amount_requested": "50000"
            },
            "supporting_documents_table": [
                {"doc_type": "application_form", "filename": "app.txt", "status": "Verified", "classification_method": "keyword"}
            ],
            "validation_findings": [
                {"audit": "Consistency Check", "passed": True, "evidence": "Names match across docs"}
            ],
            "recommendation": "APPROVED FOR PROCESSING"
        }

    if schema is not None:
        return schema.model_validate(data)
    return data
