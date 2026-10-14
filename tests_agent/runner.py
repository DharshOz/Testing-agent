import hashlib
import json
import logging
import os
import shutil
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

import yaml
from pipeline.contracts import (
    FileInfo,
    ExtractTextInput, ExtractTextOutput,
    ExtractFieldsInput, ExtractFieldsOutput,
    CheckDocumentsInput, CheckDocumentsOutput,
    ValidateTicketInput, ValidateTicketOutput,
    GenerateReportInput, GenerateReportOutput
)
from pipeline.stages import (
    extract_text, extract_fields, check_documents, validate_ticket, generate_report,
    STAGE_REGISTRY
)
from pipeline.llm_client import load_and_render_prompt
from tests_agent.graph import get_stages_in_order, downstream_of
from tests_agent.assertions.schema import assert_schema
from tests_agent.assertions.rules import assert_rules
from tests_agent.assertions.judge import assert_judge

logger = logging.getLogger("tests_agent.runner")


class TestRunner:
    def __init__(self, config_path: str = "tests_agent/config.yaml"):
        self.config_path = Path(config_path)
        with open(self.config_path, "r", encoding="utf-8") as f:
            self.config = yaml.safe_load(f) or {}

        self.cases_dir = Path(self.config.get("paths", {}).get("cases_dir", "tests_agent/cases"))
        self.baseline_file = Path(self.config.get("paths", {}).get("baseline_file", "tests_agent/baseline.json"))
        self.cache_dir = Path(self.config.get("paths", {}).get("cache_dir", ".tests_agent_cache"))
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        self.baseline = self._load_baseline()

    def _load_baseline(self) -> Dict[str, bool]:
        if self.baseline_file.is_file():
            try:
                with open(self.baseline_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    def save_baseline(self, new_baseline: Dict[str, bool]):
        self.baseline_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.baseline_file, "w", encoding="utf-8") as f:
            json.dump(new_baseline, f, indent=2)

    def _get_cache_path(self, stage_name: str, prompt_file: Optional[str], input_json_str: str) -> Path:
        prompt_hash = "no_prompt"
        if prompt_file and os.path.exists(prompt_file):
            try:
                _, prompt_hash = load_and_render_prompt(prompt_file, {})
            except Exception:
                pass
        key = hashlib.sha256(f"{stage_name}_{prompt_hash}_{input_json_str}".encode()).hexdigest()
        return self.cache_dir / f"{stage_name}_{key}.json"

    def load_case_input(self, case_id: str) -> Dict[str, Any]:
        case_dir = self.cases_dir / case_id
        input_file = case_dir / "input.json"
        if not input_file.is_file():
            raise FileNotFoundError(f"Missing input.json for case {case_id}")
        with open(input_file, "r", encoding="utf-8") as f:
            return json.load(f)

    def load_case_expected(self, case_id: str) -> Dict[str, Any]:
        case_dir = self.cases_dir / case_id
        exp_file = case_dir / "expected.yaml"
        if not exp_file.is_file():
            return {}
        with open(exp_file, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def load_case_meta(self, case_id: str) -> Dict[str, Any]:
        case_dir = self.cases_dir / case_id
        meta_file = case_dir / "meta.yaml"
        if not meta_file.is_file():
            return {"split": "tune", "must_pass": True}
        with open(meta_file, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def load_golden_output(self, case_id: str, stage_name: str) -> Optional[Dict[str, Any]]:
        golden_path = self.cases_dir / case_id / "golden" / f"{stage_name}_output.json"
        if golden_path.is_file():
            with open(golden_path, "r", encoding="utf-8") as f:
                return json.load(f)
        return None

    def save_golden_output(self, case_id: str, stage_name: str, output_obj: Any):
        golden_dir = self.cases_dir / case_id / "golden"
        golden_dir.mkdir(parents=True, exist_ok=True)
        golden_path = golden_dir / f"{stage_name}_output.json"
        data = output_obj.model_dump() if hasattr(output_obj, "model_dump") else output_obj
        with open(golden_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    # =========================================================================
    # STAGE EXECUTION DISPATCHER
    # =========================================================================
    def execute_stage(
        self,
        case_id: str,
        stage_name: str,
        upstream_outputs: Dict[str, Any],
        use_cache: bool = True
    ) -> Any:
        """Executes a single stage using provided upstream outputs."""
        case_input = self.load_case_input(case_id)
        ticket_id = case_input.get("ticket_id", case_id)
        category = case_input.get("category", "grant_request")

        # Resolve files
        files_info = []
        for f in case_input.get("files", []):
            rel_path = f.get("path")
            abs_path = str(self.cases_dir / case_id / rel_path) if rel_path else ""
            files_info.append(FileInfo(
                filename=f.get("filename", Path(rel_path).name),
                path=abs_path,
                sha256=f.get("sha256", "")
            ))

        if stage_name == "extract_text":
            inp = ExtractTextInput(ticket_id=ticket_id, files=files_info)
            return extract_text(inp)

        elif stage_name == "extract_fields":
            ext_text_out = upstream_outputs.get("extract_text")
            if not ext_text_out:
                raise ValueError("extract_fields requires extract_text output")
            if isinstance(ext_text_out, dict):
                ext_text_out = ExtractTextOutput.model_validate(ext_text_out)
            inp = ExtractFieldsInput(ticket_id=ticket_id, extracted_files=ext_text_out.files)
            return extract_fields(inp)

        elif stage_name == "check_documents":
            ext_text_out = upstream_outputs.get("extract_text")
            if not ext_text_out:
                raise ValueError("check_documents requires extract_text output")
            if isinstance(ext_text_out, dict):
                ext_text_out = ExtractTextOutput.model_validate(ext_text_out)
            inp = CheckDocumentsInput(
                ticket_id=ticket_id,
                category=category,
                files=files_info,
                extracted_files=ext_text_out.files
            )
            return check_documents(inp)

        elif stage_name == "validate_ticket":
            ext_fields_out = upstream_outputs.get("extract_fields")
            check_docs_out = upstream_outputs.get("check_documents")
            ext_text_out = upstream_outputs.get("extract_text")

            if isinstance(ext_fields_out, dict):
                ext_fields_out = ExtractFieldsOutput.model_validate(ext_fields_out)
            if isinstance(check_docs_out, dict):
                check_docs_out = CheckDocumentsOutput.model_validate(check_docs_out)
            if isinstance(ext_text_out, dict):
                ext_text_out = ExtractTextOutput.model_validate(ext_text_out)

            inp = ValidateTicketInput(
                ticket_id=ticket_id,
                category=category,
                extracted_fields=ext_fields_out.fields,
                document_check=check_docs_out,
                extracted_files=ext_text_out.files
            )
            return validate_ticket(inp)

        elif stage_name == "generate_report":
            ext_fields_out = upstream_outputs.get("extract_fields")
            check_docs_out = upstream_outputs.get("check_documents")
            val_out = upstream_outputs.get("validate_ticket")

            if isinstance(ext_fields_out, dict):
                ext_fields_out = ExtractFieldsOutput.model_validate(ext_fields_out)
            if isinstance(check_docs_out, dict):
                check_docs_out = CheckDocumentsOutput.model_validate(check_docs_out)
            if isinstance(val_out, dict):
                val_out = ValidateTicketOutput.model_validate(val_out)

            out_docx = f"./reports/test_report_{case_id}_{stage_name}.docx"
            inp = GenerateReportInput(
                ticket_id=ticket_id,
                category=category,
                created_at="2026-10-14T00:00:00",
                extracted_fields=ext_fields_out.fields,
                document_check=check_docs_out,
                validation=val_out,
                output_docx_path=out_docx
            )
            return generate_report(inp)

        else:
            raise ValueError(f"Unknown stage {stage_name}")

    # =========================================================================
    # ASSERTION EVALUATOR (Layered: Schema -> Rules -> Judge)
    # =========================================================================
    def evaluate_stage_assertions(
        self,
        case_id: str,
        stage_name: str,
        output_obj: Any,
        upstream_outputs: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """
        Layered evaluation:
        1. Schema assertion (stops early on failure)
        2. Deterministic rule assertions
        3. LLM Judge rubrics
        """
        expected_all = self.load_case_expected(case_id)
        expected_stage = expected_all.get(stage_name, {})
        output_dict = output_obj.model_dump() if hasattr(output_obj, "model_dump") else output_obj

        # Context construction for rules/judge
        context: Dict[str, Any] = {}
        if "extract_text" in upstream_outputs:
            et = upstream_outputs["extract_text"]
            files = et.get("files", []) if isinstance(et, dict) else [f.model_dump() for f in et.files]
            context["raw_texts"] = [f.get("raw_text", "") for f in files]
        if "check_documents" in upstream_outputs:
            cd = upstream_outputs["check_documents"]
            context["prev_stage_doc_check"] = cd if isinstance(cd, dict) else cd.model_dump()
        if "validate_ticket" in upstream_outputs:
            vt = upstream_outputs["validate_ticket"]
            context["prev_verdict"] = vt.get("verdict") if isinstance(vt, dict) else vt.verdict

        all_assertions: List[Dict[str, Any]] = []

        # Layer 1: Schema Validation
        schema_results = assert_schema(stage_name, output_dict)
        all_assertions.extend(schema_results)
        if any(not r["passed"] for r in schema_results):
            # Hard stop early on schema error
            return all_assertions

        # Layer 2: Deterministic Rules
        rules_results = assert_rules(stage_name, output_dict, expected_stage, context)
        all_assertions.extend(rules_results)

        # Layer 3: LLM Judge Rubric (if specified in expected.yaml)
        if "judge_rubrics" in expected_stage:
            judge_results = assert_judge(stage_name, output_dict, expected_stage, context)
            all_assertions.extend(judge_results)

        return all_assertions

    # =========================================================================
    # TEST MODES: (a) STAGE TEST (Isolated)  (b) PROPAGATION TEST
    # =========================================================================
    def run_stage_test(self, case_id: str, stage_name: str) -> Tuple[Any, List[Dict[str, Any]]]:
        """
        STAGE TEST: Runs stage N on frozen golden outputs of stages < N.
        Guarantees isolated verification of stage N's prompt without upstream pollution.
        """
        all_stages = get_stages_in_order()
        idx = all_stages.index(stage_name)

        # Prepare frozen upstream outputs from golden
        upstream_outputs = {}
        for s in all_stages[:idx]:
            golden = self.load_golden_output(case_id, s)
            if golden is None and s != "extract_text":
                raise FileNotFoundError(f"Missing golden output for {case_id} at stage {s}. Run snapshot.py first.")
            upstream_outputs[s] = golden

        output = self.execute_stage(case_id, stage_name, upstream_outputs)
        assertions = self.evaluate_stage_assertions(case_id, stage_name, output, upstream_outputs)
        return output, assertions

    def run_propagation_test(
        self,
        case_id: str,
        start_stage: str
    ) -> Dict[str, Tuple[Any, List[Dict[str, Any]]]]:
        """
        PROPAGATION TEST: Runs start_stage live, feeds its new output to stage+1 live,
        then stage+2 live, checking assertions along the way.
        Detects if a change in start_stage caused downstream regressions.
        """
        all_stages = get_stages_in_order()
        idx = all_stages.index(start_stage)

        # Build live chain
        chain_outputs: Dict[str, Any] = {}
        for s in all_stages[:idx]:
            chain_outputs[s] = self.load_golden_output(case_id, s)

        results: Dict[str, Tuple[Any, List[Dict[str, Any]]]] = {}

        for stage in all_stages[idx:]:
            # Run live
            output = self.execute_stage(case_id, stage, chain_outputs)
            output_dict = output.model_dump() if hasattr(output, "model_dump") else output
            chain_outputs[stage] = output_dict

            assertions = self.evaluate_stage_assertions(case_id, stage, output, chain_outputs)
            results[stage] = (output, assertions)

        return results

    # =========================================================================
    # REGRESSION CHECKER
    # =========================================================================
    def detect_regressions(
        self,
        case_id: str,
        stage_name: str,
        assertions: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Checks if any assertion that previously passed in baseline now fails.
        Also flags failures in must_pass cases.
        """
        meta = self.load_case_meta(case_id)
        must_pass = meta.get("must_pass", True)
        regressions = []

        for a in assertions:
            key = f"{case_id}::{stage_name}::{a['name']}"
            passed_now = a["passed"]
            passed_before = self.baseline.get(key, None)

            # Regression definition: passed before, fails now
            if passed_before is True and not passed_now:
                reg = dict(a)
                reg["regression_type"] = "baseline_regression"
                regressions.append(reg)
            elif passed_before is None and not passed_now and must_pass:
                # New failure in a must_pass case
                reg = dict(a)
                reg["regression_type"] = "must_pass_failure"
                regressions.append(reg)

        return regressions
