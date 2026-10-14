import pytest
from tests_agent.graph import downstream_of, get_stages_in_order, upstream_of
from tests_agent.detect_changes import map_files_to_stages, get_test_plan
from tests_agent.fix_agent import validate_diff_scope


def test_graph_stages_order():
    order = get_stages_in_order()
    assert order == [
        "extract_text",
        "extract_fields",
        "check_documents",
        "validate_ticket",
        "generate_report"
    ]


def test_graph_downstream_of():
    # extract_text is the root: all other stages are downstream
    assert downstream_of("extract_text") == [
        "extract_fields",
        "check_documents",
        "validate_ticket",
        "generate_report"
    ]

    # check_documents only flows to validate_ticket and generate_report
    assert downstream_of("check_documents") == [
        "validate_ticket",
        "generate_report"
    ]

    # extract_fields flows to validate_ticket and generate_report
    assert downstream_of("extract_fields") == [
        "validate_ticket",
        "generate_report"
    ]

    # validate_ticket only flows to generate_report
    assert downstream_of("validate_ticket") == [
        "generate_report"
    ]

    # generate_report is the sink: nothing downstream
    assert downstream_of("generate_report") == []


def test_graph_upstream_of():
    assert "extract_text" in upstream_of("check_documents")
    assert "check_documents" in upstream_of("validate_ticket")
    assert "validate_ticket" in upstream_of("generate_report")


def test_detect_changes_mapping():
    # Prompts map to exact stages
    stages1 = map_files_to_stages(["prompts/extract_fields.md"])
    assert stages1 == {"extract_fields"}

    stages2 = map_files_to_stages(["prompts/validate_ticket.md"])
    assert stages2 == {"validate_ticket"}

    # Config change maps to check_documents
    stages3 = map_files_to_stages(["config/required_docs.yaml"])
    assert stages3 == {"check_documents"}

    # Pipeline code change affects all stages
    stages4 = map_files_to_stages(["pipeline/contracts.py"])
    assert len(stages4) == 5


def test_detect_changes_test_plan():
    changed = {"check_documents"}
    stage_tests, prop_tests = get_test_plan(changed)
    assert stage_tests == ["check_documents"]
    assert prop_tests == ["validate_ticket", "generate_report"]
    # Upstream stage (extract_text) must NOT be in the plan
    assert "extract_text" not in stage_tests
    assert "extract_text" not in prop_tests


def test_diff_scope_guard():
    # Valid diff touching only prompts/
    valid_diff = """--- a/prompts/validate_ticket.md
+++ b/prompts/validate_ticket.md
@@ -10,3 +10,4 @@
+Verify all signatures strictly.
"""
    is_valid, err = validate_diff_scope(valid_diff)
    assert is_valid is True
    assert err is None

    # Invalid diff touching code outside prompts/
    invalid_diff = """--- a/pipeline/stages.py
+++ b/pipeline/stages.py
@@ -1,3 +1,4 @@
+import hack
"""
    is_valid, err = validate_diff_scope(invalid_diff)
    assert is_valid is False
    assert "pipeline/stages.py" in err

    # Invalid diff touching root
    invalid_diff_root = """--- a/config/required_docs.yaml
+++ b/config/required_docs.yaml
@@ -1,2 +1,3 @@
"""
    is_valid, err = validate_diff_scope(invalid_diff_root)
    assert is_valid is False
