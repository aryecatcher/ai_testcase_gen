import json
import os
import sys
from unittest.mock import AsyncMock, MagicMock

# Add project root to path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from src.core.ai.llm_service import _llm_cache_key
from src.core.generation.generator import TestCaseGenerator
from src.core.output.exporter import TestCaseExporter
from src.core.output.postman_exporter import PostmanExporter
from src.models.domain import IngestionMetadata, Requirement, TestCase, TestInstruction


def test_llm_cache_key_is_stable():
    key1 = _llm_cache_key("REQ-1", "a=1", "s=2")
    key2 = _llm_cache_key("REQ-1", "a=1", "s=2")
    key3 = _llm_cache_key("REQ-1", "a=9", "s=2")

    assert key1 == key2
    assert key1 != key3
    assert key1.startswith("REQ-1_")


def test_generator_smoke_returns_case_objects():
    mock_llm = MagicMock()
    mock_llm.async_client = None
    mock_llm.async_generate_cases = AsyncMock(
        return_value=[
            {
                "title": "Case A",
                "precondition": "Ready",
                "steps": [{"action": "open", "result": "ok"}],
                "expected_result": "Success",
                "test_data": {"valid": {}, "invalid": {}},
                "priority": "P2",
                "type": "Functional",
                "methodology": ["LLM"],
            }
        ]
    )

    mock_kg = MagicMock()
    mock_kg.get_related_constraints = MagicMock(return_value="")
    mock_kg.expand_scenarios = MagicMock(return_value=[])

    generator = TestCaseGenerator(mock_llm, mock_kg)
    req = Requirement(
        id="REQ-SMOKE",
        original_text="用户可以登录系统",
        ingestion_metadata=IngestionMetadata(source_file="smoke.txt"),
    )

    cases = generator.generate([req])
    assert cases
    assert cases[0].related_req_id == "REQ-SMOKE"
    assert cases[0].test_instruction.expected_result


def test_exporters_smoke_output_non_empty():
    case1 = TestCase(
        related_req_id="REQ-1",
        title="Functional Case",
        dimension="Functional",
        test_instruction=TestInstruction(
            pre_condition="User exists",
            steps=["Step 1"],
            expected_result="OK",
        ),
    )
    case2 = TestCase(
        related_req_id="REQ-2",
        title="API Case",
        dimension="Interface",
        test_instruction=TestInstruction(
            pre_condition="API up",
            steps=["Call API"],
            expected_result="200",
        ),
    )

    xlsx = TestCaseExporter([case1, case2]).to_excel()
    assert isinstance(xlsx, (bytes, bytearray))
    assert len(xlsx) > 100

    postman = PostmanExporter([case1, case2]).to_collection()
    payload = json.loads(postman.decode("utf-8"))
    assert payload["info"]["name"] == "Generated API Tests"
    assert len(payload["item"]) == 1
